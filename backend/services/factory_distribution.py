"""Persistent three-track Douyin selection and conservative daily admission accounting.

Content scores are editorial heuristics, never claims about platform algorithms.
No connector, model, scheduler, or production database is invoked by this module.
Callers commit the assignment in the same transaction as their release records.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import update

from models.factory import FactoryItem, FactoryPublication, FactoryRun

CURRENT_DISTRIBUTION_POLICY = "douyin-three-tracks-v2"
DISTRIBUTION_PLATFORMS = ("douyin", "xiaohongshu", "kuaishou")
DAILY_PLATFORM_LIMIT = 3
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


def guard_publication(db, item, platform, action, *, timezone_name="Asia/Shanghai", now=None):
    """Only a nominated daily Douyin original may create a NEW account command."""
    from services.factory_team import editor_id
    if platform != 'douyin':
        raise ValueError('小红书和快手由本人按需手动上传，禁止创建自动发布任务')
    run = db.get(FactoryRun, item.run_id)
    route = (item.brief or {}).get('distribution') or {}
    if (not run or (run.settings_snapshot or {}).get('distribution_policy') != CURRENT_DISTRIBUTION_POLICY
            or route.get('policy') != CURRENT_DISTRIBUTION_POLICY):
        raise ValueError('旧发行规则已停用；历史回执可读取，新增上传须按三位剪辑师各一条的新规则排期')
    today = local_day(now, timezone_name)
    owner = editor_id(item)
    if _day(route.get('day')) != today:
        raise ValueError('发行日期与今天不符，请显式重新排期后再操作')
    if route.get('douyin_selected') is not True or route.get('platform') != 'douyin' or route.get('editor_id') != owner:
        raise ValueError('本条未入选该剪辑师当天的抖音作品；完整素材保留供本人手动上传')
    prior = db.query(FactoryPublication).filter_by(item_id=item.id).all()
    if any(job.platform != 'douyin' for job in prior):
        raise ValueError('作品有其他平台历史任务，须核对后显式重新排期')
    occupied = reserved_item_ids(db, 'douyin', today, include_prepares=True,
                                 timezone_name=timezone_name, exclude_item_id=item.id)
    if len(occupied) >= DAILY_PLATFORM_LIMIT:
        raise ValueError('抖音当天已预留或提交3条作品；未知结果也占额度，请改日安排')
    for identifier in occupied:
        other = db.get(FactoryItem, identifier)
        if other and editor_id(other) == owner:
            raise ValueError('这位剪辑师当天已有一条抖音任务，禁止用同岗作品占用其他方向名额')
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {'policy': CURRENT_DISTRIBUTION_POLICY, 'day': today, 'editor_id': owner,
            'timezone': timezone_name, 'reserved_at': timestamp.isoformat(), 'action': action}


def assign_distribution(db, run_id, day=None, slots=None, proposal=None):
    """Save all originals for manual use and nominate one per editor for Douyin.

    A SQLite write lock serializes day-wide selection. Existing nominations are
    stable; a submitted or uncertain job also reserves that editor's daily slot.
    ``proposal`` may nominate an item as 'douyin' or retain it as 'manual'.
    """
    from services.factory_team import editor_id
    from services.factory_editorial_policy import EDITOR_IDS, MANUAL_PLATFORMS
    db.execute(update(FactoryRun).where(FactoryRun.id == run_id).values(heartbeat_at=FactoryRun.heartbeat_at))
    run = db.get(FactoryRun, run_id)
    if not run or (run.settings_snapshot or {}).get('distribution_policy') != CURRENT_DISTRIBUTION_POLICY:
        raise ValueError('新增分发必须使用抖音三方向与全量人工交付的新规则')
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
    output = {}
    for key, planned in requested.items():
        route = (by_id[key].brief or {}).get('distribution')
        if route:
            if route.get('policy') != CURRENT_DISTRIBUTION_POLICY or route.get('day') != planned:
                raise ValueError('已有历史规则或日期不能静默改期，请显式迁移')
            if route.get('editor_id') != editor_id(by_id[key]):
                raise ValueError('已有分发的剪辑师归属不一致')
            if key in proposal and (proposal[key] == 'douyin') != bool(route.get('douyin_selected')):
                raise ValueError('已有抖音预选不可静默覆盖')
            output[key] = dict(route)
    for planned in sorted(set(requested.values())):
        group = [i for i in selected if requested.get(i.id) == planned]
        if len(group) > 6 or any(sum(editor_id(i) == owner for i in group) > 2 for owner in EDITOR_IDS):
            raise ValueError('每天三位剪辑师各最多2条，合计最多6条原创')
        selected_ids = {i.id for i in group}
        occupied = {editor_id(i) for i in items if i.id not in selected_ids
                    and ((i.brief or {}).get('distribution') or {}).get('policy') == CURRENT_DISTRIBUTION_POLICY
                    and ((i.brief or {}).get('distribution') or {}).get('day') == planned
                    and ((i.brief or {}).get('distribution') or {}).get('douyin_selected') is True}
        timezone_name = (run.settings_snapshot or {}).get('timezone', 'Asia/Shanghai')
        reserved = reserved_item_ids(db, 'douyin', planned, include_prepares=True, timezone_name=timezone_name)
        occupied.update(editor_id(i) for i in items if i.id in reserved and i.id not in selected_ids)
        nominations = set()
        for owner in EDITOR_IDS:
            candidates = [i for i in group if editor_id(i) == owner]
            pinned = [i for i in candidates if output.get(i.id, {}).get('douyin_selected') or i.id in reserved
                      or proposal.get(i.id) == 'douyin']
            if len(pinned) > 1 or (pinned and owner in occupied):
                raise ValueError('每位剪辑师每天只可预选一条抖音作品，不能改投或挤占其他方向名额')
            if pinned:
                nominations.add(pinned[0].id)
            elif owner not in occupied:
                eligible = [i for i in candidates if i.id not in output and proposal.get(i.id) != 'manual']
                if eligible and len(reserved | nominations) < DAILY_PLATFORM_LIMIT:
                    nominations.add(eligible[0].id)
        for item in group:
            if item.id in output:
                continue
            chosen = item.id in nominations
            output[item.id] = {'policy': CURRENT_DISTRIBUTION_POLICY, 'day': planned,
                'editor_id': editor_id(item), 'platform': 'douyin' if chosen else None,
                'douyin_selected': chosen, 'manual_platforms': list(MANUAL_PLATFORMS),
                'reason': '每位剪辑师每日预选1条抖音作品，发布仍须通过质检；全部6条保留完整成片和小红书、快手人工发布文案。'
                          if chosen else '本条完整保存供本人按需手动上传小红书、快手，不创建账号自动上传任务。'}
    for key, route in output.items():
        by_id[key].brief = {**(by_id[key].brief or {}), 'distribution': route}
    db.flush()
    return output
