"""Collect bounded, attributable official demonstration videos for a cited story.

Only HTTPS pages/media on maintained first-party paths are fetched. A story with
fewer than three distinct, moving video files is explicitly insufficient; still
images, generated placeholders and a successful HTTP response are not substitutes.
"""
from __future__ import annotations

import hashlib
import fcntl
import ipaddress
import json
import re
import socket
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

STORAGE = Path(__file__).resolve().parents[1] / "storage"
PAGE_RULES = {
    "deepmind.google": ("/models/veo/", "/models/genie/", "/models/gemini-robotics/"),
    "allenai.github.io": ("/ToyBlocks/",),
    "allenai.org": ("/blog/",),
    "huggingface.co": ("/blog/allenai/", "/blog/nvidia/", "/blog/phonon-tts/", "/allenai/", "/nvidia/"),
    "gradium.ai": ("/blog/release-phonon-open-weight/",),
    "developer.nvidia.com": ("/blog/",),
    "research.nvidia.com": ("/labs/cosmos-lab/",),
    "blogs.nvidia.com": ("/blog/",),
    "www.nvidia.com": ("/en-us/ai/cosmos/",),
    "openai.com": ("/index/",),
    "blog.google": ("/technology/ai/",),
}
MEDIA_RULES = {
    **PAGE_RULES,
    "gradium.ai": ("/assets/release-phonon-open-weight/demos/",),
    "raw.githubusercontent.com": ("/allenai/ToyBlocks/", "/nvidia/cosmos/"),
    "www.datocms-assets.com": ("/64837/",),
    "cdn-uploads.huggingface.co": ("/production/uploads/",),
    "developer-blogs.nvidia.com": ("/wp-content/uploads/",),
    "images.nvidia.com": ("/aem-dam/",),
    "cdn.openai.com": ("/",),
    "storage.googleapis.com": ("/gdm-deepmind-com-prod-public/media/", "/deepmind-media/"),
}
MAX_FILE_BYTES = 15_000_000
MAX_TOTAL_BYTES = 60_000_000
MAX_PAGE_BYTES = 1_500_000
MAX_PAGES = 6
MAX_DEPTH = 2
MAX_SECONDS = 180
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}

# Descriptions/time ranges checked against the official project page and actual
# downloaded frames, not inferred from model-generated text or a URL filename.
TOYBLOCKS = {
    "s2r_real": ("官方实体机械臂抓取、搬运并堆叠桌面积木。", ["robot", "real_robot", "stacking"], 1, 13),
    "dg_play": ("官方仿真机械臂旋转和翻转积木，寻找字母。", ["robot", "simulation", "flip", "rotate"], 0, 16),
    "dg_exec": ("16 个并行仿真环境分别执行积木堆叠任务。", ["simulation", "parallel", "automatic_data"], 0, 12),
    "dg_render": ("官方仿真中不同光照、背景、桌面与相机配置下的机器人动作。", ["simulation", "domain_randomization", "generalization"], 0, 16),
    "s2r_side": ("左侧真实机器人、右侧仿真；官方将仿真重定时以对齐抓取与放置动作。", ["robot", "simulation", "sim_to_real"], 1, 15),
    "dg_reason": ("机器人按计划移动挡路积木，右侧展示操作计划；官方原片为 3 倍速。", ["robot", "planning", "simulation"], 0, 12),
    "hero": ("47 个不同光照、背景、相机配置的仿真环境并行动作拼图。", ["simulation", "parallel", "domain_randomization"], 0, 12),
}

