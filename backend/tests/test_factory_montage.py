from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.factory_montage import (
    _anchor_time, _source_label, build_shot_plan, measure_motion,
    read_subtitle_cues, render_tech_montage, validate_shot_plan,
)


def assets():
    return [{"id": key, "kind": "official_video", "sha256": key,
             "clips": [{"start": 1, "end": 17}], "display_label": "官方演示 · 仿真",
             "source_url": f"https://example.org/{key}", "topic_tags": ["simulation"]}
            for key in ["real", "generation", "interaction"]]


def test_explicit_scene_assets_follow_the_approved_narration_order():
    scenes = [{"heading": "场景", "narration_exact": text, "asset_ids": [asset]}
              for text, asset in [("第一段展示机器人。", "real"), ("第二段生成数据。", "generation"), ("第三段翻转积木。", "interaction")]]
    cues = [{"text": scene["narration_exact"], "start": i * 4, "end": (i + 1) * 4} for i, scene in enumerate(scenes)]
    plan = build_shot_plan({"scenes": scenes}, cues, assets(), 12)
    assert [s["asset_id"] for s in plan] == ["real", "generation", "interaction"]
    assert all(s["asset_selection"] == "explicit_scene_asset_ids" for s in plan)
    assert all(1 <= s["source_start"] < s["source_end"] <= 17 for s in plan)
    assert validate_shot_plan(plan, 12)["dynamic_coverage"] == 1
    scenes[1]["asset_ids"] = ["missing"]
    with pytest.raises(ValueError, match="未知素材"):
        build_shot_plan({"scenes": scenes}, cues, assets(), 12)


def test_anchor_inside_one_long_sentence_uses_subtitle_position():
    cues = [{"text": "第一展示动作第二测试环境", "start": 4, "end": 16}]
    assert _anchor_time("第二测试环境", cues) == 10
    assert _anchor_time("不存在的内容", cues) is None


def test_plan_rejects_fake_diversity_and_insufficient_official_footage():
    base = [{"timeline_start": i * 4, "timeline_end": (i + 1) * 4, "duration": 4,
             "kind": "official_video", "source_sha256": str(i), "source_start": 0, "source_end": 4} for i in range(3)]
    assert validate_shot_plan(base, 12)["distinct_asset_count"] == 3
    base[2]["source_sha256"] = "1"
    with pytest.raises(ValueError, match="75%"):
        validate_shot_plan(base, 12)
    base[2].update(source_sha256="2", kind="schematic_animation")
    with pytest.raises(ValueError, match="75%"):
        validate_shot_plan(base, 12)


def test_static_frames_cannot_be_reported_as_dynamic_demonstration():
    static = np.zeros((9, 54, 96), dtype=np.uint8).tobytes()
    with patch("services.factory_montage.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=static)):
        with pytest.raises(ValueError, match="近乎静止"):
            measure_motion(Path("clip.mp4"), 0, 3)
    moving = np.arange(9, dtype=np.uint8)[:, None, None] * np.ones((9, 54, 96), dtype=np.uint8) * 10
    with patch("services.factory_montage.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout=moving.tobytes())):
        assert measure_motion(Path("clip.mp4"), 0, 3)["mean_frame_difference"] == 10


def test_original_subtitle_words_and_only_narration_events_are_kept(tmp_path):
    path = tmp_path / "original.ass"
    path.write_text("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,progress\n"
                    "Dialogue: 1,0:00:03.62,0:00:10.44,Default,,0,0,0,,AllenAI 推出\\NToyBlocks 框架\n")
    assert read_subtitle_cues(path) == [{"text": "AllenAI 推出 ToyBlocks 框架", "start": 3.62, "end": 10.44}]


def test_real_and_simulated_footage_have_distinct_truthful_labels():
    assert _source_label({"topic_tags": ["real_robot"]}) == "官方演示 · 实拍"
    assert _source_label({"topic_tags": ["simulation"]}) == "官方演示 · 仿真"
    assert "实拍 / 仿真对照" in _source_label({"topic_tags": ["simulation", "sim_to_real"]})
    assert _source_label({"kind": "schematic_animation"}) == "示意动画"


def test_invalid_item_cannot_escape_montage_output_directory(tmp_path):
    with pytest.raises(ValueError, match="编号"):
        render_tech_montage("../escape", {}, Path("audio"), Path("subtitle"), [], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_long_narration_cannot_recycle_short_source_windows():
    short = [{**a, "clips": [{"start": 0, "end": 8}]} for a in assets()]
    scenes = [{"heading": "演示", "narration_exact": "第一段机器人动作", "asset_ids": [a["id"] for a in short]}]
    with pytest.raises(ValueError, match="不重复素材不足"):
        build_shot_plan({"scenes": scenes}, [], short, 157.467)


def test_overlapping_windows_and_aliases_do_not_inflate_available_time():
    from services.factory_montage import require_footage_budget
    first = {**assets()[0], "clips": [{"start": 0, "end": 8}, {"start": 4, "end": 12}]}
    alias = {**first, "id": "renamed-copy"}
    assert require_footage_budget([first, alias], 12)["available_unique_seconds"] == 12
    with pytest.raises(ValueError, match="不足"):
        require_footage_budget([first, alias], 13)


def test_allocator_uses_only_unused_intervals_even_across_scenes():
    scenes = [{"heading": "演示", "narration_exact": "完整机器人动作", "asset_ids": [a["id"] for a in assets()]}]
    plan = build_shot_plan({"scenes": scenes}, [], assets(), 36)
    assert validate_shot_plan(plan, 36)["source_reuse_seconds"] == 0
    plan[-1].update(source_sha256=plan[0]["source_sha256"], source_start=plan[0]["source_start"], source_end=plan[0]["source_end"])
    with pytest.raises(ValueError, match="重复源画面"):
        validate_shot_plan(plan, 36)


def test_reencoded_repeated_action_is_detected_without_confusing_adjacent_frames():
    from services.factory_montage import _check_repeated_samples
    rng = np.random.default_rng(42)
    unique = rng.integers(20, 230, size=(24, 54, 96), dtype=np.uint8)
    assert _check_repeated_samples(unique, [(i // 12, i / 4) for i in range(24)])["passed"]
    repeated = np.concatenate([unique, unique[:8]])
    with pytest.raises(ValueError, match="重复画面序列"):
        _check_repeated_samples(repeated, [(i // 12, i / 4) for i in range(32)])


def test_decoded_video_check_rejects_duplicate_footage_in_different_files(tmp_path, monkeypatch):
    import shutil
    from services import factory_montage
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required for real decoded footage verification")
    monkeypatch.setattr(factory_montage, "ensure_memory_available", lambda: None)
    first, second = tmp_path / "original.mp4", tmp_path / "reencoded.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=192x108:rate=24", "-t", "4",
                    "-c:v", "libx264", "-threads", "1", "-crf", "18", str(first)], check=True, timeout=20)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(first), "-c:v", "libx264", "-threads", "1",
                    "-crf", "20", str(second)], check=True, timeout=20)
    assert factory_montage._sha256(first) != factory_montage._sha256(second)
    plan = [{"shot_index": i, "kind": "official_video", "asset_id": str(i), "source_start": 0, "duration": 4}
            for i in range(2)]
    with pytest.raises(ValueError, match="重复画面序列"):
        factory_montage.check_visual_repeats(plan, {"0": {"path": str(first)}, "1": {"path": str(second)}})
