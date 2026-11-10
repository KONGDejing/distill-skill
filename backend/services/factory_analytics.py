"""Read official dashboards in separate tabs; store normalized counts, never cookies."""
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from models.factory import FactoryAnalyticsSnapshot

ROOT = Path(__file__).resolve().parents[2]
PORTALS = {
    "xiaohongshu": ("https://creator.xiaohongshu.com/new/home", ".runtime/xiaohongshu-session/DevToolsActivePort"),
    "douyin": ("https://creator.douyin.com/creator-micro/home", ".runtime/creator-connectors/douyin/profile/DevToolsActivePort"),
    "kuaishou": ("https://cp.kuaishou.com/profile", ".runtime/creator-connectors/kuaishou/profile/DevToolsActivePort"),
}
PATHS = {
    "xiaohongshu": {"/api/galaxy/v2/creator/datacenter/account/base": "overview", "/api/galaxy/creator/home/personal_info": "profile"},
    "douyin": {"/aweme/janus/creator/data/overview/all/": "overview", "/aweme/v1/creator/user/info/": "profile"},
    "kuaishou": {"/rest/cp/creator/analysis/pc/home/author/overview": "overview", "/rest/cp/creator/pc/home/userInfo": "profile"},
}
METRICS = {"followers": "当前粉丝", "new_followers": "新增关注", "lost_followers": "取消关注", "net_followers": "净增粉丝",
           "views": "播放 / 观看", "impressions": "曝光", "profile_views": "主页访问", "likes": "点赞", "comments": "评论",
           "shares": "分享", "saves": "收藏", "completion_rate": "完播率", "follow_conversion_rate": "主页关注转化率（平台口径）"}


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _day_ms(value):
    return datetime.fromtimestamp(float(value) / 1000, ZoneInfo("Asia/Shanghai")).date().isoformat()


def normalize(platform, responses, body=""):
    """Only observed account overview fields; missing values stay null."""
    raw = responses.get("overview", {})
    profile = responses.get("profile", {})
    metrics = dict.fromkeys(METRICS)
    period, daily = {}, []
    if platform == "xiaohongshu":
        if raw.get("success") is not True or not raw.get("data", {}).get("seven"):
            raise ValueError("未取得小红书官方近7日统计")
        data = raw["data"]["seven"]
        mapping = {"views": "view_count", "impressions": "impl_count", "profile_views": "home_view_count", "likes": "like_count",
                   "comments": "comment_count", "shares": "share_count", "saves": "collect_count", "new_followers": "rise_fans_count",
                   "lost_followers": "loss_fans_count", "net_followers": "net_rise_fans_count", "completion_rate": "video_full_view_rate",
                   "follow_conversion_rate": "home_conversion_rise_fans_rate"}
        metrics.update({k: number(data.get(v)) for k, v in mapping.items()})
        if profile.get("success") is True:
            metrics["followers"] = number(profile.get("data", {}).get("fans_count"))
        if data.get("begin_time") and data.get("end_time"):
            period = {"start": _day_ms(data["begin_time"]), "end": _day_ms(data["end_time"])}
        for row in data.get("view_list", []):
            if row.get("date"):
                daily.append({"date": _day_ms(row["date"]), "views": number(row.get("count"))})
    elif platform == "kuaishou":
        if type(raw.get("result")) is not int or raw["result"] != 1 or "basicData" not in raw.get("data", {}):
            raise ValueError("未取得快手官方近7日统计")
        rows = {r.get("tab"): r for r in raw["data"]["basicData"]}
        mapping = {"PLAY": "views", "LIKE": "likes", "PURE_INCREASE_FAN": "net_followers", "COMMENT": "comments", "SHARE": "shares", "COMPLETE_RATIO": "completion_rate"}
        for tab, key in mapping.items():
            metrics[key] = number(rows.get(tab, {}).get("sumCount"))
        if metrics["completion_rate"] is not None:
            metrics["completion_rate"] *= 100
        if type(profile.get("result")) is int and profile["result"] == 1:
            metrics["followers"] = number(profile.get("data", {}).get("coreUserInfo", {}).get("fansNum"))
        for row in rows.get("PLAY", {}).get("trendData", []):
            day = str(row.get("date", ""))
            if re.fullmatch(r"\d{8}", day):
                daily.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "views": number(row.get("count"))})
        if daily:
            period = {"start": min(r["date"] for r in daily), "end": max(r["date"] for r in daily)}
    elif platform == "douyin":
        if type(raw.get("status_code")) is not int or raw["status_code"] != 0 or not raw.get("data"):
            raise ValueError("未取得抖音官方近7日统计")
        data = raw["data"]
        for key, field in {"views": "play", "likes": "digg", "comments": "comment", "shares": "share", "profile_views": "profile",
                           "new_followers": "new_fans", "lost_followers": "cancel_fans"}.items():
            entry = data.get(field, {})
            if type(entry.get("status_code")) is int and entry["status_code"] == 0:
                metrics[key] = number(entry.get("current_count"))
        if all(metrics[k] is not None for k in ("new_followers", "lost_followers")):
            metrics["net_followers"] = metrics["new_followers"] - metrics["lost_followers"]
        if type(profile.get("status_code")) is int and profile["status_code"] == 0:
            metrics["followers"] = number(profile.get("user_profile", {}).get("follower_count"))
        # The overview's `fans.current_count` sums daily follower stock; it is
        # neither new followers nor the current follower count. Never use it.
        match = re.search(r"统计周期[：:]\s*(\d{4}\.\d{2}\.\d{2})\s*[-至]\s*(\d{4}\.\d{2}\.\d{2})", body)
        if match:
            period = {"start": match[1].replace(".", "-"), "end": match[2].replace(".", "-")}
    else:
        raise ValueError("平台数据尚未接入")
    return {"metrics": metrics, "period": {**period, "label": "官方后台近7日（非今日实时）"}, "daily": daily,
            "sources": [PORTALS[platform][0]], "attribution_note": "账号整体数据，包含历史作品；不能归因于今天的新视频。未取得的转化指标显示未接入，不用净增粉丝/播放量冒充关注转化率。"}


