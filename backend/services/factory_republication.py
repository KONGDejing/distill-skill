"""Explicit owner-confirmed replacement; retain the original jobs and ledger receipt."""
from copy import deepcopy
from datetime import datetime, timezone
import fcntl

from models.factory import FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryWorkOrder
from services import creator_connector as connector, factory_publishing as publishing
from services.factory_branding import require_owner_attribution
from services.factory_caption_layout import require_caption_safety
from services.factory_distribution import guard_publication, local_day
from services.factory_montage import validate_publication_media


def schedule_owner_confirmed_replacement(db, item_id, media, variant, due_at, *,
                                        owner_confirmed_withdrawn=False, confirmation=''):
    """Operator-only action after a real owner statement, never an automatic retry.

    Same original and release slot; a revision gets its own connector ledger key.
    There is no deletion, login bypass, new daily original, or receipt erasure.
    """
    if owner_confirmed_withdrawn is not True or not confirmation.strip():
        raise ValueError('需要账号本人明确确认已撤下旧作，不能自动重试')
    now = datetime.now(timezone.utc)
    if due_at.tzinfo is None or due_at <= now:
        raise ValueError('请指定有时区的未来重发时间')
    item = db.get(FactoryItem, item_id)
    if not item:
        raise ValueError('作品不存在')
    if local_day(due_at) != (item.brief.get('distribution') or {}).get('day'):
        raise ValueError('本次仅允许在同日原排期内替换，不自动跨日重发')
    if item.media.get('submission_revision'):
        raise ValueError('本条已有独立重发版本，请查看现有排期与回执，不重复安排')
    if variant.get('platform') != 'douyin':
        raise ValueError('本次替换仅针对抖音')
    require_owner_attribution(variant)
    require_caption_safety(media, variant)
    validate_publication_media(media)
    publishing.require_automation('douyin')
    publishing.LOCK_DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    with (publishing.LOCK_DIRECTORY / 'publication-douyin.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        jobs = db.query(FactoryPublication).filter_by(item_id=item_id, platform='douyin').all()
        old_publish = next((j for j in jobs if j.action == 'publish'), None)
        if (not old_publish or old_publish.status != 'submitted'
                or not publishing._valid_evidence('douyin', old_publish.result.get('evidence'))):
            raise ValueError('旧提交缺少有效回执，保留原记录等待核对')
        if db.query(FactoryPublication).filter(FactoryPublication.platform == 'douyin',
                FactoryPublication.status.in_(['queued', 'running'])).first():
            raise ValueError('抖音正在操作其他任务，请待当前操作结束后安排替换')
        held = connector.connection_state('douyin').get('prepared_item_id')
        if held:
            raise ValueError('抖音仍有已上传表单，不能覆盖；请先完成原任务')
        slot = db.query(FactoryRelease).filter_by(item_id=item_id).one()
        guard_publication(db, item, 'douyin', 'prepare')
        ledger = connector._ledger('douyin')
        previous = ledger.get(item_id, {})
        if (previous.get('status') != 'submitted'
                or previous.get('evidence') != old_publish.result.get('evidence')):
            raise ValueError('连接器旧回执与发布记录不一致，未安排重发')
        authorization = {'owner_confirmed_withdrawn': True, 'confirmation': confirmation.strip(),
                         'confirmed_at': now.isoformat(), 'submission_revision': 1,
                         'replacement_sha256': variant['delivery_quality']['delivery_sha256']}
        original = {'media': deepcopy(item.media),
                    'package': deepcopy(next(p for p in item.packages if p['platform'] == 'douyin')),
                    'release': {'due_at': slot.due_at.isoformat(), 'status': slot.status,
                                'results': deepcopy(slot.results)}, 'authorization': authorization}
        for job in jobs:
            if job.action in {'prepare', 'publish'}:
                job.action += '_r0'
        db.flush()
        item.media = {**item.media, **deepcopy(media), 'submission_revision': 1,
                      'prior_submission': original,
                      'caption_replacement': {**authorization, 'status': 'scheduled',
                                              'scheduled_at': due_at.isoformat()}}
        replacement = {**deepcopy(variant), 'status': 'package_ready', 'published': False}
        for key in ('publication_job_id', 'submission_evidence', 'publication_error'):
            replacement.pop(key, None)
        item.packages = [replacement if p['platform'] == 'douyin' else p for p in item.packages]
        slot.due_at = due_at.astimezone(timezone.utc).replace(tzinfo=None)
        slot.jitter_seconds = int((slot.due_at-(slot.nominal_at or slot.due_at)).total_seconds())
        slot.status = 'scheduled'
        slot.results = {'replacement': {'status': 'scheduled', **authorization,
                                      'original_publish_job_id': old_publish.id}}
        work = db.get(FactoryWorkOrder, 'caption-replacement:'+item_id)
        if work:
            work.status = 'in_progress'
            work.evidence = {**work.evidence, **authorization, 'scheduled_at': due_at.isoformat()}
        db.add(FactoryEvent(run_id=item.run_id, agent_id='publisher', stage='owner_replacement',
            status='completed', message='账号本人确认手动撤下旧作，修正版已重新排期；旧文件及回执保留。',
            detail={'item_id': item_id, 'due_at': due_at.isoformat(), **authorization}))
        # Authorization alone never sends a command. A crash before DB commit
        # retains the old canonical jobs, which still prevent another submit.
        previous = {**previous, 'replacement_authorization': authorization}
        connector._write(connector.platform_directory('douyin') / 'submissions.json',
                         {**ledger, item_id: previous})
        db.commit()
        return slot


def replace_unsubmitted_owner_voice(db, item_id, media, variant, *, owner_approved=False):
    """Atomically swap a validated voice export before any canonical upload.

    Preserve fallback files and withdrawn-work receipts. Revision authorization
    is rebound only while its new ledger key has never been attempted.
    """
    if owner_approved is not True:
        raise ValueError('需要本人确认新音色，未替换成片')
    item = db.get(FactoryItem,item_id)
    if not item or item.status != 'completed' or variant.get('platform') != 'douyin':
        raise ValueError('只能替换已完成的待发布抖音成片')
    if (media.get('voice_profile') != 'owner_clone'
            or media.get('tts_engine') != 'cosyvoice2-owner-reference'
            or not 135 <= float(media.get('duration',0)) <= 165):
        raise ValueError('本人音色或约150秒时长未通过验收')
    from services.factory_voice import selected_owner_voice
    selected = selected_owner_voice(db)
    if (not selected or selected['voice_name'] != media.get('voice_name')
            or selected['voice_reference_sha256'] != media.get('voice_reference_sha256')):
        raise ValueError('成片与本人当前确认的音色录音不一致')
    require_owner_attribution(variant)
    require_caption_safety(media,variant)
    validate_publication_media(media)
    publishing.require_automation('douyin')
    publishing.LOCK_DIRECTORY.mkdir(mode=0o700,exist_ok=True)
    with (publishing.LOCK_DIRECTORY/'publication-douyin.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if db.query(FactoryPublication).filter(FactoryPublication.item_id==item_id,
                FactoryPublication.platform=='douyin',FactoryPublication.action.in_(['prepare','publish'])).first():
            raise ValueError('本条已有上传或提交任务，不能覆盖绑定成片')
        if db.query(FactoryPublication).filter(FactoryPublication.platform=='douyin',
                FactoryPublication.status.in_(['queued','running'])).first():
            raise ValueError('平台仍在执行其他任务，待当前操作结束后再切换')
        if connector.connection_state('douyin').get('prepared_item_id')==item_id:
            raise ValueError('本条官方上传表单已绑定旧文件，未替换')
        slot=db.query(FactoryRelease).filter_by(item_id=item_id).one()
        if slot.status not in {'scheduled','waiting_media','blocked'}:
            raise ValueError('本条没有有效的待发布排期，需先重新安排时间')
        guard_publication(db,item,'douyin','prepare')
        ledger=connector._ledger('douyin')
        revision=item.media.get('submission_revision',0)
        key=f'{item_id}:r{revision}' if revision else item_id
        if key in ledger:
            raise ValueError('当前版本已有提交尝试，禁止用换声覆盖记录')
        old_media=deepcopy(item.media)
        old_package=deepcopy(next(p for p in item.packages if p['platform']=='douyin'))
        if revision:
            original=ledger.get(item_id,{})
            auth=original.get('replacement_authorization') or {}
            if auth.get('owner_confirmed_withdrawn') is not True or auth.get('submission_revision')!=revision:
                raise ValueError('撤下旧作的重发授权不完整，未替换')
            auth={**auth,'previous_replacement_sha256':auth.get('replacement_sha256'),
                  'replacement_sha256':variant['delivery_quality']['delivery_sha256'],
                  'owner_voice_approved_at':datetime.now(timezone.utc).isoformat()}
            connector._write(connector.platform_directory('douyin')/'submissions.json',
                             {**ledger,item_id:{**original,'replacement_authorization':auth}})
        history=list(old_media.get('voice_revisions') or [])
        history.append({'previous_audio_path':old_media.get('audio_path'),
                        'previous_video_path':old_media.get('video_path'),'previous_package':old_package})
        updated={**old_media,**deepcopy(media),'publication_hold':None,
                 'voice_revisions':history,'pending_owner_voice':{'status':'completed','owner_approved':True}}
        if revision:
            updated['caption_replacement']={**updated.get('caption_replacement',{}),**auth}
        item.media=updated
        item.packages=[deepcopy(variant) if p['platform']=='douyin' else p for p in item.packages]
        db.add(FactoryEvent(run_id=item.run_id,agent_id='reviewer',stage='owner_voice_delivery',status='completed',
            message='本人确认的音色版已完成配音、字幕和镜头同步验收，换入待发布成片；原版文件与历史回执保留。',
            detail={'item_id':item_id,'duration':media['duration'],'voice_name':media['voice_name']}))
        db.commit()
        return item
