"""Edit attributed, moving demonstration footage around an approved narration.

The original voice and subtitle text remain unchanged. An inspectable edit list
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
from typing import Any
from services.factory_resources import ensure_memory_available

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
    if len({a["sha256"] for a in official}) < 3:
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
    plan, asset_uses = [], {}
    for index, scene in enumerate(scenes):
        start, end = starts[index], starts[index + 1] if index + 1 < len(starts) else duration
        if end - start < 2:
            raise ValueError("分镜不足两秒，请与相邻分镜合并")
        choices, selection = _scene_assets(scene, assets)
        count = max(1, math.ceil((end - start) / 4.5))
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
            eligible = [a for a in choices if any(c["end"] - c["start"] >= shot_duration for c in a["clips"])]
            if not eligible:
                raise ValueError("分镜所选素材没有足够长的可用时间窗")
            previous = plan[-1]["asset_id"] if plan else None
            candidates = [a for a in eligible if a["id"] != previous] or eligible
            asset = min(candidates, key=lambda a: asset_uses.get(a["id"], 0))
            uses = asset_uses.get(asset["id"], 0)
            windows = [c for c in asset["clips"] if c["end"] - c["start"] >= shot_duration]
            window = windows[uses % len(windows)]
            slack = max(0.0, window["end"] - window["start"] - shot_duration)
            # Revisit a different source moment on subsequent uses, never loop
            # a frozen endpoint to fill missing footage.
            source_start = window["start"] + slack * ((uses * 0.61803398875) % 1)
            regions = asset.get("focus_regions", [])
            region = regions[uses % len(regions)] if regions else None
            plan.append({"shot_index": len(plan), "scene_index": index, "asset_id": asset["id"],
                         "timeline_start": cuts[number], "timeline_end": cuts[number + 1],
                         "duration": shot_duration, "source_start": round(source_start, 6),
                         "source_end": round(source_start + shot_duration, 6),
                         "source_url": asset.get("source_url"), "source_sha256": asset["sha256"],
                         "kind": asset["kind"], "display_label": ("相关背景演示 · 非本事件实拍" if scene.get("evidence_role") == "background" else asset["display_label"]),
                         "asset_selection": selection, "narration_anchor": scene.get("narration_exact"),
                         "match_reason": scene.get("match_reason", ""),
                         "timing_basis": timing[index], "heading": str(scene.get("heading", "")),
                         "focus_region": region, "layout": "full_frame_and_detail" if region else "full_frame",
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
    if any(s["duration"] < 2 - 1 / FPS or s["duration"] > 6 + 1 / FPS for s in plan):
        raise ValueError("镜头时长必须在 2–6 秒之间")
    official_duration = sum(s["duration"] for s in plan if s["kind"] == "official_video")
    distinct = len({s["source_sha256"] for s in plan if s["kind"] == "official_video"})
    coverage = min(1.0, official_duration / duration)
    if coverage < 0.75 or distinct < 3:
        raise ValueError("成片必须至少 75% 官方动态素材并使用 3 个不同素材")
    return {"dynamic_coverage": round(coverage, 4), "distinct_asset_count": distinct,
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


def _render_shot(asset: dict, shot: dict, target: Path):
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


def _montage_subtitles(cues: list[dict], plan: list[dict], assets: dict, content: dict, target: Path):
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             "Style: Narration,Noto Sans CJK SC,48,&H00FFFFFF,&H000000FF,&H00100B06,&H80000000,1,0,0,0,100,100,0,0,1,2,0,2,90,90,225,1",
             "Style: Credit,Noto Sans CJK SC,27,&H00F5F5F5,&H000000FF,&H00100B06,&H80000000,0,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
             "Style: Accent,Noto Sans CJK SC,38,&H00D1F0A0,&H000000FF,&H00100B06,&H80000000,1,0,0,0,100,100,0,0,1,1,0,7,0,0,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def add(layer, start, end, style, text):
        lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")

    for cue in cues:
        clean = cue["text"].strip().rstrip("，,。！？!?；;：:、")
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
            until = min(end, start + 1.3)
            annotation_intervals.append((start, until))
            heading = f"{shot['scene_index'] + 1:02d}  /  {shot['heading']}"
            add(3, start, until, "Accent", r"{\move(40,205,54,205,0,260)\fad(120,180)}" + _ass_escape(heading[:28]))
    channel = str(content.get("channel_name") or "AI 科技观察")
    add(2, 0, plan[-1]["timeline_end"], "Credit", r"{\pos(54,1824)\fs24\alpha&H45&}" + _ass_escape(channel))
    add(2, 0, plan[-1]["timeline_end"], "Credit", r"{\pos(872,1824)\fs24\alpha&H45&}AI 配音")
    target.write_text("\n".join(lines), encoding="utf-8")
    return annotation_intervals


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


def render_tech_montage(item_id: str, content: dict, audio_path: Path, subtitle_path: Path,
                        assets: list[dict], output_dir: Path) -> dict:
    """Produce a new preview; never alter the source media, database or publish."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("无效内容编号")
    ensure_memory_available()
    audio_path, subtitle_path = Path(audio_path).resolve(), Path(subtitle_path).resolve()
    duration = _duration(audio_path)
    if not 10 <= duration <= 240:
        raise ValueError("混剪口播时长必须在 10–240 秒内")
    prepared = validate_assets(assets)
    cues = read_subtitle_cues(subtitle_path)
    plan = build_shot_plan(content, cues, prepared, duration)
    quality = validate_shot_plan(plan, duration)
    target = Path(output_dir).resolve() / item_id
    target.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps({"content": content, "audio_sha": _sha256(audio_path),
        "subtitles_sha": _sha256(subtitle_path), "assets": prepared, "renderer": "tech_montage_v3"},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if (target / "media.json").exists():
        old = json.loads((target / "media.json").read_text())
        if old.get("fingerprint") == fingerprint:
            old["quality"] = {**old["quality"], **inspect_media(Path(old["video_path"]))}
            return old
        raise ValueError("输出目录已包含另一版混剪，请为新版本选择新目录")
    sources = {a["id"]: a for a in prepared}
    used_ids = {s["asset_id"] for s in plan}
    motion = {}
    for shot in plan:
        key = str(shot["shot_index"])
        asset = sources[shot["asset_id"]]
        candidates = [shot["source_start"]]
        for window in asset["clips"]:
            slack = window["end"] - window["start"] - shot["duration"]
            if slack >= 0:
                candidates.extend(window["start"] + slack * fraction for fraction in (0.5, 1.0))
        failure = None
        for attempt, start in enumerate(candidates[:5]):
            try:
                motion[key] = measure_motion(Path(asset["path"]), start, shot["duration"])
                if attempt:
                    shot.update(source_start=round(start, 6), source_end=round(start + shot["duration"], 6),
                                motion_window_reselected=True)
                break
            except ValueError as exc:
                failure = exc
        else:
            raise ValueError(f"素材 {asset['id']} 的可用时间窗均未通过动态质检") from failure
    (target / "edit-decision-list.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    (target / "visual-sources.json").write_text(json.dumps({"assets": [a for a in prepared if a["id"] in used_ids]}, ensure_ascii=False, indent=2))
    video, audio, subtitle = target / "video.mp4", target / "narration.mp3", target / "subtitles.ass"
    if audio_path != audio:
        shutil.copy2(audio_path, audio)
    annotations = _montage_subtitles(cues, plan, sources, content, subtitle)
    music = target / "original-ambient.wav"
    ensure_memory_available()
    _ambient_music(duration, [s["timeline_start"] for s in plan], music)
    soundtrack = target / "soundtrack.m4a"
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-i", str(music), "-filter_complex",
          "[0:a]asplit=2[voice][control];[1:a][control]sidechaincompress=threshold=0.015:ratio=6:attack=20:release=250[bed];[voice][bed]amix=inputs=2:normalize=0,alimiter=limit=0.89[out]",
          "-map", "[out]", "-t", str(duration), "-c:a", "aac", "-b:a", "192k", str(soundtrack)], timeout=90)
    with tempfile.TemporaryDirectory(prefix="montage-", dir=target) as temp:
        work = Path(temp)
        entries = []
        for shot in plan:
            ensure_memory_available()
            part = work / f"shot_{shot['shot_index']:03d}.mp4"
            _render_shot(sources[shot["asset_id"]], shot, part)
            entries.append(f"file '{part.name}'")
            print(f"Montage shot {shot['shot_index'] + 1}/{len(plan)}: {shot['asset_id']}", flush=True)
        (work / "shots.txt").write_text("\n".join(entries))
        shutil.copy2(subtitle, work / "subtitles.ass")
        ensure_memory_available()
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
                    "self_made_animation_ratio": round(1 - quality["dynamic_coverage"], 4),
                    "emphasis_annotation_ratio": round(sum(end - start for start, end in annotations) / duration, 4),
                    "complete_source_frames_preserved": True, "source_audio_unchanged_sha256": _sha256(audio),
                    "subtitle_timing": "original_approved_narration_cues_preserved",
                    "scene_timing": "exact_text_anchor_with_subtitle_time_interpolation; not_word_or_semantic_alignment",
                    "music": "original_synthesized_quiet_ambient_with_voice_sidechain_ducking",
                    "review_required": "Motion sampling does not verify scene semantics; inspect the edit list and preview."})
    result = {"video_path": str(video), "audio_path": str(audio), "cover_path": str(cover),
              "subtitle_path": str(subtitle), "mixed_audio_path": str(soundtrack), "duration": quality["duration"],
              "width": WIDTH, "height": HEIGHT, "renderer_kind": "tech_montage", "renderer_version": 3,
              "tts_engine": "existing_verified_narration_reused", "quality": quality,
              "edl_path": str(target / "edit-decision-list.json"),
              "visual_manifest_path": str(target / "visual-sources.json"),
              "visual_sources": [{key: a.get(key) for key in ("id", "source_title", "source_url", "credit", "rights_note", "license_url", "sha256")} for a in prepared if a["id"] in used_ids],
              "fingerprint": fingerprint, "published": False}
    (target / "media.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
