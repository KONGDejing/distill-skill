from unittest.mock import Mock
from services import factory_voice


def test_audio_only_generation_keeps_display_units_and_reuses_verified_cache(tmp_path, monkeypatch):
    # This unit test uses fixture bytes; memory-pressure behavior has its own tests.
    monkeypatch.setattr(factory_voice, "ensure_memory_available", lambda: None)
    async def unavailable(*args):
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
    assert "65MB" in (tmp_path / "test-item/voice/subtitles.ass").read_text()
    assert "video_path" not in first
    synth.assert_called_once()
