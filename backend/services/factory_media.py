"""Render source-backed AI explainers with real speech and inspectable media QA.

Edge provides word timestamps when reachable. A local CosyVoice installation can
provide a real, explicitly labelled fallback, using its shipped reference voice.
No silent or placeholder audio is considered a successful production result.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

WIDTH, HEIGHT, FPS = 1080, 1920, 30
BACKEND = Path(__file__).resolve().parents[1]
FONT_REGULAR = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
FONT_BOLD = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
DISPLAY_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+/_-]*(?:\s+[0-9]+(?:\.[0-9]+)*)?|\d+(?:\.\d+)*|\s+|.")
CHINESE_DIGITS = "零一二三四五六七八九"


def _run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} 执行失败：{result.stderr[-1600:]}")
    return result


def _probe(path: Path) -> dict:
    return json.loads(_run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], timeout=30).stdout)


def _duration(path: Path) -> float:
    return float(_probe(path).get("format", {}).get("duration", 0))


def _chinese_integer(value: int) -> str:
    """A deterministic cardinal reading, rather than the model's number guess."""
    if value == 0:
        return "零"
    if value >= 10000:
        base, unit = (100000000, "亿") if value >= 100000000 else (10000, "万")
        high, low = divmod(value, base)
        result = _chinese_integer(high) + unit
        if low:
            result += ("零" if low < base // 10 else "") + _chinese_integer(low)
        return result
    result, zero = "", False
    for divisor, unit in [(1000, "千"), (100, "百"), (10, "十"), (1, "")]:
        digit, value = divmod(value, divisor)
        if digit:
            if zero:
                result += "零"
            result += CHINESE_DIGITS[digit] + unit
            zero = False
        elif result and value:
            zero = True
    return result[1:] if result.startswith("一十") else result


def _number_reading(value: str) -> str:
    parts = value.split(".")
    # Keep leading zeroes and each decimal/version component explicit.
    first = "".join(CHINESE_DIGITS[int(c)] for c in parts[0]) if len(parts[0]) > 1 and parts[0][0] == "0" else _chinese_integer(int(parts[0]))
    return first + "".join("点" + "".join(CHINESE_DIGITS[int(c)] for c in p) for p in parts[1:])


def _speech_text(text: str) -> str:
    """Normalize narration only. Original spelling remains on screen/subtitles."""
    text = text.replace("**", "").strip()
    # ASCII word boundaries would miss 'NVIDIA刚' because Chinese is a word
    # character in Python regular expressions.
    text = re.sub(r"(?<![A-Za-z])NVIDIA(?![A-Za-z])", "英伟达", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z])AI(?![A-Za-z])", "人工智能", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])3D(?![A-Za-z0-9])", "三维", text, flags=re.I)
    # Keep byte units distinct from bits (Mb) and model suffixes distinct from
    # physical units such as m. Spell the Phonon identifier's M as a letter;
    # this makes no assumption about what its model number measures.
    text = re.sub(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*MB(?![A-Za-z])", r"\1兆字节", text)
    text = re.sub(r"(?<![A-Za-z0-9])(Phonon\s+\d+)m(?![A-Za-z0-9])", r"\1，字母 M", text, flags=re.I)
    text = re.sub(r"(?<!\d)\d{1,3}(?:,\d{3})+(?:\.\d+)?", lambda m: m[0].replace(",", ""), text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*[%％]", lambda m: "百分之" + _number_reading(m[1]), text)
    text = re.sub(r"(?<!\d)(\d{4})(?=年)", lambda m: "".join(CHINESE_DIGITS[int(c)] for c in m[1]), text)
    return re.sub(r"\d+(?:\.\d+)*", lambda m: _number_reading(m[0]), text)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.findall(r"[^。！？!?\n]+[。！？!?]?", text) if s.strip()]


def _speech_segments(display: str, spoken: str) -> list[dict]:
    """Bound long mixed-language specifications while preserving their text."""
    if len(display) <= 64 or not re.search(r"[A-Za-z]", display) or not re.search(r"\d", display):
        return [{"display": display, "spoken": spoken}]
    pieces = []
    for clause in re.findall(r"[^，,、；;。！？!?]+[，,、；;。！？!?]?", display):
        # An identifier followed immediately by a capacity is particularly
        # prone to stuttering. Keep its number separate from the capacity.
        fragments = re.split(r"(?=约\s*\d+(?:\.\d+)?\s*MB)", clause) if re.search(r"[A-Za-z]", clause) else [clause]
        pieces.extend(piece for piece in fragments if piece.strip())
    if len(pieces) > 12:
        pieces = pieces[:11] + ["".join(pieces[11:])]
    if len(pieces) < 2:
        return [{"display": display, "spoken": spoken}]
    return [{"display": piece, "spoken": _speech_text(piece).rstrip("，,、；;。！？!?") + "。"} for piece in pieces]


def _safe_break(text: str, limit: int) -> int:
    for token in DISPLAY_TOKEN.finditer(text):
        if token.start() < limit < token.end() and token.start() > 0:
            return token.start()
    return limit


def _chunks(text: str, limit: int = 25) -> list[str]:
    """Keep every character, including long English tokens and numbered lists."""
    pieces = re.findall(r"[^，,。！？!?；;：:]+[，,。！？!?；;：:]?", text)
    result: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        while len(piece) > limit:
            split = _safe_break(piece, limit)
            result.append(piece[:split])
            piece = piece[split:].lstrip()
        if piece:
            result.append(piece)
    return result


def _timed_chunks(text: str, start: float, end: float) -> list[dict]:
    chunks = _chunks(text)
    total = sum(len(c) for c in chunks) or 1
    result, cursor = [], start
    for index, chunk in enumerate(chunks):
        stop = end if index == len(chunks) - 1 else cursor + (end - start) * len(chunk) / total
        result.append({"text": chunk, "start": cursor, "end": stop})
        cursor = stop
    return result


async def _edge_speech(text: str, voice: str, audio: Path) -> list[dict]:
    import edge_tts

    kwargs: dict[str, Any] = {"rate": "+0%", "pitch": "+0Hz"}
    params = inspect.signature(edge_tts.Communicate).parameters
    if "boundary" in params:
        kwargs["boundary"] = "WordBoundary"
    if "connect_timeout" in params:
        kwargs.update(connect_timeout=10, receive_timeout=35)
    speech = edge_tts.Communicate(text, voice, **kwargs)
    words = []
    with audio.open("wb") as output:
        async for event in speech.stream():
            if event["type"] == "audio":
                output.write(event["data"])
            elif event["type"] in ("WordBoundary", "SentenceBoundary"):
                words.append({"text": event["text"], "start": event["offset"] / 10_000_000,
                              "end": (event["offset"] + event["duration"]) / 10_000_000})
    if audio.stat().st_size < 1000 or not words:
        raise RuntimeError("配音缺少有效音频或字幕时间戳")
    grouped, buffer = [], []
    for word in words:
        if buffer and len("".join(w["text"] for w in buffer)) + len(word["text"]) > 25:
            grouped.extend(_timed_chunks("".join(w["text"] for w in buffer), buffer[0]["start"], buffer[-1]["end"]))
            buffer = []
        buffer.append(word)
    if buffer:
        grouped.extend(_timed_chunks("".join(w["text"] for w in buffer), buffer[0]["start"], buffer[-1]["end"]))
    return grouped


def _cosyvoice_speech(text: str, audio: Path, work: Path, display_text: str | None = None) -> list[dict]:
    from services.factory_resources import ensure_memory_available
    ensure_memory_available()
    engine = Path(os.getenv("COSYVOICE_DIR", str(BACKEND / "engines" / "CosyVoice"))).resolve()
    python = Path(os.getenv("FACTORY_COSYVOICE_PYTHON", str(engine / ".venv" / "bin" / "python")))
    model = Path(os.getenv("COSYVOICE_MODEL_DIR", str(BACKEND / "storage" / "models" / "CosyVoice2-0.5B"))).resolve()
    if not python.exists() or not (model / "cosyvoice2.yaml").exists():
        raise RuntimeError("Edge-TTS 不可用，且未找到本地 CosyVoice Python 环境或模型")
    reference = engine / "asset" / "zero_shot_prompt.wav"
    if not reference.exists():
        raise RuntimeError("本地 CosyVoice 标准参考音色不存在")
    job = work / "speech_job.json"
    wav = work / "speech.wav"
    timing = work / "speech_timings.json"
    job.write_text(json.dumps({"text": text, "sentences": [{"display": s, "spoken": _speech_text(s)} for s in _sentences(display_text or text)],
                               "engine": str(engine), "model": str(model),
                               "reference": str(reference), "wav": str(wav), "timing": str(timing)},
                              ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    # The bundled environment may use a CUDA build newer than the host driver.
    env["CUDA_VISIBLE_DEVICES"] = os.getenv("FACTORY_COSYVOICE_CUDA_DEVICES", "")
    env["OMP_NUM_THREADS"] = "4"
    env["MKL_NUM_THREADS"] = "4"
    result = subprocess.run([str(python), str(Path(__file__).resolve()), "--cosyvoice-job", str(job)],
                            capture_output=True, text=True, timeout=1200, env=env)
    if result.returncode or not wav.exists() or not timing.exists():
        raise RuntimeError(f"CosyVoice 真实配音失败：{result.stderr[-1400:]}")
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af", "loudnorm=I=-18:TP=-2:LRA=11",
          "-c:a", "libmp3lame", "-q:a", "2", str(audio)], timeout=90)
    return json.loads(timing.read_text(encoding="utf-8"))


def _cosyvoice_job(job_path: str) -> None:
    """Run within the existing engine's isolated Python environment."""
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    engine = Path(job["engine"])
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(engine / "third_party" / "Matcha-TTS"))
    try:
        import huggingface_hub_compat  # Installed by the existing engine setup.
    except ImportError:
        pass
    import torch
    import soundfile as sf
    import torchaudio

    # Recent torchaudio versions ignore backend='soundfile' and require
    # TorchCodec. CosyVoice only reads PCM reference files here; keep this
    # compatibility adapter local to its isolated subprocess.
    def load_pcm(path, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, **kwargs):
        data, rate = sf.read(path, start=frame_offset, frames=num_frames, dtype="float32", always_2d=True)
        tensor = torch.from_numpy(data.T.copy() if channels_first else data.copy())
        return tensor, rate

    torchaudio.load = load_pcm
    # Factory narration is normalized above. The optional frontend otherwise
    # tries to download a ModelScope model during every cold start and can
    # reinterpret already-normalized numbers. Disable it only in this isolated
    # inference process; the user's CosyVoice installation is unchanged.
    sys.modules["wetext"] = None
    sys.modules["ttsfrd"] = None
    from cosyvoice.cli.cosyvoice import CosyVoice2

    torch.set_num_threads(4)
    model = CosyVoice2(job["model"], load_jit=False, load_trt=False, load_vllm=False, fp16=False)
    model.add_zero_shot_spk("希望你以后能够做的比我还好呦。", job["reference"], "factory_reference")
    sentences = job.get("sentences") or [{"display": s, "spoken": s} for s in _sentences(job["text"])]
    audio_parts, timings, cursor = [], [], 0.0
    for sentence_index, entry in enumerate(sentences):
        sentence = entry["spoken"].strip()
        if not sentence:
            continue
        fragments = _speech_segments(entry["display"], sentence)
        sentence_audio, sentence_cues, segment_cursor = [], [], cursor
        for segment_index, fragment in enumerate(fragments):
            parts = [part["tts_speech"].detach().cpu() for part in model.inference_zero_shot(
                fragment["spoken"], "", "", zero_shot_spk_id="factory_reference", stream=False, text_frontend=False,
            )]
            if not parts:
                raise RuntimeError("CosyVoice 未生成音频")
            segment_audio = torch.cat(parts, dim=1)
            segment_end = segment_cursor + segment_audio.shape[1] / model.sample_rate
            if segment_end - segment_cursor <= 0.1:
                raise RuntimeError("CosyVoice 生成的音频片段时长无效")
            for cue in _timed_chunks(fragment["display"], segment_cursor, segment_end):
                cue.update(speech_segment_index=segment_index, speech_segment_start=segment_cursor,
                           speech_segment_end=segment_end)
                sentence_cues.append(cue)
            sentence_audio.append(segment_audio)
            segment_cursor = segment_end
            if segment_index < len(fragments) - 1:
                gap = torch.zeros((1, int(model.sample_rate * 0.18)))
                sentence_audio.append(gap)
                segment_cursor += gap.shape[1] / model.sample_rate
        samples = torch.cat(sentence_audio, dim=1)
        duration = samples.shape[1] / model.sample_rate
        if duration <= 0.1:
            raise RuntimeError("CosyVoice 生成的音频时长无效")
        for cue in sentence_cues:
            cue.update(sentence_index=sentence_index, sentence_start=cursor,
                       sentence_end=cursor + duration, sentence_text=entry["display"])
            timings.append(cue)
        audio_parts.extend([samples, torch.zeros((1, int(model.sample_rate * 0.18)))])
        cursor += duration + 0.18
    if not audio_parts:
        raise RuntimeError("文案为空")
    # Leave no gratuitous trailing silence in the produced narration.
    sf.write(job["wav"], torch.cat(audio_parts[:-1], dim=1).squeeze().numpy(), model.sample_rate)
    Path(job["timing"]).write_text(json.dumps(timings, ensure_ascii=False), encoding="utf-8")


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    path = FONT_BOLD if bold else FONT_REGULAR
    if not path.exists():
        raise RuntimeError("缺少 Noto Sans CJK 中文字体，请安装 fonts-noto-cjk")
    return ImageFont.truetype(str(path), size)


