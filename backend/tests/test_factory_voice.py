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


def test_owner_voice_uses_exact_reference_and_never_calls_stock_voice(tmp_path,monkeypatch,voice_backend):
    calls,_=voice_backend
    profile={'voice_profile':'owner_clone','voice_name':'clone:owner-sample','voice_label':'本人音色',
        'voice_rate':'+0%','voice_pitch':'+0Hz','voice_reference_sha256':'reference-one',
        '_reference_path':'owner-reference.wav','_reference_text':'这是参考录音的逐字文本。'}
    def local(text,audio,work,display_text,**kwargs):
        assert kwargs=={'reference_path':'owner-reference.wav','reference_text':'这是参考录音的逐字文本。'}
        audio.write_bytes(b'cloned audio')
        return [{'text':display_text,'start':0,'end':5}]
    clone=Mock(side_effect=local)
    monkeypatch.setattr(factory_voice.speech,'_cosyvoice_speech',clone)
    content={'script':'机器人练习用的示范从哪里来？'}
    first=factory_voice.synthesize_factory_audio('owner',content,tmp_path,owner_voice=profile)
    second=factory_voice.synthesize_factory_audio('owner',content,tmp_path,owner_voice=profile)
    assert first==second and first['tts_engine']=='cosyvoice2-owner-reference'
    assert calls==[] and clone.call_count==1
    assert '_reference_path' not in first and '_reference_text' not in first
    changed={**profile,'voice_reference_sha256':'reference-two'}
    factory_voice.synthesize_factory_audio('owner',content,tmp_path,owner_voice=changed)
    assert clone.call_count==2


def test_owner_clone_failure_does_not_fall_back_or_damage_previous_audio(tmp_path,monkeypatch,voice_backend):
    calls,_=voice_backend
    factory_voice.synthesize_factory_audio('preserve',{'script':'已完成的旧版。'},tmp_path)
    directory=tmp_path/'preserve/voice';before={p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}
    monkeypatch.setattr(factory_voice.speech,'_cosyvoice_speech',Mock(side_effect=RuntimeError('private diagnostic')))
    profile={'voice_profile':'owner_clone','_reference_path':'ref.wav','_reference_text':'逐字参考文本。'}
    with pytest.raises(RuntimeError,match='本人音色合成失败.*未回退普通音色') as error:
        factory_voice.synthesize_factory_audio('preserve',{'script':'新版。'},tmp_path,owner_voice=profile)
    assert 'private diagnostic' not in str(error.value)
    assert len(calls)==1
    assert before=={p.name:p.read_bytes() for p in directory.iterdir() if p.is_file()}


def test_backend_owner_selection_requires_calibrated_unchanged_reference(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database import Base
    from models.user_profile import UserProfile
    from services.factory_branding import file_sha256
    engine=create_engine(f'sqlite:///{tmp_path / "voices.db"}')
    Base.metadata.create_all(engine,tables=[UserProfile.__table__])
    source=tmp_path/'upload.wav';source.write_bytes(b'original reference')
    reference=tmp_path/'trimmed.wav';reference.write_bytes(b'calibrated reference')
    with sessionmaker(bind=engine)() as db:
        user=UserProfile(tts_voice='clone:owner',voice_clone_samples=[{'id':'owner','name':'本人音色','path':str(source)}])
        db.add(user);db.commit()
        with pytest.raises(ValueError,match='尚未校准'):
            factory_voice.selected_owner_voice(db)
        user.voice_clone_samples=[{**user.voice_clone_samples[0],'factory_reference':{
            'path':str(reference),'prompt_text':'原录音中的逐字文本。','sha256':file_sha256(reference),
            'source_sha256':file_sha256(source)}}];db.commit()
        profile=factory_voice.selected_owner_voice(db)
        assert profile['voice_profile']=='owner_clone' and profile['_reference_path']==str(reference)
        calibrated={**user.voice_clone_samples[0]['factory_reference'],'prosody':'connected_paragraph_v1'}
        user.voice_clone_samples=[{**user.voice_clone_samples[0],'factory_reference':calibrated}];db.commit()
        connected=factory_voice.selected_owner_voice(db)
        assert connected['_connected_speech'] is True and connected['voice_prosody']=='connected_paragraph_v1'
        source.write_bytes(b'changed recording')
        with pytest.raises(ValueError,match='录音已变化'):
            factory_voice.selected_owner_voice(db)
        user.tts_voice='zh-CN-YunyangNeural';db.commit()
        assert factory_voice.selected_owner_voice(db) is None
    engine.dispose()


def test_connected_owner_speech_is_an_explicit_cache_and_backend_option(tmp_path,monkeypatch,voice_backend):
    profile={'voice_profile':'owner_clone','voice_name':'clone:owner','voice_label':'本人音色',
        '_reference_path':'ref.wav','_reference_text':'参考录音文字。','_connected_speech':True,
        'voice_prosody':'connected_paragraph_v1','voice_reference_sha256':'approved-reference'}
    def local(text,audio,work,display_text,**kwargs):
        assert kwargs['connected_speech'] is True
        audio.write_bytes(b'connected speech')
        return [{'text':display_text,'start':0,'end':5}]
    monkeypatch.setattr(factory_voice.speech,'_cosyvoice_speech',local)
    result=factory_voice.synthesize_factory_audio('connected',{'script':'先看动作。再看结果。'},tmp_path,owner_voice=profile)
    assert result['voice_prosody']=='connected_paragraph_v1'
    assert result['subtitle_timing']=='connected_utterance_audio_duration_with_phrase_interpolation'
    assert '_connected_speech' not in result


def test_connected_utterances_preserve_paragraphs_and_all_words_without_tiny_sentence_restarts():
    from services.factory_media import _connected_utterances,_speech_text
    text='先看机器人动作。再看完成了什么任务？这一步要对照原始演示。\n接着检查条件。最后给出结论。'
    groups=_connected_utterances(text)
    assert groups==['先看机器人动作。再看完成了什么任务？这一步要对照原始演示。','接着检查条件。最后给出结论。']
    assert ''.join(groups)==text.replace('\n','')
    long='容量65MB，版本3.2。'*12
    groups=_connected_utterances(long,limit=48)
    assert all(len(g)<=48 for g in groups)
    assert ''.join(groups)==long
    assert ''.join(_speech_text(g) for g in groups)==_speech_text(long)


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
