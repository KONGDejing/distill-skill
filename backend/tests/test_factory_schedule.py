"""Clock and release guarantees; no real upload, model call or broker."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryConfig, FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryRun
from services import factory_schedule as schedule
from services import factory_service, factory_resources

TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'clock.db'}")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in
        (FactoryConfig, FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryRun)])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(factory_resources, "memory_state", lambda **kw: {"blocked": False})
    monkeypatch.setattr(schedule.publishing, "refresh_job", lambda db, job: job)
    monkeypatch.setattr(schedule.publishing, "_connection_state", lambda platform: {
        "authenticated": True, "prepared_item_id": "item", "capabilities": {"prepare": True, "publish": True}})
    with sessions() as db:
        config = FactoryConfig(id=1, daily_target=10, publish_enabled=True,
            publish_start_at=datetime(2026, 11, 9, 0), schedule_time="00:30")
        db.add(config)
        db.add(FactoryRun(id="run", active_slot=None, status="completed"))
        db.add(FactoryItem(id="item", run_id="run", position=0, status="completed",
            content={"title": "有证据的 AI 科技视频"}, media={"video_path": "test.mp4"}))
        slot = FactoryRelease(due_at=datetime(2026, 11, 9, 9), item_id="item", platforms=["xiaohongshu", "douyin", "kuaishou"])
        db.add(slot)
        db.commit()
        db.get(FactoryRun, "run").active_slot = None
        db.commit()
        begin = Mock(side_effect=lambda _db, _item, action, platform: SimpleNamespace(id=f"{platform}:{action}"))
        monkeypatch.setattr(schedule.publishing, "begin_publication", begin)
        yield SimpleNamespace(db=db, config=config, slot=slot, begin=begin)
    engine.dispose()


def test_ten_local_times_never_backfill_or_duplicate_after_restart(env):
    db, config = env.db, env.config
    db.delete(env.slot)
    db.commit()
    now = datetime(2026, 11, 9, 18, 5, tzinfo=TZ)
    for _ in range(2):
        schedule.plan_slots(db, config, now)
    slots = db.query(FactoryRelease).order_by(FactoryRelease.due_at).all()
    assert len(slots) == 14  # Four remaining today, ten tomorrow.
    assert slots[0].due_at == datetime(2026, 11, 9, 11)  # 19:00 local.
    assert all("bilibili" not in s.platforms for s in slots)
    tomorrow = [s.due_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%H:%M") for s in slots[4:]]
    assert tomorrow == config.publish_times


def test_each_day_has_one_production_run_with_same_slot_mapping(env, monkeypatch):
    db, config = env.db, env.config
    db.delete(env.slot)
    db.commit()
    now = datetime(2026, 11, 10, 0, 30, tzinfo=TZ)
    schedule.plan_slots(db, config, now)
    def start(db, **kw):
        run = FactoryRun(id="daily", target_count=kw["target_count"], schedule_key=kw["schedule_key"])
        db.add(run)
        db.commit()
        return run
    create = Mock(side_effect=start)
    monkeypatch.setattr(factory_service, "start_run", create)
    schedule.plan_production(db, config, now)
    schedule.plan_production(db, config, now)
    create.assert_called_once()
    assert create.call_args.kwargs["target_count"] == 10
    assert [s.position for s in db.query(FactoryRelease).filter_by(run_id="daily").order_by(FactoryRelease.due_at)] == list(range(10))


def test_prepared_video_waits_until_due_and_submits_once(env):
    db, slot = env.db, env.slot
    db.add(FactoryPublication(item_id="item", platform="xiaohongshu", action="prepare", status="prepared"))
    db.commit()
    item = db.get(FactoryItem, "item")
    state = schedule.step_platform(db, slot, item, "xiaohongshu", slot.due_at - timedelta(seconds=1), True)
    assert state["status"] == "prepared"
    env.begin.assert_not_called()
    state = schedule.step_platform(db, slot, item, "xiaohongshu", slot.due_at, True)
    assert state["status"] == "publishing"
    env.begin.assert_called_once_with(db, "item", "publish", "xiaohongshu")
    db.add(FactoryPublication(item_id="item", platform="xiaohongshu", action="publish", status="submitted"))
    db.commit()
    assert schedule.step_platform(db, slot, item, "xiaohongshu", slot.due_at, True)["status"] == "submitted"
    assert env.begin.call_count == 1


@pytest.mark.parametrize("minutes,status", [(-21, "waiting"), (-20, "uploading"), (16, "missed")])
def test_upload_lead_and_late_window(env, minutes, status):
    result = schedule.step_platform(env.db, env.slot, env.db.get(FactoryItem, "item"),
        "xiaohongshu", env.slot.due_at + timedelta(minutes=minutes), True)
    assert result["status"] == status
    assert env.begin.call_count == (1 if status == "uploading" else 0)


def test_explicit_catch_up_reopens_only_todays_assigned_slots_and_expires(env):
    db, slot = env.db, env.slot
    slot.run_id, slot.status = "run", "missed"
    other = FactoryRelease(run_id="run", due_at=slot.due_at - timedelta(days=1), status="missed")
    db.add(other)
    db.commit()
    result = schedule.authorize_catch_up(db, db.get(FactoryRun, "run"), env.config,
                                       datetime(2026, 11, 9, 20, tzinfo=TZ))
    assert result["release_ids"] == [slot.id]
    assert slot.status == "scheduled" and other.status == "missed"
    assert schedule.catch_up_until(db, other) is None
    item = db.get(FactoryItem, "item")
    assert schedule.step_platform(db, slot, item, "xiaohongshu", datetime(2026, 11, 9, 15, 59), True)["status"] == "uploading"
    env.begin.reset_mock()
    assert schedule.step_platform(db, slot, item, "xiaohongshu", datetime(2026, 11, 9, 16), True)["status"] == "missed"
    env.begin.assert_not_called()


def test_catch_up_does_not_replay_uncertain_submits_or_bypass_pause(env):
    db, slot = env.db, env.slot
    slot.run_id = "run"
    db.add(FactoryPublication(item_id="item", platform="douyin", action="publish", status="failed",
                             result={"submission_attempted": True, "submission_uncertain": True}))
    db.add(FactoryPublication(item_id="item", platform="xiaohongshu", action="publish", status="submitted"))
    db.commit()
    schedule.authorize_catch_up(db, db.get(FactoryRun, "run"), env.config, datetime(2026, 11, 9, 20, tzinfo=TZ))
    item = db.get(FactoryItem, "item")
    now = datetime(2026, 11, 9, 12)
    assert schedule.step_platform(db, slot, item, "douyin", now, True)["status"] == "blocked"
    assert schedule.step_platform(db, slot, item, "xiaohongshu", now, True)["status"] == "submitted"
    assert schedule.step_platform(db, slot, item, "kuaishou", now, False)["status"] == "paused"
    env.begin.assert_not_called()


def test_catch_up_does_not_publish_future_slot_early(env):
    db, slot = env.db, env.slot
    slot.run_id, slot.due_at = "run", datetime(2026, 11, 9, 14)
    db.add(FactoryPublication(item_id="item", platform="xiaohongshu", action="prepare", status="prepared"))
    db.commit()
    schedule.authorize_catch_up(db, db.get(FactoryRun, "run"), env.config, datetime(2026, 11, 9, 20, tzinfo=TZ))
    assert schedule.step_platform(db, slot, db.get(FactoryItem, "item"), "xiaohongshu",
                                  slot.due_at - timedelta(seconds=1), True)["status"] == "prepared"
    env.begin.assert_not_called()


def test_failed_or_unreviewed_media_is_never_published(env):
    env.db.get(FactoryItem, "item").status = "failed"
    env.db.commit()
    schedule.advance_slot(env.db, env.slot, env.config, env.slot.due_at)
    env.begin.assert_not_called()
    assert env.slot.status == "waiting_media"
    schedule.advance_slot(env.db, env.slot, env.config, env.slot.due_at + timedelta(minutes=16))
    assert env.slot.status == "missed"


def test_platform_paused_or_capability_missing_never_claims_success(env, monkeypatch):
    env.config.platforms = ["xiaohongshu"]
    monkeypatch.setattr(schedule.publishing, "_connection_state", lambda platform: {"authenticated": True, "capabilities": {}})
    item = env.db.get(FactoryItem, "item")
    assert schedule.step_platform(env.db, env.slot, item, "kuaishou", env.slot.due_at, True)["status"] == "blocked"
    assert schedule.step_platform(env.db, env.slot, item, "douyin", env.slot.due_at, False)["status"] == "paused"
    env.begin.assert_not_called()


def test_uncertain_submission_and_busy_browser_do_not_replay(env):
    db, slot = env.db, env.slot
    db.add(FactoryPublication(item_id="item", platform="douyin", action="publish", status="failed",
        result={"submission_uncertain": True}))
    db.add(FactoryPublication(item_id="other", platform="xiaohongshu", action="prepare", status="running"))
    db.commit()
    for platform in ("douyin", "xiaohongshu"):
        assert schedule.step_platform(db, slot, db.get(FactoryItem, "item"), platform, slot.due_at, True)["status"] == "blocked"
    env.begin.assert_not_called()


def test_one_success_does_not_claim_all_platforms_submitted(env):
    env.db.add(FactoryPublication(item_id="item", platform="xiaohongshu", action="publish", status="submitted"))
    env.db.commit()
    schedule.advance_slot(env.db, env.slot, env.config, env.slot.due_at + timedelta(minutes=16))
    assert env.slot.status == "partial"
    assert env.slot.results["xiaohongshu"]["status"] == "submitted"
    assert env.slot.results["douyin"]["status"] == "missed"
    env.begin.assert_not_called()


def test_restart_recovers_original_mapping_even_if_an_earlier_slot_expired(env, monkeypatch):
    db = env.db
    later = FactoryRelease(due_at=env.slot.due_at + timedelta(hours=2), platforms=env.slot.platforms)
    db.add(later)
    db.flush()
    run = FactoryRun(id="recovered", schedule_key="releases:2026-11-09", target_count=2,
        settings_snapshot={"release_slot_ids": [env.slot.id, later.id]})
    db.add(run)
    db.commit()
    create = Mock()
    monkeypatch.setattr(factory_service, "start_run", create)
    schedule.plan_production(db, env.config, datetime(2026, 11, 9, 18, 30, tzinfo=TZ))
    assert (env.slot.run_id, env.slot.position) == ("recovered", 0)
    assert (later.run_id, later.position) == ("recovered", 1)
    create.assert_not_called()


def test_release_settings_reject_invalid_or_duplicate_times_and_volume_mismatch(env):
    from fastapi import HTTPException
    from pydantic import ValidationError
    from routers.factory import SettingsUpdate, update_settings
    for times in (["24:00"], ["09:10", "09:10"], []):
        with pytest.raises(ValidationError):
            SettingsUpdate(publish_times=times)
    with pytest.raises(HTTPException) as exc:
        update_settings(SettingsUpdate(daily_target=3), env.db)
    assert exc.value.status_code == 422
    assert env.config.daily_target == 10


def test_platform_review_does_not_hold_next_original_upload(env):
    db=env.db
    db.add(FactoryPublication(item_id='item',platform='kuaishou',action='publish',status='submitted',result={'message':'审核中'}))
    db.add(FactoryItem(id='next',run_id='run',position=1,status='completed',media={'video_path':'next.mp4'}))
    next_slot=FactoryRelease(due_at=env.slot.due_at + timedelta(hours=1),item_id='next',platforms=['kuaishou'])
    db.add(next_slot);db.commit()
    result=schedule.step_platform(db,next_slot,db.get(FactoryItem,'next'),'kuaishou',next_slot.due_at,True)
    assert result['status']=='uploading'
    assert env.begin.call_args.args[1:]==('next','prepare','kuaishou')


def test_waiting_media_shows_actual_production_failure(env):
    item=env.db.get(FactoryItem,'item');item.status='failed';item.error='模型服务额度不足'
    env.db.commit()
    schedule.advance_slot(env.db,env.slot,env.config,env.slot.due_at)
    assert '额度不足' in env.slot.results['kuaishou']['message']
    env.begin.assert_not_called()


def test_uncertain_old_submission_reports_hold_and_never_retries(env):
    item=env.db.get(FactoryItem,'item');item.media={**item.media,'publication_hold':'旧短片停发'}
    env.db.add(FactoryPublication(item_id='item',platform='kuaishou',action='publish',status='failed',
        result={'message':'没有成功回执','submission_uncertain':True,'submission_attempted':True}))
    env.db.commit()
    result=schedule.step_platform(env.db,env.slot,item,'kuaishou',env.slot.due_at,True)
    assert '此旧版已停发' in result['message'] and '小麦正在核对' in result['message']
    env.begin.assert_not_called()
