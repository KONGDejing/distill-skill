"""Persistent one-platform routing and conservative daily admission accounting.

Content scores are editorial heuristics, never claims about platform algorithms.
No connector, model, scheduler, or production database is invoked by this module.
Callers commit the assignment in the same transaction as their release records.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from itertools import product
from zoneinfo import ZoneInfo

from sqlalchemy import update

from models.factory import FactoryItem, FactoryPublication, FactoryRun

CURRENT_DISTRIBUTION_POLICY = "single-platform-v1"
DISTRIBUTION_PLATFORMS = ("douyin", "xiaohongshu", "kuaishou")
DAILY_PLATFORM_LIMIT = 2
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
    """Validate only a NEW operation; idempotent receipt reads bypass this guard."""
    if platform not in DISTRIBUTION_PLATFORMS:
        raise ValueError("当前单平台发行计划只支持抖音、小红书和快手")
    run = db.get(FactoryRun, item.run_id)
    route = (item.brief or {}).get("distribution")
    today = local_day(now, timezone_name)
    strict = (run.settings_snapshot or {}).get("distribution_policy") == CURRENT_DISTRIBUTION_POLICY
    if strict or route is not None:
        if not isinstance(route, dict) or route.get("policy") != CURRENT_DISTRIBUTION_POLICY:
            raise ValueError("请先为这条作品确定唯一发行平台")
        if _day(route.get("day")) != today:
            raise ValueError("发行日期与今天不符，请显式重新排期后再操作")
        if route.get("platform") != platform:
            raise ValueError("这条作品已分配到其他平台，禁止跨平台重复发行")
    jobs = db.query(FactoryPublication).filter_by(item_id=item.id).all()
    prior_platforms = {job.platform for job in jobs}
    if route is None and not prior_platforms:
        raise ValueError("请先为这条作品分配唯一发行平台，再开始上传或发布")
    if len(prior_platforms) > 1:
        raise ValueError("旧作品已有多平台任务，仅保留历史回执，禁止新增发行操作")
    if prior_platforms and platform not in prior_platforms:
        raise ValueError("这条作品已有固定平台任务，禁止改投或跨平台扩散")
    occupied = reserved_item_ids(db, platform, today, include_prepares=action == "prepare",
                                timezone_name=timezone_name, exclude_item_id=item.id)
    if len(occupied) >= DAILY_PLATFORM_LIMIT:
        raise ValueError(f"该平台当天已预留或提交 {DAILY_PLATFORM_LIMIT} 条作品；未知结果也占额度，请改日安排")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {"policy": CURRENT_DISTRIBUTION_POLICY, "day": today,
            "timezone": timezone_name, "reserved_at": timestamp.isoformat(), "action": action}


def _content_scores(item):
    brief, content = item.brief or {}, item.content or {}
    text = " ".join(str(value) for value in (
        brief.get("title", ""), brief.get("category", ""), brief.get("angle", ""),
        content.get("title", ""), brief.get("audience_value", ""))).lower()
    terms = {
        "douyin": ("发布", "新品", "热点", "游戏", "视频", "动态", "画面", "突破"),
        "xiaohongshu": ("指南", "教程", "步骤", "清单", "评测", "怎么", "如何", "选型", "工具", "应用"),
        "kuaishou": ("机器人", "机械臂", "操作", "实操", "搭建", "动手", "推车", "积木", "制作"),
    }
    matches = {platform: [term for term in words if term in text] for platform, words in terms.items()}
    return {platform: len(words) for platform, words in matches.items()}, matches


def assign_distribution(db, run_id, day=None, slots=None, proposal=None):
    """Return {item_id: distribution}; flush, but leave commit to the caller.

    ``slots`` maps selected item IDs to ISO days; otherwise all run items use day.
    Optional director ``proposal`` maps IDs to a platform or {platform, reason}.
    Existing assignments and any job pin the platform. A SQLite write lock is
    acquired before reading capacities and held until the caller commits, so
    concurrent assignments cannot both consume the same remaining capacity.
    """
    db.execute(update(FactoryRun).where(FactoryRun.id == run_id).values(
        heartbeat_at=FactoryRun.heartbeat_at))
    run = db.get(FactoryRun, run_id)
    if run is None:
        raise ValueError("生产批次不存在")
    timezone_name = (run.settings_snapshot or {}).get("timezone", "Asia/Shanghai")
    all_items = db.query(FactoryItem).order_by(FactoryItem.run_id, FactoryItem.position, FactoryItem.id).all()
    run_items = [item for item in all_items if item.run_id == run_id]
    by_id = {item.id: item for item in run_items}
    if slots is None:
        if day is None:
            raise ValueError("请提供发行日期或逐条日期映射")
        requested = {item.id: _day(day) for item in run_items}
    else:
        if not isinstance(slots, dict) or not slots or set(slots) - set(by_id):
            raise ValueError("日期映射必须引用本批次的有效作品")
        requested = {item_id: _day(value) for item_id, value in slots.items()}
    proposal = proposal or {}
    if not isinstance(proposal, dict) or set(proposal) - set(requested):
        raise ValueError("负责人平台提议包含无效作品")
    proposed, proposal_reasons = {}, {}
    for item_id, value in proposal.items():
        platform = value.get("platform") if isinstance(value, dict) else value
        if platform not in DISTRIBUTION_PLATFORMS:
            raise ValueError("负责人平台提议无效")
        proposed[item_id] = platform
        proposal_reasons[item_id] = value.get("reason", "负责人按内容选定") if isinstance(value, dict) else "负责人按内容选定"
    jobs = db.query(FactoryPublication).all()
    jobs_by_item = defaultdict(list)
    for job in jobs:
        jobs_by_item[job.item_id].append(job)
    output, fixed = {}, {}
    for item_id, planned_day in requested.items():
        item = by_id[item_id]
        previous = (item.brief or {}).get("distribution")
        platforms = {job.platform for job in jobs_by_item[item_id]}
        if len(platforms) > 1 or platforms - set(DISTRIBUTION_PLATFORMS):
            raise ValueError("旧作品已有多平台或停用平台任务，不能新增单平台路由")
        if previous is not None:
            if (not isinstance(previous, dict) or previous.get("policy") != CURRENT_DISTRIBUTION_POLICY
                    or previous.get("platform") not in DISTRIBUTION_PLATFORMS):
                raise ValueError("已有发行分配无效，不能静默覆盖")
            if previous.get("day") != planned_day:
                raise ValueError("已有发行日期固定，不能静默改期")
            fixed[item_id] = previous["platform"]
            output[item_id] = dict(previous)
        if platforms:
            platform = next(iter(platforms))
            if item_id in fixed and fixed[item_id] != platform:
                raise ValueError("发行路由与已有平台任务冲突")
            fixed[item_id] = platform
        if item_id in proposed:
            if item_id in fixed and fixed[item_id] != proposed[item_id]:
                raise ValueError("已有路由或平台任务不可改投")
            fixed[item_id] = proposed[item_id]
    for planned_day in sorted(set(requested.values())):
        selected = [item for item in run_items if requested.get(item.id) == planned_day]
        selected_ids = {item.id for item in selected}
        if len(selected) > len(DISTRIBUTION_PLATFORMS) * DAILY_PLATFORM_LIMIT:
            raise ValueError("每天最多分配 6 条原创作品")
        occupied = {platform: set() for platform in DISTRIBUTION_PLATFORMS}
        for other in all_items:
            route = (other.brief or {}).get("distribution") or {}
            if (other.id not in selected_ids and route.get("policy") == CURRENT_DISTRIBUTION_POLICY
                    and route.get("day") == planned_day and route.get("platform") in occupied):
                occupied[route["platform"]].add(other.id)
        for job in jobs:
            if (job.item_id not in selected_ids and job.platform in occupied
                    and reserves_quota(job, True) and quota_day(job, timezone_name) in {None, planned_day}):
                occupied[job.platform].add(job.item_id)
        capacity = {platform: DAILY_PLATFORM_LIMIT - len(ids) for platform, ids in occupied.items()}
        choices = [(fixed[item.id],) if item.id in fixed else DISTRIBUTION_PLATFORMS for item in selected]
        scores = {item.id: _content_scores(item) for item in selected}
        best, best_score = None, None
        for candidate in product(*choices):
            if any(candidate.count(platform) > capacity[platform] for platform in DISTRIBUTION_PLATFORMS):
                continue
            score = sum(scores[item.id][0][platform] for item, platform in zip(selected, candidate))
            if best_score is None or score > best_score:
                best, best_score = candidate, score
        if best is None:
            raise ValueError("当天各平台最多 2 条的发行额度不足，请调整日期或有效提议")
        for item, platform in zip(selected, best):
            if item.id in output:
                continue
            if item.id in proposed:
                reason = "负责人内容适配提议（启发式，非平台官方推荐规则）：" + str(proposal_reasons[item.id])[:400]
            elif jobs_by_item[item.id]:
                reason = "沿用已有平台任务，禁止改投；不代表平台官方推荐规则"
            else:
                words = scores[item.id][1][platform]
                reason = "内容适配启发式（非平台官方推荐规则）：" + ("、".join(words) if words else "题材综合平衡") + "；按全体作品择优并遵守每日平台上限2条"
            output[item.id] = {"platform": platform, "day": planned_day, "reason": reason,
                               "policy": CURRENT_DISTRIBUTION_POLICY}
    # Validate every candidate before mutating any brief.
    for item_id, route in output.items():
        item = by_id[item_id]
        item.brief = {**(item.brief or {}), "distribution": route}
    db.flush()
    return output
