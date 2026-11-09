from pathlib import Path
from typing import Annotated, Literal
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database import get_db
from config import settings
from agents.registry import AGENTS, PLATFORMS
from models.factory import FactoryRun, FactoryItem, FactoryEvent, FactoryPublication
from services.factory_service import get_config, config_dict, run_dict, run_detail, start_run, queue_available, FACTORY_DIR, TERMINAL
from services.factory_resources import MemoryPressure, ensure_memory_available, memory_state

router = APIRouter()
LOGIN_DIR = Path(__file__).resolve().parents[2] / ".runtime" / "qa"


class StartRun(BaseModel):
    target_count: int | None = Field(default=None, ge=1, le=10)


class SettingsUpdate(BaseModel):
    daily_target: int | None = Field(default=None, ge=1, le=10)
    schedule_enabled: bool | None = None
    schedule_time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    auto_publish_xiaohongshu: bool | None = None
    platforms: list[Literal["douyin", "xiaohongshu", "kuaishou"]] | None = Field(default=None, min_length=1, max_length=3)
    visual_style: Literal["tech_montage", "cards"] | None = None
    publish_enabled: bool | None = None
    publish_times: list[Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]] | None = Field(default=None, min_length=1, max_length=10)

    @field_validator("publish_times", "platforms")
    @classmethod
    def distinct_values(cls, values):
        if values is not None and len(values) != len(set(values)):
            raise ValueError("不能有重复的时间或平台")
        return sorted(values) if values is not None else values


def require_run(db, run_id):
    run = db.get(FactoryRun, run_id)
    if not run:
        raise HTTPException(404, "生产批次不存在")
    return run


@router.get("")
def overview(db: Session = Depends(get_db)):
    config = config_dict(get_config(db))
    runs = db.query(FactoryRun).order_by(FactoryRun.created_at.desc()).limit(30).all()
    active = next((run for run in runs if run.status not in TERMINAL), None)
    employees = []
    for agent in AGENTS:
        event = db.query(FactoryEvent).filter_by(run_id=active.id, agent_id=agent["id"]).filter(FactoryEvent.stage.notin_(["model_call", "model_retry"])).order_by(FactoryEvent.id.desc()).first() if active else None
        employees.append({**agent, "status": "working" if event and event.status == "running" else "idle",
                          "current_task": event.message if event and event.status == "running" else None})
    platforms = [dict(platform) for platform in PLATFORMS]
    from services.xiaohongshu_connector import connection_state
    login = connection_state()
    if login.get("authenticated"):
        platform = next(p for p in platforms if p["id"] == "xiaohongshu")
        platform["login_status"] = "authenticated"
        platform["account_name"] = login.get("display_name")
        verified = db.query(FactoryPublication).filter_by(platform="xiaohongshu", status="submitted").first() is not None
        platform["status"] = "connected" if verified else "not_connected"
        platform["description"] = f"{login.get('display_name') or '账号'} 已登录，" + ("浏览器提交已验证；最终审核以平台为准。" if verified else "视频提交功能等待首条验证。")
    from services.creator_connector import connection_state as creator_state
    for platform in platforms:
        platform["enabled"] = platform["id"] in config["platforms"]
        if not platform["enabled"]:
            platform.update(status="paused", description="小麦负责后续 B站长视频运营；当前不参与短视频生产与发布。" if platform["id"] == "bilibili" else "已退出当前短视频发行计划。")
            continue
        if platform["id"] not in {"douyin", "kuaishou", "bilibili"}:
            continue
        state = creator_state(platform["id"])
        platform["login_status"] = "authenticated" if state.get("authenticated") else state.get("state_code", "unknown")
        platform["account_name"] = state.get("display_name")
        platform["capabilities"] = state.get("capabilities", {})
        verified = db.query(FactoryPublication).filter_by(platform=platform["id"], status="submitted").first() is not None
        platform["status"] = "connected" if state.get("authenticated") and verified else "not_connected"
        platform["description"] = ((f"{state.get('display_name') or '账号'} 已登录；" + ("已核验提交回执，公开状态以平台审核为准。" if verified else "上传与提交待实际验证。"))
                                   if state.get("authenticated") else f"{state.get('status', '未连接')}；完成官方扫码后继续验证发布。")
    from services.factory_schedule import upcoming_releases
    from services.factory_editorial import STRATEGY
    from services.factory_visuals import library_summary
    return {"settings": config, "agents": employees, "runs": [run_dict(run) for run in runs],
            "strategy": STRATEGY, "asset_library": library_summary(),
            "releases": upcoming_releases(db, get_config(db)),
            "platforms": platforms, "runtime": {"model": settings.CLAUDE_MODEL, "queue_available": queue_available(), "memory": memory_state()}}


