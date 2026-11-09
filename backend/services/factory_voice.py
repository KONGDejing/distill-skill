"""Speech and original-text subtitle assets, independent of any visual template."""
import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path
from services import factory_media as speech
from services.factory_resources import ensure_memory_available


def synthesize_factory_audio(item_id, content, output_dir, voice="zh-CN-XiaoxiaoNeural"):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("无效的作品编号")
    display = str(content.get("script", "")).replace("**", "").strip()
    if not display:
        raise ValueError("口播文案为空")
    spoken = speech._speech_text(display)
    directory = Path(output_dir).resolve() / item_id / "voice"
    directory.mkdir(parents=True, exist_ok=True)
    audio, subtitles = directory / "narration.mp3", directory / "subtitles.ass"
    manifest = directory / "voice.json"
    fingerprint = hashlib.sha256(json.dumps([display, spoken, voice, 3], ensure_ascii=False).encode()).hexdigest()
    if manifest.is_file():
        try:
            cached = json.loads(manifest.read_text())
            if cached.get("fingerprint") == fingerprint and audio.is_file() and subtitles.is_file():
                if 1 < speech._duration(audio) <= 240:
                    return cached
        except (ValueError, RuntimeError, OSError):
            pass
    engine, fallback = "edge-tts", None
    ensure_memory_available()
    with tempfile.TemporaryDirectory(prefix="speech-", dir=directory) as temporary:
        try:
            cues = asyncio.run(speech._edge_speech(spoken, voice, audio))
            timing_method = "speech_word_timestamps"
        except Exception as exc:
            fallback = f"Edge-TTS unavailable ({type(exc).__name__})"
            ensure_memory_available()
            cues = speech._cosyvoice_speech(spoken, audio, Path(temporary), display_text=display)
            engine = "cosyvoice2-local-reference"
            timing_method = "sentence_audio_duration_with_phrase_interpolation"
    duration = speech._duration(audio)
    if not 1 < duration <= 240:
        raise RuntimeError("配音时长异常")
    if engine == "edge-tts" and display != spoken:
        units, basis = speech._sentence_units(display, cues, duration)
        cues = []
        for index, unit in enumerate(units):
            for cue in speech._timed_chunks(unit["text"], unit["start"], unit["end"]):
                cue.update(sentence_index=index, sentence_start=unit["start"], sentence_end=unit["end"],
                           sentence_text=unit["text"], sentence_timing_basis=basis)
                cues.append(cue)
        timing_method = "word_timestamps_with_sentence_and_phrase_interpolation"
    speech._subtitles(cues, duration, subtitles)
    timings = directory / "speech-timings.json"
    timings.write_text(json.dumps(cues, ensure_ascii=False, indent=2))
    result = {"audio_path": str(audio), "subtitle_path": str(subtitles), "duration": duration,
              "tts_engine": engine, "fallback_reason": fallback, "fingerprint": fingerprint,
              "speech_timings_path": str(timings), "subtitle_timing": timing_method}
    temporary_manifest = manifest.with_suffix(".tmp")
    temporary_manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary_manifest.replace(manifest)
    return result
