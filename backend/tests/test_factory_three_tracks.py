from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
from models.factory import FactoryConfig, FactoryRun, FactoryItem, FactoryPublication, FactoryRelease
from services.factory_distribution import assign_distribution, guard_publication, CURRENT_DISTRIBUTION_POLICY as POLICY
from services.factory_editorial_policy import (editorial_policy, editor_profile, validate_track_assets,
                                             validate_track_picture_coverage, matching_people)
from services.factory_platform_policy import automation_enabled
from services.factory_team import candidate_editor
from services.factory_schedule import bind_editor_releases, reconcile_editor_picks

DAY = '2026-11-16'
NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
OWNERS = ('producer', 'producer2', 'producer3')


@pytest.fixture
def db():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session: yield session
    engine.dispose()


def batch(db, key='daily'):
    run = FactoryRun(id=key, active_slot=None, settings_snapshot={'distribution_policy': POLICY, 'timezone': 'Asia/Shanghai'})
    db.add(run);db.flush();run.active_slot=None;db.flush()
    items=[]
    for index, owner in enumerate(('producer',)*4+('producer3',)*2):
        i=FactoryItem(run_id=run.id,position=index,brief={'editor_id':owner},review={'approved':True,'score':90})
        db.add(i);items.append(i)
    db.flush();return run,items


def test_saved_policy_retains_watch_scope_and_replaces_daily_paper_plan():
    p=editorial_policy()
    assert p['production']['daily_total']==6 and p['production']['douyin_daily_total']==4
    assert p['production']['editor_daily_quotas']=={'producer':4,'producer2':0,'producer3':2}
    assert p['production']['weekly_papers']['total']==2
    assert len(p['people'])==50 and Counter(x['region'] for x in p['people'])=={'海外':40,'国内':10}
    assert {'Geoffrey Hinton','Mark Zuckerberg','Lisa Su'} <= {x['name'] for x in p['people']}
    assert '新智元' in editor_profile('producer')['visual']
    assert '论文PPT' in editor_profile('producer2')['visual']
    assert automation_enabled('douyin') and not automation_enabled('kuaishou')
    assert not automation_enabled('xiaohongshu')


def test_uniform_sample_is_saved_and_polling_does_not_redraw(db,monkeypatch):
    import secrets
    sample=Mock(side_effect=lambda population,k:population[-k:] if k else [])
    monkeypatch.setattr(secrets.SystemRandom,'sample',lambda self,population,k:sample(population,k))
    run,items=batch(db)
    routes=assign_distribution(db,run.id,DAY)
    assert sum(r['douyin_selected'] for r in routes.values())==4
    assert sum(not r['douyin_selected'] for r in routes.values())==2
    assert all(r['manual_platforms']==['xiaohongshu','kuaishou'] and not r['kuaishou_selected'] for r in routes.values())
    db.commit();assert assign_distribution(db,run.id,DAY)==routes
    assert sample.call_count==1
    for i in items:i.review={'approved':True,'score':100-i.position}
    reconcile_editor_picks(db,run)
    assert {i.id for i in items if i.brief['distribution']['douyin_selected']}=={k for k,v in routes.items() if v['douyin_selected']}
    assert run.settings_snapshot['douyin_random_selections'][DAY]['method']=='system_random_sample_without_replacement'


def test_owner_pins_two_and_remaining_two_are_random_without_replacement(db):
    run,items=batch(db)
    pin={items[0].id:'douyin',items[4].id:'douyin'}
    routes=assign_distribution(db,run.id,DAY,proposal=pin)
    assert all(routes[key]['douyin_selected'] and routes[key]['owner_pinned'] for key in pin)
    selected={key for key,value in routes.items() if value['douyin_selected']}
    assert len(selected)==4 and set(pin)<=selected
    assert len(run.settings_snapshot['douyin_random_selections'][DAY]['drawn_ids'])==2
    with pytest.raises(ValueError,match='不可静默'):
        assign_distribution(db,run.id,DAY,proposal={next(k for k in routes if k not in selected):'douyin'})


def test_daily_planning_rejects_paper_but_weekly_papers_are_manual(db):
    run,items=batch(db);items[0].brief={'editor_id':'producer2'}
    with pytest.raises(ValueError,match='每周独立'):
        assign_distribution(db,run.id,DAY)
    db.rollback()
    run=FactoryRun(id='weekly',active_slot=None,target_count=1,settings_snapshot={'distribution_policy':POLICY,'production_kind':'weekly_papers'})
    db.add(run);db.flush()
    paper=FactoryItem(run_id=run.id,position=0,brief={'editor_id':'producer2'},content={});db.add(paper);db.flush()
    route=assign_distribution(db,run.id,DAY)[paper.id]
    assert route['production_kind']=='weekly_papers' and not route['douyin_selected']
    with pytest.raises(ValueError,match='论文视频'):
        guard_publication(db,paper,'douyin','prepare',now=NOW)


def test_same_editor_can_fill_multiple_slots_but_four_reservations_stop_fifth(db):
    run,items=batch(db)
    routes=assign_distribution(db,run.id,DAY,proposal={item.id:'douyin' if item.position<4 else 'manual' for item in items})
    for item in items[:4]:
        guard_publication(db,item,'douyin','prepare',now=NOW)
        db.add(FactoryPublication(item_id=item.id,platform='douyin',action='publish',status='failed',
            result={'submission_uncertain':True},created_at=NOW.replace(tzinfo=None)))
        db.flush()
    fifth=items[4];fifth.brief={**fifth.brief,'distribution':{**routes[fifth.id],'douyin_selected':True,'platform':'douyin'}}
    with pytest.raises(ValueError,match='4条'):
        guard_publication(db,fifth,'douyin','prepare',now=NOW)
    with pytest.raises(ValueError,match='手动'):
        guard_publication(db,items[0],'kuaishou','prepare',now=NOW)


