"""Official login views for creator platforms; private profiles are never served."""
from html import escape
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from database import get_db
from models.factory import FactoryPublication
from services import creator_connector
from services.factory_resources import MemoryPressure

router = APIRouter()


def require_platform(platform):
    if platform not in creator_connector.PLATFORMS:
        raise HTTPException(404, "该创作平台尚未接入")
    return creator_connector.PLATFORMS[platform]


@router.get("/platforms/{platform}/login-state")
def login_state(platform: str):
    require_platform(platform)
    return JSONResponse(creator_connector.connection_state(platform), headers={"Cache-Control": "no-store, private"})


@router.post("/platforms/{platform}/connect", status_code=202)
def connect(platform: str):
    require_platform(platform)
    try:
        return creator_connector.submit_command(platform, "login")
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/platforms/{platform}/login-image", include_in_schema=False)
def login_image(platform: str):
    require_platform(platform)
    state = creator_connector.connection_state(platform)
    path = creator_connector.login_image_path(platform)
    if not state.get("qr_ready") or not path.is_file():
        raise HTTPException(404, "当前没有可用的官方登录二维码，请启动或刷新登录会话")
    try:
        data = path.read_bytes()
    except OSError:
        raise HTTPException(404, "二维码正在更新，请稍候") from None
    return Response(data, media_type="image/png", headers={"Cache-Control": "no-store, private"})


@router.get("/platforms/{platform}/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(platform: str):
    from services.creator_verification import page_token
    details = require_platform(platform)
    page = _LOGIN_PAGE.replace("__PLATFORM__", platform).replace("__NAME__", escape(details["name"]))
    page = page.replace("__VERIFICATION_TOKEN__", page_token(platform))
    return HTMLResponse(page, headers={"Cache-Control": "no-store, private"})


def authorize_verification(platform, request):
    from services.creator_verification import authorize
    require_platform(platform)
    try:
        authorize(platform, request.headers.get("X-Factory-Verification", ""), request.headers.get("origin"),
                  str(request.base_url).rstrip("/"), request.headers.get("sec-fetch-site"))
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from None


@router.get("/platforms/{platform}/verification-frame", include_in_schema=False)
def verification_frame(platform: str, request: Request):
    from services.creator_verification import frame
    authorize_verification(platform, request)
    try:
        data, identifier = frame(platform)
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-store, private",
                                                               "X-Verification-Frame": identifier})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "暂时无法读取官方验证窗口，请稍候重试") from None


@router.post("/platforms/{platform}/verification-input", include_in_schema=False)
async def verification_input(platform: str, request: Request):
    from services.creator_verification import interact
    authorize_verification(platform, request)
    # Read in memory, outside durable connector commands and validation errors
    # that might echo a user's one-time code back in a response.
    raw = await request.body()
    if len(raw) > 2048:
        raise HTTPException(400, "验证请求过长")
    try:
        import json
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(400, "无效验证请求") from None
    try:
        result = await run_in_threadpool(interact, platform, value.get("action"), value.get("frame_id"),
                                        value.get("x"), value.get("y"), value.get("code"))
        return JSONResponse(result, headers={"Cache-Control": "no-store, private"})
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "官方验证操作暂未完成，请查看窗口结果") from None


@router.get("/platforms/{platform}/login-commands/{command_id}")
def login_command(platform: str, command_id: str):
    require_platform(platform)
    try:
        result = creator_connector.command_result(platform, command_id)
    except ValueError as exc:
        raise HTTPException(404, "登录任务不存在") from None
    if result.get("action") != "login":
        raise HTTPException(404, "登录任务不存在")
    return JSONResponse({key: result.get(key) for key in
        ("platform", "command_id", "status", "message", "created_at", "updated_at")},
        headers={"Cache-Control": "no-store, private"})


@router.post("/items/{item_id}/platforms/{platform}/{action}", status_code=202)
def creator_action(item_id: str, platform: str, action: str, db: Session = Depends(get_db)):
    require_platform(platform)
    from services.factory_publishing import begin_publication, job_dict
    try:
        return job_dict(begin_publication(db, item_id, action, platform=platform))
    except MemoryPressure as exc:
        raise HTTPException(409, f"内存保护：{exc}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/platforms/{platform}/jobs/{job_id}")
def creator_job(platform: str, job_id: str, db: Session = Depends(get_db)):
    require_platform(platform)
    from services.factory_publishing import refresh_job, job_dict
    job = db.get(FactoryPublication, job_id)
    if not job or job.platform != platform:
        raise HTTPException(404, "该平台任务不存在")
    return job_dict(refresh_job(db, job))


@router.post("/platforms/{platform}/jobs/{job_id}/publish", status_code=202)
def creator_publish(platform: str, job_id: str, db: Session = Depends(get_db)):
    require_platform(platform)
    from services.factory_publishing import begin_publication, job_dict
    job = db.get(FactoryPublication, job_id)
    if not job or job.platform != platform:
        raise HTTPException(404, "该平台任务不存在")
    try:
        return job_dict(begin_publication(db, job.item_id, "publish", platform=platform))
    except MemoryPressure as exc:
        raise HTTPException(409, f"内存保护：{exc}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


_LOGIN_PAGE = (Path(__file__).resolve().parents[1] / "templates/creator_login.html").read_text(encoding="utf-8")
