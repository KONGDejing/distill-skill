"""Durable publication jobs. Upload, submission and public visibility are distinct."""
import time
from urllib.parse import urlsplit
from sqlalchemy.exc import IntegrityError
from database import SessionLocal
from models.factory import FactoryItem, FactoryPublication, FactoryEvent
from services.factory_resources import ensure_memory_available


def _platform_name(platform):
    if platform == "xiaohongshu":
        return "小红书"
    from services.creator_connector import PLATFORMS
    if platform not in PLATFORMS:
        raise ValueError("不支持的发布平台")
    return PLATFORMS[platform]["name"]


def _connection_state(platform):
    if platform == "xiaohongshu":
        from services.xiaohongshu_connector import connection_state
        return connection_state()
    from services.creator_connector import connection_state
    return connection_state(platform)


def _command_result(platform, command_id):
    if platform == "xiaohongshu":
        from services.xiaohongshu_connector import command_result
        return command_result(command_id)
    from services.creator_connector import command_result
    return command_result(platform, command_id)


def _submit_command(platform, action, payload):
    if platform == "xiaohongshu":
        from services.xiaohongshu_connector import submit_command
        return submit_command(action, payload)
    from services.creator_connector import submit_command
    return submit_command(platform, action, payload)


def _valid_evidence(platform, evidence):
    if platform != "xiaohongshu":
        from services.creator_connector import _valid_evidence as validate
        return validate(platform, evidence)
    # Preserve receipts already produced by the established XHS connector,
    # which validates the official response hostname before recording a note ID.
    kind = evidence.get("type") if isinstance(evidence, dict) else None
    if kind == "api_receipt":
        return bool(evidence.get("note_id"))
    if kind == "success_page":
        address = urlsplit(evidence.get("url", ""))
        return address.hostname == "creator.xiaohongshu.com" and "发布成功" in evidence.get("text", "")
    return False


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
    platform_name = _platform_name(job.platform)
    result = _command_result(job.platform, job.command_id)
    if not isinstance(result, dict):
        result = {"status": "failed", "submission_uncertain": job.action == "publish", "message": "平台返回状态无效，需核对实际结果"}
    if result.get("status") == "submitted":
        evidence = result.get("evidence") or {}
        valid = _valid_evidence(job.platform, evidence)
        if result.get("platform") not in (None, job.platform) or result.get("item_id") not in (None, job.item_id):
            valid = False
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
            package["published"] = False
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
            message=f"{platform_name}：{result.get('message', job.status)}"))
    db.commit()
    return job


def begin_publication(db, item_id, action, platform="xiaohongshu"):
    platform_name = _platform_name(platform)
    if action not in {"prepare", "publish"}:
        raise ValueError("不支持的发布操作")
    item = db.get(FactoryItem, item_id)
    if not item or item.status != "completed" or not item.media.get("video_path"):
        raise ValueError("需要先完成视频制作、审核与平台素材包")
    if item.media.get("publication_hold"):
        raise ValueError(f"暂缓提交：{item.media['publication_hold']}")
    variant = next((p for p in item.packages or [] if p["platform"] == platform), None)
    if not variant:
        raise ValueError(f"缺少{platform_name}发布文案")
    existing = db.query(FactoryPublication).filter_by(item_id=item_id, platform=platform, action=action).first()
    reuse_job = False
    if existing:
        refresh_job(db, existing)
        # A submission attempt is never blindly retried: it could already exist online.
        if (action == "publish" and not safe_to_retry(existing)) or existing.status in {"queued", "running", "submitted"}:
            return existing
        submission = db.query(FactoryPublication).filter_by(item_id=item_id, platform=platform, action="publish").first()
        if action == "prepare" and submission and not safe_to_retry(submission):
            raise ValueError("该作品已有提交尝试，请先在平台核对结果，禁止重复发布")
        if existing.status == "prepared" and _connection_state(platform).get("prepared_item_id") == item_id:
            return existing
        reuse_job = True
    if action == "publish":
        prepared = db.query(FactoryPublication).filter_by(item_id=item_id, platform=platform, action="prepare").first()
        if prepared:
            refresh_job(db, prepared)
        if not prepared or prepared.status != "prepared":
            raise ValueError("请先上传并准备这条视频，再提交发布")
    if platform != "xiaohongshu":
        state = _connection_state(platform)
        if not state.get("authenticated"):
            raise ValueError(f"请先在{platform_name}官方页面扫码并完成账号核验")
        if state.get("capabilities", {}).get(action) is not True:
            step = "上传" if action == "prepare" else "发布"
            raise ValueError(f"{platform_name}{step}页面尚未完成真实联调，未创建操作任务")
    ensure_memory_available()
    payload = {"item_id": item.id}
    if action == "prepare":
        payload.update(video_path=item.media["video_path"], cover_path=item.media["cover_path"],
                       title=variant["title"], caption=variant.get("caption", ""), tags=variant.get("tags", []))
    job = existing if reuse_job else FactoryPublication(item_id=item_id, platform=platform, action=action)
    if reuse_job and action == "publish":
        db.add(FactoryEvent(run_id=item.run_id, agent_id="publisher", stage="platform_retry",
            status="info", message=f"{platform_name}：上次操作已确认未点击提交，重新准备后安全重试",
            detail={"previous_command_id": job.command_id, "previous_result": job.result}))
    job.status, job.result = "queued", {}
    job.command_id = None
    if not reuse_job:
        db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(FactoryPublication).filter_by(item_id=item_id, platform=platform, action=action).one()
    try:
        command = _submit_command(platform, action, payload)
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
