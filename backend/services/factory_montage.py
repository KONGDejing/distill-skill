"""Edit attributed, moving demonstration footage around an approved narration.

The original voice and approved narration words remain unchanged; captions are
regrouped at scene and semantic boundaries. An inspectable edit list
records every source time window and the narration anchor that selected it.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
from services.factory_resources import ensure_memory_available, MemoryPressure

from services.factory_media import (
    FPS, HEIGHT, WIDTH, _ass_escape, _ass_time, _duration, _font, _probe, _run,
    _wrap, inspect_media,
)


def _seconds(value: str) -> float:
    hour, minute, second = value.strip().split(":")
    return int(hour) * 3600 + int(minute) * 60 + float(second)


def read_subtitle_cues(path: Path) -> list[dict]:
    cues = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].strip().split(",", 9)
        if len(fields) != 10 or fields[0] != "1":
            continue
        text = re.sub(r"\{[^}]*\}", "", fields[9]).replace(r"\N", " ")
        if not text.strip():
            continue
        cues.append({"start": _seconds(fields[1]), "end": _seconds(fields[2]), "text": text.strip()})
    if not cues:
        raise ValueError("混剪需要带时间戳的原始口播字幕")
    return cues


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", text.lower())


def _anchor_time(anchor: str, cues: list[dict]) -> float | None:
    needle = _compact(anchor)
    if len(needle) < 4:
        return None
    lengths = [len(_compact(c["text"])) for c in cues]
    position = "".join(_compact(c["text"]) for c in cues).find(needle)
    if position < 0:
        return None
    offset = 0
    for cue, length in zip(cues, lengths):
        if position < offset + length:
            fraction = (position - offset) / max(1, length)
            return cue["start"] + fraction * (cue["end"] - cue["start"])
        offset += length
    return None


def _source_label(asset: dict) -> str:
    if "背景" in str(asset.get("date_label", "")):
        date = re.search(r"\d{4}-\d{2}-\d{2}", asset["date_label"])
        return "历史背景 · " + ("发布于" + date.group() if date else "非本次试点")
    if asset.get("kind") == "paper_page":
        label = {'technical_report': '官方技术报告', 'official_document': '官方原文资料'}.get(asset.get('document_kind'), '论文原文')
        return f"{label} · 第{asset['paper_page']}页"
    if asset.get("asset_role") == "short_animated_preview_not_full_demo":
        return "官方预览片段 · 非完整演示"
    if asset.get("kind") == "schematic_animation":
        return "示意动画"
    tags = set(asset.get("topic_tags", []))
    if "ai_generated" in tags:
        return "官方样片 · AI生成"
    if "world_model" in tags:
        return "官方演示 · 交互世界"
    if "sim_to_real" in tags:
        return "官方演示 · 实拍 / 仿真对照"
    if "real_robot" in tags:
        return "官方演示 · 实拍"
    if "simulation" in tags:
        return "官方演示 · 仿真"
    return "官方演示"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def validate_assets(assets: list[dict]) -> list[dict]:
    prepared, ids = [], set()
    for raw in assets:
        asset = dict(raw)
        identifier = str(asset.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identifier) or identifier in ids:
            raise ValueError("素材 id 缺失、重复或格式无效")
        ids.add(identifier)
        path = Path(asset["path"]).resolve()
        if not path.is_file():
            raise ValueError(f"素材不存在：{identifier}")
        if asset.get("kind") == "paper_page":
            if (_sha256(path) != asset.get("page_sha256")
                    or _sha256(Path(asset["paper_path"])) != asset.get("paper_sha256")):
                raise ValueError("论文页面或原文改变，需重新提取原文画面")
            prepared.append({**asset, "sha256": asset["page_sha256"], "duration": 35,
                             "display_label": _source_label(asset)})
            continue
        if asset.get("kind") not in {"official_video", "schematic_animation"}:
            raise ValueError("只接受真实官方视频或明确标注的示意动画")
        if asset["kind"] == "official_video" and not str(asset.get("source_url", "")).startswith("https://"):
            raise ValueError("官方演示必须记录 HTTPS 来源页面")
        info = _probe(path)
        streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        length = float(info.get("format", {}).get("duration", 0))
        if not streams or length < 2 or path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise ValueError(f"素材不是可用动态视频：{identifier}")
        clips = []
        for clip in asset.get("clips") or [{"start": 0, "end": length}]:
            start, end = float(clip["start"]), float(clip["end"])
            if not (0 <= start < end <= length + 0.05) or end - start < 2:
                raise ValueError(f"素材时间窗无效：{identifier}")
            clips.append({**clip, "start": start, "end": min(end, length)})
        for region in asset.get("focus_regions", []):
            rect = region.get("rect", [])
            if len(rect) != 4 or not all(isinstance(n, (int, float)) for n in rect):
                raise ValueError("局部放大区域必须为归一化 x/y/width/height")
            x, y, w, h = rect
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.001 or y + h > 1.001:
                raise ValueError("局部放大区域超出原画面")
        prepared.append({**asset, "path": str(path), "duration": length, "clips": clips,
                         "sha256": _sha256(path), "display_label": _source_label(asset)})
    official = [a for a in prepared if a["kind"] == "official_video"]
    if len({a["sha256"] for a in official}) < 3 and len([a for a in prepared if a["kind"] == "paper_page"]) < 3:
        raise ValueError("科技演示混剪至少需要 3 个不同的官方视频素材")
    return prepared


def _scene_assets(scene: dict, assets: list[dict]) -> tuple[list[dict], str]:
    by_id = {a["id"]: a for a in assets}
    requested = list(scene.get("asset_ids") or [])
    if requested:
        missing = [a for a in requested if a not in by_id]
        if missing:
            raise ValueError(f"分镜引用未知素材：{', '.join(missing)}")
        return [by_id[a] for a in requested], "explicit_scene_asset_ids"
    text = str(scene.get("heading", "")) + str(scene.get("body", ""))
    rules = {"随机": {"domain_randomization", "generalization"},
             "泛化": {"domain_randomization", "generalization"},
             "数据": {"automatic_data", "parallel"},
             "翻转": {"flip", "rotate"}, "交互": {"physical_interaction"},
             "堆叠": {"stacking", "physical_interaction"}}
    desired = set().union(*(tags for word, tags in rules.items() if word in text))
    ranked = [(len(desired & set(a.get("topic_tags", []))), a) for a in assets]
    best = max((score for score, _ in ranked), default=0)
    selected = [a for score, a in ranked if score == best] if best else assets
    return selected, "topic_tag_heuristic_requires_editorial_review"


def _available_windows(asset: dict, occupied: dict) -> list[tuple[float, float]]:
    """Subtract used source time, including aliases and overlapping clip windows."""
    windows = []
    for clip in sorted(asset["clips"], key=lambda c: c["start"]):
        start = math.ceil(clip["start"] * FPS - 1e-6) / FPS
        end = math.floor(clip["end"] * FPS + 1e-6) / FPS
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(end, windows[-1][1]))
        elif end > start:
            windows.append((start, end))
    for used_start, used_end in occupied.get(asset["sha256"], []):
        remaining = []
        for start, end in windows:
            if used_end <= start or used_start >= end:
                remaining.append((start, end))
            else:
                if used_start > start:
                    remaining.append((start, used_start))
                if used_end < end:
                    remaining.append((used_end, end))
        windows = remaining
    return windows


def require_footage_budget(assets: list[dict], duration: float) -> dict:
    occupied, total = {}, 0.0
    for asset in assets:
        windows = _available_windows(asset, occupied)
        total += sum(end - start for start, end in windows)
        occupied.setdefault(asset["sha256"], []).extend(windows)
    if total + 1e-6 < math.ceil(duration * FPS) / FPS:
        raise ValueError(f"不重复素材不足：可用{total:.1f}秒，口播需要{duration:.1f}秒；请补充对应素材，禁止循环或重复取片段凑时长")
    return {"available_unique_seconds": round(total, 3), "required_seconds": duration}


def build_shot_plan(content: dict, cues: list[dict], assets: list[dict], duration: float) -> list[dict]:
    """Resolve explicit source selections, then cut on subtitle-based anchors."""
    scenes = [s for s in content.get("scenes", []) if isinstance(s, dict)]
    if not scenes:
        raise ValueError("科技演示需要带口播锚点的分镜计划")
    starts, timing = [0.0], ["opening_includes_hook"]
    for index, scene in enumerate(scenes[1:], 1):
        anchored = _anchor_time(str(scene.get("narration_exact", "")), cues)
        if anchored is None:
            raise ValueError(f"第 {index + 1} 个分镜的 narration_exact 未出现在字幕中")
        if anchored <= starts[-1] or anchored >= duration:
            raise ValueError("分镜口播锚点必须按原稿顺序且不能重叠")
        starts.append(anchored)
        timing.append("exact_text_match_within_cue_time_interpolation")
    require_footage_budget(assets, duration)
    plan, asset_uses, occupied = [], {}, {}
    for index, scene in enumerate(scenes):
        start, end = starts[index], starts[index + 1] if index + 1 < len(starts) else duration
        if end - start < 2:
            raise ValueError("分镜不足两秒，请与相邻分镜合并")
        choices, selection = _scene_assets(scene, assets)
        paper = any(a["kind"] == "paper_page" for a in choices)
        if paper and (len(choices) != 1 or end - start > 35):
            raise ValueError("论文页镜头只能选择一页原文且最多35秒，不能反复铺同一页")
        count = 1 if paper else max(1, math.ceil((end - start) / 4.5))
        if (end - start) / count < 2:
            count -= 1
        # Quantize every cut to a video frame, without altering the voice.
        cuts = [round((start + (end - start) * n / count) * FPS) / FPS for n in range(count + 1)]
        if index == 0:
            cuts[0] = 0.0
        if index == len(scenes) - 1:
            cuts[-1] = math.ceil(duration * FPS) / FPS
        for number in range(count):
            shot_duration = cuts[number + 1] - cuts[number]
            eligible = [a for a in choices if not (a["kind"] == "paper_page" and a["sha256"] in occupied)
                        and any(end - start + 1e-6 >= shot_duration for start, end in _available_windows(a, occupied))]
            if not eligible:
                raise ValueError(f"第{index + 1}段没有足够的不重复素材；请补素材或重排分镜，禁止重复片段")
            previous = plan[-1]["asset_id"] if plan else None
            candidates = [a for a in eligible if a["id"] != previous] or eligible
            asset = min(candidates, key=lambda a: asset_uses.get(a["id"], 0))
            uses = asset_uses.get(asset["id"], 0)
            source_start = next(start for start, end in _available_windows(asset, occupied)
                                if end - start + 1e-6 >= shot_duration)
            occupied.setdefault(asset["sha256"], []).append((source_start, source_start + shot_duration))
            regions = asset.get("focus_regions", [])
            region = regions[uses % len(regions)] if regions else None
            highlights = []
            for highlight in scene.get("paper_highlights", []):
                anchored = _anchor_time(highlight.get("narration_exact", ""), cues)
                if anchored is None or not start <= anchored < end:
                    raise ValueError("论文高亮的口播锚点不在当前讲解段落中")
                highlights.append({"text": highlight["text"], "start": max(0, anchored - cuts[number])})
            plan.append({"shot_index": len(plan), "scene_index": index, "asset_id": asset["id"],
                         "timeline_start": cuts[number], "timeline_end": cuts[number + 1],
                         "duration": shot_duration, "source_start": round(source_start, 6),
                         "source_end": round(source_start + shot_duration, 6),
                         "source_url": asset.get("source_url"), "source_sha256": asset["sha256"],
                         "kind": asset["kind"], "display_label": ("相关背景演示 · 非本事件实拍" if scene.get("evidence_role") == "background" else asset["display_label"]),
                         "asset_selection": selection, "narration_anchor": scene.get("narration_exact"),
                         "match_reason": scene.get("match_reason", ""),
                         "timing_basis": timing[index], "heading": str(scene.get("heading", "")),
                         "body": str(scene.get("body", "")),
                         "focus_region": region, "layout": "full_frame_and_detail" if region else "full_frame",
                         "paper_focus": scene.get("paper_focus", [0, 0, 1, 1]),
                         "paper_page": asset.get("paper_page"), "evidence_quote": scene.get("evidence_quote"),
                         "paper_highlights": highlights,
                         "complete_source_frame_preserved": True})
            asset_uses[asset["id"]] = uses + 1
    validate_shot_plan(plan, duration)
    return plan


def validate_shot_plan(plan: list[dict], duration: float) -> dict:
    if not plan or abs(plan[0]["timeline_start"]) > 1 / FPS:
        raise ValueError("镜头清单必须从零开始")
    for previous, current in zip(plan, plan[1:]):
        if abs(previous["timeline_end"] - current["timeline_start"]) > 1 / FPS:
            raise ValueError("镜头清单存在空隙或重叠")
    if abs(plan[-1]["timeline_end"] - duration) > 2 / FPS:
        raise ValueError("镜头清单没有覆盖完整口播")
    if any(s["duration"] < 2 - 1 / FPS or s["duration"] > (35 if s["kind"] == "paper_page" else 6) + 1 / FPS for s in plan):
        raise ValueError("镜头时长必须在 2–6 秒之间")
    official_duration = sum(s["duration"] for s in plan if s["kind"] == "official_video")
    distinct = len({s["source_sha256"] for s in plan if s["kind"] == "official_video"})
    coverage = min(1.0, official_duration / duration)
    paper_shots = [s for s in plan if s["kind"] == "paper_page"]
    if paper_shots:
        if (len({s["source_sha256"] for s in paper_shots}) != len(paper_shots)
                or len(paper_shots) < 3 or plan[0].get("paper_page") != 1):
            raise ValueError("论文画面需从标题作者页开始，至少三页原文且不能重复回放同页")
    elif coverage < 0.75 or distinct < 3:
        raise ValueError("成片必须至少 75% 官方动态素材并使用 3 个不同素材")
    used = {}
    for shot in plan:
        start, end = shot.get("source_start"), shot.get("source_end")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end) or start < 0
                or abs(end - start - shot["duration"]) > 1e-5
                or abs(shot["timeline_end"] - shot["timeline_start"] - shot["duration"]) > 1e-5):
            raise ValueError("镜头必须记录与成片时长一致的真实源时间窗，不能延长或冻结补帧")
        key = shot["source_sha256"]
        for old_start, old_end in used.get(key, []):
            if min(end, old_end) - max(start, old_start) > 1e-5:
                raise ValueError("检测到重复源画面：同一素材的时间窗重叠，裁切或放大也不能重复使用")
        used.setdefault(key, []).append((start, end))
    return {"dynamic_coverage": round(coverage, 4), "distinct_asset_count": distinct,
            "source_reuse_seconds": 0, "source_uniqueness_passed": True,
            "shot_count": len(plan), "shortest_shot": round(min(s["duration"] for s in plan), 3),
            "longest_shot": round(max(s["duration"] for s in plan), 3)}


def measure_motion(path: Path, start: float, duration: float) -> dict:
    import numpy as np
    result = subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-ss", str(start),
                             "-t", str(duration), "-i", str(path), "-an", "-vf", "fps=3,scale=96:54",
                             "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("动态素材抽样解码失败")
    frames = np.frombuffer(result.stdout, dtype=np.uint8)
    frames = frames[:len(frames) // (96 * 54) * (96 * 54)].reshape((-1, 54, 96))
    if len(frames) < 3:
        raise ValueError("素材没有足够的动态视频帧")
    changes = np.abs(np.diff(frames.astype(np.float32), axis=0)).mean(axis=(1, 2))
    score = float(changes.mean())
    if score < 0.12:
        raise ValueError("所选素材窗口近乎静止，不能当作动态演示")
    return {"sampled_frames": len(frames), "mean_frame_difference": round(score, 4),
            "method": "decoded_96x54_grayscale_at_3fps; motion_test_not_semantic_proof"}


def _check_repeated_samples(frames, positions, sample_fps=4) -> dict:
    """Find sustained repeated source sequences, not ordinary adjacent video frames."""
    import numpy as np
    frames = np.asarray(frames, dtype=np.float32)
    length = int(sample_fps * 1.5)
    thumb = frames.reshape((-1, 9, 6, 16, 6)).mean(axis=(2, 4)).reshape((-1, 144))
    for index in range(sample_fps * 2, len(frames) - length + 1):
        candidates = np.flatnonzero(np.abs(thumb[:index - sample_fps * 2 + 1] - thumb[index]).mean(axis=1) < 0.8)
        for earlier in candidates:
            difference = float(np.abs(frames[earlier:earlier + length] - frames[index:index + length]).mean())
            if difference < 0.8:
                raise ValueError(f"检测到疑似重复画面序列：镜头{positions[earlier][0] + 1}与镜头{positions[index][0] + 1}，"
                                 "请换用不重复的动作片段并复核；换文件名或转码不能视为新素材")
    return {"passed": True, "sampled_frames": len(frames), "sample_fps": sample_fps,
            "minimum_sequence_seconds": 1.5,
            "method": "source_pixels_96x54_sequence_comparison; sampled_check_not_semantic_guarantee"}


def check_visual_repeats(plan, sources):
    import numpy as np
    frames, positions = [], []
    for shot in plan:
        if shot["kind"] == "paper_page":
            continue  # A held original document is not looping action footage.
        ensure_memory_available()
        result = subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-ss", str(shot["source_start"]),
            "-t", str(shot["duration"]), "-i", sources[shot["asset_id"]]["path"], "-an",
            "-vf", "fps=4,scale=96:54", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60)
        if result.returncode or len(result.stdout) // (96 * 54) < math.floor(shot["duration"] * 4) - 1:
            raise ValueError("素材解码帧不足，不能冻结尾帧填充")
        samples = np.frombuffer(result.stdout, dtype=np.uint8).reshape((-1, 54, 96))
        frames.extend(samples)
        positions.extend((shot["shot_index"], n / 4) for n in range(len(samples)))
    if not frames:
        return {"passed": True, "sampled_frames": 0, "method": "no_action_footage; original_pages_checked_by_hash"}
    return _check_repeated_samples(frames, positions)


def validate_publication_media(media):
    """New submissions must reference exactly the files that passed the checks."""
    if media.get("renderer_kind", media.get("style")) != "tech_montage":
        return
    quality = media.get("quality", {})
    evidence = quality.get("uniqueness_evidence", {})
    if (media.get("renderer_version", 0) < 4 or evidence.get("version") != 1
            or quality.get("source_uniqueness_passed") is not True
            or quality.get("visual_repeat_check", {}).get("passed") is not True):
        raise ValueError("暂缓提交：成片未通过不重复画面检查，请按新标准重新剪辑")
    try:
        video, edl = Path(media["video_path"]), Path(media["edl_path"])
        if _sha256(video) != evidence["video_sha256"] or _sha256(edl) != evidence["edl_sha256"]:
            raise ValueError("成片或剪辑记录已变化，需要重新验收")
        validate_shot_plan(json.loads(edl.read_text()), media["duration"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("暂缓提交：不重复画面验收证据无效：" + str(exc)) from exc


def _render_shot(asset: dict, shot: dict, target: Path):
    if asset["kind"] == "paper_page":
        return _render_paper_shot(asset, shot, target)
    # Keep the complete source frame visible. Portrait phone demonstrations use
    # the available height; wide footage may add a labelled detail view below.
    region = shot.get("focus_region")
    split = 3 if region else 2
    graph = [f"[0:v]fps={FPS},setpts=PTS-STARTPTS,split={split}[b][f]" + ("[d]" if region else ""),
             "[b]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=14:2,scale=1080:1920,eq=brightness=-0.30:saturation=0.65[bg]",
             f"[f]scale=1024:{690 if region else 1280}:force_original_aspect_ratio=decrease,setsar=1[full]"]
    if region:
        x, y, w, h = region["rect"]
        graph.extend([f"[d]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=1024:650:force_original_aspect_ratio=decrease,setsar=1[detail]",
                      "[bg][full]overlay=(W-w)/2:246+(690-h)/2[top]",
                      "[top][detail]overlay=(W-w)/2:935+(650-h)/2[composed]"])
    else:
        graph.append("[bg][full]overlay=(W-w)/2:270+(1280-h)/2[composed]")
    graph.append("[composed]drawbox=x=0:y=1590:w=iw:h=330:color=black@0.48:t=fill,format=yuv420p[out]")
    command = ["ffmpeg", "-y", "-v", "error", "-threads", "2", "-filter_complex_threads", "2",
               "-ss", str(shot["source_start"]), "-i", asset["path"], "-filter_complex", ";".join(graph),
               "-map", "[out]", "-an", "-t", f"{shot['duration']:.6f}", "-r", str(FPS),
               "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "20", str(target)]
    _run(command, timeout=240)


def _render_paper_shot(asset, shot, target):
    """Show a real PDF crop with its page context; no invented figures or text."""
    from PIL import Image, ImageDraw, ImageOps
    page = Image.open(asset["path"]).convert("RGB")
    x, y, w, h = shot["paper_focus"]
    crop = page.crop((round(x * page.width), round(y * page.height),
                      round((x + w) * page.width), round((y + h) * page.height)))
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (13, 23, 36))
    detail = ImageOps.contain(crop, (1008, 1070))
    canvas.paste(detail, ((WIDTH - detail.width) // 2, 450 + (1070 - detail.height) // 2))
    context = ImageOps.contain(page, (190, 260))
    canvas.paste(context, (56, 175))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((56 + x * context.width, 175 + y * context.height,
                    56 + (x + w) * context.width, 175 + (y + h) * context.height), outline=(80, 220, 180), width=4)
    draw.text((280, 205), "原文定位", font=_font(33, True), fill=(110, 230, 190))
    draw.text((280, 263), f"第 {asset['paper_page']} 页", font=_font(46, True), fill="white")
    for line, text in enumerate(_wrap(shot.get("body", ""), _font(30, True), 960)[:2]):
        draw.text((60, 1540 + line * 37), text, font=_font(30, True), fill=(215, 235, 230))
    still = target.with_suffix(".png")
    canvas.save(still)
    # Hold the authentic crop for reading; highlights appear with the narration.
    frame_count = math.ceil(shot["duration"] * FPS)
    filters = [f"zoompan=z=1:x=0:y=0:d={frame_count}:s=1080x1920:fps={FPS}"]
    from services.factory_papers import highlight_rectangles
    offset_x, offset_y = (WIDTH - detail.width) / 2, 450 + (1070 - detail.height) / 2
    for highlight in shot.get("paper_highlights", []):
        for hx, hy, hw, hh in highlight_rectangles(asset, highlight["text"], shot["paper_focus"]):
            # Only mark text inside the actual displayed crop.
            left, top = max(x, hx), max(y, hy)
            right, bottom = min(x + w, hx + hw), min(y + h, hy + hh)
            if right <= left or bottom <= top:
                raise ValueError("高亮原文位于裁切区域之外，请调整原文取景")
            bx, by = offset_x + (left - x) / w * detail.width, offset_y + (top - y) / h * detail.height
            bw, bh = (right - left) / w * detail.width, (bottom - top) / h * detail.height
            enable = f"gte(t,{highlight['start']:.3f})"
            filters.append(f"drawbox=x={bx:.1f}:y={by:.1f}:w={max(3,bw):.1f}:h={max(3,bh):.1f}:color=yellow@0.25:t=fill:enable='{enable}'")
            filters.append(f"drawbox=x={bx-2:.1f}:y={by-2:.1f}:w={max(3,bw)+4:.1f}:h={max(3,bh)+4:.1f}:color=0xE5BE42:t=2:enable='{enable}'")
    filters.append("format=yuv420p")
    _run(["ffmpeg", "-y", "-v", "error", "-threads", "1", "-filter_threads", "1", "-i", str(still),
          "-vf", ",".join(filters),
          "-an", "-t", str(shot["duration"]), "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "20", str(target)], timeout=240)


SUBTITLE_ALGORITHM_VERSION = 2
SUBTITLE_TIMING_METHOD = "approved_script_punctuation_with_original_cue_character_interpolation_and_scene_clipping; not_word_level_ASR"


def _dangling_negation(text: str) -> bool:
    """Do not leave an incomplete negative predicate on its own screen."""
    return bool(re.search(r"(?:不意味着|不代表|不能证明|不能说明|不证明|不等于|并非|不是|不应该|不能|没有)$", _compact(text)))


def _subtitle_text_spans(text: str) -> list[tuple[int, int]]:
    """Group complete punctuation clauses; never cut an unpunctuated predicate.

    Long indivisible clauses may wrap across more lines. Arbitrarily truncating
    them to a character budget could remove a qualification or negative prefix.
    This deterministic rule is not a semantic parser.
    """
    pairs = {"“": "”", "‘": "’", "（": "）", "(": ")", "[": "]", "【": "】"}
    stack, pieces, begin = [], [], 0
    strong = "。！？!?；;"
    for index, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char == '"':
            if stack and stack[-1] == '"':
                stack.pop()
            else:
                stack.append('"')
        sentence_period = char == "." and (index + 1 == len(text) or text[index + 1].isspace())
        if not stack and (char in strong + "，,：:" or sentence_period):
            pieces.append((begin, index + 1, char in strong or sentence_period))
            begin = index + 1
    if begin < len(text):
        pieces.append((begin, len(text), True))
    spans, begin, end = [], None, None
    for left, right, sentence_end in pieces:
        if begin is not None:
            previous = text[begin:end]
            if (len(_compact(text[begin:right])) > 28 and len(_compact(previous)) >= 6
                    and not _dangling_negation(previous)):
                spans.append((begin, end))
                begin = None
        if begin is None:
            begin = left
        end = right
        if sentence_end and not _dangling_negation(text[begin:end]):
            spans.append((begin, end))
            begin = end = None
    if begin is not None:
        if _dangling_negation(text[begin:end]):
            raise ValueError("分镜在否定前缀后截断，请保留完整受否定短语")
        spans.append((begin, end))
    return [(left, right) for left, right in spans if _compact(text[left:right])]


def _retimed_subtitle_cues(cues: list[dict], plan: list[dict], content: dict) -> list[dict]:
    """Restore approved punctuation and estimate unit times inside original cues.

    No speech is generated or recognized. Character-proportional interpolation
    inherits the original cue timing errors and does not claim word-level ASR.
    Actual frame-quantized scene boundaries take precedence for display timing.
    """
    scenes = content.get("scenes") or []
    script = content.get("script") or "".join(str(s.get("narration_exact", "")) for s in scenes)
    if not script:
        raise ValueError("字幕重组需要已审核口播原稿，不能猜测标点或补造文案")
    spoken = _compact(script)
    if not spoken or spoken != "".join(_compact(c["text"]) for c in cues):
        raise ValueError("原字幕文字与已审核口播不一致，不能自动重组")
    times, previous_end = [], 0.0
    for cue in cues:
        start, end = float(cue["start"]), float(cue["end"])
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end) or start < previous_end - 1e-6:
            raise ValueError("原字幕时间必须有限、有序且不重叠")
        previous_end = end
        length = len(_compact(cue["text"]))
        times.extend((start + n * (end - start) / length, start + (n + 1) * (end - start) / length)
                     for n in range(length))
    locations = [index for index, char in enumerate(script) if _compact(char)]
    if len(locations) != len(spoken):
        raise ValueError("口播字符无法按原字幕位置可靠映射")
    scene_ranges = []
    for shot in plan:
        index = shot["scene_index"]
        if scene_ranges and scene_ranges[-1]["index"] == index:
            scene_ranges[-1]["end"] = shot["timeline_end"]
        else:
            anchor = (scenes[index].get("narration_exact") if 0 <= index < len(scenes) else None) or shot.get("narration_anchor", "")
            scene_ranges.append({"index": index, "start": shot["timeline_start"], "end": shot["timeline_end"], "anchor": anchor})
    if not scene_ranges:
        raise ValueError("字幕重组需要已审核分镜时序")
    scene_ranges[0]["position"] = 0
    for before, scene in zip(scene_ranges, scene_ranges[1:]):
        needle = _compact(scene["anchor"])
        position = spoken.find(needle, before["position"] + 1) if needle else -1
        if position < 0:
            raise ValueError("分镜口播锚点未按顺序出现在已审核原稿中")
        scene["position"] = position
    follow = _compact(str(content.get("follow_prompt") or ""))
    follow_position = spoken.rfind(follow) if follow else -1
    result = []
    for number, scene in enumerate(scene_ranges):
        left = locations[scene["position"]] if number else 0
        next_position = scene_ranges[number + 1]["position"] if number + 1 < len(scene_ranges) else len(spoken)
        right = locations[next_position] if next_position < len(locations) else len(script)
        cuts = [left]
        if scene["position"] < follow_position < next_position:
            cuts.append(locations[follow_position])
        cuts.append(right)
        for first, last in zip(cuts, cuts[1:]):
            text = script[first:last]
            for local_start, local_end in _subtitle_text_spans(text):
                start_offset, end_offset = first + local_start, first + local_end
                token_start = len(_compact(script[:start_offset]))
                token_end = len(_compact(script[:end_offset]))
                # Round inward to ASS centiseconds so no future scene text can
                # leak before the video frame where that scene actually starts.
                start = math.ceil(max(times[token_start][0], scene["start"]) * 100 - 1e-7) / 100
                end = math.floor(min(times[token_end - 1][1], scene["end"]) * 100 + 1e-7) / 100
                if end <= start:
                    raise ValueError("字幕语义单位无法放入对应分镜时间，请复核原时标和分镜")
                result.append({"start": start, "end": end, "text": script[start_offset:end_offset].strip(),
                               "scene_index": scene["index"]})
    if "".join(_compact(c["text"]) for c in result) != spoken:
        raise ValueError("字幕重组未完整保留已审核口播")
    return result


def _montage_subtitles(cues: list[dict], plan: list[dict], assets: dict, content: dict, target: Path):
    display_cues = _retimed_subtitle_cues(cues, plan, content)
    lines = ["[Script Info]", f"; SubtitleAlgorithmVersion: {SUBTITLE_ALGORITHM_VERSION}",
             f"; SubtitleTiming: {SUBTITLE_TIMING_METHOD}", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             "Style: Narration,Noto Sans CJK SC,48,&H00FFFFFF,&H000000FF,&H00100B06,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,90,90,225,1",
             "Style: Credit,Noto Sans CJK SC,27,&H00F5F5F5,&H000000FF,&H00100B06,&H80000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
             "Style: Accent,Noto Sans CJK SC,38,&H00D1F0A0,&H000000FF,&H00100B06,&H80000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def add(layer, start, end, style, text):
        lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")

    for cue in display_cues:
        clean = cue["text"].strip()
        wrapped = _wrap(clean, _font(48, True), 880)
        add(1, cue["start"], cue["end"], "Narration", r"\N".join(_ass_escape(s) for s in wrapped))
    seen_scenes, annotation_intervals = set(), []
    for shot in plan:
        start, end = shot["timeline_start"], shot["timeline_end"]
        asset = assets[shot["asset_id"]]
        source = str(asset.get("credit") or asset.get("source_title") or asset["id"])
        source = source.removeprefix("画面来源：").replace("（官方演示）", "")
        add(2, start, end, "Credit", r"{\pos(54,112)}" + _ass_escape(source[:50]))
        add(2, start, end, "Credit", r"{\pos(54,155)\c&HCEF2A5&}" + _ass_escape(shot["display_label"]))
        if shot.get("focus_region"):
            add(2, start, end, "Credit", r"{\pos(54,894)\fs24}" + _ass_escape("局部放大 · " + str(shot["focus_region"].get("label", "动作细节"))))
        if shot["scene_index"] not in seen_scenes:
            seen_scenes.add(shot["scene_index"])
            until = end if shot["kind"] == "paper_page" else min(end, start + 1.3)
            annotation_intervals.append((start, until))
            heading = f"{shot['scene_index'] + 1:02d}  /  {shot['heading']}"
            position = r"{\pos(280,350)\fs32}" if shot["kind"] == "paper_page" else r"{\move(40,205,54,205,0,260)\fad(120,180)}"
            add(3, start, until, "Accent", position + _ass_escape(heading[:23]))
    channel = str(content.get("channel_name") or "AI 科技观察")
    add(2, 0, plan[-1]["timeline_end"], "Credit", r"{\pos(54,1824)\fs24\alpha&H45&}" + _ass_escape(channel))
    add(2, 0, plan[-1]["timeline_end"], "Credit", r"{\pos(872,1824)\fs24\alpha&H45&}AI 配音")
    target.write_text("\n".join(lines), encoding="utf-8")
    return {"annotation_intervals": annotation_intervals, "subtitle_quality": {
        "subtitle_algorithm_version": SUBTITLE_ALGORITHM_VERSION,
        "subtitle_timing": SUBTITLE_TIMING_METHOD,
        "subtitle_original_cue_count": len(cues), "subtitle_display_cue_count": len(display_cues),
        "subtitle_text_preserved": True, "subtitle_scene_boundaries_enforced": True,
        "subtitle_segmentation": "approved_sentence_and_clause_punctuation; incomplete_negation_prefix_join; separate_follow_prompt",
        "subtitle_timing_limit": "按原cue字符位置插值，继承原时标误差；不是逐字ASR或自动语义审核。"}}


def _ambient_music(duration: float, shot_times: list[float], target: Path):
    """Original synthesized tones, deliberately quiet beneath speech."""
    import numpy as np
    import soundfile as sf
    rate = 48000
    t = np.arange(math.ceil(duration * rate), dtype=np.float64) / rate
    envelope = np.minimum(1, t / 1.2) * np.minimum(1, np.maximum(0, duration - t) / 1.5)
    left = np.zeros_like(t)
    right = np.zeros_like(t)
    for index, frequency in enumerate([110.0, 164.8138, 220.0, 261.6256]):
        swell = 0.5 + 0.5 * np.sin(2 * np.pi * (0.035 + index * 0.007) * t + index)
        left += 0.0025 * swell * np.sin(2 * np.pi * frequency * t)
        right += 0.0025 * swell * np.sin(2 * np.pi * frequency * t + 0.13 * index)
    for cut in shot_times[1:]:
        dt = t - cut
        active = (dt >= 0) & (dt < 0.14)
        pulse = np.zeros_like(t)
        pulse[active] = 0.003 * np.exp(-35 * dt[active]) * np.sin(2 * np.pi * (850 * dt[active] - 1600 * dt[active] ** 2))
        left += pulse
        right += pulse
    sf.write(target, np.column_stack([left * envelope, right * envelope]).astype("float32"), rate)


def _browser_preview(video: Path) -> Path:
    """Keep the publication MP4 and provide playback in browsers without H.264."""
    _wait_for_render_memory()
    target = video.with_suffix(".webm")
    temporary = video.with_name("preview-building.webm")
    _run(["ffmpeg", "-y", "-v", "error", "-threads", "2", "-filter_threads", "2", "-i", str(video),
          "-vf", "scale=720:1280", "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
          "-row-mt", "1", "-threads", "2", "-crf", "36", "-b:v", "0", "-c:a", "libopus", "-b:a", "96k",
          str(temporary)], timeout=600)
    if abs(_duration(temporary) - _duration(video)) > .2:
        raise ValueError("网页预览版时长与成片不一致，停止交付")
    temporary.replace(target)
    return target


def _wait_for_render_memory(timeout=60):
    """Pause at a render boundary during short pressure spikes; keep all gates."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return ensure_memory_available()
        except MemoryPressure:
            if time.monotonic() >= deadline:
                raise
            print("机器资源繁忙，渲染等待内存恢复（未放宽内存阈值）", flush=True)
            time.sleep(2)


