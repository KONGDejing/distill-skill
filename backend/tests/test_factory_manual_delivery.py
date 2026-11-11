"""Manual delivery must keep producing while every account operation is disabled."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from models.factory import FactoryConfig, FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryRun
from routers import creator_platforms, factory
from services import factory_schedule as schedule
from services import factory_publishing as publishing
from services.factory_handoff import HANDOFF_VERSION, build_handoff


@pytest.fixture
def manual(tmp_path, monkeypatch):
    from services import factory_platform_policy
    monkeypatch.setattr(factory_platform_policy, "automation_enabled", lambda _: False)
    monkeypatch.setattr(creator_platforms, "automation_enabled", lambda _: False)
    engine = create_engine(f"sqlite:///{tmp_path / 'manual.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[model.__table__ for model in
        (FactoryConfig, FactoryRun, FactoryItem, FactoryPublication, FactoryRelease, FactoryEvent)])
    db = sessionmaker(bind=engine)()
    config = FactoryConfig(id=1, publish_enabled=False, publish_jitter_seconds=0)
    run = FactoryRun(id="manual-run", status="completed")
    db.add_all([config, run])
    db.flush()
    run.active_slot = None
    package = tmp_path / "douyin.zip"
    package.write_bytes(b"fixture archive")
    item = FactoryItem(id="manual-item", run_id=run.id, position=0, status="completed",
        brief={"distribution": {"platform": "douyin", "day": "2026-11-11", "policy": "single-platform-v1"}},
        media={"video_path": "fixture.mp4"})
    copy = tmp_path / "manual-copy.txt"
    copy.write_text("fixture handoff", encoding="utf-8")
    handoff = build_handoff({"sources": [{"url": "https://example.invalid/source"}]},
        {"platform": "douyin", "title": "fixture title", "caption": "fixture caption", "tags": ["fixture"]},
        item.brief["distribution"])
    item.packages = [{"platform": "douyin", "status": "package_ready", "path": str(package),
                      "copy_path": str(copy), "handoff_version": HANDOFF_VERSION, "manual_handoff": handoff}]
    slot = FactoryRelease(id="manual-slot", run_id=run.id, position=0, item_id=item.id,
        due_at=datetime(2026, 11, 11, 2), nominal_at=datetime(2026, 11, 11, 2), jitter_seconds=0,
        platforms=["douyin"])
    db.add_all([item, slot])
    db.commit()
    forbidden = Mock(side_effect=AssertionError("manual flow must never access an account or replay a job"))
    for attr in ("refresh_job", "_connection_state", "_command_result", "_submit_command"):
        monkeypatch.setattr(publishing, attr, forbidden)
    from services import creator_connector, creator_verification, xiaohongshu_connector
    for module, names in ((creator_connector, ("connection_state", "submit_command", "login_image_path")),
                          (xiaohongshu_connector, ("connection_state", "submit_command")),
                          (creator_verification, ("page_token", "authorize", "frame", "interact"))):
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    yield SimpleNamespace(db=db, config=config, run=run, item=item, slot=slot, forbidden=forbidden)
    forbidden.assert_not_called()
    db.close()
    engine.dispose()


def test_manual_default_and_settings_do_not_enable_account_automation(manual):
    assert manual.config.manual_delivery_enabled is True
    for update in (factory.SettingsUpdate(auto_publish_xiaohongshu=True),):
        with pytest.raises(HTTPException) as error:
            factory.update_settings(update, manual.db)
        assert error.value.status_code == 409
    result = factory.update_settings(factory.SettingsUpdate(manual_delivery_enabled=False), manual.db)
    assert result["manual_delivery_enabled"] is False and result["publish_enabled"] is False
    result = factory.update_settings(factory.SettingsUpdate(manual_delivery_enabled=True), manual.db)
    assert result["manual_delivery_enabled"] is True and result["publish_enabled"] is False
    assert manual.config.auto_publish_xiaohongshu is False


def test_scheduler_dispatches_delivery_tick_with_auto_publish_disabled(manual, monkeypatch):
    from tasks import factory_tasks
    @contextmanager
    def sessions():
        yield manual.db
    monkeypatch.setattr(factory_tasks, "init_db", Mock())
    monkeypatch.setattr(factory_tasks, "SessionLocal", sessions)
    tick = Mock(return_value={"delivery": "running"})
    monkeypatch.setattr(schedule, "tick", tick)
    assert factory_tasks._schedule_factory() == {"delivery": "running"}
    tick.assert_called_once_with(manual.db, manual.config)


def test_tick_plans_production_and_marks_ready_at_due_without_publish_switch(manual, monkeypatch):
    plan = Mock()
    produce = Mock(return_value={"status": "waiting"})
    monkeypatch.setattr(schedule, "plan_slots", plan)
    monkeypatch.setattr(schedule, "plan_production", produce)
    now = datetime(2026, 11, 11, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
    schedule.tick(manual.db, manual.config, now - timedelta(seconds=1))
    assert manual.slot.status == "scheduled"
    assert manual.slot.results["douyin"]["status"] == "waiting"
    schedule.tick(manual.db, manual.config, now)
    assert plan.call_count == produce.call_count == 2
    assert manual.slot.status == "manual_ready"
    assert manual.db.query(FactoryPublication).count() == 0
    saved = dict(manual.slot.results)
    schedule.tick(manual.db, manual.config, now + timedelta(minutes=1))
    assert manual.slot.status == "manual_ready" and manual.slot.results == saved


@pytest.mark.parametrize("change,expected", [
    ("disabled", "paused"), ("missing_package", "waiting"), ("hold", "blocked"),
    ("no_route", "blocked"), ("wrong_route", "blocked"), ("wrong_day", "blocked"), ("late", "missed"),
    ("legacy_zip", "waiting"), ("incomplete_handoff", "waiting"), ("missing_copy", "waiting"),
])
def test_manual_readiness_does_not_bypass_delivery_requirements(manual, change, expected):
    now, enabled = manual.slot.due_at, True
    if change == "disabled":
        enabled = False
    elif change == "missing_package":
        manual.item.packages = [{"platform": "douyin", "path": "/missing/manual-package.zip"}]
    elif change == "hold":
        manual.item.media = {"publication_hold": "revision required"}
    elif change == "no_route":
        manual.item.brief = {}
    elif change == "wrong_route":
        manual.item.brief = {"distribution": {"platform": "kuaishou"}}
    elif change == "wrong_day":
        manual.item.brief = {"distribution": {**manual.item.brief["distribution"], "day": "2026-11-12"}}
    elif change == "legacy_zip":
        manual.item.packages = [{**manual.item.packages[0], "handoff_version": None}]
    elif change == "incomplete_handoff":
        manual.item.packages = [{**manual.item.packages[0], "manual_handoff": {"version": HANDOFF_VERSION}}]
    elif change == "missing_copy":
        manual.item.packages = [{**manual.item.packages[0], "copy_path": "/missing/handoff-copy.txt"}]
    else:
        now += timedelta(minutes=16)
    result = schedule.step_platform(manual.db, manual.slot, manual.item, "douyin", now, enabled)
    assert result["status"] == expected
    assert manual.db.query(FactoryPublication).count() == 0


@pytest.mark.parametrize("status,result,expected", [
    ("submitted", {"message": "历史提交回执"}, "submitted"),
    ("failed", {"submission_uncertain": True, "message": "未取得回执"}, "blocked"),
    ("running", {"message": "历史处理中"}, "blocked"),
])
def test_historical_publication_is_read_only_and_never_replayed(manual, status, result, expected):
    job = FactoryPublication(item_id=manual.item.id, platform="douyin", action="publish",
        status=status, command_id="historical", result=result)
    manual.db.add(job)
    manual.db.commit()
    assert schedule.step_platform(manual.db, manual.slot, manual.item, "douyin", manual.slot.due_at, True)["status"] == expected
    assert factory.get_publication_job(job.id, manual.db)["status"] == status
    assert creator_platforms.creator_job("douyin", job.id, manual.db)["status"] == status
    assert creator_platforms.creator_publish("douyin", job.id, manual.db)["id"] == job.id
    assert factory.publish_prepared_job(job.id, manual.db)["id"] == job.id
    assert job.status == status and job.result == result
    assert manual.db.query(FactoryPublication).count() == 1


def test_manual_routes_do_not_start_login_read_account_or_issue_verification(manual):
    app = FastAPI()
    app.include_router(factory.router, prefix="/api/factory")
    app.include_router(creator_platforms.router, prefix="/api/factory")
    app.dependency_overrides[get_db] = lambda: manual.db
    with TestClient(app) as client:
        for platform in ("douyin", "kuaishou", "bilibili", "xiaohongshu"):
            base = f"/api/factory/platforms/{platform}"
            state = client.get(base + "/login-state")
            assert state.status_code == 200 and state.json()["state_code"] == "manual"
            assert client.post(base + "/connect").status_code == 409
            assert client.get(base + "/login-image").status_code in (409, 410)
            page = client.get(base + "/login")
            assert page.status_code == 200 and "<script" not in page.text
            assert client.post(f"/api/factory/items/{manual.item.id}/platforms/{platform}/prepare").status_code == 409
            if platform != "xiaohongshu":
                assert client.get(base + "/verification-frame").status_code == 409
                response = client.post(base + "/verification-input", json={"code": "private-code"})
                assert response.status_code == 409 and "private-code" not in response.text
    assert manual.db.query(FactoryPublication).count() == 0


def test_overview_uses_manual_policy_without_reading_any_account(manual, monkeypatch):
    from services import factory_analytics, factory_operations, factory_llm, factory_visuals
    monkeypatch.setattr(factory_analytics, "overview", lambda db: {})
    monkeypatch.setattr(factory_operations, "overview", lambda db: {})
    monkeypatch.setattr(factory_visuals, "library_summary", lambda: {})
    monkeypatch.setattr(factory_llm, "model_runtime", lambda: {})
    monkeypatch.setattr(factory, "queue_available", lambda: False)
    monkeypatch.setattr(factory, "memory_state", lambda: {"blocked": False})
    result = factory.overview(manual.db)
    assert result["settings"]["manual_delivery_enabled"] is True
    assert len(result["platforms"]) == 4
    for platform in result["platforms"]:
        assert platform["status"] == platform["login_status"] == "manual"
        assert platform["automation"]["enabled"] is False
        assert platform["capabilities"] == {"prepare": False, "publish": False}
