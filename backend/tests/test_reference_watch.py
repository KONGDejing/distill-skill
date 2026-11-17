from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.blogger import Blogger, BloggerWatch
from models.blogger_content_dna import BloggerContentDNA
from models.source_video import SourceVideo
from services import reference_watch as watch
from routers.bloggers import update_watch, WatchUpdate, refresh_watch


@pytest.fixture
def dbs(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "reference-watch.db"}')
    Base.metadata.create_all(engine, tables=[Blogger.__table__, BloggerWatch.__table__, SourceVideo.__table__, BloggerContentDNA.__table__])
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(watch, 'SessionLocal', sessions)
    monkeypatch.setattr(watch, 'ensure_memory_available', lambda: None)
    with sessions() as db:
        blogger = Blogger(name='测试博主', platform='douyin', profile_url='https://www.douyin.com/user/test')
        db.add(blogger);db.commit();identifier=blogger.id
    yield sessions, identifier
    engine.dispose()


def post(identifier='1234567890123456789', at=1700000000, author='test', name='测试博主'):
    return {'aweme_id': identifier, 'desc': '真实新作', 'create_time': at,
            'author': {'sec_uid': author, 'nickname': name}}


def test_discovery_uses_verified_author_and_real_publish_dates_not_pinned_order():
    items = [post(at=1600000000), post('2234567890123456789'),
             post('3234567890123456789', author='sidebar'), post('4234567890123456789', name='同名误选'),
             post('bad'), post('5234567890123456789', at=999999999999)]
    items[0]['is_top'] = True
    selected = watch.select_posts(items, 'test', '测试博主', datetime(2026,11,17))
    assert [p['post_id'] for p in selected] == ['2234567890123456789', '1234567890123456789']
    assert all(p['reference_only'] for p in selected)


@pytest.mark.parametrize('url,valid', [
    ('https://www.douyin.com/aweme/v1/web/aweme/post/?cursor=0',True),
    ('https://www-hj.douyin.com/aweme/v1/web/aweme/post/?cursor=0',True),
    ('https://www-hj.douyin.com.evil.example/aweme/v1/web/aweme/post/',False),
    ('https://www.douyin.com/aweme/v1/web/aweme/detail/',False),
])
def test_observed_normal_page_endpoint_accepts_real_site_host_but_not_recommendations(url,valid):
    assert watch.is_profile_posts_response(url) is valid


@pytest.mark.parametrize('url', ['https://evil.example/user/test', 'http://www.douyin.com/user/test',
    'https://www.douyin.com/video/123', 'http://127.0.0.1'])
def test_profile_rejects_non_official_or_wrong_type_urls(url):
    with pytest.raises(ValueError): watch.profile_link(url)


def test_incremental_import_dedups_manual_short_url_metadata_and_bounds_daily_total(dbs):
    sessions, identifier = dbs
    now = datetime(2026,11,17,6)
    selected = watch.select_posts([post(str(1234567890123456780+i), at=1700000000+i) for i in range(10)], 'test','测试博主')
    with sessions() as db:
        old = selected[0]
        db.add(SourceVideo(blogger_id=identifier, source_url='https://v.douyin.com/share/',
            extra_data={'webpage_url': old['source_url']}));db.commit()
        first = watch.import_discovered(db, identifier, selected, now, 4)
        second = watch.import_discovered(db, identifier, selected, now, 4)
        third = watch.import_discovered(db, identifier, selected, now, 4)
        assert len(first) == 3 and len(second) == 1 and third == []
        assert db.query(SourceVideo).count() == 5
        assert all(v.extra_data['reference_only'] for v in db.query(SourceVideo).filter(SourceVideo.id.in_(first)))


def test_watch_toggle_and_refresh_do_not_duplicate_active_checks(dbs):
    sessions, identifier = dbs
    with sessions() as db:
        assert update_watch(identifier, WatchUpdate(enabled=True), db)['status'] == 'waiting'
        w=db.get(BloggerWatch,identifier);w.status='importing';w.next_check_at=datetime(2026,12,1);db.commit()
        assert refresh_watch(identifier, db)['next_check_at'].startswith('2026-12-01')
        assert not update_watch(identifier, WatchUpdate(enabled=False), db)['enabled']


def test_verification_block_retains_prior_dna_and_never_reports_new_analysis(dbs, monkeypatch):
    sessions, identifier = dbs
    with sessions() as db:
        db.add(BloggerWatch(blogger_id=identifier, enabled=True))
        db.add(BloggerContentDNA(blogger_id=identifier, version=5));db.commit()
    monkeypatch.setattr(watch,'discover_posts',Mock(side_effect=ValueError('官方验证未完成')))
    watch.check_watch(identifier)
    with sessions() as db:
        w=db.get(BloggerWatch,identifier)
        assert w.status=='blocked' and '验证' in w.error_message and w.last_analyzed_at is None
        assert db.query(BloggerContentDNA).one().version==5
        assert not db.query(SourceVideo).count()