@router.patch("/settings")
def update_settings(data: SettingsUpdate, db: Session = Depends(get_db)):
    config = get_config(db)
    updates = data.model_dump(exclude_none=True)
    merged = {**config_dict(config), **updates}
    if merged["publish_enabled"] and len(merged["publish_times"]) != merged["daily_target"]:
        raise HTTPException(422, "每个发布时间对应一条原创视频，时间数量应等于每日原创目标")
    if merged["publish_enabled"]:
        updates["auto_publish_xiaohongshu"] = False
        if not config.publish_enabled:
            config.publish_start_at = datetime.utcnow()
    for key, value in updates.items():
        setattr(config, key, value)
    db.commit()
    return config_dict(config)


@router.post("/runs", status_code=202)
def create_run(data: StartRun, db: Session = Depends(get_db)):
    try:
        return run_dict(start_run(db, data.target_count))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    from services.factory_publishing import refresh_job
    for job in db.query(FactoryPublication).join(FactoryItem, FactoryPublication.item_id == FactoryItem.id).filter(FactoryItem.run_id == run_id, FactoryPublication.status.in_(["queued", "running"])):
        refresh_job(db, job)
    return run_detail(db, run)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    if run.status in TERMINAL and run.status != "paused_memory":
        return run_dict(run)
    run.cancel_requested = True
    if run.status in {"queued", "paused_memory"}:
        run.status, run.active_slot, run.finished_at = "cancelled", None, datetime.utcnow()
    db.add(FactoryEvent(run_id=run.id, agent_id="director", stage="cancelling", status="cancelled", message="已请求停止，当前步骤结束后不再分派新任务"))
    db.commit()
    return run_dict(run)


@router.post("/runs/{run_id}/catch-up")
def catch_up_run(run_id: str, db: Session = Depends(get_db)):
    from services.factory_schedule import authorize_catch_up
    run = require_run(db, run_id)
    try:
        return authorize_catch_up(db, run, get_config(db))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/runs/{run_id}/retry", status_code=202)
def retry_run(run_id: str, db: Session = Depends(get_db)):
    run = require_run(db, run_id)
    if run.status not in {"failed", "partial", "cancelled", "paused_memory"}:
        raise HTTPException(409, "只有失败、部分完成、已取消或内存暂停的批次可以恢复")
    if run.llm_calls >= run.settings_snapshot.get("max_llm_calls", 32):
        raise HTTPException(409, "本轮模型调用预算已用完，请创建新批次")
    if not queue_available():
        raise HTTPException(503, "任务队列未连接")
    if db.query(FactoryRun).filter(FactoryRun.active_slot.isnot(None), FactoryRun.id != run_id).first():
        raise HTTPException(409, "已有批次在执行")
    if db.query(FactoryRun).filter(FactoryRun.status == "paused_memory", FactoryRun.id != run_id).first():
        raise HTTPException(409, "请先恢复或取消因内存暂停的批次")
    try:
        ensure_memory_available()
    except MemoryPressure as exc:
        raise HTTPException(409, f"暂不能恢复：{exc}") from None
    was_memory_paused = run.status == "paused_memory"
    run.status, run.stage, run.active_slot = "queued", "queued", "factory"
    run.cancel_requested, run.error, run.finished_at = False, None, None
    run.report = {}
    run.heartbeat_at = datetime.utcnow()
    for item in db.query(FactoryItem).filter_by(run_id=run.id):
        if item.status != "completed" and not was_memory_paused:
            item.status, item.error, item.revisions = "planned", None, 0
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "已有批次在执行") from None
    try:
        from tasks.factory_tasks import produce_run
        run.task_id = produce_run.delay(run.id).id
        db.commit()
    except Exception:
        run.status, run.active_slot = "failed", None
        run.error = "重试入队失败，请检查任务队列"
        db.commit()
        raise HTTPException(503, run.error) from None
    return run_dict(run)


