"""Owner-confirmed replacement keeps one original and never resets an uncertain retry."""
from datetime import datetime,timedelta,timezone
from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryEvent,FactoryItem,FactoryPublication,FactoryRelease,FactoryRun,FactoryWorkOrder
from services import factory_republication as replacement, creator_connector as connector
from services.factory_distribution import local_day,publication_usage


@pytest.fixture
def case(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "replacement.db"}')
    Base.metadata.create_all(engine,tables=[c.__table__ for c in
        (FactoryEvent,FactoryItem,FactoryPublication,FactoryRelease,FactoryRun,FactoryWorkOrder)])
    monkeypatch.setattr(connector,'RUNTIME',tmp_path/'private')
    monkeypatch.setattr(connector,'connection_state',lambda _: {'authenticated':True})
    monkeypatch.setattr(replacement.publishing,'LOCK_DIRECTORY',tmp_path/'locks')
    monkeypatch.setattr(replacement.publishing,'require_automation',lambda _: None)
    for name in ('require_owner_attribution','require_caption_safety','validate_publication_media','guard_publication'):
        monkeypatch.setattr(replacement,name,lambda *a,**kw: None)
    due=datetime.now(timezone.utc)+timedelta(minutes=2)
    evidence={'type':'success_page','text':'发布成功','url':'https://creator.douyin.com/creator-micro/content/manage'}
    with sessionmaker(bind=engine)() as db:
        run=FactoryRun(status='completed');db.add(run);db.flush()
        item=FactoryItem(run_id=run.id,position=0,status='completed',brief={'distribution':{'day':local_day(due)}},
            media={'video_path':'old.mp4'},packages=[{'platform':'douyin','video_path':'old.mp4'}])
        db.add(item);db.flush()
        db.add_all([FactoryPublication(item_id=item.id,platform='douyin',action='prepare',status='prepared'),
            FactoryPublication(item_id=item.id,platform='douyin',action='publish',status='submitted',result={'evidence':evidence}),
            FactoryRelease(item_id=item.id,run_id=run.id,platforms=['douyin'],status='submitted',
                due_at=datetime.utcnow(),nominal_at=due.replace(tzinfo=None),results={'douyin':{'status':'submitted'}})])
        db.commit()
        directory=connector._directories('douyin')
        connector._write(directory/'submissions.json',{item.id:{'status':'submitted','evidence':evidence}})
        yield db,item,due,evidence
    engine.dispose()


def test_owner_confirmation_is_required_and_unknown_attempts_are_not_retried(case):
    db,item,due,evidence=case
    with pytest.raises(ValueError,match='本人明确确认'):
        replacement.schedule_owner_confirmed_replacement(db,item.id,{}, {},due)
    job=db.query(FactoryPublication).filter_by(action='publish').one()
    job.status='failed';job.result={'submission_uncertain':True};db.commit()
    with pytest.raises(ValueError,match='旧提交缺少有效回执'):
        replacement.schedule_owner_confirmed_replacement(db,item.id,{}, {'platform':'douyin'},due,
            owner_confirmed_withdrawn=True,confirmation='已在官方端撤下')
    assert db.query(FactoryPublication).filter_by(action='publish_r0').count()==0
    assert 'replacement_authorization' not in connector._ledger('douyin')[item.id]


def test_replacement_retains_old_job_file_receipt_and_uses_same_release_and_original(case):
    db,item,due,evidence=case
    variant={'platform':'douyin','video_path':'corrected.mp4','delivery_quality':{'delivery_sha256':'approved-hash'}}
    old_slot=db.query(FactoryRelease).one();old_slot_id=old_slot.id
    slot=replacement.schedule_owner_confirmed_replacement(db,item.id,{'video_path':'new-raw.mp4'},variant,due,
        owner_confirmed_withdrawn=True,confirmation='已在官方端手动撤下旧作')
    assert slot.id==old_slot_id and slot.status=='scheduled'
    assert slot.due_at==due.replace(tzinfo=None)
    assert item.media['prior_submission']['media']['video_path']=='old.mp4'
    assert item.media['submission_revision']==1
    assert db.query(FactoryItem).count()==1 and db.query(FactoryRelease).count()==1
    old=db.query(FactoryPublication).filter_by(action='publish_r0').one()
    assert old.result['evidence']==evidence and old.status=='submitted'
    assert connector._ledger('douyin')[item.id]['evidence']==evidence
    assert connector._ledger('douyin')[item.id]['replacement_authorization']['owner_confirmed_withdrawn']
    # New canonical jobs reserve the same original once, rather than a fifth film.
    for action in ('prepare','publish'):
        db.add(FactoryPublication(item_id=item.id,platform='douyin',action=action,status='queued',
            result={'quota_reservation':{'day':local_day(due)}}))
    db.commit()
    assert publication_usage(db,'douyin',local_day(due),include_prepares=True)==1
    with pytest.raises(ValueError,match='已有独立重发版本'):
        replacement.schedule_owner_confirmed_replacement(db,item.id,{},variant,due,
            owner_confirmed_withdrawn=True,confirmation='已撤下')


