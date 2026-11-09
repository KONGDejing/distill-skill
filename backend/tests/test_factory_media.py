import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.factory_media import (_chunks, _subtitles, _timed_chunks, _speech_text, _speech_segments,
                                    _scene_timeline, _wrap, _font, inspect_media, render_factory_video)


def test_long_subtitles_preserve_the_end_of_the_script(tmp_path):
    script = "这是一段超过单条字幕长度的中文内容，需要完整保留最后一句，不能因为换行而丢掉结论。"
    assert "".join(_chunks(script)) == script
    cues = _timed_chunks(script, 1.2, 14.5)
    assert cues[-1]["end"] == 14.5
    target = tmp_path / "subtitles.ass"
    _subtitles(cues, 14.5, target)
    assert "结论" in target.read_text(encoding="utf-8")


def test_silent_video_cannot_pass_media_quality():
    probe = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                          {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "40"}}
    result = subprocess.CompletedProcess([], 0, stdout="", stderr="mean_volume: -91.0 dB")
    with patch("services.factory_media._probe", return_value=probe), patch("services.factory_media._run", return_value=result):
        with pytest.raises(RuntimeError, match="静音"):
            inspect_media(Path("video.mp4"))


def test_invalid_item_path_cannot_escape_output_dir(tmp_path):
    with pytest.raises(ValueError, match="编号"):
        render_factory_video("../escape", {"script": "你好。"}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_numbers_and_brands_are_read_without_changing_their_magnitude():
    assert _speech_text("NVIDIA刚推出160亿参数模型，支持11个视角。") == "英伟达刚推出一百六十亿参数模型，支持十一个视角。"
    assert _speech_text("版本1.10，版本2.3.4，0.05秒，65MB，2026年，12.5%。") == "版本一点一零，版本二点三点四，零点零五秒，六十五兆字节，二零二六年，百分之十二点五。"
    assert _speech_text("GPT-4.1支持3D；16,000个样本。") == "GPT-四点一支持三维；一万六千个样本。"


def test_byte_units_and_model_letters_preserve_their_distinct_meaning():
    assert _speech_text("Phonon 31m 约65MB，Phonon 40m约75 MB。") == "Phonon 三十一，字母 M 约六十五兆字节，Phonon 四十，字母 M约七十五兆字节。"
    assert _speech_text("容量1.5MB，速率65Mb，距离31m。") == "容量一点五兆字节，速率六十五Mb，距离三十一m。"
    assert _speech_text("BAMB与Phonon 40model。") == "BAMB与Phonon 四十model。"


def test_long_specifications_split_without_losing_identifiers_or_capacity():
    text = "Gradium 开源 Phonon TTS，Phonon 31m 约 65MB、Phonon 40m 约 75MB，基于流匹配技术生成音频。"
    parts = _speech_segments(text, _speech_text(text))
    assert "".join(part["display"] for part in parts) == text
    assert 2 <= len(parts) <= 12
    assert any(part["spoken"] == "约 六十五兆字节。" for part in parts)
    assert any(part["spoken"] == "约 七十五兆字节。" for part in parts)
    short = "Phonon 31m 使用 Apache 2.0 许可。"
    assert _speech_segments(short, _speech_text(short)) == [{"display": short, "spoken": _speech_text(short)}]


def test_scene_timing_uses_speech_boundaries_instead_of_equal_duration():
    sentences = ["模型已经发布。", "Transfer-Auto支持驾驶场景。", "Sim-Auto生成雷达数据。", "机器人还能预测动作。", "基于160亿参数并允许商用。"]
    starts, ends = [0, 7, 17, 25, 31], [6.8, 16.8, 24.8, 30.8, 40]
    cues = []
    for i, sentence in enumerate(sentences):
        cues.append({"text": sentence, "start": starts[i], "end": ends[i], "sentence_index": i,
                     "sentence_start": starts[i], "sentence_end": ends[i], "sentence_text": sentence})
    scenes = [{"heading": "模型发布", "body": "新模型"}, {"heading": "驾驶场景", "body": "Transfer-Auto"},
              {"heading": "传感器", "body": "Sim-Auto生成雷达数据"}, {"heading": "商用", "body": "160亿参数允许商用"}]
    timeline, basis = _scene_timeline(scenes, "".join(sentences), cues, 40)
    assert [r["start"] for r in timeline] == [0, 7, 17, 31]
    assert timeline[-1]["end"] == 40
    assert "measured_sentence_audio_boundaries" in basis
    assert all(r["method"] == "lexical_match_heuristic" for r in timeline)
    scenes[-1]["narration_exact"] = sentences[-1]
    anchored, _ = _scene_timeline(scenes, "".join(sentences), cues, 40)
    assert anchored[-1]["method"] == "exact_text_anchor"


def test_latin_model_name_keeps_its_version_on_the_same_line():
    lines = _wrap("所有模型基于160亿参数的Cosmos 3 Nano", _font(51, True), 826)
    assert any("Cosmos 3" in line for line in lines)
