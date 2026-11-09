"""Official-video collection boundaries, without contacting public services."""
import json
import socket
from pathlib import Path

import httpx
import pytest

from services import factory_visuals as visuals


def test_library_reuses_verified_bytes_but_rejects_corruption(monkeypatch, tmp_path):
    import hashlib
    monkeypatch.setattr(visuals, "STORAGE", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"verified video bytes")
    asset = {"id": "asset1", "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
             "reference_source_url": "https://example.org/one", "source_title": "One", "bytes": source.stat().st_size}
    visuals._archive_asset(asset)
    visuals._archive_asset({**asset, "reference_source_url": "https://example.org/two", "source_title": "Two"})
    copy = tmp_path / "copy.mp4"
    visuals._reuse_archived("asset1", copy)
    assert copy.read_bytes() == source.read_bytes()
    assert visuals.library_summary()["source_pages"] == 2
    cached = tmp_path / "factory/asset-library/asset1.mp4"
    cached.write_bytes(b"corrupt")
    copy.unlink()
    visuals._reuse_archived("asset1", copy)
    assert not copy.exists()


@pytest.mark.parametrize("url", [
    "http://allenai.github.io/ToyBlocks/media/a.mp4",
    "https://allenai.github.io.evil.example/ToyBlocks/media/a.mp4",
    "https://allenai.github.io/ToyBlocks/%252e%252e/private.mp4",
    "https://user:password@allenai.github.io/ToyBlocks/media/a.mp4",
    "https://127.0.0.1/ToyBlocks/media/a.mp4",
])
def test_media_urls_reject_nonofficial_and_ambiguous_paths(url):
    with pytest.raises(ValueError):
        visuals._url(url, media=True)


def test_public_host_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ValueError, match="非公网"):
        visuals._public_host("https://allenai.github.io/ToyBlocks/")


