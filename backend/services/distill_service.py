"""
Claude API distillation service.
Analyzes blogger video transcripts to extract content DNA.
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
    "catchphrases": ["口头禅/高频词1", "口头禅2", ...],
    "sentence_pattern": "句式特点（如：短句为主/排比句多/反问句多）",
    "opening_style": "开头风格",
    "closing_style": "结尾风格",
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
2. 给出原文中的具体例子作为支撑
3. 不要泛泛而谈，要说清楚这个博主"为什么火"
4. 保持客观分析，不做价值判断

以下是博主的视频转录文本：
{transcripts}
"""


def analyze_blogger_content(transcripts: list[str]) -> dict | None:
    """
    Analyze blogger transcripts using Claude API.
    Returns the content DNA dict, or None on failure.
    """
    if not transcripts:
        return None

    combined = "\n\n---\n\n".join(
        f"视频 {i+1}:\n{t}" for i, t in enumerate(transcripts)
    )

    # Truncate if too long (Claude context limit consideration)
    max_chars = 80000
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n\n[文本过长已截断]"

    prompt = DISTILL_PROMPT.replace("{transcripts}", combined)

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, base_url=settings.ANTHROPIC_BASE_URL)
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
            return json.loads(json_str)
        return None
    except Exception as e:
        print(f"Distill analysis error: {e}")
        return None
