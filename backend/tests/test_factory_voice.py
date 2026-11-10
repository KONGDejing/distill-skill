import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from services import factory_voice


def test_audio_only_generation_keeps_display_units_and_reuses_verified_cache(tmp_path, monkeypatch):
    # This unit test uses fixture bytes; memory-pressure behavior has its own tests.
    monkeypatch.setattr(factory_voice, "ensure_memory_available", lambda: None)
    async def unavailable(*args, **kwargs):
        raise RuntimeError("offline")

    def local(text, audio, work, display_text):
        assert text == "容量六十五兆字节。"
        assert display_text == "容量65MB。"
        audio.write_bytes(b"fixture audio")
        return [{"text": display_text, "start": 0, "end": 5}]

    synth = Mock(side_effect=local)
    monkeypatch.setattr(factory_voice.speech, "_edge_speech", unavailable)
    monkeypatch.setattr(factory_voice.speech, "_cosyvoice_speech", synth)
    monkeypatch.setattr(factory_voice.speech, "_duration", lambda _: 5)
    first = factory_voice.synthesize_factory_audio("test-item", {"script": "容量65MB。"}, tmp_path)
    second = factory_voice.synthesize_factory_audio("test-item", {"script": "容量65MB。"}, tmp_path)
    assert first == second
    assert first["tts_engine"] == "cosyvoice2-local-reference"
    assert first["voice_profile"] == "default"
    assert first["voice_name"] == "zh-CN-XiaoxiaoNeural"
    assert first["voice_rate"] == "+0%" and first["voice_pitch"] == "+0Hz"
    assert "65MB" in (tmp_path / "test-item/voice/subtitles.ass").read_text()
    assert "video_path" not in first
    synth.assert_called_once()


@pytest.fixture
def voice_backend(monkeypatch):
    monkeypatch.setattr(factory_voice, "ensure_memory_available", lambda: None)
    monkeypatch.setattr(factory_voice.speech, "_duration", lambda _: 5)
    calls = []

    async def edge(text, voice, audio, *, rate="+0%", pitch="+0Hz"):
        calls.append({"voice": voice, "rate": rate, "pitch": pitch})
        audio.write_bytes((voice + rate + pitch).encode())
        return [{"text": text, "start": 0.1, "end": 4.9}]

    local = Mock(side_effect=AssertionError("This test must not use the local female reference"))
    monkeypatch.setattr(factory_voice.speech, "_edge_speech", edge)
    monkeypatch.setattr(factory_voice.speech, "_cosyvoice_speech", local)
    return calls, local


@pytest.mark.parametrize("routing", [
    {"voice_profile": "calm_male"},
    {"editor_id": "producer2"},
    {"editor_id": "producer2", "voice_profile": "default"},
])
def test_calm_male_route_has_verified_options_and_reuses_own_cache(tmp_path, voice_backend, routing):
    calls, local = voice_backend
    content = {"script": "先看论文真正解决了什么问题。", **routing}
    first = factory_voice.synthesize_factory_audio("paper", content, tmp_path)
    second = factory_voice.synthesize_factory_audio("paper", content, tmp_path)
    assert calls == [{"voice": "zh-CN-YunyangNeural", "rate": "-8%", "pitch": "+0Hz"}]
    assert first == second
    assert {key: first[key] for key in ("voice_profile", "voice_name", "voice_label", "voice_rate", "voice_pitch")} == {
        "voice_profile": "calm_male", "voice_name": "zh-CN-YunyangNeural", "voice_label": "沉稳男声",
        "voice_rate": "-8%", "voice_pitch": "+0Hz",
    }
    assert first["tts_engine"] == "edge-tts" and first["fallback_reason"] is None
    local.assert_not_called()


