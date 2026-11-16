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
    for index, owner in enumerate(OWNERS*2):
        i=FactoryItem(run_id=run.id,position=index,brief={'editor_id':owner},review={'approved':True,'score':90})
        db.add(i);items.append(i)
    db.flush();return run,items


def test_saved_policy_covers_three_distinct_genres_and_exact_owner_watch_scope():
    p=editorial_policy()
    assert p['production']['daily_total']==6 and p['production']['douyin_daily_total']==3
    assert len(p['people'])==50 and Counter(x['region'] for x in p['people'])=={'海外':40,'国内':10}
    assert {x['background'] for x in p['people'] if x['region']=='国内'}==set(p['domestic_company_allowlist'])
    assert {'Geoffrey Hinton','Mark Zuckerberg','Lisa Su','David Goeckeler','Sanjay Mehrotra'} <= {x['name'] for x in p['people']}
    assert '真实落地' in editor_profile('producer')['direction']
    assert '论文PPT' in editor_profile('producer2')['visual']
    assert '新闻' in editor_profile('producer3')['direction']
    p['people'].clear();assert len(editorial_policy()['people'])==50
    assert automation_enabled('douyin') and automation_enabled('kuaishou')
    assert not automation_enabled('xiaohongshu')


def test_topic_ownership_follows_story_not_generic_hardware_or_paper_keywords():
    for category in ['AI陪伴玩具','智能硬件','科技展会','端侧AI','无人船舶']:
        assert candidate_editor({'category':category,'editor_track':'technology_scenes'},[])=='producer'
    cited=[{'id':'paper','kind':'paper','url':'https://arxiv.org/abs/2601.12345'}]
    assert candidate_editor({'editor_track':'technology_scenes','source_ids':['paper']},cited)=='producer'
    assert candidate_editor({'category':'论文讲解','source_ids':['paper']},cited)=='producer2'
    assert candidate_editor({'category':'海外公司新闻','editor_track':'people_news'},[])=='producer3'


def test_all_six_saved_for_both_manual_platforms_but_only_one_per_editor_nominated(db):
    r,items=batch(db);routes=assign_distribution(db,r.id,DAY)
    assert len(routes)==6
    chosen=[i for i in items if routes[i.id]['douyin_selected']]
    assert Counter(i.brief['editor_id'] for i in chosen)==dict.fromkeys(OWNERS,1)
    assert all(x['manual_platforms']==['xiaohongshu','kuaishou'] for x in routes.values())
    db.commit();assert assign_distribution(db,r.id,DAY)==routes
    with pytest.raises(ValueError,match='不可静默'):
        assign_distribution(db,r.id,DAY,proposal={items[3].id:'douyin'})


def test_old_rules_and_manual_platforms_cannot_create_new_account_commands(db):
    r,items=batch(db);assign_distribution(db,r.id,DAY)
    with pytest.raises(ValueError,match='手动'):guard_publication(db,items[0],'xiaohongshu','prepare',now=NOW)
    with pytest.raises(ValueError,match='未入选'):guard_publication(db,items[0],'kuaishou','prepare',now=NOW)
    with pytest.raises(ValueError,match='未入选'):guard_publication(db,items[3],'douyin','prepare',now=NOW)
    r.settings_snapshot={'distribution_policy':'single-platform-v1'}
    with pytest.raises(ValueError,match='旧发行规则'):guard_publication(db,items[0],'douyin','prepare',now=NOW)


def test_upload_reserves_editor_quota_and_unknown_submissions_are_not_duplicated(db):
    r,items=batch(db);assign_distribution(db,r.id,DAY)
    guard_publication(db,items[0],'douyin','prepare',now=NOW)
    db.add(FactoryPublication(item_id=items[0].id,platform='douyin',action='prepare',status='prepared',
        created_at=NOW.replace(tzinfo=None)));db.flush()
    # Tampering a second item into the nomination still cannot spend the first one's quota.
    route=dict(items[3].brief['distribution']);route.update(platform='douyin',douyin_selected=True)
    items[3].brief={**items[3].brief,'distribution':route}
    with pytest.raises(ValueError,match='同岗'):guard_publication(db,items[3],'douyin','prepare',now=NOW)
    for i in items[1:3]:
        guard_publication(db,i,'douyin','prepare',now=NOW)
        db.add(FactoryPublication(item_id=i.id,platform='douyin',action='publish',status='failed',
            result={'submission_uncertain':True},created_at=NOW.replace(tzinfo=None)));db.flush()
    items[4].brief={**items[4].brief,'distribution':{**items[4].brief['distribution'],'platform':'douyin','douyin_selected':True}}
    with pytest.raises(ValueError,match='3条'):guard_publication(db,items[4],'douyin','prepare',now=NOW)


