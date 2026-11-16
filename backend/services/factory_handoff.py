"""Deterministic, local text handoffs for account owners to publish manually."""
from copy import deepcopy


HANDOFF_VERSION = 5
_PLATFORM_NAMES = {"douyin": "抖音", "xiaohongshu": "小红书", "kuaishou": "快手", "bilibili": "哔哩哔哩"}
_AI_DISCLOSURE = "AI辅助整理文案、配音和剪辑；参考来源见正文及随附资料"
_REPLY_TEMPLATES = (
    {"question": "这是你亲自测试或现场拍摄的吗？",
     "reply": "这条内容依据所列来源整理，不是我的实测或亲历记录。具体能力和适用范围请以原始资料为准。"},
    {"question": "资料来源在哪里？",
     "reply": "资料来源见正文和随附资料。你想核对哪一句？可以指出具体内容，再一起对照原文。"},
    {"question": "这个结论适用于所有情况吗？",
     "reply": "具体条件和限制请以原文为准，不能从这条内容推断所有场景都适用。你关心哪个使用场景？"},
)


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _texts(value):
    values = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    return list(dict.fromkeys(text for entry in values if (text := _text(entry))))


def _sources(content):
    result = []
    for source in content.get("sources") or []:
        if isinstance(source, str):
            source = {"url": source}
        if not isinstance(source, dict) or not _text(source.get("url")):
            continue
        # Carry evidence references, never unrelated account or connector fields.
        entry = {key: source[key] for key in ("id", "title", "url", "published_at", "fetched_at", "kind")
                 if source.get(key) is not None}
        if entry not in result:
            result.append(deepcopy(entry))
    if not result:
        raise ValueError("人工发布包缺少资料来源，请先补齐真实来源")
    return result


def build_handoff(content, variant, distribution=None, publication_warning=None):
    """Use approved platform copy; optional extras never require model calls.

    ``variant.manual_handoff`` may contain reviewed writing overrides. Missing
    extras use existing approved wording or generic prompts, without new claims.
    This function does not assign a platform, verify publication, or change state.
    """
    if not isinstance(content, dict) or not isinstance(variant, dict):
        raise ValueError("人工发布包需要定稿内容和平台文案")
    platform = _text(variant.get("platform"))
    title, caption = _text(variant.get("title")), _text(variant.get("caption"))
    tags = list(dict.fromkeys(tag for value in _texts(variant.get("tags"))
                             if (tag := value.lstrip("#＃").strip())))
    if not platform or not title or not caption or not tags:
        raise ValueError("平台定稿必须包含 platform、title、caption 和 tags，不能凭空补写发布事实")
    if distribution is not None and not isinstance(distribution, dict):
        raise ValueError("发行分配必须是包含唯一 platform 的对象")
    route = {key: deepcopy(distribution[key]) for key in ("platform", "day", "timezone", "reason", "policy", "manual_platforms", "douyin_selected", "kuaishou_selected", "kuaishou_auto_policy", "editor_id")
             if distribution and distribution.get(key) is not None}
    from services.factory_editorial_policy import POLICY
    new_policy = route.get('policy') == POLICY
    if new_policy:
        allowed = set(route.get('manual_platforms', [])) | {'douyin'}  # A copy is not publication authorization.
        if platform not in allowed:
            raise ValueError('平台不在本条人工交付或抖音预选范围内')
    elif route and route.get('platform') != platform:
        raise ValueError('历史发布包跨平台分配不一致')
    if publication_warning is not None and not isinstance(publication_warning, str):
        raise ValueError("历史发布提醒必须是可供本人阅读的文字")

    extra = variant.get("manual_handoff")
    extra = extra if isinstance(extra, dict) else {}
    hooks = _texts(extra.get("hooks"))
    for fallback in (_text(content.get("hook")), title, "先看来源，再看这条内容能说明什么。",
                     "这条内容的结论和边界，先分开看。"):
        if len(hooks) >= 3:
            break
        if fallback and fallback not in hooks:
            hooks.append(fallback)
    follow = (_text(content.get("follow_prompt")) or _text(extra.get("follow_prompt"))
              or "如果这些内容对你有帮助，欢迎关注，一起对照来源看懂新进展。")
    replies = []
    values = extra.get("reply_templates")
    for value in values if isinstance(values, (list, tuple)) else []:
        if not isinstance(value, dict):
            continue
        question, reply = _text(value.get("question")), _text(value.get("reply"))
        if question and reply and question not in {entry["question"] for entry in replies}:
            replies.append({"question": question, "reply": reply})
    for fallback in _REPLY_TEMPLATES:
        if len(replies) >= 3:
            break
        if fallback["question"] not in {entry["question"] for entry in replies}:
            replies.append(dict(fallback))

    name = _PLATFORM_NAMES.get(platform, platform)
    steps = [
        f'使用本条完整MP4和{name}专用标题、正文，按需手动选用小红书或快手；两平台均不启用自动化。',
        '全部6条原创均保存；每位剪辑师各1条预选抖音，抖音每天最多3条。查看工厂实际回执，避免对同一平台重复上传。',
        '如本条有历史提交、审核或结果未明，请本人先在对应平台确认是否已存在；确认之前不要再次上传。',
        '本人使用官方应用或创作后台上传完整MP4；大标题已放中上部，口播与关注提醒已写入成片。',
        '填写本包发布标题和正文，正文已合并相关话题与真实AI制作说明。',
        '按官方要求如实填写AI制作声明，并由本人核对后提交。',
        '本人记录作品链接和实际发布状态；package_ready只表示素材齐全，不代表平台已接收。',
        '互动模板仅用于本人置顶或回复收到的真实留言。',
    ]
    return {
        "version": HANDOFF_VERSION, "platform": platform, "title": title, "caption": caption, "tags": tags,
        "description": caption + "\n\n" + " ".join("#" + tag for tag in tags) + "\n\n" + (_text(extra.get("ai_disclosure")) or _AI_DISCLOSURE),
        "cover_text": _text(extra.get("cover_text")) or title,
        "alternate_titles": _texts(extra.get("alternate_titles"))[:3], "hooks": hooks[:3],
        "follow_prompt": follow, "ai_disclosure": _text(extra.get("ai_disclosure")) or _AI_DISCLOSURE,
        "pinned_comment": (_text(extra.get("pinned_comment"))
                           or "资料来源见正文及随附资料。你最想进一步核对哪一点？欢迎留下具体问题，一起对照原文。"),
        "reply_templates": replies, "sources": _sources(content), "distribution": route,
        "publication_warning": _text(publication_warning), "manual_upload_steps": steps,
    }


