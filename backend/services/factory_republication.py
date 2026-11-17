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
