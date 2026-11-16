from datetime import datetime, timezone
from unittest.mock import Mock

from services import factory_news_intake as intake, factory_research as research

NOW=datetime(2026,11,16,12,tzinfo=timezone.utc)


def test_article_discovery_only_keeps_real_same_host_article_links():
    parser=intake.ArticleLinks('https://www.anthropic.com/news')
    parser.feed('<a href="/news/new-model">A real dated model announcement</a>'
                '<a href="https://untrusted.example/news/fake">An unrelated external announcement</a>'
                '<a href="/company/leadership">Company leadership directory</a>')
    assert [r['url'] for r in parser.rows]==['https://www.anthropic.com/news/new-model']


def test_index_article_requires_actual_recent_publication_date_and_original_body(monkeypatch):
    article='<main><h1>Original release</h1><p>'+('Actual original report. '*30)+'</p></main>'
    record={'url':'https://www.anthropic.com/news/release','title':'Original release','kind':'news'}
    for date in [None,'2099-01-01','2020-01-01']:
        metadata='' if date is None else '<script type="application/ld+json">{"datePublished":"'+date+'"}</script>'
        monkeypatch.setattr(research,'_fetch_url',lambda url:(metadata+article,url))
        source,warning=intake._article(record,NOW)
        assert source is None and warning
    monkeypatch.setattr(research,'_fetch_url',lambda url:('<time datetime="2026-11-15"></time>'+article,url))
    source,warning=intake._article(record,NOW)
    assert source['published_at'].startswith('2026-11-15') and 'Actual original' in source['text'] and warning is None


def test_owner_core_company_pool_is_explicit_and_collection_reports_gaps(monkeypatch):
    companies={row[0] for row in intake.COMPANY_SOURCES}
    assert {'OpenAI','Anthropic','Meta','Alphabet / Google','SpaceX','AMD','SanDisk','Micron'} <= companies
    mock=Mock(return_value=([], 'source unavailable'))
    monkeypatch.setattr(research,'_load_feed',mock)
    monkeypatch.setattr(research,'_fetch_url',Mock(side_effect=OSError('offline')))
    result=intake.collect_company_news()
    assert result['sources']==[] and len(result['company_checks'])==14
    assert len(result['warnings'])==14
    assert all(p['status']=='no_verified_lead' for p in result['company_checks'])


def test_deepmind_overlay_links_keep_research_title_instead_of_empty_text():
    parser=intake.ArticleLinks('https://deepmind.google/blog/')
    parser.feed('<a data-event-content-name="Robots learn new dexterous skills - Learn more" href="/blog/new-robot/"></a>')
    assert parser.rows[0]['title']=='Robots learn new dexterous skills'
    assert parser.rows[0]['url']=='https://deepmind.google/blog/new-robot/'


def test_spacex_reads_actual_mission_summary_not_image_metadata(monkeypatch):
    import json
    body='A real completed flight summary. '*20
    raw={'missionId':'starship-flight-15','title':'Starship Flight 15','endDate':'2026-11-14',
         'paragraphs':[{'content':body}],'imageDesktop':{'caption':'Navigation metadata is not event evidence'}}
    monkeypatch.setattr(research,'_fetch_url',lambda url:(json.dumps(raw),url))
    source,warning=intake._article({'url':'https://content.spacex.com/api/spacex-website/missions/starship-flight-15'},NOW)
    assert warning is None and source['text']==body
    assert source['publication_precision']=='date'
    assert source['url'].endswith('?missionId=starship-flight-15')
    raw['endDate']='2099-01-01'
    assert intake._article({'url':'https://content.spacex.com/api/spacex-website/missions/starship-flight-15'},NOW)[0] is None


def test_failed_top_links_are_replaced_by_further_verified_leads(monkeypatch):
    monkeypatch.setattr(intake, 'COMPANY_SOURCES', [('one', 'feed', 'https://example.org')])
    records = [{'url': f'https://example.org/news/{i}', 'kind': 'news',
                'title': 'Actual company announcement'} for i in range(6)]
    monkeypatch.setattr(intake, '_candidates', lambda entry, now: ('one', records, None))
    checked = []
    def article(row, now):
        checked.append(row['url'])
        if row['url'].endswith('/0'):
            return None, 'First article unavailable'
        return {**row, 'text': 'Verified original body', 'id': row['url']}, None
    monkeypatch.setattr(intake, '_article', article)
    result = intake.collect_company_news(limit=2)
    assert len(result['sources']) == 2
    assert len(checked) == result['article_check_count'] == 3
    assert result['discovered_candidate_count'] == 6
    assert result['warnings'] == ['First article unavailable']


def test_google_keeps_multiple_research_and_product_leads(monkeypatch):
    products = [{'url': f'https://blog.google/technology/ai/product-{i}/', 'title': 'AI product news'} for i in range(6)]
    html = ''.join(f'<a href="/blog/research-{i}/">Robotics research number {i}</a>' for i in range(6))
    monkeypatch.setattr(research, '_load_feed', lambda url, now: (products, None))
    monkeypatch.setattr(research, '_fetch_url', lambda url: (html, url))
    _, rows, warning = intake._candidates(('Alphabet / Google', 'feed', 'https://blog.google/technology/ai/rss/'), NOW)
    assert warning is None and len(rows) == 6
    assert sum('deepmind.google' in row['url'] for row in rows) == 3
    assert sum('blog.google' in row['url'] for row in rows) == 3


def test_news_taxonomy_does_not_displace_actual_company_events():
    parser = intake.ArticleLinks('https://www.anthropic.com/news')
    parser.feed('<a href="/news/announcements">Announcements</a>'
                '<a href="/news/partnerships">Partnerships</a>'
                '<a href="/news/roadshow-launch">Anthropic launches roadshow for proposed initial public offering</a>')
    assert [r['url'] for r in parser.rows] == ['https://www.anthropic.com/news/roadshow-launch']


def test_undated_major_headline_is_checked_first_but_never_accepted_without_body(monkeypatch):
    monkeypatch.setattr(intake, 'COMPANY_SOURCES', [('one', 'index', 'https://example.org')])
    rows = [{'url': f'https://example.org/{i}', 'title': title, 'kind': 'news'}
            for i, title in enumerate(['Company update', 'Company IPO roadshow', 'Real dated news'])]
    monkeypatch.setattr(intake, '_candidates', lambda entry, now: ('one', rows, None))
    checked = []
    def article(row, now):
        checked.append(row['title'])
        return (None, 'Missing original body') if 'IPO' in row['title'] else ({**row, 'text': 'Actual original report'}, None)
    monkeypatch.setattr(intake, '_article', article)
    result = intake.collect_company_news(limit=1)
    assert checked[0] == 'Company IPO roadshow'
    assert len(result['sources']) == 1 and 'IPO' not in result['sources'][0]['title']
