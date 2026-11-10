"""Pinned long originals must never broaden ordinary video collection limits."""
import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from services import factory_visuals as visuals


@pytest.fixture
def reviewed(monkeypatch, tmp_path):
    catalog = copy.deepcopy(visuals.REVIEWED_LOCAL_ASSETS)
    sources, seen = [], set()
    for index, (url, entry) in enumerate(catalog.items()):
        data = f"original independent video {index}".encode()
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        path = tmp_path / entry["local_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        for ref in entry["reference_urls"]:
            if ref not in seen:
                sources.append({"id": f"source-{len(sources)}", "url": ref, "title": "NVIDIA official source"})
                seen.add(ref)
    monkeypatch.setattr(visuals, "STORAGE", tmp_path)
    monkeypatch.setattr(visuals, "REVIEWED_LOCAL_ASSETS", catalog)
    # Every fixture original exceeds the unchanged ordinary collector limit.
    monkeypatch.setattr(visuals, "MAX_FILE_BYTES", 4)
    requests = []
    client_type = httpx.Client

    def offline(request):
        requests.append(str(request.url))
        raise AssertionError("Pinned local imports must not require network access")

    monkeypatch.setattr(visuals.httpx, "Client", lambda **kw: client_type(transport=httpx.MockTransport(offline), **kw))
    probes = []

    def probe(path, deadline=None, *, max_duration=180):
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        spec = next(e for e in catalog.values() if e["sha256"] == digest)
        assert max_duration >= spec["duration"]
        probes.append(digest)
        return {"duration": spec["duration"], "width": 1280, "height": 720, "fps": 29.97,
                "motion_verified": True, "motion_score": 3}

    monkeypatch.setattr(visuals, "_probe", probe)
    return catalog, sources, requests, probes


def test_exact_sources_import_originals_offline_and_preserve_179_unique_seconds(reviewed, tmp_path):
    catalog, sources, requests, probes = reviewed
    result = visuals.collect_visuals(sources, tmp_path / "output", limit=3)
    assert result["status"] == "ready" and not result["warnings"] and not requests
    assert len(result["assets"]) == len(probes) == 3
    assert sum(c["end"]-c["start"] for a in result["assets"] for c in a["clips"]) == 179
    for asset in result["assets"]:
        spec = catalog[asset["url"]]
        assert asset["sha256"] == hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest() == spec["sha256"]
        assert asset["source_chain"] == [asset["reference_source_url"], asset["url"]]
        assert asset["source_id"] in {s["id"] for s in sources}
        assert "NVIDIA" in asset["credit"] and "资料画面" in asset["credit"]
        assert asset["reuse_basis"] == "reviewed_exact_source_url_and_pinned_original_sha256"
    assert visuals.MAX_FILE_BYTES == 4 and visuals.MAX_TOTAL_BYTES == 60_000_000


def test_archive_reuse_rechecks_pins_and_ignores_changed_window_metadata(reviewed, tmp_path):
    catalog, sources, requests, probes = reviewed
    first = visuals.collect_visuals(sources, tmp_path / "first", limit=3)
    for spec in catalog.values():
        (tmp_path / spec["local_path"]).unlink()
    for record in (tmp_path / "factory/asset-library").glob("*.json"):
        value = json.loads(record.read_text())
        value["clips"] = [{"start": 0, "end": 12}]
        record.write_text(json.dumps(value))
    second = visuals.collect_visuals(sources, tmp_path / "second", limit=3)
    assert second["status"] == "ready" and not requests
    assert len(probes) == 6
    assert [a["clips"] for a in second["assets"]] == [a["clips"] for a in first["assets"]]


@pytest.mark.parametrize("change", ["query", "unrelated"])
def test_similar_source_cannot_authorize_import(reviewed, tmp_path, change):
    _, sources, requests, probes = reviewed
    wrong = [{**s, "url": s["url"] + "?other=1" if change == "query" else "https://www.nvidia.cn/geforce/news/unrelated/"} for s in sources]
    result = visuals.collect_visuals(wrong, tmp_path / "output", limit=3)
    assert result["status"] == "insufficient" and not result["assets"] and not probes and not requests


def test_corrupt_originals_and_forged_archive_hashes_never_reach_probe(reviewed, tmp_path):
    catalog, sources, _, probes = reviewed
    for url, spec in catalog.items():
        path = tmp_path / spec["local_path"]
        path.write_bytes(b"x" * spec["bytes"])
        visuals._archive_asset({"id": hashlib.sha256(url.encode()).hexdigest()[:20], "url": url,
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "duration": spec["duration"],
            "bytes": spec["bytes"], "reference_source_url": spec["reference_urls"][0], "source_title": "Forged archive metadata"})
    result = visuals.collect_visuals(sources, tmp_path / "output", limit=3)
    assert result["status"] == "insufficient" and not result["assets"] and not probes
    assert any("固定SHA256" in w for w in result["warnings"])
    assert not list((tmp_path / "output").glob("*.part"))


@pytest.mark.parametrize("budget", ["file", "total"])
def test_reviewed_import_enforces_both_local_budgets(reviewed, tmp_path, monkeypatch, budget):
    catalog, sources, _, probes = reviewed
    if budget == "file":
        monkeypatch.setattr(visuals, "REVIEWED_LOCAL_MAX_FILE_BYTES", 5)
    else:
        monkeypatch.setattr(visuals, "REVIEWED_LOCAL_MAX_TOTAL_BYTES", sum(e["bytes"] for e in catalog.values()) - 1)
    result = visuals.collect_visuals(sources, tmp_path / "output", limit=3)
    assert result["status"] == "insufficient"
    assert len(probes) == (0 if budget == "file" else 2)
    assert any("专用本地导入预算" in w for w in result["warnings"])


def test_symlinked_pinned_original_is_rejected_even_if_bytes_match(reviewed, tmp_path):
    catalog, sources, _, probes = reviewed
    for index, spec in enumerate(catalog.values()):
        path = tmp_path / spec["local_path"]
        outside = tmp_path / f"not-a-catalog-original-{index}.mp4"
        path.rename(outside)
        path.symlink_to(outside)
    result = visuals.collect_visuals(sources, tmp_path / "output", limit=3)
    assert not result["assets"] and not probes


def test_pinned_metadata_requires_pinned_bytes_and_returns_independent_windows():
    url, spec = next(iter(visuals.REVIEWED_LOCAL_ASSETS.items()))
    wrong = visuals._metadata({"url": url, "source_title": "NVIDIA"}, {"duration": spec["duration"], "sha256": "wrong"})
    assert wrong["clips"][0]["start"] == 0
    right = visuals._metadata({"url": url, "source_title": "NVIDIA"}, {"duration": spec["duration"], "sha256": spec["sha256"]})
    assert right["clips"][2]["start"] == 406
    right["clips"][2]["start"] = 1
    again = visuals._metadata({"url": url, "source_title": "NVIDIA"}, {"duration": spec["duration"], "sha256": spec["sha256"]})
    assert again["clips"][2]["start"] == 406


def test_normal_probe_still_rejects_long_files(monkeypatch, tmp_path):
    info = {"streams": [{"width": 1280, "height": 720, "r_frame_rate": "30/1"}], "format": {"duration": "660.492"}}
    monkeypatch.setattr(visuals.subprocess, "check_output", lambda *a, **k: json.dumps(info).encode())
    with pytest.raises(ValueError, match="短视频素材"):
        visuals._probe(tmp_path / "long-unreviewed.mp4")


def test_local_import_does_not_open_nvidia_download_allowlist():
    for url in [*visuals.REVIEWED_LOCAL_ASSETS, "https://images.nvidia.cn/cn/youtube-replicates/unknown.mp4"]:
        with pytest.raises(ValueError, match="白名单"):
            visuals._url(url, media=True)