def _match_text(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", _speech_text(text).lower())


def _match_score(description: str, sentence: str) -> float:
    left, right = _match_text(description), _match_text(sentence)
    grams = lambda s: {s[i:i + 2] for i in range(max(0, len(s) - 1))}
    a, b = grams(left), grams(right)
    score = len(a & b) / max(1, min(len(a), len(b)))
    # Product identifiers distinguish closely related chapters better than
    # common Chinese words such as 模型 and 数据.
    names = {m[0].lower() for m in re.finditer(r"[A-Za-z][A-Za-z0-9-]{2,}", description)}
    score += sum(0.3 for name in names if name in sentence.lower())
    return score


def _sentence_units(script: str, cues: list[dict], duration: float) -> tuple[list[dict], str]:
    measured = {}
    for cue in cues:
        if "sentence_index" in cue:
            measured[cue["sentence_index"]] = {"text": cue["sentence_text"], "start": cue["sentence_start"], "end": cue["sentence_end"]}
    if measured:
        return [measured[key] for key in sorted(measured)], cues[0].get("sentence_timing_basis", "measured_sentence_audio_boundaries")
    # Edge's available cues are word/phrase timestamps. Estimate the sentence
    # boundary within those cues by text position, and disclose the estimate.
    sentences = _sentences(script)
    weights = [max(1, len(_match_text(s))) for s in sentences]
    cue_weights = [max(1, len(_match_text(c["text"]))) for c in cues]
    total, cue_total = sum(weights) or 1, sum(cue_weights) or 1

    def time_at(fraction):
        position, cursor = fraction * cue_total, 0
        for cue, weight in zip(cues, cue_weights):
            if position <= cursor + weight:
                ratio = max(0, min(1, (position - cursor) / weight))
                return cue["start"] + ratio * (cue["end"] - cue["start"])
            cursor += weight
        return duration

    result, cursor = [], 0
    for sentence, weight in zip(sentences, weights):
        result.append({"text": sentence, "start": time_at(cursor / total), "end": time_at((cursor + weight) / total)})
        cursor += weight
    return result, "sentence_boundaries_estimated_from_speech_timestamps"


def _scene_timeline(scenes: list[dict], script: str, cues: list[dict], duration: float) -> tuple[list[dict], str]:
    """Choose ordered speech boundaries; similarity is never semantic proof."""
    units, basis = _sentence_units(script, cues, duration)
    if len(units) < len(scenes):
        units = [{"text": c["text"], "start": c["start"], "end": c["end"]} for c in cues]
        basis = "phrase_audio_timestamps_with_interpolation"
    if len(units) < len(scenes):
        # Very short narration cannot provide a distinct boundary per card.
        # Keep this rare fallback visible rather than claim exact alignment.
        return [{"scene_index": i, "start": duration * i / len(scenes), "end": duration * (i + 1) / len(scenes),
                 "method": "duration_estimate_insufficient_speech_units", "score": None}
                for i in range(len(scenes))], "duration_estimate_insufficient_speech_units"

    script_positions, offset = [], 0
    for unit in units:
        pos = script.find(unit["text"], offset)
        script_positions.append(pos)
        if pos >= 0:
            offset = pos + len(unit["text"])
    scores, anchors = [], []
    for scene in scenes:
        anchor = str(scene.get("narration_exact", "")).strip()
        anchor_pos = script.find(anchor) if len(anchor) >= 6 else -1
        anchor_unit = None
        if anchor_pos >= 0:
            for j, pos in enumerate(script_positions):
                if pos >= 0 and pos <= anchor_pos < pos + len(units[j]["text"]):
                    anchor_unit = j
                    break
        anchors.append(anchor_unit)
        scores.append([100 if j == anchor_unit else (-100 if anchor_unit is not None else _match_score(
            str(scene.get("heading", "")) + " " + str(scene.get("body", "")), unit["text"]))
            for j, unit in enumerate(units)])

    # A monotonic dynamic program prevents two cards selecting the same
    # sentence and prevents a later card jumping backwards in the narration.
    paths = {0: (scores[0][0], [0])}
    for i in range(1, len(scenes)):
        next_paths = {}
        for j in range(i, len(units) - (len(scenes) - i - 1)):
            eligible = [(score + scores[i][j], path + [j]) for k, (score, path) in paths.items() if k < j]
            if eligible:
                next_paths[j] = max(eligible, key=lambda pair: pair[0])
        paths = next_paths
    indices = max(paths.values(), key=lambda pair: pair[0])[1]
    starts = [0.0] + [float(units[j]["start"]) for j in indices[1:]]
    timeline = []
    for i, j in enumerate(indices):
        exact = anchors[i] == j
        timeline.append({"scene_index": i, "start": starts[i], "end": starts[i + 1] if i + 1 < len(starts) else duration,
                         "matched_speech_text": units[j]["text"], "speech_unit_index": j,
                         "method": "exact_text_anchor" if exact else "lexical_match_heuristic",
                         "score": 1.0 if exact else round(scores[i][j], 3)})
    return timeline, basis + "; scene_assignment_is_text_matching_not_semantic_verification"


def _wrap(text: str, font, width: int, max_lines: int | None = None) -> list[str]:
    lines, line = [], ""
    for token in DISPLAY_TOKEN.findall(str(text)):
        if "\n" in token:
            lines.append(line)
            line = ""
        elif line and font.getlength(line + token) > width:
            lines.append(line.rstrip())
            line = token.lstrip()
        else:
            line += token
        # An unusually long token still has to fit; normal product names stay
        # whole, including a following version such as 'Cosmos 3'.
        while font.getlength(line) > width:
            cut = 1
            while cut < len(line) and font.getlength(line[:cut + 1]) <= width:
                cut += 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        while font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _text(draw, xy: tuple[int, int], text: str, size: int, fill: str, width: int,
          bold: bool = False, max_lines: int | None = None, spacing: int = 16):
    font, (x, y) = _font(size, bold), xy
    for line in _wrap(text, font, width, max_lines):
        draw.text((x, y), line, font=font, fill=fill)
        y += size + spacing
    return y


def _draw_card(content: dict, scene: dict, index: int, total: int, path: Path, cover: bool = False):
    from PIL import Image, ImageDraw
    import numpy as np

    y = np.linspace(0, 1, HEIGHT)[:, None, None]
    top = np.array([9, 17, 32], dtype=float)[None, None, :]
    bottom = np.array([15, 38, 54], dtype=float)[None, None, :]
    gradient = np.broadcast_to(top * (1 - y) + bottom * y, (HEIGHT, WIDTH, 3)).astype("uint8")
    im = Image.fromarray(gradient, "RGB")
    draw = ImageDraw.Draw(im, "RGBA")
    # A quiet circuit grid frames the content instead of competing with it.
    for x in range(0, WIDTH, 90):
        draw.line((x, 0, x, HEIGHT), fill=(91, 172, 190, 15), width=1)
    for yy in range(0, HEIGHT, 90):
        draw.line((0, yy, WIDTH, yy), fill=(91, 172, 190, 15), width=1)
    draw.rounded_rectangle((72, 110, 338, 165), radius=27, fill=(49, 216, 184, 30), outline=(76, 225, 190, 90), width=1)
    _text(draw, (97, 117), "AI 科技观察", 28, "#72efd1", 250, bold=True)
    _text(draw, (766, 120), "AI / TECH", 26, "#83a5b8", 240)

    title = str(content.get("title", "人工智能新观察"))
    # Put a leading product/version on its own line if a long mixed-language
    # title would otherwise split the following Chinese phrase awkwardly.
    prefix = re.match(r"^([A-Za-z][A-Za-z0-9 ._-]*\d)\s*([\u4e00-\u9fff].+)$", title)
    if prefix and _font(59, True).getlength(title) > 920:
        title = prefix[1].strip() + "\n" + prefix[2].strip()
    _text(draw, (72, 234), title, 68 if cover else 59, "#f4f8fc", 920, bold=True, max_lines=4 if cover else 3, spacing=22)
    draw.rounded_rectangle((72, 563, 1008, 1304), radius=36, fill=(21, 42, 61, 248), outline=(76, 133, 159, 100), width=2)
    draw.rounded_rectangle((72, 563, 82, 1304), radius=5, fill="#51e5c1")
    _text(draw, (119, 603), f"{index + 1:02d} / {total:02d}", 30, "#69e7c9", 450, bold=True)
    heading = str(scene.get("heading") or "看懂关键变化")
    _text(draw, (119, 681), heading, 54, "#ffffff", 827, bold=True, max_lines=3, spacing=18)
    body = str(scene.get("body") or content.get("hook") or "从真实资料出发，理解技术与应用。")
    _text(draw, (119, 937), body, 39, "#c4d9e5", 827, max_lines=5, spacing=22)
    # Three connected nodes provide a visual theme for the chapter sequence.
    for n, xx in enumerate([166, 540, 914]):
        if n < 2:
            draw.line((xx + 14, 1374, xx + 360, 1374), fill=(76, 225, 190, 90), width=3)
        draw.ellipse((xx - 9, 1365, xx + 9, 1383), fill="#64ebc9" if n == index % 3 else "#355a6c")
    draw.rounded_rectangle((72, 1460, 1008, 1713), radius=25, fill=(4, 10, 20, 140))
    if cover:
        _text(draw, (112, 1502), str(content.get("hook") or "用一分钟看懂关键变化"), 40, "#d7eaf0", 856, max_lines=3)
    _text(draw, (74, 1774), str(content.get("channel_name") or "AI 科技观察"), 26, "#7b9aa9", 620)
    _text(draw, (816, 1774), "AI 配音", 24, "#7b9aa9", 200)
    draw.rounded_rectangle((72, 1842, 1008, 1848), radius=3, fill="#284756")
    im.save(path)


def _ass_time(value: float) -> str:
    centiseconds = max(0, round(value * 100))
    seconds, cs = divmod(centiseconds, 100)
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _subtitles(cues: list[dict], duration: float, path: Path):
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {WIDTH}", f"PlayResY: {HEIGHT}",
             "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             "Style: Default,Noto Sans CJK SC,51,&H00FFFFFF,&H000000FF,&H00201508,&H99000000,1,0,0,0,100,100,0,0,1,2,0,2,112,112,285,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for cue in cues:
        start, end = max(0, float(cue["start"])), min(duration, float(cue["end"]))
        clean = str(cue["text"]).strip().rstrip("，,。！？!?；;：:")
        if not clean or end <= start:
            continue
        wrapped = _wrap(clean, _font(51, True), 826)
        text = "\\N".join(_ass_escape(line) for line in wrapped)
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}")
    for step in range(math.ceil(duration * 3)):
        start, end = step / 3, min(duration, (step + 1) / 3)
        width = max(1, int(936 * end / duration))
        drawing = f"{{\\an7\\pos(72,1842)\\p1\\bord0\\shad0\\c&HBDC35B&}}m 0 0 l {width} 0 {width} 6 0 6"
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{drawing}")
    path.write_text("\n".join(lines), encoding="utf-8")


