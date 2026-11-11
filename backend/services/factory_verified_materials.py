"""Load locally reviewed originals without collecting or broadening URL policy.

This checks file integrity, attribution records and declared source windows. It
does not establish semantic accuracy, motion, originality or publication rights;
the reviewed evidence and the final montage's normal quality gates still apply.
No network, browser, database or media generation is used here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from services.factory_research import _validated_url
from services.factory_visuals import VIDEO_SUFFIXES, _url


FACTORY_ROOT = Path(__file__).resolve().parents[1] / "storage" / "factory"
MAX_FILE_BYTES = 80_000_000
MAX_TOTAL_BYTES = 150_000_000
MAX_MANIFEST_BYTES = 2_000_000


def _local_file(value, label):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{label}路径无效")
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{label}文件不存在或路径无效") from exc
    # Do not resolve the boundary through a symlink to an external directory.
    if not path.is_relative_to(FACTORY_ROOT.absolute()):
        raise ValueError(f"{label}必须位于 backend/storage/factory 内，不能通过符号链接逃逸")
    if not path.is_file():
        raise ValueError(f"{label}必须是普通文件")
    return path


def _reference_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError("素材来源 URL 无效")
    if _validated_url(value) != value:
        raise ValueError("素材来源必须使用确切的规范 URL，不能附加片段")
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是有限数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label}必须是有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label}必须是有限数值")
    return number


def _windows(asset):
    duration = _number(asset.get("duration"), "素材时长")
    clips = asset.get("clips")
    if duration <= 0 or not isinstance(clips, list) or not clips:
        raise ValueError("素材必须有正时长和已审核时间窗")
    ranges = []
    for clip in clips:
        if not isinstance(clip, dict):
            raise ValueError("素材时间窗必须是对象")
        start = _number(clip.get("start"), "时间窗起点")
        end = _number(clip.get("end"), "时间窗终点")
        if not 0 <= start < end <= duration:
            raise ValueError("素材时间窗必须非负、有长度且不超过原片时长")
        ranges.append((start, end))
    ranges.sort()
    if any(start < previous_end for (_, previous_end), (start, _) in zip(ranges, ranges[1:])):
        raise ValueError("同一素材的已审核时间窗不能重叠")


def load_verified_assets(manifest_path, sources, *, minimum_assets=3) -> list[dict]:
    """Return 3–16 intact originals; an original-document companion may request 1–16.

    Paths may be absolute or relative to the process working directory. Every
    resolved file must remain inside backend/storage/factory. Only source URLs
    actually cited by an asset are validated; unrelated context sources need not
    be valid media origins. Source-chain pages use the research host policy, and
    the final exact media URL uses the unchanged media host-and-path policy.
    """
    manifest = _local_file(manifest_path, "素材清单")
    if manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("素材清单文件过大")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("素材清单不是有效 UTF-8 JSON") from exc
    assets = data.get("assets") if isinstance(data, dict) else None
    if type(minimum_assets) is not int or minimum_assets not in (1, 3):
        raise ValueError("素材最小数量只能为3，或原始文档配套演示的1")
    if not isinstance(assets, list) or not minimum_assets <= len(assets) <= 16:
        raise ValueError(f"已核验素材清单必须包含 {minimum_assets}–16 条素材")
    if not isinstance(sources, (list, tuple)):
        raise ValueError("素材来源必须是来源列表")
    source_urls = {s.get("url") for s in sources if isinstance(s, dict) and isinstance(s.get("url"), str)}
    prepared, identifiers, hashes, total = [], set(), set(), 0
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("素材记录必须是对象")
        identifier = asset.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier) or identifier in identifiers:
            raise ValueError("素材 id 缺失、重复或格式无效")
        identifiers.add(identifier)
        digest = asset.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("素材缺少有效的 SHA256 原文件指纹")
        if digest in hashes:
            raise ValueError("同一 SHA256 原文件不能重复计为独立素材")
        hashes.add(digest)
        if asset.get("kind") not in {"official_video", "schematic_animation"}:
            raise ValueError("本地已核验素材仅接受官方视频或明确标注的示意动画")
        if any(not isinstance(asset.get(k), str) or not asset[k].strip() for k in ("description", "credit")):
            raise ValueError("素材必须保留已审核描述和署名")
        reference = _reference_url(asset.get("reference_source_url"))
        if reference not in source_urls:
            raise ValueError("素材 reference_source_url 未出现在本条来源中")
        media_url = asset.get("url")
        if _url(media_url, media=True) != media_url or Path(urlsplit(media_url).path).suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError("素材必须记录确切的官方媒体文件 URL")
        chain = asset.get("source_chain")
        if (not isinstance(chain, list) or len(chain) < 2
                or chain[0] != reference or chain[-1] != media_url):
            raise ValueError("素材来源链必须从所引用原文开始并以确切媒体 URL 结束")
        for page in chain[1:-1]:
            _reference_url(page)
        if asset.get("source_url") not in chain[:-1]:
            raise ValueError("素材来源页面必须属于已记录的来源链")
        _windows(asset)
        path = _local_file(asset.get("path"), "素材")
        size = path.stat().st_size
        if not 0 < size <= MAX_FILE_BYTES:
            raise ValueError("单个原文件须非空且不超过 80MB")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("原文件总量不能超过 150MB")
        prepared.append({**asset, "path": str(path), "bytes": size})
    for asset in prepared:
        path = Path(asset["path"])
        digest, size = hashlib.sha256(), 0
        before = path.stat()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                size += len(chunk)
                if size > asset["bytes"]:
                    raise ValueError("原文件在核验时发生变化")
                digest.update(chunk)
        after = path.stat()
        if (size != asset["bytes"] or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ino != after.st_ino):
            raise ValueError("原文件在核验时发生变化")
        if digest.hexdigest() != asset["sha256"]:
            raise ValueError(f"原文件 SHA256 不匹配：{asset['id']}")
    return prepared
