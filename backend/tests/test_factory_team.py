"""The six-video plan enforces editor quotas without model calls or live DBs."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.registry import AGENT_MAP, DAILY_EDITOR_QUOTAS, DEFAULT_PUBLISH_TIMES
from database import Base
from models.factory import FactoryConfig, FactoryEvent, FactoryItem, FactoryRun
from services.factory_team import assign_editors, candidate_editor, editor_quotas, validate_editor_plan


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def briefs():
    return [{"title": f"Topic {index}", "category": category, "source_ids": []}
            for index, category in enumerate(["工具指南", "论文讲解", "科技展会", "概念科普", "论文讲解", "硬件热点"])]


def test_persisted_defaults_are_six_and_three_editors_have_two_each(db):
    config, run = FactoryConfig(), FactoryRun()
    db.add_all([config, run]); db.commit()
    assert config.daily_target == run.target_count == 6
    assert config.publish_times == list(DEFAULT_PUBLISH_TIMES) == ["06:00", "08:00", "10:30", "17:00", "20:00", "22:00"]
    assert config.publish_jitter_seconds == 900
    assert config.platforms == ["douyin", "xiaohongshu", "kuaishou"]
    assert len(config.publish_times) * len(config.platforms) == 18
    assert all(AGENT_MAP[owner]["daily_quota"] == 2 for owner in DAILY_EDITOR_QUOTAS)


@pytest.mark.parametrize("target", range(1, 7))
def test_partial_run_quotas_are_balanced_and_never_exceed_daily_caps(target):
    quotas = editor_quotas(target)
    assert sum(quotas.values()) == target
    assert set(quotas) == set(DAILY_EDITOR_QUOTAS)
    assert max(quotas.values()) <= 2 and max(quotas.values()) - min(quotas.values()) <= 1


@pytest.mark.parametrize("target", [0, 7, 10, True, 6.0])
def test_oversized_or_invalid_new_run_cannot_allocate_quotas(target):
    with pytest.raises(ValueError):
        editor_quotas(target)


def test_selection_validation_returns_ordered_ids_without_mutation():
    plan = briefs(); original = deepcopy(plan)
    owners = validate_editor_plan(plan, [], editor_quotas())
    assert owners == ["producer", "producer2", "producer3", "producer", "producer2", "producer3"]
    assert Counter(owners) == DAILY_EDITOR_QUOTAS and plan == original
    # A paper's cited source takes priority over an inaccurate generic label.
    assert candidate_editor({"category": "科技动态", "source_ids": ["paper"]},
                            [{"id": "paper", "url": "https://arxiv.org/abs/2601.12345"}]) == "producer2"


def test_six_topics_are_rejected_when_track_quota_or_owner_is_wrong():
    plan = briefs(); plan[1]["category"] = "工具指南"
    with pytest.raises(ValueError, match="小影2需要2条、实际1条"):
        validate_editor_plan(plan, [], editor_quotas())
    plan = briefs(); plan[1]["editor_id"] = "producer"
    with pytest.raises(ValueError, match="负责人不符合岗位"):
        validate_editor_plan(plan, [], editor_quotas())
    with pytest.raises(ValueError, match="配额"):
        validate_editor_plan(briefs(), [], {"producer": 4, "producer2": 1, "producer3": 1})


def seed(db, plan=None, quotas=True):
    run = FactoryRun(target_count=6, sources=[], settings_snapshot={"editor_quotas": editor_quotas()} if quotas else {})
    db.add(run); db.flush()
    items = [FactoryItem(run_id=run.id, position=index, brief=brief) for index, brief in enumerate(plan or briefs())]
    db.add_all(items); db.commit()
    return run, items


def test_quota_assignment_skips_deferred_and_is_idempotent(db):
    run, items = seed(db)
    deferred = FactoryItem(run_id=run.id, position=6, status="deferred", brief={"category": "论文讲解"})
    db.add(deferred); db.commit()
    assert assign_editors(db, run.id) == DAILY_EDITOR_QUOTAS
    assert assign_editors(db, run.id) == DAILY_EDITOR_QUOTAS
    assert "editor_id" not in deferred.brief
    assert db.query(FactoryEvent).filter_by(stage="editor_assignment").count() == 1


def test_imbalanced_new_plan_is_rejected_before_any_assignment(db):
    plan = briefs(); plan[1]["category"] = "工具指南"
    run, items = seed(db, plan)
    with pytest.raises(ValueError, match="剪辑配额不匹配"):
        assign_editors(db, run.id)
    assert all("editor_id" not in item.brief for item in items)


def test_documented_material_shortfall_admits_ready_roles_without_changing_day_target(db):
    run, items = seed(db, [briefs()[0], briefs()[1], briefs()[4]])
    run.plan = {'production_quotas': {'producer': 1, 'producer2': 2, 'producer3': 0},
                'material_shortfall': {'producer': 1, 'producer2': 0, 'producer3': 2}}
    db.commit()
    assert assign_editors(db, run.id) == {'producer': 1, 'producer2': 2, 'producer3': 0}
    assert run.target_count == 6 and run.settings_snapshot['editor_quotas'] == DAILY_EDITOR_QUOTAS
    assert [item.brief['editor_id'] for item in items] == ['producer', 'producer2', 'producer2']


@pytest.mark.parametrize('gap', [None, {'producer': 0, 'producer2': 0, 'producer3': 0}])
def test_partial_quota_cannot_silently_hide_missing_work(db, gap):
    run, _ = seed(db, [briefs()[0], briefs()[1], briefs()[4]])
    run.plan = {'production_quotas': {'producer': 1, 'producer2': 2, 'producer3': 0}, 'material_shortfall': gap}
    db.commit()
    with pytest.raises(ValueError, match='保留真实岗位缺口'):
        assign_editors(db, run.id)
    assert db.query(FactoryEvent).count() == 0


def test_started_owner_is_kept_even_when_current_topic_classifier_differs(db):
    run, items = seed(db)
    items[0].brief = {"category": "论文讲解", "editor_id": "producer"}
    items[0].status = "rendering"
    db.commit()
    assert assign_editors(db, run.id) == DAILY_EDITOR_QUOTAS
    assert items[0].brief["editor_id"] == "producer"


def test_legacy_started_ownership_and_deferred_drafts_are_untouched(db):
    run, items = seed(db, quotas=False)
    items[1].status = "rendering"
    items[2].status = "deferred"
    db.commit()
    counts = assign_editors(db, run.id)
    assert sum(counts.values()) == 5
    assert items[1].brief["editor_id"] == "producer"
    assert "editor_id" not in items[2].brief


def test_future_api_inputs_reject_seven_or_ten_video_plans():
    from pydantic import ValidationError
    from routers.factory import SettingsUpdate, StartRun
    for target in (7, 10):
        with pytest.raises(ValidationError):
            StartRun(target_count=target)
        with pytest.raises(ValidationError):
            SettingsUpdate(daily_target=target)
    with pytest.raises(ValidationError):
        SettingsUpdate(publish_times=[f"{hour:02d}:00" for hour in range(6, 13)])


def test_batch_retry_does_not_reactivate_deferred_drafts(db, monkeypatch):
    from routers import factory as api
    from tasks.factory_tasks import produce_run
    run, items = seed(db)
    run.status, run.active_slot = "cancelled", None
    items[0].status, items[1].status = "deferred", "failed"
    items[0].error = "保留稿，待后续排期"
    db.commit()
    queued = Mock(return_value=SimpleNamespace(id="test-task"))
    monkeypatch.setattr(api, "queue_available", lambda: True)
    monkeypatch.setattr(api, "ensure_memory_available", lambda: None)
    monkeypatch.setattr(produce_run, "delay", queued)
    api.retry_run(run.id, db, None)
    assert items[0].status == "deferred" and items[0].error == "保留稿，待后续排期"
    assert items[1].status == "planned"
    queued.assert_called_once_with(run.id)


def test_explicit_retry_of_deferred_requires_rescheduling(db, monkeypatch):
    from fastapi import HTTPException
    from routers import factory as api
    run, items = seed(db)
    run.status, run.active_slot = "cancelled", None
    items[0].status = "deferred"
    db.commit()
    queue = Mock(side_effect=AssertionError("Deferred drafts must not reach queue checks"))
    monkeypatch.setattr(api, "queue_available", queue)
    with pytest.raises(HTTPException) as error:
        api.retry_run(run.id, db, api.RetryRun(item_ids=[items[0].id]))
    assert error.value.status_code == 409 and "重新排期" in error.value.detail
    assert items[0].status == "deferred" and run.status == "cancelled"
    queue.assert_not_called()
