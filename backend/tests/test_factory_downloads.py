from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import HTTPException
from routers import factory


def fixture_item(tmp_path, monkeypatch):
    root = tmp_path / 'factory'
    root.mkdir()
    video = root / 'internal-video.mp4'
    video.write_bytes(b'video')
    archive = root / 'internal-package.zip'
    archive.write_bytes(b'archive')
    item = SimpleNamespace(content={'title': 'DLSS 5拿奖，画面谁说了算？'},
        media={'video_path': str(video)}, packages=[{'platform': 'douyin', 'path': str(archive)}])
    monkeypatch.setattr(factory, 'FACTORY_DIR', root)
    return item, SimpleNamespace(get=lambda *_: item)


def test_video_and_archive_download_with_chinese_titles(tmp_path, monkeypatch):
    item, db = fixture_item(tmp_path, monkeypatch)
    video = factory.download_video('opaque-id', db)
    archive = factory.download_package('opaque-id', 'douyin', db)
    assert unquote(video.headers['content-disposition']) == "attachment; filename*=utf-8''DLSS 5拿奖，画面谁说了算？.mp4"
    assert unquote(archive.headers['content-disposition']) == "attachment; filename*=utf-8''抖音—DLSS 5拿奖，画面谁说了算？.zip"
    assert video.path.name == 'internal-video.mp4'
    assert 'opaque-id' not in video.headers['content-disposition']


@pytest.mark.parametrize('outside', [False, True])
def test_video_download_rejects_missing_or_outside_file(tmp_path, monkeypatch, outside):
    item, db = fixture_item(tmp_path, monkeypatch)
    path = tmp_path / 'private.mp4' if outside else tmp_path / 'factory' / 'missing.mp4'
    if outside:
        path.write_bytes(b'private')
    item.media = {'video_path': str(path)}
    with pytest.raises(HTTPException) as exc:
        factory.download_video('id', db)
    assert exc.value.status_code == 404


def test_cover_download_uses_finished_lettered_cover(tmp_path, monkeypatch):
    item, db = fixture_item(tmp_path, monkeypatch)
    cover = tmp_path / 'factory' / 'finished-cover.png'
    cover.write_bytes(b'lettered png')
    item.media['cover_path'] = str(tmp_path / 'factory' / 'old-cover.png')
    item.packages[0]['cover_path'] = str(cover)
    response = factory.download_cover('id', db)
    assert response.path == cover
    assert 'DLSS 5拿奖，画面谁说了算？—封面.png' in unquote(response.headers['content-disposition'])


def test_video_download_prefers_complete_video(tmp_path, monkeypatch):
    item, db = fixture_item(tmp_path, monkeypatch)
    complete = tmp_path / 'factory' / 'complete.mp4'
    complete.write_bytes(b'complete video with cover')
    item.packages[0]['video_path'] = str(complete)
    assert factory.download_video('id', db).path == complete