def test_voice_and_rate_change_invalidate_audio_cache(tmp_path, monkeypatch, voice_backend):
    calls, _ = voice_backend
    content = {"script": "先看论文真正解决了什么问题。"}
    female = factory_voice.synthesize_factory_audio("same-item", content, tmp_path)
    male_content = {**content, "editor_id": "producer2"}
    male = factory_voice.synthesize_factory_audio("same-item", male_content, tmp_path)
    monkeypatch.setitem(factory_voice.VOICE_PROFILES["calm_male"], "voice_rate", "-10%")
    slower = factory_voice.synthesize_factory_audio("same-item", male_content, tmp_path)
    assert len(calls) == 3
    assert calls[0]["voice"] == "zh-CN-XiaoxiaoNeural"
    assert calls[1]["rate"] == "-8%" and calls[2]["rate"] == "-10%"
    assert len({female["fingerprint"], male["fingerprint"], slower["fingerprint"]}) == 3


def test_male_failure_never_uses_female_fallback_or_damages_existing_files(tmp_path, monkeypatch, voice_backend):
    _, local = voice_backend
    content = {"script": "先看论文真正解决了什么问题。"}
    factory_voice.synthesize_factory_audio("preserve-item", content, tmp_path)
    directory = tmp_path / "preserve-item/voice"
    previous = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}

    async def interrupted(text, voice, audio, **kwargs):
        audio.write_bytes(b"partial stream")
        raise RuntimeError("private endpoint or credential must not appear in error text")

    monkeypatch.setattr(factory_voice.speech, "_edge_speech", interrupted)
    with pytest.raises(RuntimeError, match="沉稳男声合成失败.*未回退其他声线") as error:
        factory_voice.synthesize_factory_audio("preserve-item", {**content, "voice_profile": "calm_male"}, tmp_path)
    assert "credential" not in str(error.value)
    assert previous == {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    assert not [path for path in directory.glob("speech-*") if path.is_dir()]
    local.assert_not_called()


def test_male_cache_requires_matching_profile_and_actual_edge_engine(tmp_path, voice_backend):
    calls, _ = voice_backend
    content = {"script": "先看实验数据。", "voice_profile": "calm_male"}
    factory_voice.synthesize_factory_audio("paper", content, tmp_path)
    manifest = tmp_path / "paper/voice/voice.json"
    cached = json.loads(manifest.read_text())
    cached["tts_engine"] = "cosyvoice2-local-reference"
    manifest.write_text(json.dumps(cached))
    regenerated = factory_voice.synthesize_factory_audio("paper", content, tmp_path)
    assert len(calls) == 2 and regenerated["tts_engine"] == "edge-tts"


def test_voice_profile_rejects_unknown_config_and_keeps_other_explicit_voice():
    with pytest.raises(ValueError, match="未知配音声线"):
        factory_voice.resolve_voice_profile({"voice_profile": "calm_mlae"})
    profile = factory_voice.resolve_voice_profile({"editor_id": "producer1"}, "zh-CN-XiaoyiNeural")
    assert profile["voice_name"] == "zh-CN-XiaoyiNeural" and profile["voice_rate"] == "+0%"


@pytest.mark.parametrize("options,expected", [({}, ("+0%", "+0Hz")),
                                            ({"rate": "-8%", "pitch": "+0Hz"}, ("-8%", "+0Hz")),
                                            ({"rate": "-8%", "pitch": "-2Hz"}, ("-8%", "-2Hz"))])
def test_edge_speech_accepts_tone_options_and_keeps_three_argument_calls(tmp_path, monkeypatch, options, expected):
    captured = []

    class Communicate:
        def __init__(self, text, voice, *, rate, pitch, boundary=None, connect_timeout=10, receive_timeout=60):
            captured.append((rate, pitch, boundary, connect_timeout, receive_timeout))

        async def stream(self):
            yield {"type": "audio", "data": b"fixture" * 200}
            yield {"type": "WordBoundary", "text": "论文", "offset": 1_000_000, "duration": 20_000_000}

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Communicate))
    cues = asyncio.run(factory_voice.speech._edge_speech("论文", "zh-CN-YunyangNeural", tmp_path / "test.mp3", **options))
    assert captured == [(*expected, "WordBoundary", 10, 35)]
    assert cues == [{"text": "论文", "start": 0.1, "end": 2.1}]