def test_no_new_samples_skips_download_and_repeated_model_analysis(dbs,monkeypatch):
    sessions,identifier=dbs
    selected=watch.select_posts([post()], 'test','测试博主')
    with sessions() as db:
        db.add(SourceVideo(blogger_id=identifier,source_url=selected[0]['source_url'],status='transcribed',transcript='实际口播'))
        db.commit()
        signature=watch._sample_signature(db,identifier)
        db.add(BloggerWatch(blogger_id=identifier,enabled=True,analysis_signature=signature));db.commit()
    monkeypatch.setattr(watch,'discover_posts',lambda *args:('https://www.douyin.com/user/test',selected))
    from tasks.celery_app import run_download_and_transcribe,run_distill_analysis
    download,analyze=Mock(),Mock()
    monkeypatch.setattr(run_download_and_transcribe,'run',download);monkeypatch.setattr(run_distill_analysis,'run',analyze)
    watch.check_watch(identifier)
    download.assert_not_called();analyze.assert_not_called()
    with sessions() as db:
        w=db.get(BloggerWatch,identifier);assert w.status=='up_to_date' and w.summary['new_count']==0


def test_reference_methods_reach_editors_but_are_not_news_evidence(dbs):
    sessions,identifier=dbs
    with sessions() as db:
        db.add(BloggerWatch(blogger_id=identifier,enabled=True,status='blocked'))
        db.add(BloggerContentDNA(blogger_id=identifier,version=3,
            viral_techniques={'visual_style':{'opening':'真实动作开场'}}));db.commit()
    brief=watch.reference_brief('producer')
    assert brief[0]['version']==3 and '真实动作' in brief[0]['methods']
    assert brief[0]['last_check_status']=='blocked' and '不是本期新闻事实' in brief[0]['scope']
    assert watch.reference_brief('producer2')==[]


def test_new_sample_pipeline_updates_dna_once_then_reuses_it(dbs,monkeypatch):
    sessions,identifier=dbs
    selected=watch.select_posts([post()], 'test','测试博主')
    with sessions() as db:
        db.add(BloggerWatch(blogger_id=identifier,enabled=True));db.commit()
    monkeypatch.setattr(watch,'discover_posts',lambda *args:('https://www.douyin.com/user/test',selected))
    from tasks.celery_app import run_download_and_transcribe,run_distill_analysis
    def download(video_id):
        with sessions() as db:
            v=db.get(SourceVideo,video_id);v.status='transcribed';v.transcript='真实新作口播';db.commit()
        return {'status':'transcribed'}
    def analyze(blogger_id):
        with sessions() as db:
            db.add(BloggerContentDNA(blogger_id=blogger_id,version=1));db.commit()
        return {'status':'ready'}
    download_call,analysis_call=Mock(side_effect=download),Mock(side_effect=analyze)
    monkeypatch.setattr(run_download_and_transcribe,'run',download_call)
    monkeypatch.setattr(run_distill_analysis,'run',analysis_call)
    watch.check_watch(identifier);watch.check_watch(identifier)
    assert download_call.call_count==analysis_call.call_count==1
    with sessions() as db:
        w=db.get(BloggerWatch,identifier)
        assert w.status=='up_to_date' and w.last_analyzed_at and w.analysis_signature
        assert db.query(SourceVideo).count()==1


def test_interrupted_pending_import_resumes_without_duplicate_record(dbs,monkeypatch):
    sessions,identifier=dbs
    selected=watch.select_posts([post()], 'test','测试博主')
    with sessions() as db:
        db.add(BloggerWatch(blogger_id=identifier,enabled=True))
        db.add(SourceVideo(blogger_id=identifier,source_url=selected[0]['source_url'],status='pending',extra_data={'auto_reference':True}))
        db.commit()
    monkeypatch.setattr(watch,'discover_posts',lambda *args:('https://www.douyin.com/user/test',selected))
    from tasks.celery_app import run_download_and_transcribe
    download=Mock(return_value={'error':'未取得完整视频'})
    monkeypatch.setattr(run_download_and_transcribe,'run',download)
    watch.check_watch(identifier)
    download.assert_called_once()
    with sessions() as db:
        w=db.get(BloggerWatch,identifier)
        assert w.status=='partial' and w.summary['new_count']==0
        assert len(w.summary['failed_import_ids'])==1 and w.last_analyzed_at is None
        assert db.query(SourceVideo).count()==1
