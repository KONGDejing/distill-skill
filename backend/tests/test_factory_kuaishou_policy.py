from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryRun, FactoryItem, FactoryPublication, FactoryRelease, FactoryConfig
from services.factory_distribution import CURRENT_DISTRIBUTION_POLICY, assign_distribution, guard_publication
from services.factory_kuaishou_policy import POLICY, assign_kuaishou
from services import factory_schedule as schedule, factory_platform_policy as platform_policy

DAY = '2026-11-16'
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


@pytest.fixture
def env():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        run = FactoryRun(id='run', active_slot=None, settings_snapshot={
            'distribution_policy': CURRENT_DISTRIBUTION_POLICY, 'kuaishou_auto_policy': POLICY, 'timezone': 'Asia/Shanghai'})
        db.add(run);db.flush();run.active_slot=None
        items = []
        for pos, owner in enumerate(('producer','producer2','producer3') * 2):
            track = {'producer':'technology_scenes','producer2':'paper_explainer','producer3':'people_news'}[owner]
            item = FactoryItem(run_id=run.id,position=pos,status='completed',brief={'editor_id':owner,'editor_track':track},
                               review={'approved':True,'score':95},packages=[{'platform':'douyin'}])
            db.add(item);items.append(item)
        db.flush();assign_distribution(db,run.id,DAY);db.commit()
        yield db,run,items
    engine.dispose()


def test_two_kuaishou_picks_use_other_originals_and_prefer_scene_and_news(env):
    db,run,items=env
    chosen=[i for i in items if i.brief['distribution']['kuaishou_selected']]
    assert len(chosen)==2
    assert {i.brief['editor_track'] for i in chosen}=={'technology_scenes','people_news'}
    assert all(not i.brief['distribution']['douyin_selected'] for i in chosen)
    for i in chosen:guard_publication(db,i,'kuaishou','prepare',now=NOW)
    with pytest.raises(ValueError,match='未入选'):guard_publication(db,items[0],'kuaishou','prepare',now=NOW)


def test_unknown_submission_reserves_limit_and_cannot_cross_to_douyin(env):
    db,run,items=env
    chosen=[i for i in items if i.brief['distribution']['kuaishou_selected']]
    for item in chosen:
        db.add(FactoryPublication(item_id=item.id,platform='kuaishou',action='publish',status='failed',
                                 created_at=NOW.replace(tzinfo=None),result={'submission_uncertain':True}))
    db.flush()
    leftover=next(i for i in items if i.position>=3 and i not in chosen)
    leftover.brief={**leftover.brief,'distribution':{**leftover.brief['distribution'],
                   'kuaishou_selected':True,'platform':'kuaishou'}}
    with pytest.raises(ValueError,match='2条'):guard_publication(db,leftover,'kuaishou','prepare',now=NOW)
    item=chosen[0];item.brief={**item.brief,'distribution':{**item.brief['distribution'],
           'douyin_selected':True,'platform':'douyin'}}
    with pytest.raises(ValueError,match='其他平台'):guard_publication(db,item,'douyin','prepare',now=NOW)


def test_restoring_kuaishou_does_not_authorize_historical_runs(env):
    db,run,items=env
    run.settings_snapshot={k:v for k,v in run.settings_snapshot.items() if k!='kuaishou_auto_policy'}
    chosen=next(i for i in items if i.brief['distribution']['kuaishou_selected'])
    with pytest.raises(ValueError,match='旧任务不能自动恢复'):
        guard_publication(db,chosen,'kuaishou','prepare',now=NOW)


def test_kuaishou_upload_pins_the_complementary_douyin_choice(env):
    db,run,items=env
    chosen=next(i for i in items if i.brief['distribution']['kuaishou_selected'])
    chosen.review={'approved':True,'score':100}
    db.add(FactoryPublication(item_id=chosen.id,platform='kuaishou',action='prepare',status='prepared',created_at=NOW.replace(tzinfo=None)))
    db.commit();schedule.reconcile_editor_picks(db,run)
    assert chosen.brief['distribution']['kuaishou_selected']
    assert not chosen.brief['distribution']['douyin_selected']


