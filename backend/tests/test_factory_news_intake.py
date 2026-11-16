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
