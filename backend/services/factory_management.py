"""The owner's four-week growth contract and evidence-based employee scorecards."""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from models.factory import FactoryAnalyticsSnapshot, FactoryGrowthPlan, FactoryItem, FactoryPublication, FactoryRun, FactoryWorkOrder
from services.factory_editorial import validate_follow_prompt
from services.factory_team import editor_id
from agents.registry import EDITOR_IDS

WEEKS = [
    {"week": 1, "focus": "建立基线、修复交付，验证150秒样片", "actions": ["同一主题只改变开场方式做对照", "每日核对原始资料、声音与画面", "启用三平台数据采集，接入B站账号"], "decision": "先保证真实完整的发布与数据；样片不过关就返工，不用重复视频凑10条"},
    {"week": 2, "focus": "优化开头和讲解结构", "actions": ["每个平台至少比较3组同系列内容", "单独比较问题式与结果式开场", "根据评论里的真实疑问补充下一条选题"], "decision": "样本不足就继续收集，不把一次高播放当规律"},
    {"week": 3, "focus": "形成系列，验证持续关注理由", "actions": ["在有实际数据支持的栏目增加2个制作名额", "比较两种自然关注提醒，每次只变一个因素", "B站专题用多个来源解释一个完整问题"], "decision": "同时检查完播与净增粉丝，避免只追播放或堆砌热点"},
    {"week": 4, "focus": "复核可重复的内容方式", "actions": ["复测表现较好的结构，核对相同统计周期", "停止反复低效且没有明确改进依据的内容套路", "提交四平台月报、遗留问题和下月安排"], "decision": "报告未达标平台与原因，不承诺必然涨粉，不买粉、不互粉冲数"},
]
RHYTHM = [
    {"time": "00:30", "owner": "老周 / 小探", "task": "自动开工，筛选有出处的候选与素材，确定10条原创计划"},
    {"time": "制作过程中", "owner": "小文 / 小影 / 小审", "task": "按步骤交接；脚本、素材对齐或时长不合格时有界返工"},
    {"time": "每30秒", "owner": "老周", "task": "巡检生产、内存、失败和不确定回执，更新问题责任人"},
    {"time": "每30分钟", "owner": "小数", "task": "读取官方数据，保留采集时间、平台统计周期与缺失字段"},
    {"time": "10个既定时段", "owner": "小麦", "task": "提前上传并按时提交；逐条保存回执，异常结果不盲目重发"},
    {"time": "每周三 / 周六 20:00", "owner": "小麦 / 小影", "task": "B站每周2条6–10分钟专题；账号与长视频流程完成接入后启用"},
    {"time": "22:30", "owner": "老周", "task": "生成当日事实日报、未解决问题、优化验收与明日安排"},
]
UPGRADES = {
    "director": "以回执、质量验收和增长差距调整任务；未解决问题明确负责人和下一步，不能只看完成数量。",
    "researcher": "每条选题附原始出处、事件日期、争议归属和可用画面；同一产品一天最多两个不同问题，论文先看方法与局限。",
    "writer": "每条先写一句观众问题，正文给案例和边界；用实测语速修订约150秒，关注提醒不承诺不存在的下期。",
    "producer": "先画叙事分镜再选片；记录素材时间窗与使用记录，保留完整动作，关键术语出现时同步展示对应画面。",
    "reviewer": "区分脚本事实审核、素材描述对齐和成片技术检查；未进行逐帧语义验收不得写成已通过。",
    "publisher": "同一账号串行上传与发布，先核对真实回执；评论按问题回复，商业承诺和无法确定的技术问题转待处理。",
    "analyst": "用同平台同周期比较，区分新增和净增、当前粉丝和累计统计；缺失数据不填0，不将账号整体涨粉归因给单条。",
}
UPGRADES["producer2"] = "为自己负责的视频保留完整分镜、配音和素材记录；技术概念用具体动作解释，逐段检查术语与画面的对应。"
UPGRADES["producer3"] = "为自己负责的视频打磨开场、转折和结尾；热点画面标明事件出处与时间，不能靠无关镜头制造刺激。"


