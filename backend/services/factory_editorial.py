"""Shared editorial direction and enforceable narration-to-picture contracts."""
import re


STRATEGY = {
    "objective": "打造有辨识度的 AI 个人IP，持续增长真实关注者",
    "positioning": "AI热点解读者",
    "audience_promise": "讲清AI发生了什么、证据是什么、与你有什么关系",
    "series": ["大模型进化观察", "算力与芯片讲明白", "端侧AI与硬件", "科技圈有意思", "科技争议看证据", "科技贸易与产业", "逛懂科技展会", "一篇AI论文讲明白", "具身智能观察", "AI实用指南"],
    "video_structure": "约150秒：开场问题、事件与背景、实例或原理、价值与限制、自然关注提醒；每段必须增加新信息。",
    "material_policy": "先核对资料并写完整约150秒口播，再逐句匹配画面。同一视频禁止重复源片段，裁切、放大、转码不能算新画面；素材不足须补充或改稿，不得循环或冻结凑时长。论文讲解必须展示对应论文的原文和图表，不能用其他研究背景或AI生成画面替代论文证据。素材库仅允许跨作品合理复用。",
    "primary_metrics": ["净增粉丝", "关注转化"],
    "diagnostic_metrics": ["开头留存", "完播率", "收藏与有效评论"],
    "measurement_status": "partial",
    "measurement_note": "以运营看板的实际接入状态、统计周期与采集时间为准；不以产量或提交次数推断涨粉效果。",
    "experiment": "同一内容系列只改变一个因素（如开场方式），取得同口径真实数据后再决定是否扩大使用。",
}


def validate_follow_prompt(content):
    script = content.get("script", "")
    hook = content.get("hook")
    prompt = content.get("follow_prompt")
    value = content.get("follow_value")
    if not isinstance(hook, str) or not hook.strip() or not script.startswith(hook):
        raise ValueError("开场hook必须是完整口播的原样开头")
    if not isinstance(prompt, str) or not 8 <= len(prompt) <= 55 or "关注" not in prompt:
        raise ValueError("需要8–55字自然关注提醒follow_prompt，说明持续关注的价值")
    if not script.rstrip().endswith(prompt) or script.count(prompt) != 1:
        raise ValueError("关注提醒须原样融入完整口播结尾，且只出现一次")
    if not isinstance(value, str) or not 6 <= len(value) <= 60:
        raise ValueError("follow_value需要说明这个账号将持续提供的具体价值")
    return {"hook": hook, "follow_prompt": prompt, "follow_value": value}


def validate_engagement_plan(plan, sources):
    if not isinstance(plan, dict):
        raise ValueError("老周尚未交付本条开场与关注方案")
    hooks = plan.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != 2 or any(not isinstance(h, str) or not 8 <= len(h.strip()) <= 40 for h in hooks) or hooks[0] == hooks[1]:
        raise ValueError("需要两个不同且具体的8–40字开场备选")
    if type(plan.get("selected")) is not int or plan["selected"] not in (0, 1):
        raise ValueError("老周必须明确选择一个开场")
    for key in ("viewer_value", "payoff", "opening_visual", "follow_value", "experiment"):
        if not isinstance(plan.get(key), str) or not 6 <= len(plan[key].strip()) <= 300:
            raise ValueError("开场方案缺少具体的" + key)
    known = {s["id"] for s in sources}
    ids = plan.get("source_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(s, str) or s not in known for s in ids):
        raise ValueError("开场方案必须引用已取得的真实来源")
    return plan


def validate_engagement_delivery(content, plan):
    validate_follow_prompt(content)
    if content["hook"] != plan["hooks"][plan["selected"]]:
        raise ValueError("口播未采用老周确定的开场，需要交回修改")
    answer = content.get("payoff_excerpt")
    if not isinstance(answer, str) or len(answer.strip()) < 12 or answer not in content["script"][len(content["hook"]):-len(content["follow_prompt"])]:
        raise ValueError("必须标出正文如何兑现开场承诺，由小审核对，不能只写关注提醒")


def validate_editorial_scenes(script, scenes, assets):
    """Require ordered, complete narration and explicit, justified asset choices.

    This checks coverage, not visual semantics. A separate editorial review of
    the source descriptions is required before rendering.
    """
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 18:
        raise ValueError("需要2–18段完整口播分镜")
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
