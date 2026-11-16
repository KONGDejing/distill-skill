"""Bounded first-party news discovery across the owner's overseas company pool."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html.parser import HTMLParser
import hashlib
import json
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
    ('SpaceX', 'mission_index', 'https://content.spacex.com/api/spacex-website/featured-launch-tiles'),
    ('SanDisk', 'feed', 'https://investor.sandisk.com/rss/news-releases.xml'),
    ('Micron', 'feed', 'https://investors.micron.com/rss/news-releases.xml'),
    ('Tesla', 'index', 'https://ir.tesla.com/press'),
    ('xAI', 'index', 'https://x.ai/news'),
)
MAX_LEADS_PER_COMPANY = 6
MAX_ARTICLE_CHECKS = 42


class ArticleLinks(HTMLParser):
    def __init__(self, base):
        super().__init__(); self.base = base; self.href = None; self.parts = []; self.rows = []; self.label = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            attrs = dict(attrs)
            self.href = attrs.get('href'); self.parts = []
            self.label = re.sub(r'\s*-\s*Learn more$', '', attrs.get('data-event-content-name', ''))

    def handle_data(self, data):
        if self.href is not None: self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            url = urljoin(self.base, self.href).split('#')[0]
            title = re.sub(r'\s+', ' ', ' '.join(self.parts)).strip()
            if len(title) < 10 and self.label:
                title = self.label
            address = urlsplit(url)
            if (address.hostname == urlsplit(self.base).hostname and url.rstrip('/') != self.base.rstrip('/')
                    and any(term in address.path for term in ('news/', 'newsroom/', 'press-release', '/press/', '/article/', '/blog/'))
                    and len(title) >= 10 and url not in {r['url'] for r in self.rows}):
                self.rows.append({'url': url, 'title': title[:240], 'kind': 'news'})
            self.href = None


def _candidates(entry, now):
    from services.factory_research import _load_feed, _fetch_url
    company, kind, url = entry
    if kind == 'mission_index':
        try:
            document, _ = _fetch_url(url)
            rows = [{'url': 'https://content.spacex.com/api/spacex-website/missions/' + row['link'],
                     'title': row['title'], 'kind': 'news'} for row in json.loads(document)[:10]
                    if re.fullmatch(r'[a-z0-9-]{1,100}', str(row.get('link', '')))]
            warning = None
        except Exception as exc:
            rows, warning = [], f'{company}任务索引采集失败（{type(exc).__name__}）'
    elif kind == 'feed':
        rows, warning = _load_feed(url, now)
        if company == 'Alphabet / Google':
            try:
                html, final = _fetch_url('https://deepmind.google/blog/')
                parser = ArticleLinks(final); parser.feed(html)
                # Keep research with actual demonstrations alongside product news.
                research = sorted(parser.rows, key=lambda row: not bool(re.search(r'robot|dexter|physical', row['title'], re.I)))
                # Alternate product and research leads; neither stream silently
                # consumes the other's budget before article verification.
                merged = []
                for index in range(MAX_LEADS_PER_COMPANY):
                    for stream in (rows, research):
                        if index < len(stream) and stream[index]['url'] not in {r['url'] for r in merged}:
                            merged.append(stream[index])
                rows = merged
            except Exception as exc:
                warning = warning or f'Google研究索引采集失败（{type(exc).__name__}），本轮仅保留主新闻源结果'
    else:
        try:
            html, final = _fetch_url(url); parser = ArticleLinks(final); parser.feed(html)
            rows, warning = parser.rows, None
        except Exception as exc:
            rows, warning = [], f'{company}索引采集失败（{type(exc).__name__}）'
    return company, rows[:MAX_LEADS_PER_COMPANY], warning


def _article(record, now):
    from services.factory_research import (_fetch_url, _extract_text, _parse_date,
                                           _iso, _load_source, NEWS_MAX_AGE_DAYS, MAX_TEXT_CHARS)
    if record.get('published_at'):
        return _load_source(record)
    try:
        document, url = _fetch_url(record['url'])
        if urlsplit(url).hostname == 'content.spacex.com':
            mission = json.loads(document)
            published = _parse_date(mission.get('endDate'))
            paragraphs = [row['content'] for row in mission.get('paragraphs', []) if isinstance(row.get('content'), str)]
            text = '\n'.join(paragraphs)
            if not published or not now - timedelta(days=NEWS_MAX_AGE_DAYS) <= published <= now or len(text) < 200:
                return None, 'SpaceX任务仍缺近期已发生日期和正式总结，未当作新事件'
            slug = mission.get('missionId')
            if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9-]{1,100}', slug):
                return None, 'SpaceX任务标识无效'
            page = 'https://www.spacex.com/launches/mission/?missionId=' + slug
            return {'id': hashlib.sha256(page.encode()).hexdigest()[:20], 'url': page,
                'document_url': url, 'title': mission['title'], 'text': text[:MAX_TEXT_CHARS],
                'kind': 'news', 'published_at': _iso(published), 'publication_precision': 'date',
                'fetched_at': _iso(now)}, None
        text, title = _extract_text(document)
        dates = re.findall(r'"datePublished"\s*:\s*"([^"\n]+)"', document, re.I)
        dates += re.findall(r'<time\b[^>]*datetime=["\']([^"\']+)', document, re.I)
        published = next((d for value in dates if (d := _parse_date(value))), None)
        if not published or not now - timedelta(days=NEWS_MAX_AGE_DAYS) <= published <= now or len(text) < 200:
            return None, f'{record["url"]}：未取得近期明确日期及正文，未当作新事件'
        return {'id': hashlib.sha256(url.encode()).hexdigest()[:20], 'url': url, 'title': title or record['title'],
                'text': text[:MAX_TEXT_CHARS], 'kind': 'news', 'published_at': _iso(published), 'fetched_at': _iso(now)}, None
    except Exception as exc:
        return None, f'{record["url"]}：新闻原文采集失败（{type(exc).__name__}）'


def collect_company_news(limit=18):
    from services.factory_research import _utcnow, is_major_event
    from services.factory_editorial_policy import matching_people
    if type(limit) is not int or not 1 <= limit <= MAX_ARTICLE_CHECKS:
        raise ValueError('新闻交接数量须为1–42')
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
    selected = list({row['url']: row for row in first + rest}.values())[:MAX_ARTICLE_CHECKS]
    sources = []; checked = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        # Only issue the next small batch when prior links did not yield enough
        # verified originals. Discovery breadth does not inflate LLM input.
        while checked < len(selected) and len(sources) < limit:
            batch = selected[checked:checked + min(4, limit - len(sources))]
            for record, (source, warning) in zip(batch, pool.map(lambda row: _article(row, now), batch)):
                checked += 1
                if source:
                    source['watch_company'] = record['watch_company'];source['watch_people'] = matching_people(source)
                    if source['url'] not in {s['url'] for s in sources}:
                        sources.append(source)
                if warning: warnings.append(warning)
    return {'sources': sources, 'warnings': warnings, 'company_checks': providers,
            'discovered_candidate_count': len(candidates), 'article_check_count': checked,
            'article_check_budget': MAX_ARTICLE_CHECKS,
            'checked_at': now.isoformat(), 'scope': '来源发现与事实线索；不代表热度、已看片或已完成视频'}