def test_kuaishou_schedule_jitter_is_saved_without_changing_douyin_slots(env):
    db,run,items=env
    cfg=FactoryConfig(id=1,publish_times=['17:00','20:00','22:00'],platforms=['douyin','kuaishou','xiaohongshu'],publish_jitter_seconds=900)
    db.add(cfg);db.commit();now=datetime(2026,11,16,16,tzinfo=ZoneInfo('Asia/Shanghai'))
    schedule.plan_slots(db,cfg,now)
    before={s.id:(s.nominal_at,s.due_at,s.jitter_seconds,tuple(s.platforms)) for s in db.query(FactoryRelease)}
    assert sum(row[3]==('kuaishou',) for row in before.values())==4
    schedule.plan_slots(db,cfg,now+timedelta(minutes=1))
    assert before=={s.id:(s.nominal_at,s.due_at,s.jitter_seconds,tuple(s.platforms)) for s in db.query(FactoryRelease)}
    kslots=sorted([s for s in db.query(FactoryRelease) if s.platforms==['kuaishou'] and s.nominal_at.date()==NOW.date()],key=lambda s:s.nominal_at)
    run.settings_snapshot={**run.settings_snapshot,'kuaishou_release_slot_ids':[s.id for s in kslots]}
    schedule.bind_editor_releases(db,run)
    assert len({s.item_id for s in kslots})==2
    assert all(db.get(FactoryItem,s.item_id).brief['distribution']['kuaishou_selected'] for s in kslots)


def test_transport_failure_is_blocked_not_manual_ready_and_does_not_query_account(env,monkeypatch):
    db,run,items=env;item=next(i for i in items if i.brief['distribution']['kuaishou_selected'])
    slot=FactoryRelease(run_id=run.id,item_id=item.id,due_at=NOW.replace(tzinfo=None),platforms=['kuaishou']);db.add(slot)
    monkeypatch.setattr(platform_policy,'publication_transport_state',lambda platform:{'ready':False,'reason':'Beijing TLS connection reset'})
    query=Mock(side_effect=AssertionError('No account interaction while transport is unavailable'))
    monkeypatch.setattr(schedule.publishing,'_connection_state',query)
    result=schedule.step_platform(db,slot,item,'kuaishou',slot.due_at,True)
    assert result['status']=='blocked' and 'TLS' in result['message']
    query.assert_not_called()


def test_transport_failure_prevents_new_queue_or_browser_and_does_not_fake_login(tmp_path,monkeypatch):
    from services import creator_connector
    monkeypatch.setattr(platform_policy,'publication_transport_state',lambda platform:{'ready':False,'reason':'Beijing TLS connection reset'})
    monkeypatch.setattr(creator_connector,'platform_directory',lambda platform:tmp_path)
    startup=Mock(side_effect=AssertionError('Transport failure must not start account browser'))
    monkeypatch.setattr(creator_connector,'_ensure_daemon',startup)
    with pytest.raises(ValueError,match='TLS'):
        creator_connector.submit_command('kuaishou','login')
    state=creator_connector.connection_state('kuaishou')
    assert state['state_code']=='network_error' and not state['authenticated']
    assert state['automation_enabled'] and not state['qr_ready']
    startup.assert_not_called()


def test_first_enable_near_kuaishou_slot_never_creates_a_past_draw(env,monkeypatch):
    db,run,items=env
    cfg=FactoryConfig(id=1,publish_times=['17:00','20:00','22:00'],platforms=['douyin','kuaishou'],publish_jitter_seconds=900)
    db.add(cfg);db.commit();now=datetime(2026,11,16,18,59,tzinfo=ZoneInfo('Asia/Shanghai'))
    monkeypatch.setattr(schedule,'randbelow',lambda upper:0)
    schedule.plan_kuaishou_slots(db,cfg,now)
    slots=[s for s in db.query(FactoryRelease) if s.platforms==['kuaishou']]
    assert all(s.due_at>=schedule.utc(now) and -900<=s.jitter_seconds<=900 for s in slots)
    before={s.id:s.due_at for s in slots}
    schedule.plan_kuaishou_slots(db,cfg,now+timedelta(minutes=2))
    assert before=={s.id:s.due_at for s in db.query(FactoryRelease)}