# Normalized x/y/width/height regions visually checked in the v2 montage.
# Keep the wide source as context and show these action details below it.
TOYBLOCKS_FOCUS = {
    "s2r_real": [
        {"rect": [0.18, 0.04, 0.65, 0.91], "label": "机械臂与积木"},
        {"rect": [0.25, 0.08, 0.55, 0.83], "label": "抓取与放置"},
        {"rect": [0.13, 0.09, 0.70, 0.87], "label": "桌面动作"},
    ],
    "dg_play": [
        {"rect": [0, 0, 0.5, 0.5], "label": "翻转积木视角"},
        {"rect": [0.5, 0.5, 0.5, 0.5], "label": "另一仿真视角"},
    ],
    "dg_exec": [
        {"rect": [0, 0, 0.25, 0.25], "label": "一个并行仿真环境"},
        {"rect": [0.25, 0.25, 0.25, 0.25], "label": "另一个并行仿真环境"},
    ],
    "dg_render": [
        {"rect": [0, 0, 1 / 3, 1 / 3], "label": "随机环境之一"},
        {"rect": [1 / 3, 1 / 3, 1 / 3, 1 / 3], "label": "另一光照与背景"},
        {"rect": [2 / 3, 2 / 3, 1 / 3, 1 / 3], "label": "另一相机与桌面"},
    ],
    "hero": [
        {"rect": [0.375, 1 / 6, 0.25, 1 / 3], "label": "随机环境 2×2 区域"},
        {"rect": [0.5, 0.5, 0.25, 1 / 3], "label": "另一组随机环境 2×2 区域"},
    ],
    "dg_reason": [
        {"rect": [0, 0.08, 0.75, 0.92], "label": "规划后的操作（原片 3 倍速）"},
    ],
}

# Official launch-page descriptions. Selected source windows are still decoded
# and visually inspected before a reviewed delivery is accepted.
PHONON = {
    "on-watch": ("Apple Watch 关闭 Wi-Fi 后逐步引导制作咖啡的官方演示。", ["on_device", "watch", "offline"]),
    "repcoach": ("iPhone 上离线健身计数与语音指导的官方演示。", ["on_device", "phone", "offline"]),
    "chrome-extension": ("Phonon 在浏览器扩展中朗读网页文本的官方演示。", ["on_device", "browser", "speech"]),
    "offline-fm": ("浏览器中的离线恐龙游戏，Phonon 为电台主持生成语音。", ["on_device", "browser", "voices"]),
    "nativ-translate-mobile": ("手机本地英法翻译演示：Qwen 翻译、Phonon 朗读、MLX 推理。", ["on_device", "phone", "languages"]),
    "nativ-translate-desktop": ("桌面本地英法翻译演示：Qwen 翻译、Phonon 朗读、MLX 推理。", ["on_device", "desktop", "languages"]),
}

# Verified operating moments, excluding the talking-head introduction and end
# screens. Keep full-frame provenance even when also displaying a close-up.
PHONON_CLIPS = {
    "on-watch": [(13.4, 34.8), (57, 66)],
    "repcoach": [(7, 48)],
    "chrome-extension": [(2, 30)],
}
PHONON_FOCUS = {
    "on-watch": [{"rect": [0.15, 0.08, 0.72, 0.88], "label": "手表离线操作"}],
    "chrome-extension": [{"rect": [0.07, 0.16, 0.86, 0.77], "label": "浏览器朗读界面"}],
}


def _url(value, *, media=False):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("素材 URL 无效")
    parts = urlsplit(value)
    rules = MEDIA_RULES if media else PAGE_RULES
    path = unquote(unquote(parts.path or "/"))
    try:
        allowed = (parts.scheme == "https" and parts.hostname in rules
                   and not parts.username and not parts.password and parts.port in (None, 443)
                   and not any(segment in {".", ".."} for segment in path.split("/"))
                   and "\\" not in path and "\x00" not in path
                   and any((path + "/").lower().startswith(prefix.lower())
                           for prefix in rules[parts.hostname]))
    except ValueError:
        allowed = False
    if not allowed:
        raise ValueError("素材 URL 不在官方域名与路径白名单中")
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _public_host(url):
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("素材域名解析到非公网地址")


def _transfer(client, url, *, media, max_bytes, deadline, budget, target=None):
    current = _url(url, media=media)
    history = [current]
    for _ in range(5):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("视觉素材采集超过总时间限制")
        _public_host(current)
        timeout = httpx.Timeout(min(12, remaining), connect=min(5, remaining))
        with client.stream("GET", current, timeout=timeout) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("素材重定向缺少地址")
                current = _url(urljoin(current, location), media=media)
                history.append(current)
                continue
            response.raise_for_status()
            if int(response.headers.get("content-length", "0")) > min(max_bytes, budget[0]):
                raise ValueError("素材超过下载体积限制")
            data = bytearray()
            count = 0
            file = target.open("wb") if target else None
            try:
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    budget[0] -= len(chunk)
                    if count > max_bytes or budget[0] < 0:
                        raise ValueError("素材超过下载体积限制")
                    if time.monotonic() > deadline:
                        raise TimeoutError("视觉素材采集超过总时间限制")
                    if file:
                        file.write(chunk)
                    else:
                        data.extend(chunk)
            finally:
                if file:
                    file.close()
            return bytes(data), current, history
    raise ValueError("素材重定向次数过多")


