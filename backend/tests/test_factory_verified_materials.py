"""Local import boundaries; no live data, decoding or external requests."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from services import factory_verified_materials as verified


REFERENCE = "https://deepmind.google/blog/gemini-robotics-2-5-accelerates-real-world-physical-intelligence/"
PROJECT = "https://deepmind.google/models/gemini-robotics/"
MEDIA = "https://storage.googleapis.com/gdm-deepmind-com-prod-public/media/reviewed/"


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    root = tmp_path / "storage" / "factory"
    root.mkdir(parents=True)
    monkeypatch.setattr(verified, "FACTORY_ROOT", root)
    def no_network(*args, **kwargs):
        raise AssertionError("Verified material loading must remain offline")
    monkeypatch.setattr(httpx, "Client", no_network)
    assets = []
    for index in range(10):
        path = root / f"original-{index}.webm"
        path.write_bytes(f"independent original fixture {index}".encode())
        url = f"{MEDIA}{index}.webm"
        assets.append({"id": f"clip-{index}", "path": str(path), "url": url,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
            "reference_source_url": REFERENCE, "source_url": PROJECT,
            "source_chain": [REFERENCE, PROJECT, url], "duration": 18.5,
            "kind": "official_video", "description": "官方操作资料，原片2倍速；不能作为独立实测。",
            "credit": "Google DeepMind", "date_label": "2系列背景，发布于2026-07-30",
            "date_limit": "系列发布日，不代表本片拍摄日期",
            "clips": [{"start": 2, "end": 8, "description": "已核对动作"},
                      {"start": 8, "end": 12, "description": "后续独立动作"}]})
    manifest = root / "manifest.json"
    def save():
        manifest.write_text(json.dumps({"assets": assets}, ensure_ascii=False), encoding="utf-8")
        return manifest
    return root, assets, save, [{"id": "news", "url": REFERENCE},
                                {"id": "unused-context", "url": "https://apptronik.com/apollo/apollo-2"}]


def test_loads_all_ten_originals_offline_and_keeps_reviewed_context(bundle):
    _, assets, save, sources = bundle
    loaded = verified.load_verified_assets(save(), sources)
    assert len(loaded) == 10  # The normal six-file collection limit must not apply.
    assert loaded == assets
    assert loaded[0]["clips"][0]["description"] == "已核对动作"
    assert loaded[0]["date_limit"] == "系列发布日，不代表本片拍摄日期"
    assert "2倍速" in loaded[0]["description"]
    assert all("approved" not in asset and "semantic_review_passed" not in asset for asset in loaded)


@pytest.mark.parametrize("escape", ["manifest-external", "manifest-symlink", "asset-external", "asset-symlink", "asset-parent"])
def test_rejects_files_outside_factory_including_symlink_and_parent_escape(bundle, escape):
    root, assets, save, sources = bundle
    outside = root.parent / "outside.webm"
    outside.write_bytes(Path(assets[0]["path"]).read_bytes())
    manifest = save()
    if escape.startswith("manifest"):
        outside.write_bytes(manifest.read_bytes())
        if escape == "manifest-external":
            manifest = outside
        else:
            manifest = root / "linked-manifest.json"
            manifest.symlink_to(outside)
    else:
        if escape == "asset-external":
            assets[0]["path"] = str(outside)
        elif escape == "asset-parent":
            assets[0]["path"] = str(root / ".." / outside.name)
        else:
            link = root / "linked-original.webm"
            link.symlink_to(outside)
            assets[0]["path"] = str(link)
        manifest = save()
    with pytest.raises(ValueError, match="storage/factory"):
        verified.load_verified_assets(manifest, sources)


def test_rejects_changed_original_even_when_manifest_hash_was_valid(bundle):
    _, assets, save, sources = bundle
    manifest = save()
    Path(assets[3]["path"]).write_bytes(b"replacement file")
    with pytest.raises(ValueError, match="SHA256"):
        verified.load_verified_assets(manifest, sources)


@pytest.mark.parametrize("invalid", ["media-host", "media-path", "chain-end", "chain-start", "chain-middle", "uncited-reference", "untrusted-reference"])
def test_requires_exact_official_media_and_an_unbroken_cited_source_chain(bundle, invalid):
    _, assets, save, sources = bundle
    asset = assets[0]
    if invalid == "media-host":
        asset["url"] = "https://example.org/video.webm"
        asset["source_chain"][-1] = asset["url"]
    elif invalid == "media-path":
        asset["url"] = "https://storage.googleapis.com/unreviewed-bucket/video.webm"
        asset["source_chain"][-1] = asset["url"]
    elif invalid == "chain-end":
        asset["source_chain"][-1] = MEDIA + "different-original.webm"
    elif invalid == "chain-start":
        asset["source_chain"][0] = PROJECT
    elif invalid == "chain-middle":
        asset["source_chain"][1] = "https://example.org/project/"
    elif invalid == "uncited-reference":
        sources[:] = [{"url": PROJECT}]
    else:
        asset["reference_source_url"] = asset["source_chain"][0] = "https://example.org/news/"
        sources.append({"url": asset["reference_source_url"]})
    with pytest.raises(ValueError):
        verified.load_verified_assets(save(), sources)


@pytest.mark.parametrize("invalid", ["nan", "negative", "past-end", "overlap", "zero-length", "boolean", "duration-infinite"])
def test_rejects_nonfinite_or_invalid_reviewed_windows(bundle, invalid):
    _, assets, save, sources = bundle
    asset, first = assets[0], assets[0]["clips"][0]
    if invalid == "nan":
        first["start"] = float("nan")
    elif invalid == "negative":
        first["start"] = -1
    elif invalid == "past-end":
        asset["clips"][-1]["end"] = asset["duration"] + .01
    elif invalid == "overlap":
        asset["clips"][-1]["start"] = first["end"] - .01
    elif invalid == "zero-length":
        first["end"] = first["start"]
    elif invalid == "boolean":
        first["start"] = False
    else:
        asset["duration"] = float("inf")
    with pytest.raises(ValueError, match="时间窗|时长"):
        verified.load_verified_assets(save(), sources)


@pytest.mark.parametrize("count", [2, 17])
def test_enforces_manifest_asset_count(bundle, count):
    _, assets, save, sources = bundle
    assets[:] = (assets * 2)[:count]
    with pytest.raises(ValueError, match="3–16"):
        verified.load_verified_assets(save(), sources)


@pytest.mark.parametrize("budget", ["file", "total"])
def test_uses_actual_file_sizes_for_download_budgets(bundle, monkeypatch, budget):
    _, assets, save, sources = bundle
    if budget == "file":
        monkeypatch.setattr(verified, "MAX_FILE_BYTES", Path(assets[0]["path"]).stat().st_size - 1)
    else:
        monkeypatch.setattr(verified, "MAX_TOTAL_BYTES", sum(Path(a["path"]).stat().st_size for a in assets) - 1)
    for asset in assets:
        asset["bytes"] = 1  # Under-reported metadata cannot evade actual byte limits.
    with pytest.raises(ValueError, match="80MB|150MB"):
        verified.load_verified_assets(save(), sources)


def test_same_original_cannot_be_counted_as_another_asset(bundle):
    _, assets, save, sources = bundle
    assets[1].update(path=assets[0]["path"], sha256=assets[0]["sha256"])
    with pytest.raises(ValueError, match="不能重复"):
        verified.load_verified_assets(save(), sources)


def test_malformed_manifest_is_rejected_before_media_is_loaded(bundle):
    _, _, save, sources = bundle
    manifest = save()
    manifest.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        verified.load_verified_assets(manifest, sources)


def test_original_document_companions_keep_all_integrity_checks(bundle):
    _, assets, save, sources = bundle
    del assets[2:]
    with pytest.raises(ValueError, match='3–16'):
        verified.load_verified_assets(save(), sources)
    assert len(verified.load_verified_assets(save(), sources, minimum_assets=1)) == 2
    Path(assets[0]['path']).write_bytes(b'tampered original')
    with pytest.raises(ValueError, match='SHA256'):
        verified.load_verified_assets(save(), sources, minimum_assets=1)
