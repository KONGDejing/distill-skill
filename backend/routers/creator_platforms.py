"""Official login views for creator platforms; private profiles are never served."""
from html import escape
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from models.factory import FactoryPublication
from services import creator_connector

router = APIRouter()


def require_platform(platform):
    if platform not in creator_connector.PLATFORMS:
        raise HTTPException(404, "该创作平台尚未接入")
    return creator_connector.PLATFORMS[platform]


@router.get("/platforms/{platform}/login-state")
def login_state(platform: str):
    require_platform(platform)
    return creator_connector.connection_state(platform)


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
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store, private"})


@router.get("/platforms/{platform}/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(platform: str):
    details = require_platform(platform)
    return _LOGIN_PAGE.replace("__PLATFORM__", platform).replace("__NAME__", escape(details["name"]))


@router.post("/items/{item_id}/platforms/{platform}/{action}", status_code=202)
def creator_action(item_id: str, platform: str, action: str, db: Session = Depends(get_db)):
    require_platform(platform)
    from services.factory_publishing import begin_publication, job_dict
    try:
        return job_dict(begin_publication(db, item_id, action, platform=platform))
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
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


_LOGIN_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>连接__NAME__ · 短视频 AI 内容工厂</title>
<style>body{margin:0;background:#0b121a;color:#e5edf4;font:16px system-ui,sans-serif}main{max-width:1100px;margin:30px auto;padding:20px}h1{font-size:25px}p{color:#a8bac7;line-height:1.8}img{max-width:100%;border-radius:16px;background:white}a,strong{color:#73edc7}button{padding:12px 20px;margin:12px 0 20px;cursor:pointer;border:0;border-radius:10px;background:#73edc7;color:#10251e}#error{color:#fbbf24}</style>
<main><a href="/">← 返回工厂</a><h1>连接__NAME__创作平台</h1>
<p>点击启动后，用__NAME__ App 扫描官方页面二维码，并在手机上确认登录。若平台要求额外验证，请按官方提示完成。<br>登录状态：<strong id="state">正在读取</strong></p>
<button id="connect" onclick="connect()">启动登录 / 刷新二维码</button><p id="error"></p><img id="qr" hidden alt="__NAME__官方登录二维码">
<p>登录成功与视频提交成功分别记录。上传及提交将继续通过官方创作页面验证。</p></main>
<script>
const base='/api/factory/platforms/__PLATFORM__';
async function refresh(){try{const r=await fetch(base+'/login-state',{cache:'no-store'});if(!r.ok)throw new Error('状态读取失败');const s=await r.json();document.getElementById('state').textContent=s.status+(s.display_name?' · '+s.display_name:'');document.getElementById('error').textContent=s.error||'';const qr=document.getElementById('qr');qr.hidden=!s.qr_ready;if(s.qr_ready)qr.src=base+'/login-image?t='+Date.now()}catch(e){document.getElementById('state').textContent='暂时无法连接'}}
async function connect(){const button=document.getElementById('connect');button.disabled=true;document.getElementById('state').textContent='正在打开官方登录会话';try{const r=await fetch(base+'/connect',{method:'POST'});if(!r.ok){const value=await r.json();throw new Error(value.detail||'启动失败')}setTimeout(refresh,2500)}catch(e){document.getElementById('error').textContent=e.message}finally{button.disabled=false}}
refresh();setInterval(refresh,5000);
</script></html>"""