def handoff_files(handoff):
    """Return filename -> Unicode text; callers write these as plain UTF-8 files."""
    if handoff.get("version") != HANDOFF_VERSION:
        raise ValueError("不支持的人工发布包版本")
    route = handoff["distribution"]
    name = _PLATFORM_NAMES.get(handoff["platform"], handoff["platform"])
    copy = [f"人工发布文案｜{name}", "标题", handoff["title"], "", "正文（整段复制）", handoff["description"]]
    interaction = ["账号本人互动文案", "以下仅供账号本人置顶或回复真实留言；这些问题是模板，不表示已收到这些留言。",
                   "不得用模型生成的文字冒充观众评论或互动记录。", "", "置顶评论", handoff["pinned_comment"]]
    for index, entry in enumerate(handoff["reply_templates"], 1):
        interaction += ["", f"回复模板 {index}", "适用的真实问题：" + entry["question"], "本人回复：" + entry["reply"]]
    sources = ["资料来源", "请对照原文确认具体条件与限制；引用来源不等于本账号已实测或亲历。"]
    for index, source in enumerate(handoff["sources"], 1):
        sources += ["", f"{index}. {_text(source.get('title')) or '原始来源'}", source["url"]]
        if source.get("published_at"):
            sources.append("来源日期：" + str(source["published_at"]))
        if source.get("fetched_at"):
            sources.append("资料采集时间：" + str(source["fetched_at"]))
    instructions = ["手动发布说明", f"本包提供{name}专用文案，完整成片由本人按需手动上传。",
                    "素材包就绪（package_ready）不代表已提交、已通过审核或已发布。"]
    if route.get("day"):
        instructions.append("计划日期：" + str(route["day"]))
    if handoff["publication_warning"]:
        instructions += ["", "历史发布提醒", handoff["publication_warning"]]
    instructions += ["", *[f"{i}. {step}" for i, step in enumerate(handoff["manual_upload_steps"], 1)]]
    return {name: "\n".join(lines).rstrip() + "\n" for name, lines in (
        ("发布文案.txt", copy), ("互动回复模板.txt", interaction),
        ("资料来源.txt", sources), ("手动发布说明.txt", instructions))}
