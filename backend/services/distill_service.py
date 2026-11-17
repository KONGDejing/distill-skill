"""
Configured-model distillation with optional real visual samples.
Analyzes reference transcripts and sampled frames without copying account identity.
"""
import json
import anthropic
from config import settings

DISTILL_PROMPT = """你是一位顶级短视频内容策略专家。请深度分析以下博主的视频转录文本，提取这个博主的内容基因。

请严格按照以下 JSON 格式输出分析结果：

{
  "value_positioning": {
    "core_values": ["核心理念1", "核心理念2", ...],
    "persona": "人设描述（一句话概括这个博主的人设）",
    "persona_traits": ["人设特征1", "特征2", ...],
    "target_audience": "目标受众画像",
    "emotional_appeal": "主要调动什么情绪（如：焦虑、共鸣、愤怒、向上等）",
    "differentiation": "与同赛道博主的差异化"
  },
  "viral_techniques": {
    "hook_patterns": [
      {"type": "钩子类型（如：反常识开头、痛点前置、数字悬念）", "description": "具体描述", "example": "原文中的例子"}
    ],
    "narrative_structure": "叙事结构（如：问题→分析→方案→展望）",
    "rhythm_control": "节奏控制方式（如：快节奏剪辑/停顿留白/递进加速）",
    "interaction_guide": "互动引导方式（如：提问式结尾/投票式引导/槽点预埋）",
    "climax_design": "高潮/记忆点设计方式"
  },
  "content_preferences": {
    "top_topics": ["高频话题1", "话题2", ...],
    "content_angles": ["常用切入角度1", "角度2", ...],
    "taboo_topics": ["避讳的话题"],
    "format_preference": "内容形式偏好（如：口播/情景剧/干货讲解/vlog）",
    "optimal_duration": "最佳时长范围（秒）"
  },
  "language_style": {
    "tone": "整体语气（如：犀利直接/温和治愈/幽默调侃）",
    "catchphrases": ["只保留可泛化的话术习惯，不保留姓名、账号名、地域身份、公司/学校、感谢观看、我是XXX等个人标识话术"],
    "sentence_pattern": "句式特点（如：短句为主/排比句多/反问句多）",
    "opening_style": "开头风格，只描述结构方法，不保留原博主自我介绍或账号称呼",
    "closing_style": "结尾风格，只描述互动方式，不保留感谢观看、我是XXX、关注XXX等署名式话术",
    "pace": "语速感受（快/中/慢）"
  },
  "content_calendar": {
    "estimated_frequency": "估计发布频率",
    "best_content_types": ["效果最好的内容类型"],
    "suggested_posting_times": ["建议发布时段"]
  }
}

分析要求：
1. 必须从每个维度的具体细节出发，给出可操作的结论
2. 给出原文中的具体例子作为支撑，但例子只能用于分析，不能要求后续文案复用
3. 具体分析辨识度和表达方法；缺少真实播放与涨粉数据时，不声称解释了流量因果。
4. 保持客观分析，不做价值判断
5. 严禁把博主个人身份沉淀为可复用内容基因，包括姓名、昵称、账号名、地域身份、公司/学校、家庭成员、联系方式、个人经历细节、"我是XXX"、"感谢大家观看"、"关注XXX"等署名式/个人标识内容
6. 如果转录里出现个人信息，只抽象成可泛化的方法论，例如“自我介绍式开场”应抽象成“直接建立话题身份”，不要保留具体身份文本

7. 样本不能支持发布频率、最佳时段和涨粉效果时明确写“样本不足”，不得猜测。\n8. 若提供真实时间点抽帧，只分析实际可见构图、标题位置、图解和字幕；不能把抽帧当完整运动审查，不能声称听过未提供的声音。请额外输出visual_style对象，字段包括cover、opening、picture_layout、evidence_highlighting、subtitle、adaptation_rules、scope。无图片时visual_style.scope必须写“仅文本，未核对画面”。\n以下是博主的视频转录文本：
{transcripts}
"""


def analyze_blogger_content(transcripts: list[str], *, image_paths=None, video_metadata=None) -> dict | None:
    """
    Analyze actual reference transcripts and optional frame samples with the configured model.
    Returns the content DNA dict, or None on failure.
    """
    if not transcripts:
        return None

    combined = "\n\n---\n\n".join(
        f"视频 {i+1}:\n{t}" for i, t in enumerate(transcripts)
    )

    # Bound the reference packet independently of the configured provider.
    max_chars = 80000
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n\n[文本过长已截断]"

    prompt = DISTILL_PROMPT.replace("{transcripts}", combined)

    if video_metadata:
        prompt += "\n实际视频测量与抽帧范围：\n" + json.dumps(video_metadata, ensure_ascii=False)
    try:
        from services.factory_llm import model_runtime
        runtime = model_runtime()
        if runtime['provider'] == 'codex':
            from services.factory_codex import create_response
            message = create_response(model=runtime['model'], system='你是内容研究员。只根据提供的真实转写和抽帧提炼可操作表达方法，输出JSON。',
                messages=[{'role':'user','content':prompt}], max_tokens=6000, image_paths=image_paths, reasoning_effort='high')
        elif runtime['provider'] == 'openai':
            from services.factory_openai import create_response
            message = create_response(model=runtime['model'], system='只依据实际样本分析，输出JSON。',
                messages=[{'role':'user','content':prompt}], max_tokens=6000)
        else:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, auth_token=settings.ANTHROPIC_AUTH_TOKEN or None, base_url=settings.ANTHROPIC_BASE_URL)
            message = client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=4096,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
            )

        # Extract text from response (handle thinking models that return multiple blocks)
        text_blocks = [b.text for b in message.content if b.type == "text"]
        response_text = text_blocks[0] if text_blocks else ""

        # Extract JSON from response
        # Try to find JSON block
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response_text[json_start:json_end]
            value = json.loads(json_str)
            required = ('value_positioning','viral_techniques','content_preferences','language_style','content_calendar')
            if not isinstance(value, dict) or not all(isinstance(value.get(key), dict) for key in required):
                return None
            if isinstance(value.get('visual_style'), dict):
                value['viral_techniques'] = {**value['viral_techniques'], 'visual_style': value['visual_style']}
            value['_model_usage'] = {'provider':runtime['provider'],'model':runtime['model'],
                'input_tokens':message.usage.input_tokens,'output_tokens':message.usage.output_tokens,
                'actual_reference_images':len(image_paths or []) if runtime['provider']=='codex' else 0}
            return value
        return None
    except Exception as e:
        print(f"Distill analysis error: {type(e).__name__}")
        return None
