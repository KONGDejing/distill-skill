"""Offline assignment, day accounting and non-expansion invariants."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryItem, FactoryPublication, FactoryRun
from services import factory_distribution as distribution

DAY = '2026-11-11'
NOW = datetime(2026, 11, 11, 4, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'distribution.db'}")
    Base.metadata.create_all(engine, tables=[FactoryRun.__table__, FactoryItem.__table__, FactoryPublication.__table__])
    with sessionmaker(bind=engine)() as session:
        yield session
        session.rollback()
    engine.dispose()


def run_items(db, titles, policy=True):
    run = FactoryRun(active_slot=None, settings_snapshot={'timezone': 'Asia/Shanghai',
        **({'distribution_policy': distribution.CURRENT_DISTRIBUTION_POLICY} if policy else {})})
    db.add(run); db.flush()
    run.active_slot = None
    db.flush()
    items = [FactoryItem(run_id=run.id, position=i, brief={'title': title, 'editor_id': 'producer2'})
             for i, title in enumerate(titles)]
    db.add_all(items); db.flush()
    return run, items


def route(platform, day=DAY):
    return {'platform': platform, 'day': day, 'reason': 'fixture', 'policy': distribution.CURRENT_DISTRIBUTION_POLICY}


def job(db, item, platform='douyin', action='publish', status='queued', result=None, at=NOW):
    entry = FactoryPublication(item_id=item.id, platform=platform, action=action,
                               status=status, result=result or {}, created_at=at.replace(tzinfo=None))
    db.add(entry); db.flush()
    return entry


def test_six_content_routes_are_persistent_balanced_and_not_editor_bound(db):
    run, items = run_items(db, ['新品发布游戏画面突破', '视频动态热点发布', '工具指南如何选型',
                              '应用教程步骤清单', '机器人机械臂操作', '推车积木动手搭建'])
    assigned = distribution.assign_distribution(db, run.id, DAY)
    assert [assigned[i.id]['platform'] for i in items] == ['douyin'] * 2 + ['xiaohongshu'] * 2 + ['kuaishou'] * 2
    assert all('非平台官方推荐规则' in value['reason'] for value in assigned.values())
    db.commit(); db.expire_all()
    assert distribution.assign_distribution(db, run.id, DAY) == assigned
    assert {i.brief['editor_id'] for i in items} == {'producer2'}
    with pytest.raises(ValueError, match='不可改投'):
        distribution.assign_distribution(db, run.id, DAY, proposal={items[0].id: 'xiaohongshu'})
    with pytest.raises(ValueError, match='不能静默改期'):
        distribution.assign_distribution(db, run.id, '2026-11-12')


def test_global_matching_beats_greedy_and_counts_other_run_assignments(db):
    other_run, occupied = run_items(db, ['other'] * 4)
    for item, platform in zip(occupied, ['douyin', 'douyin', 'xiaohongshu', 'kuaishou']):
        item.brief = {**item.brief, 'distribution': route(platform)}
    run, items = run_items(db, ['工具指南机器人', '工具教程清单'])
    assigned = distribution.assign_distribution(db, run.id, DAY)
    # Giving the first item its own highest score would lose the better global pairing.
    assert assigned[items[0].id]['platform'] == 'kuaishou'
    assert assigned[items[1].id]['platform'] == 'xiaohongshu'


def test_director_proposal_must_fit_capacity_without_partial_mutation(db):
    run, items = run_items(db, ['AI'] * 3)
    with pytest.raises(ValueError, match='额度不足'):
        distribution.assign_distribution(db, run.id, DAY, proposal={i.id: 'douyin' for i in items})
    assert all('distribution' not in i.brief for i in items)
    chosen = distribution.assign_distribution(db, run.id, DAY,
        proposal={items[0].id: {'platform': 'xiaohongshu', 'reason': '教程适合收藏'}})
    assert chosen[items[0].id]['platform'] == 'xiaohongshu'
    assert '负责人' in chosen[items[0].id]['reason']


def test_any_existing_job_pins_platform_and_legacy_multiplatform_is_not_reassigned(db):
    run, items = run_items(db, ['AI', 'AI'])
    job(db, items[0], action='prepare', status='failed')
    selected = {items[0].id: DAY}
    with pytest.raises(ValueError, match='不可改投'):
        distribution.assign_distribution(db, run.id, slots=selected, proposal={items[0].id: 'kuaishou'})
    assert distribution.assign_distribution(db, run.id, slots=selected)[items[0].id]['platform'] == 'douyin'
    job(db, items[1], 'xiaohongshu', status='submitted')
    job(db, items[1], 'kuaishou', status='submitted')
    with pytest.raises(ValueError, match='旧作品已有多平台'):
        distribution.assign_distribution(db, run.id, slots={items[1].id: DAY})


def test_slots_can_select_multiple_days_and_limit_each_day(db):
    run, items = run_items(db, ['AI'] * 7)
    with pytest.raises(ValueError, match='每天最多'):
        distribution.assign_distribution(db, run.id, DAY)
    slots = {i.id: DAY if i.position < 6 else '2026-11-12' for i in items}
    assigned = distribution.assign_distribution(db, run.id, slots=slots)
    assert assigned[items[-1].id]['day'] == '2026-11-12'


@pytest.mark.parametrize('status,result,expected', [
    ('queued', {}, True), ('running', {}, True), ('submitted', {}, True),
    ('failed', {}, True), ('uncertain', {}, True),
    ('failed', {'submission_attempted': False}, False),
    ('failed', {'submission_attempted': False, 'submission_uncertain': True}, True),
])
def test_publish_unknown_is_reserved_not_zero(status, result, expected):
    assert distribution.reserves_quota(SimpleNamespace(action='publish', status=status, result=result)) is expected


def test_quota_uses_actual_reservation_day_and_deduplicates_prepare_publish(db):
    run, items = run_items(db, ['A', 'B', 'C'])
    old = datetime(2026, 11, 9, 17, tzinfo=timezone.utc)
    job(db, items[0], action='prepare', status='prepared')
    job(db, items[0], result={distribution.RESERVATION_KEY: {'day': DAY}}, at=old)
    job(db, items[1], status='failed')
    job(db, items[2], status='failed', result={'submission_attempted': False})
    assert distribution.publication_usage(db, 'douyin', DAY) == 2
    assert distribution.publication_usage(db, 'douyin', DAY, include_prepares=True) == 2
    assert distribution.publication_usage(db, 'douyin', '2026-11-10') == 0
    assert distribution.quota_day(SimpleNamespace(result={}, created_at=old.replace(tzinfo=None))) == '2026-11-10'


def test_legacy_publish_jobs_reduce_assignment_capacity(db):
    old_run, old_items = run_items(db, ['old', 'old'], policy=False)
    for item in old_items:
        job(db, item, 'douyin', status='submitted')
    run, items = run_items(db, ['新品发布', '新品发布'])
    assigned = distribution.assign_distribution(db, run.id, DAY)
    assert all(r['platform'] != 'douyin' for r in assigned.values())


def test_new_policy_requires_route_and_today_and_legacy_cannot_first_use_without_route(db):
    run, items = run_items(db, ['new'])
    with pytest.raises(ValueError, match='唯一发行平台'):
        distribution.guard_publication(db, items[0], 'douyin', 'prepare', now=NOW)
    items[0].brief = {**items[0].brief, 'distribution': route('douyin', '2026-11-12')}
    with pytest.raises(ValueError, match='发行日期与今天不符'):
        distribution.guard_publication(db, items[0], 'douyin', 'prepare', now=NOW)
    legacy, old_items = run_items(db, ['old'], policy=False)
    with pytest.raises(ValueError, match='分配唯一发行平台'):
        distribution.guard_publication(db, old_items[0], 'douyin', 'prepare', now=NOW)
    job(db, old_items[0], action='prepare', status='prepared')
    assert distribution.guard_publication(db, old_items[0], 'douyin', 'publish', now=NOW)['day'] == DAY
    with pytest.raises(ValueError, match='禁止改投'):
        distribution.guard_publication(db, old_items[0], 'kuaishou', 'prepare', now=NOW)


@pytest.mark.parametrize('result,expected', [({}, True), ({'uploaded': False}, False),
    ({'uploaded': False, 'submission_uncertain': True}, True)])
def test_failed_prepare_unknown_upload_still_reserves_admission(result, expected):
    item = SimpleNamespace(action='prepare', status='failed', result=result)
    assert distribution.reserves_quota(item, include_prepares=True) is expected