def inspect_media(video: Path) -> dict:
    info = _probe(video)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    duration = float(info["format"].get("duration", 0))
    if len(videos) != 1 or not audios or duration < 1:
        raise RuntimeError("成片质检失败：缺少有效视频、音频轨道或时长")
    if videos[0]["width"] != WIDTH or videos[0]["height"] != HEIGHT:
        raise RuntimeError("成片质检失败：视频尺寸不符合 1080×1920")
    volume = _run(["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect", "-vn", "-sn",
                   "-f", "null", "-"], timeout=90).stderr
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", volume)
    if not match or float(match[1]) < -55:
        raise RuntimeError("成片质检失败：音轨接近静音")
    return {"passed": True, "video_codec": videos[0]["codec_name"], "audio_codec": audios[0]["codec_name"],
            "width": WIDTH, "height": HEIGHT, "duration": round(duration, 3), "fps": videos[0].get("avg_frame_rate"),
            "mean_volume_db": float(match[1]), "checks": ["视频与音频轨道存在", "1080×1920 竖屏", "音轨非静音"]}


def render_factory_video(item_id: str, content: dict, output_dir: Path,
                         voice: str = "zh-CN-XiaoxiaoNeural") -> dict:
    """Return real media artifacts or raise; never report placeholder success."""
    from services.factory_resources import ensure_memory_available
    ensure_memory_available()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("无效的内容编号")
    display_script = str(content.get("script", "")).replace("**", "").strip()
    script = _speech_text(display_script)
    if not script:
        raise ValueError("口播文案为空")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / item_id
    target.mkdir(parents=True, exist_ok=True)
    video, audio, cover, subtitle = (target / n for n in ("video.mp4", "narration.mp3", "cover.png", "subtitles.ass"))
    manifest = target / "media.json"
    fingerprint = hashlib.sha256(json.dumps({"content": content, "voice": voice, "renderer": 3},
                                            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if manifest.exists():
        try:
            old = json.loads(manifest.read_text(encoding="utf-8"))
            if old.get("fingerprint") == fingerprint and all(p.exists() for p in (video, audio, cover, subtitle)):
                old["quality"] = {**old.get("quality", {}), **inspect_media(video)}
                return old
        except (ValueError, RuntimeError):
            # An interrupted or damaged artifact is regenerated on retry.
            pass
    with tempfile.TemporaryDirectory(prefix="factory-media-", dir=target) as temp:
        work = Path(temp)
        engine = "edge-tts"
        fallback_reason = None
        try:
            cues = asyncio.run(_edge_speech(script, voice, audio))
            timing_method = "speech_word_timestamps"
        except Exception as exc:
            # Keep service URLs, credentials and network payloads out of artifacts.
            fallback_reason = f"Edge-TTS unavailable ({type(exc).__name__})"
            cues = _cosyvoice_speech(script, audio, work, display_text=display_script)
            engine = "cosyvoice2-local-reference"
            timing_method = "sentence_audio_duration_with_phrase_interpolation"
        duration = _duration(audio)
        if duration <= 1 or duration > 240:
            raise RuntimeError(f"配音时长异常：{duration:.1f} 秒")
        if engine == "edge-tts" and script != display_script:
            units, basis = _sentence_units(display_script, cues, duration)
            cues = []
            for index, unit in enumerate(units):
                for cue in _timed_chunks(unit["text"], unit["start"], unit["end"]):
                    cue.update(sentence_index=index, sentence_start=unit["start"], sentence_end=unit["end"],
                               sentence_text=unit["text"], sentence_timing_basis=basis)
                    cues.append(cue)
            timing_method = "word_timestamps_with_sentence_and_phrase_interpolation"
        scenes = [s for s in content.get("scenes", []) if isinstance(s, dict)][:6]
        if not scenes:
            scenes = [{"heading": "关键观察", "body": content.get("hook") or script[:100]},
                      {"heading": "理解与应用", "body": "结合实际需求，核对信息来源与适用条件。"}]
        _draw_card(content, scenes[0], 0, len(scenes), cover, cover=True)
        _subtitles(cues, duration, subtitle)
        timeline, scene_timing = _scene_timeline(scenes, display_script, cues, duration)
        (target / "speech-timings.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "narration.txt").write_text(script, encoding="utf-8")
        concat = work / "frames.txt"
        # Generated filenames are fixed and need no untrusted shell interpolation.
        entries = []
        for index, scene in enumerate(scenes):
            card = work / f"card_{index:02d}.png"
            _draw_card(content, scene, index, len(scenes), card)
            entries.extend([f"file '{card.name}'", f"duration {timeline[index]['end'] - timeline[index]['start']:.6f}"])
        entries.append(f"file 'card_{len(scenes)-1:02d}.png'")
        concat.write_text("\n".join(entries), encoding="utf-8")
        # The working directory isolates the filter filename from path escaping.
        temporary_ass = work / "subtitles.ass"
        temporary_ass.write_bytes(subtitle.read_bytes())
        command = ["ffmpeg", "-y", "-v", "warning", "-threads", "2", "-filter_threads", "2",
                   "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(audio),
                   "-vf", "fps=30,subtitles=subtitles.ass,format=yuv420p", "-t", f"{duration:.6f}",
                   "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(video)]
        result = subprocess.run(command, cwd=work, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError(f"视频渲染失败：{result.stderr[-1400:]}")
    quality = inspect_media(video)
    quality["subtitle_cues"] = len(cues)
    quality["subtitle_timing"] = timing_method
    quality["scene_timing"] = scene_timing
    quality["scene_timeline"] = timeline
    quality["speech_normalization"] = "deterministic_chinese_numbers_and_brand_aliases; original_display_text_preserved"
    result = {"video_path": str(video), "audio_path": str(audio), "cover_path": str(cover),
              "subtitle_path": str(subtitle), "duration": quality["duration"], "tts_engine": engine,
              "width": WIDTH, "height": HEIGHT, "quality": quality, "fingerprint": fingerprint,
              "fallback_reason": fallback_reason, "renderer_version": 3,
              "speech_timings_path": str(target / "speech-timings.json"), "narration_text_path": str(target / "narration.txt")}
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--cosyvoice-job":
        _cosyvoice_job(sys.argv[2])
    else:
        raise SystemExit("This module is called by the AI content factory worker.")
