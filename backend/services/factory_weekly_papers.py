"""Two independent manual-only paper productions per local calendar week."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from models.factory import FactoryRun, FactoryWorkOrder, FactoryItem
from services.factory_editorial_policy import WEEKLY_PAPER_DAYS


def plan_weekly_papers(db, config, now):
    if not config.schedule_enabled:
        return {'status': 'disabled'}
    local = now.astimezone(ZoneInfo(config.timezone))
    monday = local.date() - timedelta(days=local.weekday())
    due = [monday + timedelta(days=offset) for offset in WEEKLY_PAPER_DAYS]
    for day in due:
        key = f'weekly-paper:{day.isoformat()}'
        if db.get(FactoryWorkOrder, key) is None:
            from services.factory_schedule import utc
            db.add(FactoryWorkOrder(key=key, kind='weekly_paper', owner='producer2',
                title='本周论文专题：真实原文与实验结果',
                acceptance='独立约150秒成片、沉稳男声、真实PDF高亮与实验依据；人工交付，禁止抖音自动发布，不占每日6条。',
                due_at=utc(local.replace(year=day.year,month=day.month,day=day.day,hour=20,minute=0,second=0,microsecond=0))))
    db.commit()
    for day in due:
        if day > local.date() or (day == local.date() and local.hour < 10):
            continue
        key = f'weekly-paper:{day.isoformat()}'
        order = db.get(FactoryWorkOrder, key)
        existing_paper = db.get(FactoryItem, (order.evidence or {}).get('preexisting_paper_item_id')) if (order.evidence or {}).get('preexisting_paper_item_id') else None
        if existing_paper and existing_paper.status == 'completed' and existing_paper.brief.get('editor_id') == 'producer2':
            order.status = 'completed'
            order.evidence = {**order.evidence, 'done':1, 'required':1, 'checked_at':local.isoformat()}
            db.commit()
            continue
        run = db.query(FactoryRun).filter_by(schedule_key=key).first()
        if run:
            order.status = 'completed' if run.status == 'completed' else 'blocked' if run.status in {'failed','partial','cancelled','paused_memory'} else 'in_progress'
            order.evidence = {**(order.evidence or {}), 'run_id':run.id,'done':run.completed_count,
                'required':1,'checked_at':local.isoformat(),'blocker':run.error}
            db.commit()
            # A failed paper remains visible; never silently create another one.
            continue
        if db.query(FactoryRun).filter(FactoryRun.active_slot.isnot(None)).first():
            return {'status':'waiting_for_production_lane'}
        from services.factory_service import start_run
        try:
            run = start_run(db,target_count=1,trigger='scheduled',schedule_key=key,production_kind='weekly_papers')
        except (ValueError,RuntimeError) as error:
            order.status='blocked';order.evidence={'blocker':str(error),'checked_at':local.isoformat()};db.commit()
            return {'status':'blocked'}
        order.status='in_progress';order.evidence={'run_id':run.id,'done':0,'required':1,'checked_at':local.isoformat()};db.commit()
        return {'status':'queued','run_id':run.id}
    return {'status':'planned'}