def render_tech_montage(item_id: str, content: dict, audio_path: Path, subtitle_path: Path,
                        assets: list[dict], output_dir: Path) -> dict:
    """Produce a new preview; never alter the source media, database or publish."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("无效内容编号")
    _wait_for_render_memory()
    audio_path, subtitle_path = Path(audio_path).resolve(), Path(subtitle_path).resolve()
    duration = _duration(audio_path)
    if not 10 <= duration <= 240:
        raise ValueError("混剪口播时长必须在 10–240 秒内")
    prepared = validate_assets(assets)
    paper_evidence = None
    if any(a["kind"] == "paper_page" for a in prepared):
        from services.factory_papers import validate_paper_scenes
        paper_evidence = validate_paper_scenes(content.get("scenes", []), prepared, content.get("document_evidence_sources"))
    cues = read_subtitle_cues(subtitle_path)
    plan = build_shot_plan(content, cues, prepared, duration)
    quality = validate_shot_plan(plan, duration)
    if paper_evidence:
        quality['document_evidence' if paper_evidence.get('document_kind') == 'official_document' else 'paper_evidence'] = paper_evidence
    target = Path(output_dir).resolve() / item_id
    target.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps({"content": content, "audio_sha": _sha256(audio_path),
        "subtitles_sha": _sha256(subtitle_path), "assets": prepared, "renderer": "tech_montage_v5_scene_subtitles"},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if (target / "media.json").exists():
        old = json.loads((target / "media.json").read_text())
        if old.get("fingerprint") == fingerprint:
            validate_publication_media(old)
            old["quality"] = {**old["quality"], **inspect_media(Path(old["video_path"]))}
            if not old.get("webm_path") or not Path(old["webm_path"]).is_file():
                old["webm_path"] = str(_browser_preview(Path(old["video_path"])))
                (target / "media.json").write_text(json.dumps(old, ensure_ascii=False, indent=2))
            return old
        raise ValueError("输出目录已包含另一版混剪，请为新版本选择新目录")
    sources = {a["id"]: a for a in prepared}
    used_ids = {s["asset_id"] for s in plan}
    motion = {}
    for shot in plan:
        key = str(shot["shot_index"])
        asset = sources[shot["asset_id"]]
        if asset["kind"] == "paper_page":
            continue
        # Do not silently move a failed window into footage already used elsewhere.
        motion[key] = measure_motion(Path(asset["path"]), shot["source_start"], shot["duration"])
    quality["visual_repeat_check"] = check_visual_repeats(plan, sources)
    (target / "edit-decision-list.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    (target / "visual-sources.json").write_text(json.dumps({"assets": [a for a in prepared if a["id"] in used_ids]}, ensure_ascii=False, indent=2))
    video, audio, subtitle = target / "video.mp4", target / "narration.mp3", target / "subtitles.ass"
    if audio_path != audio:
        shutil.copy2(audio_path, audio)
    subtitle_result = _montage_subtitles(cues, plan, sources, content, subtitle)
    annotations = subtitle_result["annotation_intervals"]
    quality.update(subtitle_result["subtitle_quality"])
    music = target / "original-ambient.wav"
    _wait_for_render_memory()
    _ambient_music(duration, [s["timeline_start"] for s in plan], music)
    soundtrack = target / "soundtrack.m4a"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-i", str(music), "-filter_complex",
          "[0:a]asplit=2[voice][control];[1:a][control]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=250[bed];[voice][bed]amix=inputs=2:normalize=0,alimiter=limit=0.89[out]",
          "-map", "[out]", "-t", str(duration), "-c:a", "aac", "-b:a", "192k", str(soundtrack)], timeout=90)
    with tempfile.TemporaryDirectory(prefix="montage-", dir=target) as temp:
        work = Path(temp)
        entries = []
        for shot in plan:
            _wait_for_render_memory()
            part = work / f"shot_{shot['shot_index']:03d}.mp4"
            _render_shot(sources[shot["asset_id"]], shot, part)
            entries.append(f"file '{part.name}'")
            print(f"Montage shot {shot['shot_index'] + 1}/{len(plan)}: {shot['asset_id']}", flush=True)
        (work / "shots.txt").write_text("\n".join(entries))
        shutil.copy2(subtitle, work / "subtitles.ass")
        _wait_for_render_memory()
        result = subprocess.run(["ffmpeg", "-y", "-v", "error", "-threads", "2", "-filter_threads", "2",
            "-f", "concat", "-safe", "0", "-i", str(work / "shots.txt"), "-i", str(soundtrack),
            "-vf", "subtitles=subtitles.ass", "-map", "0:v:0", "-map", "1:a:0", "-t", str(duration),
            "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "20", "-c:a", "copy",
            "-movflags", "+faststart", str(video)], cwd=work, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError(f"科技混剪合成失败：{result.stderr[-1400:]}")
    cover = target / "cover.png"
    from services.factory_cover import render_cover
    cover_design = render_cover(content, cover)
    quality.update(inspect_media(video))
    quality.update({"cover_design": cover_design, "editorial_review": content.get("editorial_review", {}),
                    "source_motion_samples": motion, "official_footage_ratio": quality["dynamic_coverage"],
                    "self_made_animation_ratio": round(sum(s["duration"] for s in plan if s["kind"] == "schematic_animation") / duration, 4),
                    "original_paper_ratio": round(sum(s["duration"] for s in plan if s["kind"] == "paper_page") / duration, 4),
                    "emphasis_annotation_ratio": round(sum(end - start for start, end in annotations) / duration, 4),
                    "complete_source_frames_preserved": True, "source_audio_unchanged_sha256": _sha256(audio),
                    "scene_timing": "exact_text_anchor_with_subtitle_time_interpolation; not_word_or_semantic_alignment",
                    "music": "original_synthesized_quiet_ambient_with_voice_sidechain_ducking",
                    "review_required": "Motion sampling does not verify scene semantics; inspect the edit list and preview."})
    result = {"video_path": str(video), "webm_path": None, "audio_path": str(audio), "cover_path": str(cover),
              "subtitle_path": str(subtitle), "mixed_audio_path": str(soundtrack), "duration": quality["duration"],
              "width": WIDTH, "height": HEIGHT, "renderer_kind": "tech_montage", "renderer_version": 4,
              "tts_engine": "existing_verified_narration_reused", "quality": quality,
              "edl_path": str(target / "edit-decision-list.json"),
              "visual_manifest_path": str(target / "visual-sources.json"),
              "visual_sources": [{key: a.get(key) for key in ("id", "source_title", "source_url", "credit", "rights_note", "license_url", "sha256")} for a in prepared if a["id"] in used_ids],
              "fingerprint": fingerprint, "published": False}
    quality["uniqueness_evidence"] = {"version": 1, "video_sha256": _sha256(video),
        "edl_sha256": _sha256(target / "edit-decision-list.json")}
    (target / "media.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    result["webm_path"] = str(_browser_preview(video))
    (target / "media.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
