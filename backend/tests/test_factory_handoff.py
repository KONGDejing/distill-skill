"""Manual handoffs retain approved facts and never pretend to be publications."""
from copy import deepcopy

import pytest

from services.factory_handoff import HANDOFF_VERSION, build_handoff, handoff_files


def inputs():
    return (
        {"title": "研究标题", "script": "原文只讨论条件A，未报告其他场景。", "hook": "条件A能说明什么？",
         "follow_prompt": "关注后一起对照原文看证据。",
         "sources": [{"id": "paper", "title": "原始研究", "url": "https://example.org/paper#limitations",
                      "published_at": "2026-11-10", "kind": "paper"}]},
        {"platform": "douyin", "title": "先看研究条件再判断", "caption": "原文只讨论条件A，未报告其他场景。",
         "tags": ["AI论文", "#研究条件", "AI论文"]},
    )


def test_missing_extras_produce_complete_copyable_handoff_without_new_facts():
    content, variant = inputs()
    before = deepcopy((content, variant))
    handoff = build_handoff(content, variant)
    assert handoff["version"] == HANDOFF_VERSION == 2
    assert handoff["title"] == variant["title"] and handoff["caption"] == variant["caption"]
    assert handoff["tags"] == ["AI论文", "研究条件"]
    assert handoff["cover_text"] == variant["title"]
    assert 2 <= len(handoff["hooks"]) <= 3 and handoff["hooks"][0] == content["hook"]
    assert handoff["follow_prompt"] == content["follow_prompt"]
    assert handoff["ai_disclosure"] == "AI辅助整理文案、配音和剪辑；参考来源见正文及随附资料"
    assert handoff["pinned_comment"] and len(handoff["reply_templates"]) >= 3
    assert all(set(entry) == {"question", "reply"} and all(entry.values()) for entry in handoff["reply_templates"])
    files = handoff_files(handoff)
    assert set(files) == {"发布文案.txt", "互动回复模板.txt", "资料来源.txt", "手动发布说明.txt"}
    assert all(text.encode("utf-8").decode("utf-8") == text and text.endswith("\n") for text in files.values())
    assert "#AI论文 #研究条件" in files["发布文案.txt"]
    assert "不是我的实测或亲历记录" in files["互动回复模板.txt"]
    assert "不表示已收到这些留言" in files["互动回复模板.txt"]
    assert handoff["sources"] == content["sources"]
    assert content["sources"][0]["url"] in files["资料来源.txt"]
    assert "2026-11-10" in files["资料来源.txt"]
    assert "先确认" in files["手动发布说明.txt"]
    assert (content, variant) == before


def test_reviewed_overrides_and_partial_replies_are_preserved_and_completed():
    content, variant = inputs()
    variant["manual_handoff"] = {
        "cover_text": "条件A", "alternate_titles": ["只看已报告的条件"],
        "hooks": ["先核对条件A。", "先核对条件A。"], "follow_prompt": "一起看原始资料。",
        "ai_disclosure": "文案由AI辅助整理，使用AI配音及剪辑，来源附后。",
        "pinned_comment": "这里仅讨论条件A。", "reply_templates": [
            {"question": "有其他场景的数据吗？", "reply": "所列原文未报告其他场景。"},
            {"question": "", "reply": "这个不完整模板不会交付"},
        ],
    }
    handoff = build_handoff(content, variant, {"platform": "douyin", "day": "2026-11-12"},
                            "历史提交结果未明，请本人先核对平台记录。")
    for field in ("cover_text", "alternate_titles", "follow_prompt", "ai_disclosure", "pinned_comment"):
        assert handoff[field] == variant["manual_handoff"][field]
    assert handoff["hooks"][0] == "先核对条件A。" and 2 <= len(handoff["hooks"]) <= 3
    assert handoff["reply_templates"][0] == variant["manual_handoff"]["reply_templates"][0]
    assert len(handoff["reply_templates"]) >= 3
    text = handoff_files(handoff)["手动发布说明.txt"]
    assert "2026-11-12" in text and "本包仅供抖音" in text
    assert "历史提交结果未明" in text and "确认之前不要再次上传" in text
    assert "不跨平台重复发布" in text and "不代表已提交" in text
    assert "最多2条原创" in text


def test_absent_approved_hook_and_follow_prompt_use_generic_nonfactual_prompts():
    content, variant = inputs()
    del content["hook"], content["follow_prompt"]
    variant["manual_handoff"] = {"hooks": [], "reply_templates": None}
    handoff = build_handoff(content, variant)
    assert 2 <= len(handoff["hooks"]) <= 3 and handoff["hooks"][0] == variant["title"]
    assert set(handoff["hooks"][1:]) <= {"先看来源，再看这条内容能说明什么。", "这条内容的结论和边界，先分开看。"}
    assert handoff["follow_prompt"] == "如果这些内容对你有帮助，欢迎关注，一起对照来源看懂新进展。"
    assert handoff["caption"] == variant["caption"]
    assert len(handoff["reply_templates"]) >= 3


def test_missing_approved_copy_or_sources_and_cross_platform_route_are_not_invented():
    content, variant = inputs()
    for field in ("title", "caption", "tags"):
        incomplete = {**variant, field: None}
        with pytest.raises(ValueError, match="平台定稿"):
            build_handoff(content, incomplete)
    with pytest.raises(ValueError, match="真实来源"):
        build_handoff({**content, "sources": []}, variant)
    with pytest.raises(ValueError, match="唯一分配平台"):
        build_handoff(content, variant, {"platform": "xiaohongshu", "day": "2026-11-12"})
