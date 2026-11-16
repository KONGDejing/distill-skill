"""Evidence-bound handoff from 小探3 to 小影3, before narration is written."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def collect_newsroom_sources():
    """Check overseas company news; the person roster is discovery context only."""
    from services.factory_news_intake import collect_company_news
    from services.factory_editorial_policy import people_context
    result = collect_company_news()
    return {**result, 'people_watchlist': people_context(), 'downloaded_video_count': 0}


def is_newsroom_story(brief):
    track = brief.get('editor_track')
    if track in {'technology_scenes', 'paper_explainer'}:
        return False
    if track == 'people_news':
        return True
    category = str(brief.get('category', ''))
    return any(word in category for word in ('人物', '公司新闻', '热点', '争议', '言论', '人事', '贸易'))


def validate_packet(packet, sources):
    by_id = {s["id"]: s for s in sources}
    facts = packet.get("facts") if isinstance(packet, dict) else None
    if not isinstance(facts, list) or not 1 <= len(facts) <= 12:
        raise ValueError("小探3尚未交付可核验事实，不能开始热点制作")
    normalize = lambda text: re.sub(r"\s+", " ", text).strip()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("素材事实格式无效")
        source = by_id.get(fact.get("source_id"))
        quote = fact.get("evidence_quote")
        if not source or source.get("kind") == "reference_index" or not source.get("url") or not source.get("text"):
            raise ValueError("热点事实必须来自真实正文，监测索引不能当事件证据")
        if not isinstance(quote, str) or not 12 <= len(quote) <= 240 or normalize(quote) not in normalize(source["text"]):
            raise ValueError("素材证据摘录不在实际原文中")
        if not isinstance(fact.get("claim"), str) or not fact["claim"].strip():
            raise ValueError("素材交接缺少具体事实")
    for key in ("event_date", "limitations"):
        if not isinstance(packet.get(key), str) or not packet[key].strip():
            raise ValueError("素材交接必须明确事件日期和证据边界")
    requests = packet.get("visual_requests")
    if not isinstance(requests, list) or not requests or any(not isinstance(r, dict) or r.get("source_id") not in by_id or not r.get("needed_picture") for r in requests):
        raise ValueError("素材交接必须列出有来源的具体画面需求")
    return packet