def initialize(db):
    plan = db.get(FactoryGrowthPlan, 1)
    if plan:
        # Freeze each platform's first real baseline. A late account connection
        # starts its own complete four-week observation period.
        baselines = dict(plan.baselines)
        for platform, entry in baselines.items():
            if entry.get("followers") is not None:
                continue
            snapshot = db.query(FactoryAnalyticsSnapshot).filter_by(platform=platform, status="connected").order_by(FactoryAnalyticsSnapshot.id.desc()).first()
            followers = snapshot.data.get("metrics", {}).get("followers") if snapshot else None
            if followers is not None and datetime.utcnow() - snapshot.collected_at <= timedelta(hours=2):
                baselines[platform] = {"followers": followers, "snapshot_id": snapshot.id,
                    "observed_at": snapshot.collected_at.replace(tzinfo=timezone.utc).isoformat()}
        if baselines != plan.baselines:
            plan.baselines = baselines
            db.commit()
        return plan
    baselines = {}
    for platform in ("douyin", "xiaohongshu", "kuaishou", "bilibili"):
        snapshot = db.query(FactoryAnalyticsSnapshot).filter_by(platform=platform, status="connected").order_by(FactoryAnalyticsSnapshot.id.desc()).first()
        followers = snapshot.data.get("metrics", {}).get("followers") if snapshot else None
        baselines[platform] = {"followers": followers, "snapshot_id": snapshot.id if snapshot else None,
                               "observed_at": snapshot.collected_at.replace(tzinfo=timezone.utc).isoformat() if snapshot else None}
    plan = FactoryGrowthPlan(id=1, baselines=baselines, policy={"weeks": 4, "weekly_min": .05, "weekly_stretch": .10,
        "shorts_per_day": 10, "short_seconds": 150, "bilibili": {"weekly_count": 2, "seconds_range": [360, 600],
        "weekdays": [2, 5], "publish_time": "20:00", "status": "awaiting_login_and_long_video_integration"}})
    db.add(plan); db.commit()
    return plan


def checkpoints(followers):
    if followers is None or followers <= 0:
        return []
    # These are projected paths, not measured weekly outcomes. Low-count
    # accounts may have no integer result inside the requested percentage band.
    low = high = int(followers)
    result = []
    for week in range(1, 5):
        low, high = math.ceil(low * 1.05), math.floor(high * 1.10)
        result.append({"week": week, "min_followers": low, "stretch_followers": high if high >= low else None})
    return result


def weekly_results(db, platform, baseline, now=None):
    now = now or datetime.utcnow()
    if baseline.get("followers") is None or not baseline.get("observed_at"):
        return []
    start = datetime.fromisoformat(baseline["observed_at"]).replace(tzinfo=None)
    points = [{"followers": baseline["followers"], "snapshot_id": baseline.get("snapshot_id"), "observed_at": baseline["observed_at"]}]
    results = []
    for week in range(1, 5):
        end = start + timedelta(weeks=week)
        point = None
        if now >= end:
            snapshots = db.query(FactoryAnalyticsSnapshot).filter(
                FactoryAnalyticsSnapshot.platform == platform, FactoryAnalyticsSnapshot.status == "connected",
                FactoryAnalyticsSnapshot.collected_at >= end - timedelta(minutes=45),
                FactoryAnalyticsSnapshot.collected_at <= end).order_by(FactoryAnalyticsSnapshot.collected_at.desc()).all()
            row = next((s for s in snapshots if s.data.get("metrics", {}).get("followers") is not None), None)
            if row:
                point = {"followers": row.data["metrics"]["followers"], "snapshot_id": row.id,
                         "observed_at": row.collected_at.replace(tzinfo=timezone.utc).isoformat()}
        prior = points[-1]
        growth = (point["followers"] / prior["followers"] - 1) * 100 if point and prior and prior["followers"] > 0 else None
        status = "not_due" if now < end else "missing_data" if growth is None else "above_stretch" if growth > 10 + 1e-8 else "met" if growth >= 5 - 1e-8 else "below_target"
        results.append({"week": week, "due_at": end.replace(tzinfo=timezone.utc).isoformat(), "status": status,
                        "start": prior, "end": point, "net_followers": point["followers"] - prior["followers"] if prior and point else None,
                        "growth_percent": round(growth, 2) if growth is not None else None})
        points.append(point)
    return results


