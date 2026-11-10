"""Speech and original-text subtitle assets, independent of any visual template."""
import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path
from services import factory_media as speech
from services.factory_resources import ensure_memory_available


DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
VOICE_PROFILES = {
    "calm_male": {
        "voice_name": "zh-CN-YunyangNeural",
        "voice_label": "沉稳男声",
        "voice_rate": "-8%",
        "voice_pitch": "+0Hz",
    },
}


def resolve_voice_profile(content, voice=DEFAULT_VOICE):
    """Resolve an editor's requested voice without silently changing its gender."""
    requested = content.get("voice_profile") or "default"
    if requested not in ("default", *VOICE_PROFILES):
        raise ValueError("未知配音声线配置")
    if (requested == "calm_male" or content.get("editor_id") == "producer2"
            or voice == VOICE_PROFILES["calm_male"]["voice_name"]):
        return {"voice_profile": "calm_male", **VOICE_PROFILES["calm_male"]}
    return {"voice_profile": "default", "voice_name": voice, "voice_label": "默认声线",
            "voice_rate": "+0%", "voice_pitch": "+0Hz"}


def synthesize_factory_audio(item_id, content, output_dir, voice=DEFAULT_VOICE):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("无效的作品编号")
    display = str(content.get("script", "")).replace("**", "").strip()
    if not display:
        raise ValueError("口播文案为空")
    profile = resolve_voice_profile(content, voice)
    spoken = speech._speech_text(display)
    directory = Path(output_dir).resolve() / item_id / "voice"
    directory.mkdir(parents=True, exist_ok=True)
    audio, subtitles = directory / "narration.mp3", directory / "subtitles.ass"
    manifest = directory / "voice.json"
    fingerprint = hashlib.sha256(json.dumps([display, spoken, profile, 4], ensure_ascii=False,
                                            sort_keys=True).encode()).hexdigest()
    if manifest.is_file():
        try:
            cached = json.loads(manifest.read_text())
            same_profile = all(cached.get(key) == value for key, value in profile.items())
            valid_engine = profile["voice_profile"] != "calm_male" or cached.get("tts_engine") == "edge-tts"
            if (cached.get("fingerprint") == fingerprint and same_profile and valid_engine
                    and audio.is_file() and subtitles.is_file()):
                if 1 < speech._duration(audio) <= 240:
                    return cached
        except (ValueError, RuntimeError, OSError):
            pass
    engine, fallback = "edge-tts", None
    ensure_memory_available()
    with tempfile.TemporaryDirectory(prefix="speech-", dir=directory) as temporary:
        candidate_audio = Path(temporary) / "narration.mp3"
        try:
            cues = asyncio.run(speech._edge_speech(spoken, profile["voice_name"], candidate_audio,
                                                 rate=profile["voice_rate"], pitch=profile["voice_pitch"]))
            timing_method = "speech_word_timestamps"
        except Exception as exc:
            if profile["voice_profile"] == "calm_male":
                # Do not expose service URLs or replace an explicitly requested
                # male voice with the local female reference on a failed call.
                raise RuntimeError(f"沉稳男声合成失败：Edge-TTS {type(exc).__name__}；未回退其他声线") from None
            fallback = f"Edge-TTS unavailable ({type(exc).__name__})"
            ensure_memory_available()
            cues = speech._cosyvoice_speech(spoken, candidate_audio, Path(temporary), display_text=display)
            engine = "cosyvoice2-local-reference"
            timing_method = "sentence_audio_duration_with_phrase_interpolation"
        duration = speech._duration(candidate_audio)
        if not 1 < duration <= 240:
            raise RuntimeError("配音时长异常")
        # A failed attempt must not truncate a previously completed audio file.
        candidate_audio.replace(audio)
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
              "speech_timings_path": str(timings), "subtitle_timing": timing_method, **profile}
    temporary_manifest = manifest.with_suffix(".tmp")
    temporary_manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    temporary_manifest.replace(manifest)
    return result
