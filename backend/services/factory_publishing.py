"""Durable publication jobs. Upload, submission and public visibility are distinct."""
import time
from urllib.parse import urlsplit
from sqlalchemy.exc import IntegrityError
from database import SessionLocal
from models.factory import FactoryItem, FactoryPublication, FactoryEvent


def job_dict(job):
    return {"id": job.id, "item_id": job.item_id, "platform": job.platform,
            "action": job.action, "status": job.status, "result": job.result or {},
            "message": (job.result or {}).get("message", "平台任务已创建")}


def safe_to_retry(job):
    """Only explicit proof that no submit click occurred permits another attempt."""
    result = job.result or {}
    return (job.status == "failed" and result.get("submission_attempted") is False
            and not result.get("submission_uncertain"))


def refresh_job(db, job):
    if not job.command_id or job.status in {"submitted", "failed", "prepared"}:
        return job
    from services.xiaohongshu_connector import command_result
    result = command_result(job.command_id)
    if result.get("status") == "submitted":
        evidence = result.get("evidence") or {}
        kind = evidence.get("type") if isinstance(evidence, dict) else None
        valid = kind == "api_receipt" and bool(evidence.get("note_id"))
        if kind == "success_page":
            address = urlsplit(evidence.get("url", ""))
            valid = (address.hostname == "creator.xiaohongshu.com" and "发布成功" in evidence.get("text", ""))
        if not valid:
            result = {**result, "status": "failed", "submission_uncertain": True,
                      "message": "没有取得可核验的提交回执，请在平台核对结果；不会自动重复发布"}
    job.status, job.result = result.get("status", "failed"), result
    if job.status in {"prepared", "submitted", "failed"}:
        item = db.get(FactoryItem, job.item_id)
        packages = [dict(package) for package in item.packages or []]
        for package in packages:
            if package["platform"] != job.platform:
                continue
            package["publication_job_id"] = job.id
            if job.status == "submitted":
                package["status"] = "submitted"
                package["submission_evidence"] = result.get("evidence", {})
            elif job.status == "prepared":
                package["status"] = "prepared"
            else:
                package["publication_error"] = result.get("message", "平台任务未完成")
        item.packages = packages
        db.add(FactoryEvent(run_id=item.run_id, agent_id="publisher", stage="platform_submission",
            status="completed" if job.status in {"prepared", "submitted"} else "warning",
            message=f"小红书：{result.get('message', job.status)}"))
    db.commit()
    return job


def begin_publication(db, item_id, action):
    if action not in {"prepare", "publish"}:
        raise ValueError("不支持的发布操作")
    item = db.get(FactoryItem, item_id)
    if not item or item.status != "completed" or not item.media.get("video_path"):
        raise ValueError("需要先完成视频制作、审核与平台素材包")
    if item.media.get("publication_hold"):
        raise ValueError(f"暂缓提交：{item.media['publication_hold']}")
    variant = next((p for p in item.packages or [] if p["platform"] == "xiaohongshu"), None)
    if not variant:
        raise ValueError("缺少小红书发布文案")
    existing = db.query(FactoryPublication).filter_by(item_id=item_id, platform="xiaohongshu", action=action).first()
    reuse_job = False
    if existing:
        refresh_job(db, existing)
        # A submission attempt is never blindly retried: it could already exist online.
        if (action == "publish" and not safe_to_retry(existing)) or existing.status in {"queued", "running", "submitted"}:
            return existing
        from services.xiaohongshu_connector import connection_state
        if existing.status == "prepared" and connection_state().get("prepared_item_id") == item_id:
            return existing
        submission = db.query(FactoryPublication).filter_by(item_id=item_id, platform="xiaohongshu", action="publish").first()
        if action == "prepare" and submission and not safe_to_retry(submission):
            raise ValueError("该作品已有提交尝试，请先在平台核对结果，禁止重复发布")
        reuse_job = True
    if action == "publish":
        prepared = db.query(FactoryPublication).filter_by(item_id=item_id, platform="xiaohongshu", action="prepare").first()
        if prepared:
            refresh_job(db, prepared)
        if not prepared or prepared.status != "prepared":
            raise ValueError("请先上传并准备这条视频，再提交发布")
    from services.xiaohongshu_connector import submit_command
    payload = {"item_id": item.id}
    if action == "prepare":
        payload.update(video_path=item.media["video_path"], cover_path=item.media["cover_path"],
                       title=variant["title"], caption=variant.get("caption", ""), tags=variant.get("tags", []))
    job = existing if reuse_job else FactoryPublication(item_id=item_id, action=action)
    if reuse_job and action == "publish":
        db.add(FactoryEvent(run_id=item.run_id, agent_id="publisher", stage="platform_retry",
            status="info", message="上次操作已确认未点击提交，重新准备后安全重试",
            detail={"previous_command_id": job.command_id, "previous_result": job.result}))
    job.status, job.result = "queued", {}
    job.command_id = None
    if not reuse_job:
        db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(FactoryPublication).filter_by(item_id=item_id, platform="xiaohongshu", action=action).one()
    try:
        command = submit_command(action, payload)
        job.command_id, job.result = command["command_id"], command
        db.commit()
    except Exception as exc:
        job.status, job.result = "failed", {"message": str(exc)[:500]}
        db.commit()
    return job


def auto_submit(item_id, run_id):
    from services.factory_service import check_cancel, emit
    from services.xiaohongshu_connector import connection_state, submit_command, command_result
    state = connection_state()
    if not state.get("authenticated"):
        # Reopen the retained profile, without bypassing any account verification.
        inspection = submit_command("inspect")
        for _ in range(90):
            check_cancel(run_id)
            if command_result(inspection["command_id"]).get("status") not in {"queued", "running"}:
                break
            time.sleep(1)
        state = connection_state()
    if not state.get("authenticated"):
        emit(run_id, "publisher", "platform_submission", "小红书尚未登录，已保留发布素材包，等待扫码接入", "warning")
        return
    for action in ("prepare", "publish"):
        check_cancel(run_id)
        emit(run_id, "publisher", "platform_submission", "正在上传小红书并准备发布表单" if action == "prepare" else "正在向小红书提交视频")
        with SessionLocal() as db:
            job_id = begin_publication(db, item_id, action).id
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            check_cancel(run_id)
            with SessionLocal() as db:
                job = refresh_job(db, db.get(FactoryPublication, job_id))
                status, message = job.status, (job.result or {}).get("message", "")
            if status in {"prepared", "submitted"}:
                break
            if status == "failed":
                raise RuntimeError(message or "平台操作未完成")
            time.sleep(2)
        else:
            raise RuntimeError("平台任务仍在执行，需核对结果后继续，未自动重复提交")
