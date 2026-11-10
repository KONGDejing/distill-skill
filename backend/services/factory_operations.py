"""Persistent director patrol and daily factual reports, separate from rendering."""
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from database import SessionLocal, init_db
from models.factory import FactoryAnalyticsSnapshot, FactoryDailyReport, FactoryEvent, FactoryItem, FactoryPublication, FactoryRelease, FactoryRun
from agents.registry import AGENTS
from services import factory_analytics
from services.factory_resources import memory_state

PRIVATE = Path(__file__).resolve().parents[2] / ".runtime/operations"
ASSIGNMENTS = {
    "director": "检查产量、质量、平台回执与阻塞；安排返工并在22:30汇报",
    "researcher": "收集科技热点、争议、贸易、展会、论文与具身智能的一手来源和画面",
    "writer": "围绕一个问题写约150秒讲解；兑现开场承诺，结尾自然提醒关注",
    "producer": "按口播制作分镜，整理可复用素材，检查镜头、字幕与音频对齐",
    "reviewer": "核查事实与观点边界、画面证据、完整口播和实际成片时长",
    "publisher": "按排期发布到启用平台，核验回执；跟进评论接入与异常账号",
    "analyst": "每30分钟采集可用平台真实数据，保留统计周期，汇总每日变化与缺项",
}


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def overview(db, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    today_events = db.query(FactoryEvent).filter(FactoryEvent.created_at >= start).order_by(FactoryEvent.id.desc()).limit(150).all()
    assignments = []
    for agent in AGENTS:
        last = db.query(FactoryEvent).filter(FactoryEvent.created_at >= start, FactoryEvent.agent_id == agent["id"],
            FactoryEvent.stage.notin_(["model_call", "model_retry"])).order_by(FactoryEvent.id.desc()).first()
        assignments.append({"agent_id": agent["id"], "name": agent["name"], "role": agent["role"],
                            "assignment": ASSIGNMENTS[agent["id"]], "last_progress": last.message if last else "今日尚无执行记录",
                            "last_status": last.status if last else "pending", "updated_at": stamp(last.created_at) if last else None})
    active = db.query(FactoryRun).filter(FactoryRun.active_slot.isnot(None)).first()
    alerts = []
    if active and active.status == "paused_memory":
        alerts.append({"owner": "老周", "message": "生产因内存保护暂停，保存进度等待恢复", "next_action": "检查当前内存后恢复原批次"})
    elif active and (datetime.utcnow() - active.heartbeat_at).total_seconds() > 600:
        alerts.append({"owner": "老周", "message": "生产心跳超过10分钟未更新", "next_action": "由生产调度器检查原任务并恢复，保留已有交付"})
    uncertain = db.query(FactoryPublication).filter_by(action="publish", status="failed").all()
    for job in uncertain:
        if job.result.get("submission_uncertain"):
            alerts.append({"owner": "小麦", "platform": job.platform, "item_id": job.item_id,
                           "message": job.result.get("message", "提交结果待核对"), "next_action": "核对官方作品记录，不自动重复发布"})
    items = db.query(FactoryItem).filter(FactoryItem.created_at >= start).all()
    slots = db.query(FactoryRelease).filter(FactoryRelease.due_at >= start, FactoryRelease.due_at < start + timedelta(days=1)).all()
    metrics = factory_analytics.overview(db)
    connected = [p["platform"] for p in metrics["platforms"] if p["status"] == "connected"]
    long_ready = sum(i.status == "completed" and 135 <= float(i.media.get("duration", 0)) <= 165 for i in items)
    follow_ready = sum(i.status == "completed" and bool(i.content.get("follow_prompt")) for i in items)
    improvements = [
        {"name": "自然关注提醒进入口播", "owner": "小文 / 小审", "status": "已有成片验证" if follow_ready else "待成片验证", "evidence": f"今日{follow_ready}条已完成作品含关注提醒"},
        {"name": "约2分30秒内容与实际时长", "owner": "小影 / 小审", "status": "已有成片验证" if long_ready else "待样片验收", "evidence": f"今日{long_ready}条成片达到135–165秒"},
        {"name": "四个平台运营数据", "owner": "小数", "status": "已接入" if len(connected) == 4 else "部分接入", "evidence": f"{len(connected)}/4个平台取得当前官方数据"},
        {"name": "评论读取与回复", "owner": "小麦", "status": "联调中", "evidence": "自动回复尚未通过真实回执验证，不标记为已上线"},
    ]
    completed = sum(i.status == "completed" for i in items)
    result = {"day": now.date().isoformat(), "assignments": assignments, "alerts": alerts,
              "improvements": improvements, "report_time": "22:30", "timezone": "Asia/Shanghai",
              "summary": {"completed": completed, "target": len(slots), "ready_150_seconds": long_ready,
                          "fully_submitted_slots": sum(s.status == "submitted" for s in slots),
                          "failed_items": sum(i.status == "failed" for i in items)},
              "monitoring": {"interval_seconds": 30, "last_patrol_at": None},
              "activity": [{"name": next((a["name"] for a in AGENTS if a["id"] == e.agent_id), e.agent_id),
                            "stage": e.stage, "status": e.status, "message": e.message, "at": stamp(e.created_at)}
                           for e in today_events if e.stage not in {"model_call", "model_retry"}][:16]}
    try:
        result["monitoring"].update(json.loads((PRIVATE / "heartbeat.json").read_text()))
    except (OSError, ValueError):
        pass
    reports = db.query(FactoryDailyReport).order_by(FactoryDailyReport.day.desc()).limit(7).all()
    result["reports"] = [{"day": r.day, "created_at": stamp(r.created_at), **r.data} for r in reports]
    from services.factory_management import overview as management_overview
    result["management"] = management_overview(db, metrics)
    return result


def patrol(db, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    state = overview(db, now)
    from services.factory_management import sync_work_orders
    sync_work_orders(db, state, factory_analytics.overview(db), now)
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"last_patrol_at": now.isoformat(), "open_alerts": len(state["alerts"]), "status": "running"}
    temp = PRIVATE / "heartbeat.tmp"
    temp.write_text(json.dumps(data, ensure_ascii=False)); temp.chmod(0o600)
    temp.replace(PRIVATE / "heartbeat.json")
    if now.strftime("%H:%M") >= "22:30" and not db.get(FactoryDailyReport, now.date().isoformat()):
        db.add(FactoryDailyReport(day=now.date().isoformat(), data={
            "basis": "系统按实际任务、官方回执和平台数据生成的事实日报",
            "summary": state["summary"], "assignments": state["assignments"], "alerts": state["alerts"],
            "improvements": state["improvements"], "analytics": factory_analytics.overview(db),
            "management": state.get("management"),
            "tomorrow": ["小探补充有出处的热点与可用素材，避免同一事实换标题重复生产", "小文与小影按150秒标准制作，小审核验实际成片", "小麦处理未确认回执，小数对照同周期平台数据再提出优化"]}))
        db.commit()
    return state


def serve():
    init_db()
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (PRIVATE / "patrol.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                with SessionLocal() as db:
                    patrol(db)
                    for platform in ("douyin", "xiaohongshu", "kuaishou", "bilibili"):
                        last = db.query(FactoryAnalyticsSnapshot).filter_by(platform=platform).order_by(FactoryAnalyticsSnapshot.id.desc()).first()
                        if last and datetime.utcnow() - last.collected_at < timedelta(minutes=30):
                            continue
                        if memory_state()["blocked"]:
                            break
                        try:
                            data = factory_analytics.capture(platform)
                        except Exception as exc:
                            data = {"status": "unavailable", "message": f"官方数据采集未完成（{type(exc).__name__}），保留上次数据并标记过期"}
                        factory_analytics.save_snapshot(db, platform, data)
                        patrol(db)
            except Exception:
                logging.exception("Director patrol failed; retained last state")
            time.sleep(30)


if __name__ == "__main__":
    serve()