def sync_work_orders(db, state, analytics, now):
    """Persist assignments and verify observable deliverables on every patrol."""
    plan = initialize(db)
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    runs = db.query(FactoryRun).filter(FactoryRun.created_at >= start).all()
    items = db.query(FactoryItem).filter(FactoryItem.created_at >= start).all()
    target = state["summary"]["target"] or plan.policy["shorts_per_day"]
    current_run_ids = {r.id for r in runs if r.settings_snapshot.get("target_duration_seconds") == 150}
    approved = [i for i in items if i.run_id in current_run_ids and i.review.get("approved") is True and i.content]
    ready = [i for i in items if i.status == "completed" and 135 <= float(i.media.get("duration", 0)) <= 165]
    connected = sum(p["status"] == "connected" for p in analytics["platforms"])
    specs = [
        ("researcher", "05:00", "选题与素材清单", "至少10份有正文与出处的资料，形成不少于今日目标的选题", min(sum(bool(s.get("url") and s.get("text")) for r in runs for s in r.sources), sum(len(r.plan.get("candidates", [])) for r in runs)), max(10, target)),
        ("writer", "05:30", "完成约150秒口播脚本", "每条脚本独立审核通过，开头承诺与自然关注提醒进入正文", len(approved), target),
        ("reviewer", "05:50", "核验事实、分镜与成片技术结果", "脚本审核与素材描述审核通过，成片技术检查有记录；逐帧语义抽检另列待验收", sum(i.review.get("approved") is True and i.content.get("editorial_review", {}).get("approved") is True and bool(i.media.get("quality")) for i in ready), target),
        ("publisher", "22:15", "逐时段发布并核对三平台回执", "今日各时段三平台均取得真实提交回执；公开审核另行核对", state["summary"]["fully_submitted_slots"], target),
        ("analyst", "22:25", "补齐四平台经营数据", "4个平台有2小时内官方数据，缺失指标和统计周期明确标记", connected, 4),
        ("director", "22:30", "解决阻塞并提交当日管理日报", "问题明确责任人，日报包含实际结果、未完成项与明日安排", 0, 1),
    ]
    for index, owner in enumerate(EDITOR_IDS):
        assigned = [i for i in items if i.run_id in current_run_ids and editor_id(i) == owner]
        required = len(assigned) if current_run_ids else target // 3 + int(index < target % 3)
        specs.insert(2 + index, (owner, "05:45", "独立制作分配的约150秒视频", "本人的成片实际135–165秒，保留脚本、配音、素材来源、分镜与审核记录",
                               sum(i in ready for i in assigned), required))
    for owner, clock, title, acceptance, count, required in specs:
        key = f"daily:{local.date()}:{owner}"
        order = db.get(FactoryWorkOrder, key)
        if not order:
            hour, minute = map(int, clock.split(":"))
            due = local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
            order = FactoryWorkOrder(key=key, kind="daily", owner=owner, title=title, acceptance=acceptance, due_at=due)
            db.add(order)
        from models.factory import FactoryDailyReport
        if owner == "director":
            count = int(db.get(FactoryDailyReport, str(local.date())) is not None)
        order.title, order.acceptance = title, acceptance
        order.status = "completed" if required and count >= required else "in_progress" if count else "pending"
        order.evidence = {"done": count, "required": required, "checked_at": now.isoformat()}
    # Incidents retain their own acceptance and disposition, independently of
    # daily output totals. A completed incident is not a completed video.
    for alert in state.get("alerts", []):
        key = alert.get("key")
        if not key or db.get(FactoryWorkOrder, key):
            continue
        db.add(FactoryWorkOrder(key=key, kind="issue", owner=alert.get("owner_id", "director"),
            title=alert["message"][:190], acceptance=alert["next_action"],
            due_at=now.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=2),
            status="blocked", evidence={"blocker": alert["message"], "item_id": alert.get("item_id"),
                                       "checked_at": now.isoformat()}))
    for order in db.query(FactoryWorkOrder).filter(FactoryWorkOrder.kind == "issue", FactoryWorkOrder.status != "completed"):
        identifier = order.key.rsplit(":", 1)[-1]
        resolved = False
        if order.key.startswith("issue:production:"):
            item = db.get(FactoryItem, identifier)
            resolved = bool(item and item.status == "completed" and 135 <= float(item.media.get("duration", 0)) <= 165)
        elif order.key.startswith("issue:publication:"):
            job = db.get(FactoryPublication, identifier)
            resolved = bool(job and job.status == "submitted")
        if resolved:
            order.status = "completed"
            order.evidence = {**order.evidence, "blocker": None,
                "resolution": "已取得约150秒成片" if order.key.startswith("issue:production:") else "已取得官方提交回执，公开状态仍以审核为准",
                "checked_at": now.isoformat()}
    # Separate long-form work orders cannot enter the short-video release queue.
    beginning = plan.started_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    for offset in range(28):
        day = beginning + timedelta(days=offset)
        if day.weekday() not in plan.policy["bilibili"]["weekdays"]:
            continue
        due = day.replace(hour=20, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        key = f"bilibili:{day.date()}"
        if db.get(FactoryWorkOrder, key):
            continue
        db.add(FactoryWorkOrder(key=key, kind="bilibili", owner="publisher", title="B站6–10分钟专题",
            acceptance="小探资料包→小文专题脚本→小影章节制作→小审事实与成片验收→小麦发布→小数48小时复盘；实际360–600秒及官方提交回执",
            due_at=due, status="blocked", evidence={"blocker": "长视频制作与B站投稿流程尚待联调", "seconds_range": [360, 600], "platform": "bilibili"}))
    db.commit()


def work_orders(db, now=None):
    now = now or datetime.utcnow()
    rows = db.query(FactoryWorkOrder).filter(FactoryWorkOrder.due_at >= now - timedelta(days=1)).order_by(FactoryWorkOrder.due_at).limit(40).all()
    return [{"key": r.key, "kind": r.kind, "owner": r.owner, "title": r.title, "acceptance": r.acceptance,
             "due_at": r.due_at.replace(tzinfo=timezone.utc).isoformat(), "status": r.status,
             "overdue": r.status != "completed" and now > r.due_at, "evidence": r.evidence} for r in rows]


def _score(name, weight, value, basis, denominator=None):
    return {"name": name, "weight": weight, "earned": round(weight * max(0, min(1, value)), 1) if value is not None else None,
            "basis": basis, "sample_count": denominator}


def scorecards(db, analytics):
    cutoff = datetime.utcnow() - timedelta(days=7)
    items = db.query(FactoryItem).filter(FactoryItem.created_at >= cutoff).all()
    written = [i for i in items if i.content and i.review.get("score") is not None]
    complete = [i for i in items if i.status == "completed"]
    jobs = db.query(FactoryPublication).filter(FactoryPublication.created_at >= cutoff, FactoryPublication.action == "publish").all()
    runs = {r.id: r for r in db.query(FactoryRun).filter(FactoryRun.created_at >= cutoff)}
    sources = {s["url"]: s for r in runs.values() for s in r.sources if s.get("url")}
    following = [i for i in items if i.content and runs.get(i.run_id) and runs[i.run_id].settings_snapshot.get("engagement_required")]
    follow_pass = 0
    for item in following:
        try:
            validate_follow_prompt(item.content); follow_pass += 1
        except (ValueError, TypeError, AttributeError):
            pass
    timed = [i for i in complete if runs.get(i.run_id) and runs[i.run_id].settings_snapshot.get("target_duration_seconds")]
    connected = [p for p in analytics["platforms"] if p["status"] == "connected"]
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    pending = lambda name, weight: _score(name, weight, None, "缺少可核验数据，暂不评分")
    patrol_value = None
    try:
        heartbeat = json.loads((Path(__file__).resolve().parents[2] / ".runtime/operations/heartbeat.json").read_text())
        observed = datetime.fromisoformat(heartbeat["last_patrol_at"])
        patrol_value = float((datetime.now(timezone.utc) - observed).total_seconds() <= 90)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data = {
        "director": [_score("巡检与任务安排", 20, patrol_value, "核对管理服务90秒内的巡检心跳"),
                     pending("问题按期闭环", 30), pending("四平台周增长", 30), pending("日报准时交付", 20)],
        "researcher": [_score("来源可追溯", 30, ratio(sum(bool(s.get("fetched_at") and s.get("text")) for s in sources.values()), len(sources)), "来源地址、采集时间与实际正文齐全", len(sources)),
                       pending("热点相关性与原创角度", 25), pending("事实抽检", 30), pending("新增可用素材", 15)],
        "writer": [_score("独立脚本审核", 35, ratio(sum(float(i.review["score"]) for i in written), 100 * len(written)), "小审模型评分，不等于观众满意度", len(written)),
                   _score("开场与关注提醒合同", 20, ratio(follow_pass, len(following)), "程序检查原样进入完整口播", len(following)), pending("观众留存与关注", 45)],
        "producer": [_score("实际时长验收", 25, ratio(sum(abs(float(i.media.get("duration", 0)) - runs[i.run_id].settings_snapshot["target_duration_seconds"]) <= 15 for i in timed), len(timed)), "按成片时长核验，不按脚本宣称", len(timed)),
                     _score("素材描述对齐审核", 25, ratio(sum(i.content.get("editorial_review", {}).get("approved") is True for i in complete), len(complete)), "只代表口播与已核对素材描述的审核", len(complete)),
                     pending("成片逐帧语义与听感抽检", 30), pending("平台观看体验", 20)],
        "reviewer": [pending("事实抽检准确率", 40), pending("漏检与误拒复核", 30), pending("返工后质量改善", 30)],
        "publisher": [_score("提交回执取得率", 40, ratio(sum(j.status == "submitted" for j in jobs), len(jobs)), "仅统计真实提交任务，已提交仍不等于公开审核通过", len(jobs)),
                      pending("按时发布", 25), pending("评论响应质量", 20), pending("问题闭环", 15)],
        "analyst": [_score("平台数据覆盖", 40, len(connected) / 4, "四个平台中实际取得当前数据的平台数", 4),
                    _score("统计周期完整", 20, ratio(sum(bool(p.get("period", {}).get("start") and p.get("period", {}).get("end")) for p in connected), len(connected)), "核对每个平台周期", len(connected)),
                    pending("归因与实验结论质量", 25), pending("日报数据校验", 15)],
    }
    for owner in EDITOR_IDS:
        own_complete = [i for i in complete if editor_id(i) == owner]
        own_timed = [i for i in timed if editor_id(i) == owner]
        data[owner] = [_score("实际时长验收", 25, ratio(sum(abs(float(i.media.get("duration", 0)) - runs[i.run_id].settings_snapshot["target_duration_seconds"]) <= 15 for i in own_timed), len(own_timed)), "仅核验本人完成作品的实际时长", len(own_timed)),
                       _score("素材描述对齐审核", 25, ratio(sum(i.content.get("editorial_review", {}).get("approved") is True for i in own_complete), len(own_complete)), "仅核验本人的分镜审核结果，不等于成片逐帧验收", len(own_complete)),
                       pending("成片逐帧语义与听感抽检", 30), pending("平台观看体验", 20)]
    return [{"agent_id": agent, "checks": checks, "earned": round(sum(c["earned"] or 0 for c in checks), 1),
             "measured_weight": sum(c["weight"] for c in checks if c["earned"] is not None),
             "total_score": round(sum(c["earned"] for c in checks), 1) if all(c["earned"] is not None for c in checks) else None,
             "improvement": UPGRADES[agent]} for agent, checks in data.items()]


def overview(db, analytics):
    plan = db.get(FactoryGrowthPlan, 1)
    if not plan:
        return None
    targets = []
    for entry in analytics["platforms"]:
        baseline_entry = plan.baselines.get(entry["platform"], {})
        baseline = baseline_entry.get("followers")
        current = entry.get("metrics", {}).get("followers")
        targets.append({"platform": entry["platform"], "baseline": baseline, "current": current,
                        "checkpoints": checkpoints(baseline),
                        "baseline_observed_at": baseline_entry.get("observed_at"),
                        "bootstrap_note": "零粉丝无法计算百分比，先以获得首批10位真实关注为冷启动目标；后续完整周按实际周初粉丝计算5%–10%。" if baseline == 0 else None,
                        "weekly_results": weekly_results(db, entry["platform"], baseline_entry),
                        "cumulative_growth_percent": round((current / baseline - 1) * 100, 2) if baseline and current is not None else None,
                        "state": "waiting_baseline" if baseline is None else "in_progress"})
    return {"start_at": plan.started_at.replace(tzinfo=timezone.utc).isoformat(),
            "end_at": (plan.started_at + timedelta(weeks=4)).replace(tzinfo=timezone.utc).isoformat(),
            "objective": "四个平台每周净增粉丝5%–10%，连续4周；作为经营目标追踪，不保证结果",
            "formula": "周净增粉丝率 =（周末粉丝 − 周初粉丝）÷ 周初粉丝；缺少完整周数据时不判定达标",
            "policy": plan.policy, "targets": targets, "weeks": WEEKS, "rhythm": RHYTHM,
            "scorecards": scorecards(db, analytics), "work_orders": work_orders(db),
            "measurement_note": "周末使用截止时点前45分钟内的粉丝快照，保留起止采集时间；缺一端不判达标。晚接入平台从首次真实基线起计算四周。目标表是预测路径，每周考核按实际周初粉丝计算。",
            "score_note": "评分满分100。只列已核验分项，缺少数据的分项保留待评，不补0、不据此生成虚假总分；模型评分与平台实测分开。"}
