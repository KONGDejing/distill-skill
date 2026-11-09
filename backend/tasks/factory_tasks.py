from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from tasks.celery_app import celery_app
from database import SessionLocal, init_db
from models.factory import FactoryRun
from services.factory_service import execute_run, get_config, start_run


@celery_app.task(name="factory.produce", acks_late=True, reject_on_worker_lost=True)
def produce_run(run_id):
    init_db()
    return execute_run(run_id)


@celery_app.task(name="factory.schedule")
def schedule_factory():
    """Read the live schedule every minute; a daily unique key prevents repeats."""
    init_db()
    with SessionLocal() as db:
        paused = db.query(FactoryRun).filter_by(status="paused_memory").first()
        if paused:
            return {"status": "paused_memory", "run_id": paused.id}
        active = db.query(FactoryRun).filter(FactoryRun.active_slot.isnot(None)).first()
        if active:
            if (datetime.utcnow() - active.heartbeat_at).total_seconds() > 600:
                active.task_id = produce_run.delay(active.id).id
                active.heartbeat_at = datetime.utcnow()
                db.commit()
                return {"recovered": active.id}
            return {"status": "busy"}
        config = get_config(db)
        if not config.schedule_enabled:
            return {"status": "disabled"}
        now = datetime.now(ZoneInfo(config.timezone))
        if now.strftime("%H:%M") < config.schedule_time:
            return {"status": "waiting"}
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        # A manual production run counts as today's plan, avoiding surprise duplicates.
        if db.query(FactoryRun).filter(FactoryRun.created_at >= day_start).first():
            return {"status": "already_planned"}
        key = f"daily:{now.date().isoformat()}"
        try:
            run = start_run(db, trigger="scheduled", schedule_key=key)
            return {"created": run.id}
        except (ValueError, RuntimeError) as exc:
            return {"status": "deferred", "reason": str(exc)}