class _MediaParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.videos, self.links = base, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        keys = ("src", "data-src") if tag in {"video", "source"} else ("href",) if tag == "a" else ()
        for key in keys:
            if not attrs.get(key):
                continue
            address = urljoin(self.base, attrs[key])
            # Upgrade an explicit public project link to HTTPS; never send HTTP.
            parts = urlsplit(address)
            if parts.scheme == "http" and parts.hostname in PAGE_RULES:
                address = urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
            suffix = Path(urlsplit(address).path).suffix.lower()
            try:
                if suffix in VIDEO_SUFFIXES:
                    self.videos.append({"url": _url(address, media=True),
                                        "description": (attrs.get("aria-label") or attrs.get("title") or "")[:300]})
                elif tag == "a" and "/docs" not in urlsplit(address).path:
                    self.links.append(_url(address))
            except ValueError:
                continue


def _probe(path, deadline=None):
    def timeout():
        remaining = 15 if deadline is None else min(15, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("视觉素材核验超过总时间限制")
        return remaining
    command = ["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
               "-format_whitelist", "mov,matroska,webm", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,codec_name,r_frame_rate:format=duration",
               "-of", "json", str(path)]
    data = json.loads(subprocess.check_output(command, timeout=timeout(), stderr=subprocess.DEVNULL))
    stream = (data.get("streams") or [{}])[0]
    duration = float(data.get("format", {}).get("duration", 0))
    if not 3 <= duration <= 180 or stream.get("width", 0) < 160 or stream.get("height", 0) < 90:
        raise ValueError("文件不是可用的短视频素材")
    # Reject a still frame encoded as MP4. Four low-resolution samples bound the
    # decode cost while requiring measurable visual change in the opening scene.
    pixels = subprocess.check_output([
        "ffmpeg", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "mov,matroska,webm", "-i", str(path),
        "-vf", "fps=0.5,scale=32:32,format=gray", "-frames:v", "4", "-f", "rawvideo", "pipe:1",
    ], timeout=timeout(), stderr=subprocess.DEVNULL)
    frames = [pixels[i:i + 1024] for i in range(0, len(pixels), 1024) if len(pixels[i:i + 1024]) == 1024]
    motion = max((sum(abs(a - b) for a, b in zip(first, second)) / 1024
                  for first, second in zip(frames, frames[1:])), default=0)
    if motion < 0.8:
        raise ValueError("视频没有通过动态画面核验，不能以静帧替代")
    numerator, denominator = stream.get("r_frame_rate", "0/1").split("/")
    return {"duration": duration, "width": stream["width"], "height": stream["height"],
            "fps": round(float(numerator) / max(1, float(denominator)), 3),
            "motion_verified": True, "motion_score": round(motion, 3)}


def _metadata(candidate, metadata):
    from services.factory_visual_catalog import INSPECTED_ASSETS
    url = candidate["url"]
    name = Path(urlsplit(url).path).stem
    toyblocks = (urlsplit(url).hostname == "allenai.github.io"
                 and urlsplit(url).path.startswith("/ToyBlocks/media/"))
    description = candidate.get("description") or "已引用官方页面中的动态演示，具体动作需结合原页核验。"
    tags, start, end = [], 0, min(12, metadata["duration"])
    rights = "公开可访问的官方演示；未核实本视频的独立转载许可。需保留出处，不能将代码或模型许可等同于全部视频素材的许可。"
    credit = "画面来源：" + candidate["source_title"]
    license_url = None
    focus_regions = []
    if toyblocks:
        credit = "画面来源：Ai2 / ToyBlocks（官方演示）"
        license_url = "https://github.com/allenai/ToyBlocks/blob/main/LICENSE"
        rights = "视频位于 Ai2 官方 ToyBlocks 仓库 website/media，根仓库声明 Apache-2.0；未见视频单独声明。README 指明外部下载资产另有许可；保留 Ai2 出处与许可，不声称免版权。"
        if name in TOYBLOCKS:
            description, tags, start, end = TOYBLOCKS[name]
            end = min(end, metadata["duration"])
        focus_regions = [{"rect": list(region["rect"]), "label": region["label"]}
                         for region in TOYBLOCKS_FOCUS.get(name, [])]
    elif (urlsplit(url).hostname == "gradium.ai"
          and urlsplit(url).path.startswith("/assets/release-phonon-open-weight/demos/")):
        credit = "画面来源：Gradium / Phonon（官方演示）"
        if name in PHONON:
            description, tags = PHONON[name]
        end = min(60, metadata["duration"])
        focus_regions = [{"rect": list(region["rect"]), "label": region["label"]}
                         for region in PHONON_FOCUS.get(name, [])]
    clips = [{"start": start, "end": end, "description": description}]
    if urlsplit(url).hostname == "gradium.ai" and name in PHONON_CLIPS:
        clips = [{"start": first, "end": min(last, metadata["duration"]), "description": description}
                 for first, last in PHONON_CLIPS[name] if first + 2 < metadata["duration"]] or clips
    inspected = INSPECTED_ASSETS.get(hashlib.sha256(url.encode()).hexdigest()[:20])
    if inspected:
        description, tags = inspected["description"], inspected["topic_tags"]
        clips = [{"start": 0, "end": min(inspected["end"], metadata["duration"]), "description": description}]
        credit = "画面来源：Google DeepMind（官方演示）"
    return {"label": "官方演示", "kind": "official_video", "description": description,
            "topic_tags": tags, "credit": credit, "rights_note": rights, "license_url": license_url,
            "focus_regions": focus_regions,
            "clips": clips}


def library_summary():
    records = []
    for path in (STORAGE / "factory/asset-library").glob("*.json"):
        try:
            entry = json.loads(path.read_text())
            if Path(entry["path"]).is_file():
                records.append(entry)
        except (OSError, ValueError, KeyError):
            continue
    sources = {}
    for entry in records:
        for reference in entry.get("references", []):
            sources.setdefault(reference["url"], set()).add(entry["sha256"])
    return {"videos": len({r["sha256"] for r in records}), "source_pages": len(sources),
            "ready_source_pages": sum(len(hashes) >= 3 for hashes in sources.values()),
            "bytes": sum(r.get("bytes", 0) for r in records),
            "assets": [{"id": r["id"], "description": r.get("description", "待核对画面内容"),
                        "topic_tags": r.get("topic_tags", []), "clips": r.get("clips", []),
                        "source_url": r.get("source_url"), "rights_note": r.get("rights_note"),
                        "preview_url": "/storage/" + Path(r["path"]).resolve().relative_to(STORAGE.resolve()).as_posix(),
                        "usage_count": len(r.get("uses", [])), "last_used_at": r.get("last_used_at"),
                        "reviewed": bool(r.get("topic_tags"))} for r in records]}


def _archive_asset(asset):
    directory = STORAGE / "factory/asset-library"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "index.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        metadata_path = directory / (asset["id"] + ".json")
        old = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        target = directory / (asset["id"] + Path(asset["path"]).suffix)
        if not target.exists() or old.get("sha256") != asset["sha256"]:
            temporary = target.with_suffix(".tmp")
            shutil.copyfile(asset["path"], temporary)
            temporary.replace(target)
        references = {r["url"]: r for r in old.get("references", [])}
        references[asset["reference_source_url"]] = {"url": asset["reference_source_url"], "title": asset["source_title"]}
        entry = {**asset, "path": str(target), "references": list(references.values()),
                 "uses": old.get("uses", []), "last_used_at": old.get("last_used_at"),
                 "last_seen_at": datetime.now(timezone.utc).isoformat()}
        temporary = metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entry, ensure_ascii=False, indent=2))
        temporary.replace(metadata_path)


