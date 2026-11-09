"""Shared editorial direction and enforceable narration-to-picture contracts."""
import re


STRATEGY = {
    "objective": "打造有辨识度的 AI 个人IP，持续增长真实关注者",
    "positioning": "AI热点解读者",
    "audience_promise": "讲清AI发生了什么、证据是什么、与你有什么关系",
    "series": ["AI热点讲明白", "看懂一个AI演示", "AI实用指南"],
    "primary_metrics": ["净增粉丝", "关注转化"],
    "diagnostic_metrics": ["开头留存", "完播率", "收藏与有效评论"],
    "measurement_status": "not_connected",
    "measurement_note": "平台增长数据尚未接入；不以产量或提交次数推断涨粉效果。",
    "experiment": "同一内容系列只改变一个因素（如开场方式），取得同口径真实数据后再决定是否扩大使用。",
}


def validate_editorial_scenes(script, scenes, assets):
    """Require ordered, complete narration and explicit, justified asset choices.

    This checks coverage, not visual semantics. A separate editorial review of
    the source descriptions is required before rendering.
    """
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 6:
        raise ValueError("需要2–6段完整口播分镜")
    known = {a["id"] for a in assets}
    cursor, selected = 0, set()
    substantive = lambda value: re.search(r"[\w\u4e00-\u9fff]", value)
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("分镜格式无效")
        anchor = scene.get("narration_exact")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ValueError("每段分镜需要原样口播")
        start = script.find(anchor, cursor)
        if start < 0 or substantive(script[cursor:start]):
            raise ValueError("分镜必须按顺序覆盖完整口播，不得遗漏、重写或重复句子")
        cursor = start + len(anchor)
        ids = scene.get("asset_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(a, str) or a not in known for a in ids):
            raise ValueError("分镜必须明确引用已提供的素材")
        if not isinstance(scene.get("match_reason"), str) or not scene["match_reason"].strip():
            raise ValueError("每段分镜需要解释画面与这段口播的关系")
        selected.update(ids)
    if substantive(script[cursor:]):
        raise ValueError("分镜未覆盖结尾口播")
    if len(selected) < 3:
        raise ValueError("至少选择3段不同的动态演示")
    return scenes
