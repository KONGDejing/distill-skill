"""Bounded, first-party source collection for the AI content factory.

This module retrieves source material; it never creates or embellishes facts.
News must have a recent, valid publication date. Documentation is explicitly
marked ``evergreen`` and has no inferred publication date. No credentials or
external search account are needed.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx


FEED_URLS = (
    "https://openai.com/news/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://blog.google/technology/ai/rss/",
    "https://nvidianews.nvidia.com/rss.xml",
    "https://about.fb.com/news/feed/",
    "https://rss.arxiv.org/rss/cs.RO",
    "https://rss.arxiv.org/rss/cs.AI",
)
WATCH_PAGES = (
    ("科技贸易：商务部政策解读与公告索引（须打开原文核对日期和适用范围）", "https://www.mofcom.gov.cn/zcjd/"),
    ("科技展会：CES官方新闻与活动索引（须区分往届和未来活动）", "https://www.ces.tech/press-releases/"),
)
VISUAL_SOURCE_URLS = (
    ("Google DeepMind Veo：视频生成与创作控制", "https://deepmind.google/models/veo/"),
    ("Google DeepMind Genie：可交互世界模型", "https://deepmind.google/models/genie/"),
    ("Google DeepMind Gemini Robotics：机器人理解与动作", "https://deepmind.google/models/gemini-robotics/"),
    ("Ai2 ToyBlocks：机器人仿真与真实积木操作", "https://allenai.github.io/ToyBlocks/"),
    ("Gradium Phonon：端侧语音合成演示", "https://gradium.ai/blog/release-phonon-open-weight/"),
)
EVERGREEN_URLS = (
    ("Hugging Face Transformers：Pipeline 教程", "https://huggingface.co/docs/transformers/en/pipeline_tutorial"),
    ("Google AI：提示词设计指南", "https://aistudio.google.com/docs/prompting-strategies.md"),
    ("Anthropic：提示词工程概览", "https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview"),
    ("Hugging Face：聊天模板指南", "https://huggingface.co/docs/transformers/en/chat_templating"),
)
ALLOWED_HOSTS = frozenset({
    "openai.com", "www.openai.com", "developers.openai.com", "platform.openai.com",
    "huggingface.co", "blog.google", "www.blog.google", "ai.google.dev",
    "aistudio.google.com", "docs.anthropic.com", "platform.claude.com",
    "www.anthropic.com", "anthropic.com",
    "nvidianews.nvidia.com", "about.fb.com", "deepmind.google", "allenai.github.io", "gradium.ai",
    "arxiv.org", "rss.arxiv.org", "export.arxiv.org", "www.ces.tech", "ces.tech", "www.mofcom.gov.cn",
})
MAX_BYTES = 2_000_000
MAX_TEXT_CHARS = 6000
NEWS_MAX_AGE_DAYS = 21
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
USER_AGENT = "ShortVideoAIContentFactory/0.2 (official-source-research)"
HF_OFFICIAL_ORGS = frozenset({"allenai", "nvidia", "google", "microsoft", "ibm",
                             "qwen", "meta-llama", "huggingface", "deepseek-ai", "zai-org"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_url(url: str) -> str:
    """Only explicitly maintained HTTPS hosts are fetched, including redirects."""
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError("source URL is outside the official-source allowlist")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _fetch_url(url: str) -> tuple[str, str]:
    current = _validated_url(url)
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False,
                      headers={"User-Agent": USER_AGENT,
                               "Accept": "text/markdown, text/html;q=0.9, */*;q=0.8"}) as client:
        for _ in range(5):
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect has no destination")
                    current = _validated_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError("source document exceeds the size limit")
                return content.decode("utf-8", errors="replace"), current
    raise ValueError("too many source redirects")


class _TextExtractor(HTMLParser):
    SKIP = frozenset({"script", "style", "nav", "footer", "header", "svg",
                      "form", "button", "noscript", "template"})
    BLOCK = frozenset({"p", "div", "section", "article", "main", "li", "br",
                       "h1", "h2", "h3", "h4", "pre", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skipped = 0
        self.main_depth = 0
        self.in_title = False
        self.all_text: list[str] = []
        self.main_text: list[str] = []
        self.title_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP:
            self.skipped += 1
        if tag in {"main", "article"}:
            self.main_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in self.BLOCK:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.skipped = max(0, self.skipped - 1)
        if tag in {"main", "article"}:
            self.main_depth = max(0, self.main_depth - 1)
        if tag == "title":
            self.in_title = False
        if tag in self.BLOCK:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_text.append(data)
        self._append(data)

    def _append(self, text: str) -> None:
        if not self.skipped:
            self.all_text.append(text)
            if self.main_depth:
                self.main_text.append(text)


def _clean_text(text: str) -> str:
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    result: list[str] = []
    for line in lines:
        if line and (not result or result[-1] != line):
            result.append(line)
    return "\n".join(result).strip()


def _extract_text(document: str) -> tuple[str, str]:
    if not re.match(r"\s*<", document):
        # Some documentation endpoints return Markdown to HTTP clients.
        return _clean_text(document), ""
    parser = _TextExtractor()
    parser.feed(document)
    main = _clean_text("".join(parser.main_text))
    text = main if len(main) >= 200 else _clean_text("".join(parser.all_text))
    return text, _clean_text("".join(parser.title_text))


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_feed(document: str, base_url: str, now: datetime) -> list[dict]:
    if "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
        raise ValueError("DTD and entity declarations are not accepted")
    root = ElementTree.fromstring(document)
    records: list[dict] = []
    for node in root.iter():
        if _localname(node.tag) not in {"item", "entry"}:
            continue
        fields: dict[str, str] = {}
        link = ""
        for child in node:
            key = _localname(child.tag)
            fields.setdefault(key, "".join(child.itertext()).strip())
            if key == "link" and child.get("rel", "alternate") == "alternate":
                link = child.get("href") or fields[key]
        published = _parse_date(fields.get("pubdate") or fields.get("published")
                                or fields.get("date") or fields.get("updated"))
        if not published or published < now - timedelta(days=NEWS_MAX_AGE_DAYS):
            continue
        if published > now + timedelta(hours=2):
            continue
        try:
            url = _validated_url(urljoin(base_url, link))
        except (ValueError, TypeError):
            continue
        if urlsplit(url).hostname == "huggingface.co":
            path = urlsplit(url).path.strip("/").split("/")
            # The public feed can also contain community posts. Retain the
            # editorial blog and explicitly maintained official organizations.
            if len(path) > 2 and path[1].lower() not in HF_OFFICIAL_ORGS:
                continue
        title = _clean_text(fields.get("title", ""))
        if not title or not link:
            continue
        excerpt, _ = _extract_text(fields.get("encoded") or fields.get("content")
                                   or fields.get("description") or fields.get("summary") or "")
        records.append({"title": title, "url": url, "published_at": _iso(published),
                        "excerpt": excerpt, "kind": "paper" if urlsplit(url).hostname == "arxiv.org" else "news"})
    return records


def _load_feed(url: str, now: datetime) -> tuple[list[dict], str | None]:
    try:
        document, final_url = _fetch_url(url)
        records = _parse_feed(document, final_url, now)
        return records, None if records else f"{url}：最近 {NEWS_MAX_AGE_DAYS} 天没有可用的带日期条目"
    except Exception as exc:
        return [], f"{url}：采集失败（{type(exc).__name__}）"


def _load_source(record: dict) -> tuple[dict | None, str | None]:
    warning = None
    try:
        document, url = _fetch_url(record["url"])
        text, page_title = _extract_text(document)
        title = record["title"] or page_title
        if len(text) < 200:
            raise ValueError("source page does not contain enough readable text")
    except Exception as exc:
        text = record.get("excerpt", "")
        if len(text) < 200:
            return None, f"{record['url']}：正文不可用（{type(exc).__name__}）"
        url, title = record["url"], record["title"]
        warning = f"{url}：正文不可用，本次仅使用官方 RSS 摘录"
    return {
        "id": hashlib.sha256(url.encode("utf-8")).hexdigest()[:20],
        "title": title,
        "url": url,
        "published_at": record.get("published_at"),
        "fetched_at": _iso(_utcnow()),
        "text": text[:MAX_TEXT_CHARS],
        "kind": record["kind"],
        **{key: record[key] for key in ("popularity", "evidence_scope") if key in record},
    }, warning


def _load_daily_papers(now):
    """HF is a discovery/popularity source; the original abstract is evidence."""
    try:
        document, _ = _fetch_url("https://huggingface.co/api/daily_papers")
        entries = json.loads(document)
        if not isinstance(entries, list):
            raise ValueError("invalid paper feed")
        records = []
        for entry in entries[:80]:
            paper = entry.get("paper", {})
            identifier = paper.get("id", "")
            published = _parse_date(paper.get("publishedAt"))
            if not re.fullmatch(r"\d{4}\.\d{4,5}", identifier) or not published or not now - timedelta(days=21) <= published <= now:
                continue
            votes = paper.get("upvotes")
            records.append({"title": paper.get("title", ""), "url": "https://arxiv.org/abs/" + identifier,
                "published_at": _iso(published), "kind": "paper", "excerpt": paper.get("summary", ""),
                "evidence_scope": "论文原始摘要或所取得原文；预印本不等于同行评审，不从AI生成摘要补造实验细节",
                "popularity": {"source": "Hugging Face Daily Papers", "url": "https://huggingface.co/papers/" + identifier,
                               "upvotes": votes if type(votes) is int and votes >= 0 else None, "observed_at": _iso(now)}})
        return sorted(records, key=lambda r: r["popularity"]["upvotes"] or 0, reverse=True)[:6], None
    except Exception as exc:
        return [], f"AI论文发现源暂不可用（{type(exc).__name__}）；不编造热门论文"


def collect_sources(limit: int = 12) -> dict:
    """Return official source excerpts and transparent acquisition warnings.

    Output is ``{"sources": [...], "warnings": [...]}``. Source fields are
    ``id/title/url/published_at/fetched_at/text/kind``. Limit is capped at 24.
    Recent news and evergreen documents are fetched with four bounded workers.
    An empty result is a real failure and is never replaced by invented news.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 24:
        raise ValueError("limit must be an integer between 1 and 24")
    now = _utcnow()
    warnings: list[str] = []
    candidates: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for records, warning in pool.map(lambda url: _load_feed(url, now), FEED_URLS):
            candidates.extend(records)
            if warning:
                warnings.append(warning)
    papers, paper_warning = _load_daily_papers(now)
    if paper_warning:
        warnings.append(paper_warning)

    seen: set[str] = set()
    recent: list[dict] = []
    for record in sorted(candidates, key=lambda row: row["published_at"], reverse=True):
        if record["url"] not in seen:
            seen.add(record["url"])
            recent.append(record)

    # Reserve a small documentation pool for tutorials even on busy news days.
    evergreen_slots = min(len(EVERGREEN_URLS), max(1, limit // 3))
    news_slots = max(0, limit - evergreen_slots)
    visual_count = min(len(VISUAL_SOURCE_URLS), max(1, limit // 3))
    watch_count = min(len(WATCH_PAGES), max(0, limit // 8))
    paper_count = min(len(papers), max(1, limit // 5), max(0, news_slots - visual_count - watch_count))
    selected = papers[:paper_count]
    selected.extend(r for r in recent if r["url"] not in {p["url"] for p in selected})
    selected = selected[:max(paper_count, news_slots - visual_count - watch_count)]
    selected.extend({"title": title, "url": url, "kind": "reference_index", "published_at": None,
                     "evidence_scope": "监测索引，仅用作线索；缺少原始公告或活动原文时不得据此写具体新规或当天现场"}
                    for title, url in WATCH_PAGES[:watch_count])
    selected.extend({"title": title, "url": url, "kind": "evergreen", "published_at": None}
                    for title, url in VISUAL_SOURCE_URLS[:visual_count])
    docs_count = min(len(EVERGREEN_URLS), limit - len(selected))
    selected.extend({"title": title, "url": url, "kind": "evergreen", "published_at": None}
                    for title, url in EVERGREEN_URLS[:docs_count])
    sources: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for source, warning in pool.map(_load_source, selected):
            if source:
                sources.append(source)
            if warning:
                warnings.append(warning)

    # If a news page fails, try remaining docs; never repeat an attempted URL.
    attempted = {record["url"] for record in selected}
    fallback = [{"title": title, "url": url, "kind": "evergreen", "published_at": None}
                for title, url in EVERGREEN_URLS if url not in attempted]
    fallback.extend(record for record in recent if record["url"] not in attempted)
    if fallback and len(sources) < limit:
        with ThreadPoolExecutor(max_workers=4) as pool:
            # A bounded second batch compensates for inaccessible article pages.
            for source, warning in pool.map(_load_source, fallback[:min(limit, 8)]):
                if source:
                    sources.append(source)
                if warning:
                    warnings.append(warning)
    unique: dict[str, dict] = {source["url"]: source for source in sources}
    result = list(unique.values())[:limit]
    if len(result) < limit:
        warnings.append(f"请求 {limit} 条来源，实际获取 {len(result)} 条；不补造缺失资料")
    if result and not any(source["kind"] == "news" for source in result):
        warnings.append("本次只有常青教程资料，不能据此宣称今日新闻或新品发布")
    return {"sources": result, "warnings": warnings}