def test_original_receipt_cannot_be_reported_as_a_new_revision_success(case,monkeypatch):
    db,item,due,evidence=case
    item.media={**item.media,'submission_revision':1}
    job=db.query(FactoryPublication).filter_by(action='publish').one()
    job.status='running';job.command_id='revision-command';db.commit()
    monkeypatch.setattr(replacement.publishing,'_command_result',lambda *args:{
        'status':'submitted','evidence':evidence,'submission_revision':0})
    result=replacement.publishing.refresh_job(db,job)
    assert result.status=='failed' and result.result['submission_uncertain'] is True
    assert item.media['caption_replacement']['status']=='failed'


def test_owner_voice_swap_never_overwrites_an_uploaded_or_attempted_version(case,monkeypatch):
    from services import factory_voice
    db,item,due,evidence=case
    monkeypatch.setattr(factory_voice,'selected_owner_voice',lambda db:{'voice_name':'clone:owner','voice_reference_sha256':'approved'})
    media={'voice_profile':'owner_clone','tts_engine':'cosyvoice2-owner-reference','duration':150,
           'voice_name':'clone:owner','voice_reference_sha256':'approved'}
    variant={'platform':'douyin','video_path':'new.mp4','delivery_quality':{'delivery_sha256':'new-hash'}}
    with pytest.raises(ValueError,match='本人确认新音色'):
        replacement.replace_unsubmitted_owner_voice(db,item.id,media,variant)
    with pytest.raises(ValueError,match='已有上传或提交任务'):
        replacement.replace_unsubmitted_owner_voice(db,item.id,media,variant,owner_approved=True)
    assert item.packages[0]['video_path']=='old.mp4'


def test_voice_swap_rebinds_only_the_unattempted_revision_and_preserves_old_receipt(case,monkeypatch):
    from services import factory_voice
    db,item,due,evidence=case
    monkeypatch.setattr(factory_voice,'selected_owner_voice',lambda db:{'voice_name':'clone:owner','voice_reference_sha256':'approved'})
    for job in db.query(FactoryPublication):job.action+='_r0'
    db.query(FactoryRelease).one().status='scheduled'
    item.media={**item.media,'submission_revision':1,'publication_hold':'voice rebuild'};db.commit()
    ledger=connector._ledger('douyin');ledger[item.id]['replacement_authorization']={
        'owner_confirmed_withdrawn':True,'submission_revision':1,'replacement_sha256':'old-correction-hash'}
    connector._write(connector.platform_directory('douyin')/'submissions.json',ledger)
    media={'voice_profile':'owner_clone','tts_engine':'cosyvoice2-owner-reference','duration':150,
           'voice_name':'clone:owner','voice_reference_sha256':'approved','audio_path':'owner.mp3'}
    variant={'platform':'douyin','video_path':'new.mp4','delivery_quality':{'delivery_sha256':'new-hash'}}
    replacement.replace_unsubmitted_owner_voice(db,item.id,media,variant,owner_approved=True)
    assert item.media['publication_hold'] is None and item.media['submission_revision']==1
    assert item.media['voice_revisions'][0]['previous_package']['video_path']=='old.mp4'
    previous=connector._ledger('douyin')[item.id]
    assert previous['evidence']==evidence
    assert previous['replacement_authorization']['replacement_sha256']=='new-hash'
    assert db.query(FactoryRelease).one().status=='scheduled'  # Swapping files never invents a new submit.
    # Even an uncertain attempt on the new revision permanently forbids rebinding.
    connector._write(connector.platform_directory('douyin')/'submissions.json',
                     {**connector._ledger('douyin'),item.id+':r1':{'status':'attempting'}})
    with pytest.raises(ValueError,match='已有提交尝试'):
        replacement.replace_unsubmitted_owner_voice(db,item.id,media,variant,owner_approved=True)
