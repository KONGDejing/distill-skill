"""Bounded first-party news discovery across the owner's overseas company pool."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html.parser import HTMLParser
import hashlib
import re
from urllib.parse import urljoin, urlsplit

# Feed/index failures are reported per company, never replaced by invented news.
COMPANY_SOURCES = (
    ('OpenAI', 'feed', 'https://openai.com/news/rss.xml'),
    ('Anthropic', 'index', 'https://www.anthropic.com/news'),
    ('Alphabet / Google', 'feed', 'https://blog.google/technology/ai/rss/'),
    ('Meta', 'feed', 'https://about.fb.com/news/feed/'),
    ('Microsoft', 'feed', 'https://blogs.microsoft.com/feed/'),
    ('Apple', 'feed', 'https://www.apple.com/newsroom/rss-feed.rss'),
    ('Amazon', 'feed', 'https://ir.aboutamazon.com/rss/pressrelease.aspx?language=en-US'),
    ('NVIDIA', 'feed', 'https://nvidianews.nvidia.com/rss.xml'),
    ('AMD', 'index', 'https://www.amd.com/en/newsroom/press-releases.html'),
    ('SpaceX', 'index', 'https://ir.spacex.com/news-events/news-releases/default.aspx'),
    ('SanDisk', 'feed', 'https://investor.sandisk.com/rss/news-releases.xml'),
    ('Micron', 'feed', 'https://investors.micron.com/rss/news-releases.xml'),
    ('Tesla', 'index', 'https://ir.tesla.com/press'),
    ('xAI', 'index', 'https://x.ai/news'),
)


class ArticleLinks(HTMLParser):
    def __init__(self, base):
        super().__init__(); self.base = base; self.href = None; self.parts = []; self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href'); self.parts = []

    def handle_data(self, data):
        if self.href is not None: self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            url = urljoin(self.base, self.href).split('#')[0]
            title = re.sub(r'\s+', ' ', ' '.join(self.parts)).strip()
            address = urlsplit(url)
            if (address.hostname == urlsplit(self.base).hostname and url.rstrip('/') != self.base.rstrip('/')
                    and any(term in address.path for term in ('news/', 'newsroom/', 'press-release', '/press/', '/article/'))
                    and len(title) >= 10 and url not in {r['url'] for r in self.rows}):
                self.rows.append({'url': url, 'title': title[:240], 'kind': 'news'})
            self.href = None


def _candidates(entry, now):
    from services.factory_research import _load_feed, _fetch_url
    company, kind, url = entry
    if kind == 'feed':
        rows, warning = _load_feed(url, now)
    else:
        try:
            html, final = _fetch_url(url); parser = ArticleLinks(final); parser.feed(html)
            rows, warning = parser.rows[:2], None
        except Exception as exc:
            rows, warning = [], f'{company}索引采集失败（{type(exc).__name__}）'
    return company, rows[:2], warning


def _article(record, now):
    from services.factory_research import (_fetch_url, _extract_text, _parse_date,
                                           _iso, _load_source, NEWS_MAX_AGE_DAYS, MAX_TEXT_CHARS)
    if record.get('published_at'):
        return _load_source(record)
    try:
        document, url = _fetch_url(record['url']); text, title = _extract_text(document)
        dates = re.findall(r'"datePublished"\s*:\s*"([^"\n]+)"', document, re.I)
        dates += re.findall(r'<time\b[^>]*datetime=["\']([^"\']+)', document, re.I)
        published = next((d for value in dates if (d := _parse_date(value))), None)
        if not published or not now - timedelta(days=NEWS_MAX_AGE_DAYS) <= published <= now or len(text) < 200:
            return None, f'{record["url"]}：未取得近期明确日期及正文，未当作新事件'
        return {'id': hashlib.sha256(url.encode()).hexdigest()[:20], 'url': url, 'title': title or record['title'],
                'text': text[:MAX_TEXT_CHARS], 'kind': 'news', 'published_at': _iso(published), 'fetched_at': _iso(now)}, None
    except Exception as exc:
        return None, f'{record["url"]}：新闻原文采集失败（{type(exc).__name__}）'


def collect_company_news(limit=12):
    from services.factory_research import _utcnow, is_major_event
    from services.factory_editorial_policy import matching_people
    now = _utcnow(); warnings = []; providers = []; candidates = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for company, records, warning in pool.map(lambda entry: _candidates(entry, now), COMPANY_SOURCES):
            providers.append({'company': company, 'candidate_count': len(records), 'status': 'candidates_found' if records else 'no_verified_lead', 'warning': warning})
            if warning: warnings.append(warning)
            candidates.extend({**record, 'watch_company': company} for record in records)
    # At most one first choice per company before second choices. Major events
    # remain high priority without allowing one company to occupy the whole pool.
    candidates.sort(key=lambda row: (is_major_event(row), row.get('published_at') or ''), reverse=True)
    seen = set(); first = []; rest = []
    for row in candidates:
        (rest if row['watch_company'] in seen else first).append(row); seen.add(row['watch_company'])
    selected = (first + rest)[:limit]; sources = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for record, (source, warning) in zip(selected, pool.map(lambda row: _article(row, now), selected)):
            if source:
                source['watch_company'] = record['watch_company'];source['watch_people'] = matching_people(source)
                sources.append(source)
            if warning: warnings.append(warning)
    return {'sources': sources, 'warnings': warnings, 'company_checks': providers,
            'checked_at': now.isoformat(), 'scope': '来源发现与事实线索；不代表热度、已看片或已完成视频'}
