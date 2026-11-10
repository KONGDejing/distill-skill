from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryAnalyticsSnapshot, FactoryGrowthPlan, FactoryWorkOrder
from services import factory_management as manager


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def snapshot(db, platform, count, at):
    row = FactoryAnalyticsSnapshot(platform=platform, status="connected", collected_at=at,
                                   data={"metrics": {"followers": count}})
    db.add(row); db.commit()
    return row


def test_late_platform_baseline_is_real_and_never_overwritten(db):
    now = datetime.utcnow()
    snapshot(db, "douyin", 57, now)
    plan = manager.initialize(db)
    assert plan.baselines["bilibili"]["followers"] is None
    snapshot(db, "bilibili", 19, now)
    manager.initialize(db)
    snapshot(db, "bilibili", 25, now + timedelta(hours=1))
    manager.initialize(db)
    assert plan.baselines["bilibili"]["followers"] == 19


def test_weekly_measurements_use_boundary_snapshots_not_latest_total(db):
    start = datetime(2026, 1, 1, 12)
    baseline = {"followers": 100, "observed_at": start.replace(tzinfo=timezone.utc).isoformat()}
    end = start + timedelta(weeks=1)
    snapshot(db, "douyin", 106, end - timedelta(minutes=15))
    snapshot(db, "douyin", 140, end + timedelta(days=1))
    result = manager.weekly_results(db, "douyin", baseline, now=end + timedelta(days=2))
    assert result[0]["growth_percent"] == 6
    assert result[0]["status"] == "met"
    assert result[1]["status"] == "not_due"


def test_missing_week_is_not_filled_with_zero_or_later_data(db):
    start = datetime(2026, 1, 1, 12)
    baseline = {"followers": 100, "observed_at": start.replace(tzinfo=timezone.utc).isoformat()}
    snapshot(db, "douyin", 120, start + timedelta(weeks=2, minutes=-10))
    result = manager.weekly_results(db, "douyin", baseline, now=start + timedelta(weeks=2))
    assert [r["status"] for r in result[:2]] == ["missing_data", "missing_data"]
    assert all(r["growth_percent"] is None for r in result)


def test_zero_baseline_is_recorded_but_percentage_is_undefined(db):
    start = datetime(2026, 1, 1, 12)
    baseline = {"followers": 0, "observed_at": start.replace(tzinfo=timezone.utc).isoformat()}
    snapshot(db, "bilibili", 2, start + timedelta(weeks=1, minutes=-5))
    result = manager.weekly_results(db, "bilibili", baseline, now=start + timedelta(weeks=1))
    assert result[0]["net_followers"] == 2
    assert result[0]["growth_percent"] is None
    assert manager.checkpoints(0) == []


def test_work_orders_are_durable_idempotent_and_keep_bilibili_separate(db):
    now = datetime.now(timezone.utc)
    analytics = {"platforms": [{"platform": p, "status": "not_connected"} for p in ("douyin", "xiaohongshu", "kuaishou", "bilibili")]}
    state = {"summary": {"target": 10, "fully_submitted_slots": 0}}
    manager.sync_work_orders(db, state, analytics, now)
    manager.sync_work_orders(db, state, analytics, now)
    daily = db.query(FactoryWorkOrder).filter_by(kind="daily").all()
    long = db.query(FactoryWorkOrder).filter_by(kind="bilibili").all()
    assert len(daily) == 9 and len(long) == 8
    assert all(r.status != "completed" for r in daily)
    assert all(r.status == "blocked" and r.evidence["seconds_range"] == [360, 600] for r in long)
    assert db.get(FactoryGrowthPlan, 1).policy["bilibili"]["weekly_count"] == 2


def test_scorecards_do_not_invent_full_scores_when_evidence_is_missing(db):
    cards = manager.scorecards(db, {"platforms": []})
    assert len(cards) == 9
    assert all(c["total_score"] is None for c in cards)
    reviewer = next(c for c in cards if c["agent_id"] == "reviewer")
    assert reviewer["measured_weight"] == 0
    assert all(c["earned"] is None for c in reviewer["checks"])
