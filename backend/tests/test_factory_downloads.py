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
    assert unquote(archive.headers['content-disposition']) == "attachment; filename*=utf-8''DLSS 5拿奖，画面谁说了算？—抖音发布包.zip"
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
