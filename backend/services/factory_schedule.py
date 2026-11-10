"""Durable timed releases. Production and browser jobs run independently."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from models.factory import FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryRun
from services import factory_publishing as publishing
from services.factory_resources import MemoryPressure, ensure_memory_available

PREPARE_LEAD = timedelta(minutes=20)
LATE_WINDOW = timedelta(minutes=15)
FINAL = {"submitted", "missed", "partial", "cancelled"}


def utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def catch_up_until(db, slot):
    run = db.get(FactoryRun, slot.run_id) if slot.run_id else None
    policy = (run.settings_snapshot or {}).get("authorized_catch_up", {}) if run else {}
    if slot.id not in policy.get("release_ids", []):
        return None
    try:
        return utc(datetime.fromisoformat(policy["until"]))
    except (KeyError, TypeError, ValueError):
        return None


def deadline(db, slot):
    return max(slot.due_at + LATE_WINDOW, catch_up_until(db, slot) or slot.due_at)


def authorize_catch_up(db, run, config, now=None):
    """An explicit request permits today's assigned releases until midnight.

    It neither creates past slots nor retries an uncertain submit. Future due
    times, platform settings, quality gates and memory protection still apply.
    """
    now = now or datetime.now(ZoneInfo(config.timezone))
    start = utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
    end = utc((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
    slots = db.query(FactoryRelease).filter(FactoryRelease.run_id == run.id,
        FactoryRelease.due_at >= start, FactoryRelease.due_at < end,
        FactoryRelease.status.notin_(["submitted", "cancelled"])).all()
    if not slots:
        raise ValueError("本轮没有今天待补齐的排期")
    until = end.replace(tzinfo=timezone.utc).isoformat()
    run.settings_snapshot = {**run.settings_snapshot, "authorized_catch_up": {
        "until": until, "release_ids": [slot.id for slot in slots], "requested_at": now.isoformat()}}
    for slot in slots:
        if slot.status in {"missed", "partial"}:
            slot.status = "scheduled"
    db.add(FactoryEvent(run_id=run.id, agent_id="director", stage="catch_up", status="completed",
        message="按本次明确要求补齐今日排期，补发授权截至今天结束；已提交和结果不明的作品仍保留防重保护。",
        detail={"until": until, "release_ids": [slot.id for slot in slots]}))
    db.commit()
    return {"run_id": run.id, "until": until, "release_ids": [slot.id for slot in slots]}


def release_dict(db, slot):
    item = db.get(FactoryItem, slot.item_id) if slot.item_id else None
    until = catch_up_until(db, slot)
    return {"id": slot.id, "due_at": slot.due_at.replace(tzinfo=timezone.utc).isoformat(),
            "item_id": slot.item_id, "run_id": slot.run_id, "status": slot.status,
            "title": (item.content or {}).get("title") if item else None,
            "platforms": slot.platforms, "results": slot.results or {},
            "catch_up_until": until.replace(tzinfo=timezone.utc).isoformat() if until else None}


def upcoming_releases(db, config):
    now = datetime.now(ZoneInfo(config.timezone))
    start = utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
    slots = db.query(FactoryRelease).filter(FactoryRelease.due_at >= start).order_by(FactoryRelease.due_at).limit(20)
    return [release_dict(db, slot) for slot in slots]


def plan_slots(db, config, now):
    """Only future slots are added. Enabling mid-day never backfills past times."""
    for offset in (0, 1):
        day = now.date() + timedelta(days=offset)
        start = utc(datetime.fromisoformat(day.isoformat()).replace(tzinfo=now.tzinfo))
        existing = db.query(FactoryRelease).filter(FactoryRelease.due_at >= start,
            FactoryRelease.due_at < start + timedelta(days=1)).all()
        # Changes apply to subsequent batches. Keep a producing day's mapping
        # stable so edits cannot bind a second slot to an already planned clip.
        if any(slot.run_id for slot in existing):
            continue
        for slot in existing:
            clock = slot.due_at.replace(tzinfo=timezone.utc).astimezone(now.tzinfo).strftime("%H:%M")
            if clock not in config.publish_times:
                slot.status = "cancelled"
        for clock in sorted(config.publish_times):
            due = utc(datetime.fromisoformat(f"{day.isoformat()}T{clock}").replace(tzinfo=now.tzinfo))
            if due < utc(now) or (config.publish_start_at and due < config.publish_start_at):
                continue
            slot = db.query(FactoryRelease).filter_by(due_at=due).first()
            if not slot:
                db.add(FactoryRelease(due_at=due, platforms=list(config.platforms)))
            elif not slot.run_id:
                slot.platforms = list(config.platforms)
                slot.status = "scheduled"
    db.commit()


def plan_production(db, config, now):
    from services.factory_service import start_run
    if not config.schedule_enabled or now.strftime("%H:%M") < config.schedule_time:
        return {"status": "waiting"}
    start = utc(now.replace(hour=0, minute=0, second=0, microsecond=0))
    key = f"releases:{now.date().isoformat()}"
    run = db.query(FactoryRun).filter_by(schedule_key=key).first()
    slots = db.query(FactoryRelease).filter(FactoryRelease.due_at >= start,
        FactoryRelease.due_at < start + timedelta(days=1), FactoryRelease.status != "cancelled").order_by(FactoryRelease.due_at).all()
    if run and (run.settings_snapshot or {}).get("release_slot_ids"):
        by_id = {slot.id: slot for slot in slots}
        slots = [by_id[identifier] for identifier in run.settings_snapshot["release_slot_ids"]]
    elif run and any(slot.run_id == run.id for slot in slots):
        slots = [slot for slot in slots if slot.run_id == run.id]
    elif not run:
        slots = [slot for slot in slots if slot.due_at + LATE_WINDOW >= utc(now)]
    if not slots or all(s.due_at + LATE_WINDOW < utc(now) for s in slots):
        return {"status": "no_slots"}
    if not run:
        try:
            run = start_run(db, target_count=len(slots), trigger="scheduled", schedule_key=key,
                            release_slot_ids=[slot.id for slot in slots])
        except (ValueError, RuntimeError) as exc:
            return {"status": "deferred", "reason": str(exc)}
    for position, slot in enumerate(slots):
        if not slot.run_id:
            slot.run_id, slot.position = run.id, position
    db.commit()
    return {"run_id": run.id, "status": run.status}


def _result(status, message, job=None):
    return {"status": status, "message": message, **({"job_id": job.id} if job else {})}


def step_platform(db, slot, item, platform, now, enabled):
    """Advance at most one command per tick; never retry ambiguous submissions."""
    jobs = {job.action: publishing.refresh_job(db, job) for job in
            db.query(FactoryPublication).filter_by(item_id=item.id, platform=platform).all()}
    submitted, prepared = jobs.get("publish"), jobs.get("prepare")
    if submitted:
        if submitted.status == "submitted":
            return _result("submitted", "平台已确认接收，公开状态以审核为准", submitted)
        if submitted.status in {"queued", "running"}:
            return _result("publishing", "正在核对平台提交回执", submitted)
        detail = (submitted.result or {}).get("message") or "提交未完成"
        if (submitted.result or {}).get("submission_uncertain"):
            detail = "小麦正在核对官方记录，暂不需用户操作；不会重复提交。" + detail
        if item.media.get("publication_hold"):
            detail += "；此旧版已停发，保留历史提交记录"
        return _result("blocked", detail, submitted)
    if prepared and prepared.status in {"queued", "running"}:
        return _result("uploading", "正在上传视频", prepared)
    if not enabled:
        return _result("paused", "当前发行计划已暂停此平台")
    if now >= deadline(db, slot):
        message = ("今日补发授权已到期，保留作品，未跨日自动补发" if catch_up_until(db, slot)
                   else "已超过发布时间 15 分钟，保留作品，不集中补发")
        return _result("missed", message)
    if prepared and prepared.status == "failed":
        return _result("blocked", "上传未完成，请检查任务结果后处理", prepared)
    if now < slot.due_at - PREPARE_LEAD:
        return _result("waiting", "等待提前上传")
    # Do not overwrite another video being edited or submitted in this browser.
    pending = db.query(FactoryPublication).filter(FactoryPublication.platform == platform,
        FactoryPublication.item_id != item.id, FactoryPublication.status.in_(["queued", "running"])).all()
    for other in pending:
        if publishing.refresh_job(db, other).status in {"queued", "running"}:
            return _result("blocked", "平台仍在处理上一条任务")
    state = publishing._connection_state(platform)
    if not state.get("authenticated"):
        message = ("请在官方窗口完成额外身份验证，保留当前会话" if state.get("state_code") == "needs_verification"
                   else "需要重新登录账号")
        return _result("blocked", message)
    if platform != "xiaohongshu" and not all(state.get("capabilities", {}).get(a) for a in ("prepare", "publish")):
        return _result("blocked", "账号已登录，上传与提交仍待联调")
    try:
        ensure_memory_available()
        if prepared and prepared.status == "prepared" and state.get("prepared_item_id") == item.id:
            if now < slot.due_at:
                return _result("prepared", "已提前上传，等待到点提交", prepared)
            job = publishing.begin_publication(db, item.id, "publish", platform)
            return _result("publishing", "已到发布时间，正在提交", job)
        job = publishing.begin_publication(db, item.id, "prepare", platform)
        return _result("uploading", "正在提前上传并填写发布信息", job)
    except (ValueError, RuntimeError, MemoryPressure) as exc:
        return _result("blocked", str(exc)[:300])


def advance_slot(db, slot, config, now):
    enabled = config.publish_enabled
    if not slot.item_id and slot.run_id:
        item = db.query(FactoryItem).filter_by(run_id=slot.run_id, position=slot.position).first()
        if item:
            slot.item_id = item.id
            db.commit()
    item = db.get(FactoryItem, slot.item_id) if slot.item_id else None
    if item is None or item.status != "completed":
        slot.status = "missed" if now >= deadline(db, slot) else "waiting_media"
        run = db.get(FactoryRun, slot.run_id or item.run_id) if slot.run_id or item else None
        error = (item.error if item else None) or (run.error if run else None)
        message = "制作未完成：" + error[:300] if error else "成片未就绪或未通过质检，未发布"
        slot.results = {p: _result(slot.status, message) for p in slot.platforms}
    else:
        # An explicit memory pause stops new publishing even if other clips are ready.
        paused = db.query(FactoryRun).filter_by(status="paused_memory").first() is not None
        results = {p: step_platform(db, slot, item, p, now,
                    enabled and p in config.platforms and not paused) for p in slot.platforms}
        statuses = {r["status"] for r in results.values()}
        if statuses == {"submitted"}:
            slot.status = "submitted"
        elif statuses & {"uploading", "publishing"}:
            slot.status = "publishing"
        elif now >= deadline(db, slot):
            slot.status = "partial" if "submitted" in statuses else "missed"
        elif "blocked" in statuses:
            slot.status = "blocked"
        else:
            slot.status = "scheduled"
        slot.results = results
    db.commit()


def tick(db, config, now=None):
    now = now or datetime.now(ZoneInfo(config.timezone))
    if config.publish_enabled:
        plan_slots(db, config, now)
        production = plan_production(db, config, now)
    else:
        production = {"status": "disabled"}
    slots = db.query(FactoryRelease).filter(FactoryRelease.status.notin_(FINAL),
        FactoryRelease.due_at <= utc(now) + PREPARE_LEAD).order_by(FactoryRelease.due_at).all()
    for slot in slots:
        advance_slot(db, slot, config, utc(now))
    return {"production": production, "slots_checked": len(slots)}