@router.get("/items/{item_id}/packages/{platform}")
def download_package(item_id: str, platform: str, db: Session = Depends(get_db)):
    item = db.get(FactoryItem, item_id)
    if not item:
        raise HTTPException(404, "作品不存在")
    package = next((p for p in item.packages or [] if p["platform"] == platform), None)
    if not package:
        raise HTTPException(404, "平台素材包尚未就绪")
    path = Path(package["path"]).resolve()
    if not path.is_relative_to(FACTORY_DIR.resolve()) or not path.is_file():
        raise HTTPException(404, "素材包文件不存在")
    return FileResponse(path, media_type="application/zip", filename=f"{platform}-{item_id[:8]}.zip")


@router.get("/platforms/xiaohongshu/login-image", include_in_schema=False)
def xiaohongshu_login_image():
    path = LOGIN_DIR / "xiaohongshu-login.png"
    if not path.is_file():
        raise HTTPException(404, "登录会话尚未启动")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/platforms/xiaohongshu/login-state")
def xiaohongshu_login_state():
    from services.xiaohongshu_connector import connection_state
    return connection_state()


@router.post("/platforms/xiaohongshu/connect", status_code=202)
def connect_xiaohongshu():
    from services.xiaohongshu_connector import submit_command
    return submit_command("login")


@router.post("/items/{item_id}/platforms/xiaohongshu/{action}", status_code=202)
def xiaohongshu_action(item_id: str, action: str, db: Session = Depends(get_db)):
    from services.factory_publishing import begin_publication, job_dict
    try:
        return job_dict(begin_publication(db, item_id, action))
    except MemoryPressure as exc:
        raise HTTPException(409, f"内存保护：{exc}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/platform-jobs/{job_id}")
def get_publication_job(job_id: str, db: Session = Depends(get_db)):
    from services.factory_publishing import refresh_job, job_dict
    job = db.get(FactoryPublication, job_id)
    if not job:
        raise HTTPException(404, "平台任务不存在")
    return job_dict(refresh_job(db, job))


@router.post("/platform-jobs/{job_id}/publish", status_code=202)
def publish_prepared_job(job_id: str, db: Session = Depends(get_db)):
    from services.factory_publishing import begin_publication, job_dict
    job = db.get(FactoryPublication, job_id)
    if not job:
        raise HTTPException(404, "平台任务不存在")
    try:
        return job_dict(begin_publication(db, job.item_id, "publish", platform=job.platform))
    except MemoryPressure as exc:
        raise HTTPException(409, f"内存保护：{exc}") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/platforms/xiaohongshu/login", response_class=HTMLResponse, include_in_schema=False)
def xiaohongshu_login_page():
    return """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>小红书登录 · 短视频 AI 内容工厂</title><style>body{margin:0;background:#0b121a;color:#e5edf4;font:16px system-ui,sans-serif}main{max-width:1100px;margin:40px auto;padding:20px}h1{font-size:25px}p{color:#a8bac7;line-height:1.8}img{width:100%;border-radius:16px;background:white}a{color:#73edc7}strong{color:#73edc7}</style>
<main><a href="/">← 返回工厂</a><h1>连接小红书创作平台</h1><p>用小红书 App 扫描下图中的二维码，并在手机上确认登录。二维码来自官方创作平台的真实浏览器会话。<br>登录状态：<strong id="state">正在读取</strong></p><button onclick="connect()" style="padding:12px 20px;margin-bottom:20px;cursor:pointer">启动登录 / 刷新二维码</button><img id="qr" alt="小红书官方登录二维码" src="/api/factory/platforms/xiaohongshu/login-image"><p>已登录时会保留当前账号。若平台要求额外验证，请按手机或官方页面提示完成。</p></main>
<script>async function refresh(){try{const r=await fetch('/api/factory/platforms/xiaohongshu/login-state',{cache:'no-store'});const s=await r.json();document.getElementById('state').textContent=s.status+(s.display_name?' · '+s.display_name:'');document.getElementById('qr').src='/api/factory/platforms/xiaohongshu/login-image?t='+Date.now()}catch(e){document.getElementById('state').textContent='暂时无法连接'}}async function connect(){document.getElementById('state').textContent='正在打开官方登录会话';await fetch('/api/factory/platforms/xiaohongshu/connect',{method:'POST'});setTimeout(refresh,3000)}refresh();setInterval(refresh,10000)</script></html>"""
