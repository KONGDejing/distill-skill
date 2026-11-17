"""Reference imports recover real audio/video and expose actionable failures."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.blogger import Blogger
from models.source_video import SourceVideo
from routers import bloggers, videos
from services import video_downloader as downloader


@pytest.mark.parametrize('value,expected', [
    ('复制打开抖音，看看这个视频 https://v.douyin.com/publicClip/ 尾码', 'https://v.douyin.com/publicClip/'),
    (' https://www.douyin.com/video/123。', 'https://www.douyin.com/video/123'),
    ('（https://v.douyin.com/test/）', 'https://v.douyin.com/test/'),
    ('https://example.com/video?id=abc', 'https://example.com/video?id=abc'),
])
def test_share_caption_extracts_only_public_url(value, expected):
    assert downloader.normalize_video_url(value) == expected


@pytest.mark.parametrize('value', ['not a link', 'file:///etc/passwd', 'https://localhost/a',
    'http://127.0.0.1/a', 'http://192.168.1.5/video', 'https://user:password@example.com/a',
    'https://example.com:8000/a', 'https://example.com:invalid/a'])
def test_private_or_invalid_import_urls_are_rejected(value):
    with pytest.raises(ValueError): downloader.normalize_video_url(value)


def test_ytdlp_runs_once_using_worker_python_and_never_returns_private_stderr(tmp_path, monkeypatch):
    process = Mock(return_value=SimpleNamespace(returncode=1, stdout='', stderr='private_cookie=SECRET'))
    monkeypatch.setattr(downloader.subprocess, 'run', process)
    error = downloader._download_with_ytdlp('https://example.com/video', str(tmp_path/'video.mp4'))
    assert process.call_count == 1
    assert process.call_args.args[0][:3] == [sys.executable, '-m', 'yt_dlp']
    assert 'SECRET' not in error


def test_failed_import_never_redownloads_just_to_format_an_error(tmp_path, monkeypatch):
    first, second = Mock(return_value='平台解析失败'), Mock(return_value='浏览器未取得视频')
    monkeypatch.setattr(downloader, '_download_with_ytdlp', first)
    monkeypatch.setattr(downloader, '_download_with_playwright', second)
    path, error = downloader.download_video('https://example.com/video', tmp_path, 'test')
    assert path is None and '解析失败' in error and '浏览器' in error
    assert first.call_count == second.call_count == 1


def test_cached_verified_media_avoids_both_network_paths(tmp_path, monkeypatch):
    video = tmp_path/'test.mp4';video.write_bytes(b'verified media')
    monkeypatch.setattr(downloader, 'valid_video', lambda path: Path(path) == video)
    first, second = Mock(), Mock()
    monkeypatch.setattr(downloader, '_download_with_ytdlp', first)
    monkeypatch.setattr(downloader, '_download_with_playwright', second)
    assert downloader.download_video('https://example.com/video', tmp_path, 'test') == (str(video), None)
    first.assert_not_called();second.assert_not_called()


@pytest.mark.parametrize('streams,valid', [(['video', 'audio'], True), (['video'], False), ([], False)])
def test_screenshot_or_silent_media_cannot_count_as_download(tmp_path, monkeypatch, streams, valid):
    path=tmp_path/'source.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(downloader.subprocess, 'run', Mock(return_value=SimpleNamespace(returncode=0,
        stdout=json.dumps({'streams':[{'codec_type':s} for s in streams], 'format':{'duration':'10'}}))))
    assert downloader.valid_video(path) is valid


def response(status, body, content_range=None):
    return SimpleNamespace(status=status, body=lambda:body,
                           headers={'content-range':content_range} if content_range else {})


def test_partial_content_is_assembled_without_gaps():
    request=Mock();request.get.side_effect=[response(206,b'abc','bytes 0-2/6'),response(206,b'def','bytes 3-5/6')]
    assert downloader._read_complete_media(request,'https://example.com/media') == b'abcdef'
    assert request.get.call_args_list[1].kwargs['headers']['Range'] == 'bytes=3-'


@pytest.mark.parametrize('second', [response(206,b'abc','bytes 0-2/6'),response(206,b'ef','bytes 4-5/6'),
    response(206,b'de','bytes 3-5/6'),response(200,b'abcdef'),response(403,b'')])
def test_range_repetition_gaps_truncation_and_denial_never_report_success(second):
    request=Mock();request.get.side_effect=[response(206,b'abc','bytes 0-2/6'),second]
    assert downloader._read_complete_media(request,'https://example.com/media') is None


@pytest.fixture
def import_db(tmp_path, monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path/"reference.db"}')
    Base.metadata.create_all(engine,tables=[Blogger.__table__,SourceVideo.__table__])
    session=sessionmaker(bind=engine)()
    blogger=Blogger(name='测试对标');session.add(blogger);session.commit()
    from tasks.celery_app import run_download_and_transcribe
    enqueue=Mock(return_value=SimpleNamespace(id='test-import-task'))
    monkeypatch.setattr(run_download_and_transcribe,'delay',enqueue)
    yield session,blogger,enqueue
    session.close();engine.dispose()


def test_failed_url_can_be_retried_before_any_file_exists(import_db):
    db,blogger,enqueue=import_db
    video=SourceVideo(blogger_id=blogger.id,source_url='复制打开 https://v.douyin.com/test/ 尾码',status='error',error_message='old')
    db.add(video);db.commit()
    assert videos.trigger_transcribe(video.id,db)['task_id']=='test-import-task'
    assert video.source_url=='https://v.douyin.com/test/' and video.error_message is None
    assert video.status=='pending';enqueue.assert_called_once_with(video.id)


def test_duplicate_share_reuses_existing_row_and_queue(import_db):
    db,blogger,enqueue=import_db
    added=bloggers.add_video(blogger.id,bloggers.VideoLinkAdd(source_url='分享 https://v.douyin.com/test/'),db)
    repeated=bloggers.add_video(blogger.id,bloggers.VideoLinkAdd(source_url='https://v.douyin.com/test/'),db)
    assert repeated['id']==added['id'] and repeated['reused'] is True
    assert db.query(SourceVideo).count()==1;enqueue.assert_called_once()


def test_busy_import_does_not_enqueue_another_download(import_db):
    db,blogger,enqueue=import_db
    video=SourceVideo(blogger_id=blogger.id,source_url='https://example.com/a',status='transcribing')
    db.add(video);db.commit()
    assert videos.trigger_transcribe(video.id,db)['status']=='transcribing';enqueue.assert_not_called()


def test_queue_failure_is_actionable_and_keeps_record_for_retry(import_db):
    from fastapi import HTTPException
    db,blogger,enqueue=import_db;enqueue.side_effect=RuntimeError('private broker secret')
    with pytest.raises(HTTPException) as failure:
        bloggers.add_video(blogger.id,bloggers.VideoLinkAdd(source_url='https://example.com/a'),db)
    assert failure.value.status_code==503
    video=db.query(SourceVideo).one()
    assert video.status=='error' and '重新导入' in video.error_message
    assert 'secret' not in video.error_message