def _collector(monkeypatch, tmp_path, handler):
    monkeypatch.setattr(visuals, "STORAGE", tmp_path)
    monkeypatch.setattr(visuals, "_public_host", lambda url: None)
    client_type = httpx.Client
    monkeypatch.setattr(visuals.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(visuals, "_probe", lambda *args: {
        "duration": 12, "width": 640, "height": 360, "fps": 30,
        "motion_verified": True, "motion_score": 5,
    })
    return [{"id": "source-1", "title": "Official ToyBlocks", "url": "https://huggingface.co/blog/allenai/toyblocks"}]


def test_collect_follows_explicit_project_and_preserves_complete_chain(monkeypatch, tmp_path):
    def handler(request):
        if request.url.host == "huggingface.co":
            return httpx.Response(200, text='<a href="http://allenai.github.io/ToyBlocks/">Project</a>')
        if request.url.path == "/ToyBlocks/":
            return httpx.Response(200, text=''.join(
                f'<video data-src="media/clip{i}.mp4"></video>' for i in range(3)))
        return httpx.Response(200, content=request.url.path.encode())
    sources = _collector(monkeypatch, tmp_path, handler)
    result = visuals.collect_visuals(sources, tmp_path / "output")
    assert result["status"] == "ready"
    assert len(result["assets"]) == 3
    for asset in result["assets"]:
        assert Path(asset["path"]).is_file()
        assert asset["source_chain"] == [sources[0]["url"], "https://allenai.github.io/ToyBlocks/", asset["url"]]
        assert asset["source_id"] == "source-1"
        assert asset["published"] is False
        assert asset["rights_note"]


def test_phonon_collects_linked_official_demos_without_following_setup_links(monkeypatch, tmp_path):
    visited = []
    launch = "https://gradium.ai/blog/release-phonon-open-weight"
    def handler(request):
        visited.append(str(request.url))
        if request.url.host == "huggingface.co":
            return httpx.Response(200, text=f'<a href="{launch}">Launch</a>')
        if request.url.path.startswith("/blog/"):
            return httpx.Response(200, text=''.join(
                f'<a href="/assets/release-phonon-open-weight/demos/{name}.mp4">Demo</a>'
                for name in ("on-watch", "repcoach", "chrome-extension"))
                + '<a href="/assets/release-phonon-open-weight/setup.prompt">Setup</a>'
                + '<a href="https://gradium.ai/blog/unrelated">Other</a>')
        return httpx.Response(200, content=request.url.path.encode())
    _collector(monkeypatch, tmp_path, handler)
    source = {"id": "phonon", "title": "Phonon TTS", "url": "https://huggingface.co/blog/phonon-tts"}
    result = visuals.collect_visuals([source], tmp_path / "output")
    assert result["status"] == "ready"
    assert len(result["assets"]) == 3
    for asset in result["assets"]:
        assert asset["source_chain"] == [source["url"], launch, asset["url"]]
        assert "Gradium / Phonon" in asset["credit"]
        assert asset["license_url"] is None
        assert "未核实" in asset["rights_note"]
        assert "on_device" in asset["topic_tags"]
    assert not any("setup.prompt" in url or "/unrelated" in url for url in visited)


def test_duplicate_video_bytes_cannot_satisfy_three_asset_requirement(monkeypatch, tmp_path):
    def handler(request):
        if not request.url.path.endswith(".mp4"):
            return httpx.Response(200, text=''.join(
                f'<video src="https://allenai.github.io/ToyBlocks/media/clip{i}.mp4"></video>' for i in range(3)))
        return httpx.Response(200, content=b"the same video")
    result = visuals.collect_visuals(_collector(monkeypatch, tmp_path, handler), tmp_path / "output")
    assert result["status"] == "insufficient"
    assert len(result["assets"]) == 1
    assert "未使用截图" in result["warnings"][-1]


def test_media_redirect_cannot_leave_allowlist_or_access_localhost(monkeypatch, tmp_path):
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if request.url.path.endswith(".mp4"):
            return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
        return httpx.Response(200, text='<video src="https://allenai.github.io/ToyBlocks/media/clip.mp4"></video>')
    result = visuals.collect_visuals(_collector(monkeypatch, tmp_path, handler), tmp_path / "output")
    assert result["status"] == "insufficient"
    assert not result["assets"]
    assert not any("127.0.0.1" in url for url in requested)
    assert not list((tmp_path / "output").glob("*.part"))


def test_probe_rejects_still_image_encoded_as_video(monkeypatch, tmp_path):
    info = {"streams": [{"width": 640, "height": 360, "r_frame_rate": "30/1"}],
            "format": {"duration": "12"}}
    calls = iter([json.dumps(info).encode(), bytes([30]) * 4096])
    monkeypatch.setattr(visuals.subprocess, "check_output", lambda *args, **kwargs: next(calls))
    with pytest.raises(ValueError, match="静帧替代"):
        visuals._probe(tmp_path / "fake.mp4")


def test_oversized_video_is_rejected_before_decode_and_partial_removed(monkeypatch, tmp_path):
    def handler(request):
        if request.url.path.endswith(".mp4"):
            return httpx.Response(200, content=b"12345")
        return httpx.Response(200, text='<video src="https://allenai.github.io/ToyBlocks/media/clip.mp4"></video>')
    sources = _collector(monkeypatch, tmp_path, handler)
    monkeypatch.setattr(visuals, "MAX_FILE_BYTES", 4)
    decoded = []
    monkeypatch.setattr(visuals, "_probe", lambda *args: decoded.append(args))
    result = visuals.collect_visuals(sources, tmp_path / "output")
    assert result["status"] == "insufficient"
    assert not decoded
    assert not list((tmp_path / "output").glob("*.mp4"))
    assert not list((tmp_path / "output").glob("*.part"))


def test_output_directory_symlink_cannot_escape_storage(monkeypatch, tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(visuals, "STORAGE", storage)
    with pytest.raises(ValueError, match="storage"):
        visuals.collect_visuals([], storage / "escape")


def test_verified_toyblocks_focus_regions_survive_metadata_generation():
    def metadata(name):
        return visuals._metadata({"url": f"https://allenai.github.io/ToyBlocks/media/{name}.mp4",
                                  "source_title": "ToyBlocks"}, {"duration": 60})
    hero = metadata("hero")["focus_regions"]
    assert [region["rect"] for region in hero] == [
        [0.375, 1 / 6, 0.25, 1 / 3], [0.5, 0.5, 0.25, 1 / 3],
    ]
    assert all("2×2" in region["label"] for region in hero)
    for name, count in {"s2r_real": 3, "dg_play": 2, "dg_exec": 2,
                        "dg_render": 3, "dg_reason": 1}.items():
        assert len(metadata(name)["focus_regions"]) == count
    # A renderer may edit its own shot copy without changing later jobs' crops.
    hero[0]["rect"][0] = 0
    assert metadata("hero")["focus_regions"][0]["rect"][0] == 0.375
