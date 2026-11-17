"""Persistent non-paper six-to-four sampling and conservative daily admission accounting.

Content scores are editorial heuristics, never claims about platform algorithms.
No connector, model, scheduler, or production database is invoked by this module.
Callers commit the assignment in the same transaction as their release records.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update

from models.factory import FactoryItem, FactoryPublication, FactoryRun

CURRENT_DISTRIBUTION_POLICY = "douyin-four-random-v1"
DISTRIBUTION_PLATFORMS = ("douyin", "xiaohongshu", "kuaishou")
DAILY_PLATFORM_LIMIT = 4
RESERVATION_KEY = "quota_reservation"


def _day(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("发行日期必须为 YYYY-MM-DD")
    return value


def local_day(now=None, timezone_name="Asia/Shanghai"):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _no_submit_proven(job):
    result = job.result or {}
    return (job.status == "failed" and result.get("submission_attempted") is False
            and not result.get("submission_uncertain"))


def quota_day(job, timezone_name="Asia/Shanghai"):
    reservation = (job.result or {}).get(RESERVATION_KEY)
    if isinstance(reservation, dict):
        try:
            return _day(reservation.get("day"))
        except (TypeError, ValueError):
            return None  # Unknown dates remain reserved, rather than disappear.
    return local_day(job.created_at, timezone_name) if job.created_at else None


def reserves_quota(job, include_prepares=False):
    if job.action == "publish":
        # queued/running/submitted and unknown results all consume capacity.
        return not _no_submit_proven(job)
    if not include_prepares or job.action != "prepare":
        return False
    result = job.result or {}
    return not (job.status == "failed" and result.get("uploaded") is False
                and not result.get("submission_uncertain"))


def reserved_item_ids(db, platform, day, *, include_prepares=False,
                      timezone_name="Asia/Shanghai", exclude_item_id=None):
    day = _day(day)
    jobs = db.query(FactoryPublication).filter_by(platform=platform).all()
    return {job.item_id for job in jobs
            if job.item_id != exclude_item_id and reserves_quota(job, include_prepares)
            and quota_day(job, timezone_name) in {None, day}}


def publication_usage(db, platform, day, *, include_prepares=False,
                      timezone_name="Asia/Shanghai"):
    """Unique reserved originals; prepare+publish for one item count only once."""
    return len(reserved_item_ids(db, platform, day, include_prepares=include_prepares,
                                 timezone_name=timezone_name))


def guard_publication(db, item, platform, action, *, timezone_name='Asia/Shanghai', now=None):
    from services.factory_team import editor_id
    if platform != 'douyin':
        raise ValueError('快手、小红书改为本人手动上传，禁止创建自动发布任务')
    db.execute(update(FactoryRun).where(FactoryRun.id == item.run_id).values(heartbeat_at=FactoryRun.heartbeat_at))
    db.refresh(item, ['brief'])
    run = db.get(FactoryRun, item.run_id)
    route = (item.brief or {}).get('distribution') or {}
    if (not run or run.settings_snapshot.get('distribution_policy') != CURRENT_DISTRIBUTION_POLICY
            or route.get('policy') != CURRENT_DISTRIBUTION_POLICY):
        raise ValueError('旧发行规则已停用；新增上传须按每日非论文6选4的新规则排期')
    today = local_day(now, timezone_name)
    owner = editor_id(item)
    if (owner not in {'producer', 'producer3'} or run.settings_snapshot.get('production_kind') == 'weekly_papers'
            or item.brief.get('editor_track') == 'paper_explainer'
            or item.content.get('editor_id') == 'producer2'):
        raise ValueError('论文视频不上传抖音，保留每周人工交付包')
    if _day(route.get('day')) != today:
        raise ValueError('发行日期与今天不符，请显式重新排期后再操作')
    if (route.get('douyin_selected') is not True or route.get('platform') != 'douyin'
            or route.get('editor_id') != owner):
        raise ValueError('本条未入选当天随机4条抖音作品；剩余2条保留人工上传')
    identifiers = {item.id}
    origin = item.brief.get('origin_item_id')
    if origin:
        identifiers.add(origin)
    prior = db.query(FactoryPublication).filter(FactoryPublication.item_id.in_(identifiers)).all()
    if any(job.platform != platform or (job.item_id != item.id and reserves_quota(job, True)) for job in prior):
        raise ValueError('本作品或原始版本已有平台任务，须核对，禁止重复提交')
    occupied = reserved_item_ids(db, platform, today, include_prepares=True,
        timezone_name=timezone_name, exclude_item_id=item.id)
    if len(occupied) >= DAILY_PLATFORM_LIMIT:
        raise ValueError('抖音当天已预留或提交4条作品；未知结果也占额度，请改日安排')
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {'policy': CURRENT_DISTRIBUTION_POLICY, 'day': today, 'editor_id': owner,
            'timezone': timezone_name, 'reserved_at': timestamp.isoformat(), 'action': action}


def assign_distribution(db, run_id, day=None, slots=None, proposal=None):
    """Persist a uniform sample without replacement. Reads never redraw picks.

    Explicit owner choices and any account reservation pin membership. Paper
    batches always remain manual. Call under the caller's write transaction.
    """
    from secrets import SystemRandom
    from services.factory_team import editor_id
    from services.factory_editorial_policy import MANUAL_PLATFORMS
    db.execute(update(FactoryRun).where(FactoryRun.id == run_id).values(heartbeat_at=FactoryRun.heartbeat_at))
    run = db.get(FactoryRun, run_id)
    if not run or run.settings_snapshot.get('distribution_policy') != CURRENT_DISTRIBUTION_POLICY:
        raise ValueError('新增分发必须使用日常非论文6选4规则')
    items = db.query(FactoryItem).order_by(FactoryItem.run_id, FactoryItem.position, FactoryItem.id).all()
    selected = [i for i in items if i.run_id == run_id and i.status != 'deferred']
    by_id = {i.id: i for i in selected}
    requested = {i.id: _day(day) for i in selected} if slots is None else {key: _day(value) for key, value in slots.items()}
    if not requested or set(requested) - set(by_id):
        raise ValueError('日期映射必须引用本批次的有效作品')
    proposal = proposal or {}
    if (not isinstance(proposal, dict) or set(proposal) - set(requested)
            or any(value not in ('douyin', 'manual') for value in proposal.values())):
        raise ValueError('仅可提议入选抖音或保留人工素材')
    weekly = run.settings_snapshot.get('production_kind') == 'weekly_papers'
    output = {}
    for key, planned in requested.items():
        route = (by_id[key].brief or {}).get('distribution')
        if route:
            if route.get('policy') != CURRENT_DISTRIBUTION_POLICY or route.get('day') != planned:
                raise ValueError('已有历史规则或日期不能静默改期，请显式迁移')
            if route.get('editor_id') != editor_id(by_id[key]):
                raise ValueError('已有分发的剪辑师归属不一致')
            if key in proposal and (proposal[key] == 'douyin') != bool(route.get('douyin_selected')):
                raise ValueError('已有随机抽选不可静默覆盖')
            output[key] = dict(route)
    selections = dict(run.settings_snapshot.get('douyin_random_selections') or {})
    for planned in sorted(set(requested.values())):
        group = [i for i in selected if requested.get(i.id) == planned]
        if weekly:
            if any(editor_id(i) != 'producer2' or proposal.get(i.id) == 'douyin' for i in group):
                raise ValueError('每周论文批次只接小影2，禁止选择抖音')
            nominations = set()
        else:
            if any(editor_id(i) == 'producer2' for i in group):
                raise ValueError('论文只能走每周独立批次，不能混入日常6条')
            daily = [i for i in items if i.id not in {x.id for x in group}
                and i.brief.get('distribution', {}).get('policy') == CURRENT_DISTRIBUTION_POLICY
                and i.brief.get('distribution', {}).get('day') == planned
                and i.brief.get('distribution', {}).get('production_kind') != 'weekly_papers'] + group
            if len(daily) > 6 or sum(editor_id(i) == 'producer' for i in daily) > 4 or sum(editor_id(i) == 'producer3' for i in daily) > 2:
                raise ValueError('每天小影最多4条、小影3最多2条，日常总量最多6条')
            group_ids = {i.id for i in group}
            reserved = reserved_item_ids(db, 'douyin', planned, include_prepares=True,
                timezone_name=run.settings_snapshot.get('timezone', 'Asia/Shanghai'))
            outside = reserved - group_ids
            outside.update(i.id for i in daily if i.id not in group_ids and i.brief.get('distribution', {}).get('douyin_selected'))
            pinned = {i.id for i in group if output.get(i.id, {}).get('douyin_selected')
                or i.id in reserved or proposal.get(i.id) == 'douyin'}
            capacity = max(0, DAILY_PLATFORM_LIMIT - len(outside))
            if len(pinned) > capacity:
                raise ValueError('固定选择与已有任务超过每日4条，保留历史记录等待核对')
            candidates = [i.id for i in group if i.id not in output and i.id not in pinned and proposal.get(i.id) != 'manual']
            draw_count = min(len(candidates), capacity-len(pinned))
            drawn = SystemRandom().sample(candidates, draw_count) if draw_count else []
            nominations = pinned | set(drawn)
            if drawn or planned not in selections:
                selections[planned] = {'method': 'system_random_sample_without_replacement',
                    'candidate_ids': candidates, 'drawn_ids': drawn,
                    'selected_ids': sorted(nominations), 'owner_pinned_ids': sorted({k for k,v in proposal.items() if v == 'douyin'} | {i.id for i in group if output.get(i.id,{}).get('owner_pinned')}),
                    'recorded_at': datetime.now(timezone.utc).isoformat()}
        for item in group:
            if item.id in output:
                continue
            chosen = item.id in nominations
            output[item.id] = {'policy': CURRENT_DISTRIBUTION_POLICY, 'day': planned,
                'production_kind': 'weekly_papers' if weekly else 'daily',
                'editor_id': editor_id(item), 'platform': 'douyin' if chosen else None,
                'douyin_selected': chosen, 'kuaishou_selected': False,
                'owner_pinned': proposal.get(item.id) == 'douyin',
                'manual_platforms': list(MANUAL_PLATFORMS),
                'reason': ('每周论文独立人工交付，不上抖音。' if weekly else
                    '用户明确指定今天上传抖音；保留官方核验、AI声明、署名与禁止下载。' if proposal.get(item.id) == 'douyin' else
                    '日常非论文6条中持久随机抽4条上传抖音，刷新不重抽；保留完整下载包。' if chosen else
                    '未入选抖音，完整成片与文案保留，由本人手动上传快手或小红书。')}
    for key, route in output.items():
        by_id[key].brief = {**(by_id[key].brief or {}), 'distribution': route}
    run.settings_snapshot = {**run.settings_snapshot, 'douyin_random_selections': selections}
    db.flush()
    return output