def test_three_release_slots_bind_different_editors_without_reducing_six_originals(db):
    r,items=batch(db);routes=assign_distribution(db,r.id,DAY);slots=[]
    for index in range(3):
        s=FactoryRelease(due_at=NOW.replace(tzinfo=None,hour=9+index));db.add(s);slots.append(s)
    db.flush();r.settings_snapshot={**r.settings_snapshot,'release_slot_ids':[s.id for s in slots]}
    bind_editor_releases(db,r,routes)
    assert len(items)==6 and [db.get(FactoryItem,s.item_id).brief['editor_id'] for s in slots]==list(OWNERS)
    assert all(s.platforms==['douyin'] for s in slots)


def test_finished_backup_can_replace_failed_pick_but_account_reservation_pins_choice(db):
    r,items=batch(db);assign_distribution(db,r.id,DAY)
    items[0].status='failed';items[3].status='completed';items[3].packages=[{'platform':'douyin'}]
    db.commit();reconcile_editor_picks(db,r)
    assert items[3].brief['distribution']['douyin_selected'] and not items[0].brief['distribution']['douyin_selected']
    db.add(FactoryPublication(item_id=items[3].id,platform='douyin',action='prepare',status='queued'))
    items[0].status='completed';items[0].review={'approved':True,'score':99};items[0].packages=[{'platform':'douyin'}]
    db.commit();reconcile_editor_picks(db,r)
    assert items[3].brief['distribution']['douyin_selected']


def test_ppt_is_allowed_for_paper_editor_and_rejected_for_scene_and_news_editors():
    assets=[{'kind':'paper_page'}]*3
    validate_track_assets('producer2',assets);validate_track_picture_coverage('producer2',{'dynamic_coverage':0})
    for owner in ['producer','producer3']:
        with pytest.raises(ValueError,match='PPT'):validate_track_assets(owner,assets)
        with pytest.raises(ValueError,match='动态'):validate_track_picture_coverage(owner,{'dynamic_coverage':.2})
        validate_track_picture_coverage(owner,{'dynamic_coverage':.8})
    assert matching_people({'title':'Geoffrey Hinton speaks; Mark Zuckerberg responds'})==['Mark Zuckerberg','Geoffrey Hinton']


def test_daily_production_uses_six_even_though_calendar_has_three_slots(db, monkeypatch):
    from services import factory_schedule as scheduler, factory_service
    from zoneinfo import ZoneInfo
    cfg=FactoryConfig(id=1,daily_target=6,schedule_enabled=True,schedule_time='00:30',
                      publish_times=['17:00','20:00','22:00'],publish_jitter_seconds=0)
    db.add(cfg);db.commit();now=datetime(2026,11,16,0,30,tzinfo=ZoneInfo('Asia/Shanghai'))
    def create(session, **kwargs):
        r=FactoryRun(id='actual-six',target_count=kwargs['target_count'],schedule_key=kwargs['schedule_key'],
            settings_snapshot={'distribution_policy':POLICY,'release_slot_ids':kwargs['release_slot_ids']})
        session.add(r);session.commit();return r
    start=Mock(side_effect=create);monkeypatch.setattr(factory_service,'start_run',start)
    scheduler.plan_slots(db,cfg,now);scheduler.plan_slots(db,cfg,now)
    assert db.query(FactoryRelease).count()==10  # Douyin 3 + Kuaishou 2, today and tomorrow.
    scheduler.plan_production(db,cfg,now);scheduler.plan_production(db,cfg,now)
    start.assert_called_once();assert start.call_args.kwargs['target_count']==6
    assert len(start.call_args.kwargs['release_slot_ids'])==3


def test_old_release_is_cancelled_without_an_account_command(db, monkeypatch):
    from services import factory_schedule as scheduler
    r=FactoryRun(id='old',settings_snapshot={'distribution_policy':'single-platform-v1'});db.add(r);db.flush()
    i=FactoryItem(run_id=r.id,position=0,status='completed');db.add(i);db.flush()
    slot=FactoryRelease(run_id=r.id,item_id=i.id,due_at=NOW.replace(tzinfo=None),platforms=['kuaishou']);db.add(slot)
    cfg=FactoryConfig(id=1);db.add(cfg);db.commit()
    command=Mock();monkeypatch.setattr(scheduler.publishing,'begin_publication',command)
    scheduler.advance_slot(db,slot,cfg,NOW.replace(tzinfo=None))
    assert slot.status=='cancelled';command.assert_not_called()


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