def test_four_slots_bind_distinct_items_and_keep_user_pins_first(db):
    run,items=batch(db);routes=assign_distribution(db,run.id,DAY,proposal={items[0].id:'douyin',items[4].id:'douyin'})
    slots=[FactoryRelease(due_at=NOW.replace(tzinfo=None,hour=9+n)) for n in range(4)]
    db.add_all(slots);db.flush();run.settings_snapshot={**run.settings_snapshot,'release_slot_ids':[s.id for s in slots]}
    bind_editor_releases(db,run,routes)
    assert slots[0].item_id==items[0].id and slots[1].item_id==items[4].id
    assert len({s.item_id for s in slots})==4
    before=[s.item_id for s in slots];bind_editor_releases(db,run)
    assert [s.item_id for s in slots]==before


def test_daily_six_and_four_slots_are_independent_and_kuaishou_has_no_schedule(db,monkeypatch):
    from services import factory_schedule as scheduler,factory_service
    from zoneinfo import ZoneInfo
    cfg=FactoryConfig(id=1,daily_target=6,schedule_enabled=True,schedule_time='00:30',publish_jitter_seconds=0)
    db.add(cfg);db.commit();now=datetime(2026,11,16,0,30,tzinfo=ZoneInfo('Asia/Shanghai'))
    def create(session,**kwargs):
        run=FactoryRun(id='six',target_count=kwargs['target_count'],schedule_key=kwargs['schedule_key'],
            active_slot=None,settings_snapshot={'distribution_policy':POLICY,'release_slot_ids':kwargs['release_slot_ids']})
        session.add(run);session.commit();return run
    start=Mock(side_effect=create);monkeypatch.setattr(factory_service,'start_run',start)
    scheduler.plan_slots(db,cfg,now);scheduler.plan_slots(db,cfg,now)
    assert db.query(FactoryRelease).count()==8
    assert all(slot.platforms==['douyin'] for slot in db.query(FactoryRelease))
    scheduler.plan_production(db,cfg,now);scheduler.plan_production(db,cfg,now)
    start.assert_called_once();assert start.call_args.kwargs['target_count']==6
    assert len(start.call_args.kwargs['release_slot_ids'])==4


def test_ppt_stays_only_with_paper_editor():
    assets=[{'kind':'paper_page'}]*3
    validate_track_assets('producer2',assets)
    for owner in ['producer','producer3']:
        with pytest.raises(ValueError,match='PPT'):validate_track_assets(owner,assets)
        with pytest.raises(ValueError,match='动态'):validate_track_picture_coverage(owner,{'dynamic_coverage':.2})
        validate_track_picture_coverage(owner,{'dynamic_coverage':.8})


def test_old_rules_cannot_create_new_uploads(db):
    run,items=batch(db);assign_distribution(db,run.id,DAY)
    run.settings_snapshot={'distribution_policy':'douyin-three-tracks-v2'}
    with pytest.raises(ValueError,match='旧发行规则'):
        guard_publication(db,items[0],'douyin','prepare',now=NOW)


def test_single_original_archive_contains_one_chinese_video_and_both_manual_copies(tmp_path):
    from services.factory_handoff import build_handoff
    from services.factory_original_archive import save_original_archive
    source=tmp_path/'master.mp4';source.write_bytes(b'already verified media bytes')
    cover=tmp_path/'cover.png';cover.write_bytes(b'already rendered cover')
    route={'policy':POLICY,'day':DAY,'editor_id':'producer','douyin_selected':True,
           'platform':'douyin','manual_platforms':['xiaohongshu','kuaishou']}
    content={'title':'机械手的新进展','script':'已经审核的完整口播','sources':[{'url':'https://openai.com/news/'}]}
    packages=[]
    for platform in ['xiaohongshu','kuaishou']:
        handoff=build_handoff(content,{'platform':platform,'title':platform+'标题','caption':'只使用已审核的事实','tags':['AI']},route)
        packages.append({'platform':platform,'video_path':str(source),'cover_path':str(cover),'manual_handoff':handoff})
    saved=save_original_archive(content,packages,{},tmp_path,route)
    with zipfile.ZipFile(saved['archive_path']) as archive:
        assert archive.testzip() is None
        assert [n for n in archive.namelist() if n.endswith('.mp4')]==['机械手的新进展.mp4']
        for prefix in ['小红书','快手']:
            assert archive.read(prefix+'/发布标题.txt').decode().strip().endswith('标题')
            assert '已审核' in archive.read(prefix+'/正文描述.txt').decode()
    assert saved['manual_master_path']==str(source)


def test_completed_new_policy_day_cannot_accidentally_start_another_six(db, monkeypatch):
    from services import factory_service
    r,items=batch(db);r.status='completed';r.active_slot=None
    from services.factory_distribution import local_day
    r.settings_snapshot={**r.settings_snapshot,'production_day':local_day()};r.target_count=6
    db.add(FactoryConfig(id=1));db.commit()
    monkeypatch.setattr(factory_service,'queue_available',lambda:True)
    with pytest.raises(ValueError,match='当天6条'):
        factory_service.start_run(db,target_count=6)
