"""Evidence-bound handoff from 小探3 to 小影3, before narration is written."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


def collect_newsroom_sources():
    """Open real event articles, never promote a press index into event evidence."""
    from services.factory_research import _fetch_url, _load_source, _load_feed, _utcnow

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.urls = []

        def handle_starttag(self, tag, attrs):
            if tag != "a":
                return
            href = dict(attrs).get("href", "")
            url = urljoin("https://www.ces.tech/press-releases/", href)
            address = urlsplit(url)
            if address.hostname == "www.ces.tech" and address.path.startswith("/press-releases/") and address.path.strip("/").count("/") == 1 and url not in self.urls:
                self.urls.append(url)

    sources, warnings = [], []
    try:
        document, _ = _fetch_url("https://www.ces.tech/press-releases/")
        parser = Links(); parser.feed(document)
        for url in parser.urls[:2]:
            source, warning = _load_source({"title": "", "url": url, "kind": "official_reference", "published_at": None})
            if source:
                source["evidence_scope"] = "活动原始公告；日期须按正文核对，未确认前不称今天现场"
                sources.append(source)
            if warning:
                warnings.append(warning)
    except Exception as exc:
        warnings.append(f"CES公告采集未完成（{type(exc).__name__}）")
    records, warning = _load_feed("https://nvidianews.nvidia.com/rss.xml", _utcnow())
    if warning:
        warnings.append(warning)
    for record in records[:2]:
        source, warning = _load_source(record)
        if source:
            sources.append(source)
        if warning:
            warnings.append(warning)
    return {"sources": sources, "warnings": warnings, "downloaded_video_count": 0}


def is_newsroom_story(brief):
    return any(word in str(brief.get("category", "")) for word in ("热点", "争议", "展会", "硬件", "峰会", "贸易"))


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