def capture(platform):
    from playwright.sync_api import sync_playwright
    from services.factory_resources import ensure_memory_available
    ensure_memory_available()
    if platform not in PORTALS:
        return {"status": "not_connected", "message": "B站数据尚未接入；短视频发行已暂停，未以0代替缺失数据。"}
    url, portfile = PORTALS[platform]
    port = (ROOT / portfile).read_text().splitlines()[0]
    if not port.isdigit():
        raise ValueError("浏览器连接不可用")
    responses = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:" + port, timeout=10000)
        page = browser.contexts[0].new_page()
        def observe(response):
            address = urlsplit(response.url)
            label = PATHS[platform].get(address.path)
            if label and address.hostname == urlsplit(url).hostname and response.status == 200:
                try:
                    value = response.json()
                    if isinstance(value, dict):
                        responses[label] = value
                except Exception:
                    pass
        try:
            page.on("response", observe)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(30):
                page.wait_for_timeout(500)
                if len(responses) == 2:
                    break
            body = page.locator("body").inner_text()
            value = normalize(platform, responses, body)
            return {"status": "connected", **value}
        finally:
            page.close()


def save_snapshot(db, platform, data):
    snapshot = FactoryAnalyticsSnapshot(platform=platform, status=data["status"], data=data)
    db.add(snapshot)
    db.commit()
    return snapshot


def overview(db):
    platforms = []
    for platform in ("douyin", "xiaohongshu", "kuaishou", "bilibili"):
        latest = db.query(FactoryAnalyticsSnapshot).filter_by(platform=platform).order_by(FactoryAnalyticsSnapshot.id.desc()).first()
        good = db.query(FactoryAnalyticsSnapshot).filter_by(platform=platform, status="connected").order_by(FactoryAnalyticsSnapshot.id.desc()).first()
        data = dict(good.data) if good else {"metrics": dict.fromkeys(METRICS), "period": {}}
        stale = bool(good and (datetime.utcnow() - good.collected_at > timedelta(hours=2) or latest.status != "connected"))
        platforms.append({**data, "platform": platform, "status": "stale" if stale else latest.status if latest else "not_connected",
                          "message": latest.data.get("message", "") if latest else "等待首次官方数据采集",
                          "collected_at": good.collected_at.replace(tzinfo=timezone.utc).isoformat() if good else None})
    return {"platforms": platforms, "metric_labels": METRICS, "refresh_minutes": 30}