def record_asset_usage(item_id, media):
    """Retain the exact source windows used, for deliberate future reuse."""
    path = Path(media.get("edl_path", "")).resolve()
    if not path.is_relative_to(STORAGE.resolve()) or not path.is_file():
        return
    edl = json.loads(path.read_text())
    shots = edl if isinstance(edl, list) else edl.get("shots", [])
    directory = STORAGE / "factory/asset-library"
    with (directory / "index.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for identifier in {s["asset_id"] for s in shots}:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier):
                continue
            metadata_path = directory / f"{identifier}.json"
            if not metadata_path.is_file():
                continue
            entry = json.loads(metadata_path.read_text())
            uses = [u for u in entry.get("uses", []) if u.get("item_id") != item_id]
            used = {"item_id": item_id, "at": datetime.now(timezone.utc).isoformat(),
                    "windows": [{k: s.get(k) for k in ("source_start", "source_end", "narration_anchor", "match_reason")}
                                for s in shots if s["asset_id"] == identifier]}
            entry.update(uses=[*uses, used][-100:], last_used_at=used["at"])
            temp = metadata_path.with_suffix(".tmp")
            temp.write_text(json.dumps(entry, ensure_ascii=False, indent=2)); temp.replace(metadata_path)


def _reuse_archived(identifier, path):
    metadata_path = STORAGE / "factory/asset-library" / (identifier + ".json")
    try:
        metadata = json.loads(metadata_path.read_text())
        cached = Path(metadata["path"])
        if (cached.is_symlink() or not cached.resolve().is_relative_to(metadata_path.parent.resolve())
                or not cached.is_file() or cached.stat().st_size > MAX_FILE_BYTES):
            return
        if hashlib.sha256(cached.read_bytes()).hexdigest() != metadata["sha256"]:
            return
        shutil.copyfile(cached, path)
    except (OSError, ValueError, KeyError):
        return


def collect_visuals(sources: list[dict], output_dir: str | Path, limit: int = 6) -> dict:
    """Return status, assets, warnings and manifest_path; ready requires 3 clips.

    ``sources`` must be this script's already cited source objects (id/title/url),
    not a topical search query. Fetch at most six pages/two linked project hops,
    six videos, 15 MB per file, 60 MB total and 180 seconds of network work.
    Every asset retains its complete reference -> project -> media source chain.
    """
    if (not isinstance(sources, list) or not isinstance(limit, int)
            or isinstance(limit, bool) or not 3 <= limit <= 6):
        raise ValueError("sources 必须为数组，limit 必须为 3–6")
    directory = Path(output_dir).expanduser().resolve()
    if not directory.is_relative_to(STORAGE.resolve()):
        raise ValueError("视觉素材输出目录必须位于本工程 storage 内")
    directory.mkdir(parents=True, exist_ok=True)
    warnings, queue, candidates = [], [], []
    for source in sources[:6]:
        try:
            address = _url(source["url"])
            queue.append((address, source, [address], 0))
        except (TypeError, KeyError, ValueError):
            warnings.append("一个引用来源不在视觉素材官方页面白名单中，已跳过")
    deadline, budget = time.monotonic() + MAX_SECONDS, [MAX_TOTAL_BYTES]
    visited, media_seen = set(), set()
    assets, fingerprints = [], set()
    # A retained, attributed source clip remains usable when its original page
    # is temporarily unreachable. Match the exact cited page, never a keyword.
    referenced = {s.get("url") for s in sources}
    cache_bytes = 0
    for metadata_path in sorted((STORAGE / "factory/asset-library").glob("*.json")):
        if len(assets) >= limit:
            break
        try:
            entry = json.loads(metadata_path.read_text())
            if not any(r.get("url") in referenced for r in entry.get("references", [])):
                continue
            cached = Path(entry["path"]).resolve()
            if not cached.is_relative_to((STORAGE / "factory/asset-library").resolve()) or not cached.is_file():
                continue
            size = cached.stat().st_size
            if size > MAX_FILE_BYTES or cache_bytes + size > MAX_TOTAL_BYTES:
                continue
            digest = hashlib.sha256(cached.read_bytes()).hexdigest()
            if digest != entry.get("sha256") or digest in fingerprints or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", entry.get("id", "")):
                continue
            path = directory / (entry["id"] + cached.suffix)
            if path.resolve() != cached:
                shutil.copyfile(cached, path)
            assets.append({**entry, "path": str(path), "reuse_basis": "exact_cited_source_and_verified_hash"})
            fingerprints.add(digest); cache_bytes += size
        except (OSError, ValueError, KeyError):
            continue
    with httpx.Client(follow_redirects=False, headers={"User-Agent": "ShortVideoAIContentFactory/0.3 (official-video-reference)"}) as client:
        while queue and len(assets) < limit and len(visited) < MAX_PAGES and time.monotonic() < deadline:
            address, source, chain, depth = queue.pop(0)
            if address in visited:
                continue
            visited.add(address)
            try:
                html, final, redirects = _transfer(client, address, media=False, max_bytes=MAX_PAGE_BYTES,
                                        deadline=deadline, budget=budget)
                chain = chain + redirects[1:]
                parser = _MediaParser(final)
                parser.feed(html.decode("utf-8", errors="replace"))
                for video in parser.videos:
                    if (urlsplit(video["url"]).hostname == "gradium.ai"
                            and Path(urlsplit(video["url"]).path).stem not in PHONON):
                        continue
                    if video["url"] not in media_seen:
                        media_seen.add(video["url"])
                        candidates.append({**video, "source_id": source.get("id"),
                                           "source_title": source.get("title", "官方演示"), "source_url": final,
                                           "reference_source_url": source["url"], "source_chain": chain + [video["url"]]})
                if depth < MAX_DEPTH:
                    # First inspect explicitly linked project pages, before older
                    # news articles linked from the same post or navigation bar.
                    project_links = [link for link in parser.links
                                     if urlsplit(link).hostname in {"allenai.github.io", "research.nvidia.com", "gradium.ai"}
                                     and Path(urlsplit(link).path).suffix.lower() in {"", ".html"}]
                    links = sorted(set(project_links), key=lambda link: ("github.io" not in link,
                                                                        "/sim2real" not in link and "/datagen" not in link))
                    queue.extend((link, source, chain + [link], depth + 1) for link in links if link not in visited)
            except Exception as exc:
                warnings.append(f"页面 {address} 采集失败（{type(exc).__name__}）")
        priorities = {**{name: index for index, name in enumerate(TOYBLOCKS)},
                      **{name: index for index, name in enumerate(("chrome-extension", "offline-fm",
                          "nativ-translate-mobile", "nativ-translate-desktop", "repcoach", "on-watch"))}}
        candidates.sort(key=lambda candidate: priorities.get(Path(urlsplit(candidate["url"]).path).stem, 20)
                        if urlsplit(candidate["url"]).hostname in {"allenai.github.io", "gradium.ai"} else 30)
        for candidate in candidates[:18]:
            if len(assets) >= limit or time.monotonic() >= deadline or budget[0] <= 0:
                break
            identifier = hashlib.sha256(candidate["url"].encode()).hexdigest()[:20]
            suffix = Path(urlsplit(candidate["url"]).path).suffix.lower()
            path = directory / (identifier + suffix)
            temporary = directory / (identifier + "." + uuid.uuid4().hex + ".part")
            try:
                if path.is_symlink():
                    raise ValueError("素材缓存不能是符号链接")
                final = candidate["url"]
                redirects = [final]
                if not path.is_file():
                    _reuse_archived(identifier, path)
                if not path.is_file():
                    _, final, redirects = _transfer(client, candidate["url"], media=True, max_bytes=MAX_FILE_BYTES,
                                         deadline=deadline, budget=budget, target=temporary)
                    metadata = _probe(temporary, deadline)
                    temporary.replace(path)
                else:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        raise ValueError("素材缓存超过体积限制")
                    metadata = _probe(path, deadline)
                fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
                if fingerprint in fingerprints:
                    continue
                fingerprints.add(fingerprint)
                asset = {**candidate, **metadata, **_metadata(candidate, metadata), "id": identifier,
                         "path": str(path), "download_url": final, "bytes": path.stat().st_size,
                         "sha256": fingerprint, "published": False}
                asset["source_chain"] = asset["source_chain"] + redirects[1:]
                assets.append(asset)
                _archive_asset(asset)
            except Exception as exc:
                warnings.append(f"视频 {candidate['url']} 不可用（{type(exc).__name__}：{str(exc)[:120]}）")
            finally:
                temporary.unlink(missing_ok=True)
    ready = len(assets) >= 3
    if not ready:
        warnings.append(f"动态素材不足：仅 {len(assets)} 条通过核验，需要至少 3 条；未使用截图或卡片补足。")
    manifest = directory / "manifest.json"
    result = {"status": "ready" if ready else "insufficient", "assets": assets, "warnings": warnings,
              "manifest_path": str(manifest), "created_at": datetime.now(timezone.utc).isoformat()}
    temporary = directory / ("manifest." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    return result
