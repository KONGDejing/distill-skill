"""
Script generation service using Claude API.
Generates original short-video scripts based on blogger content DNA.
"""
import json
import anthropic
from config import settings

SCRIPT_GEN_PROMPT = """你是一位资深短视频内容策划。请基于以下博主的"内容基因"，生成 {count} 条完全原创的短视频口播文案。

## 博主内容基因

价值观定位：
{value_positioning}

爆款技巧：
{viral_techniques}

选题偏好：
{content_preferences}

话术风格：
{language_style}

## 最近已生成的选题（避免重复）：
{recent_titles}

## 生成要求

1. 保持价值观定位和话术风格一致，但选题和具体内容必须是全新的
2. 使用相同的爆款技巧框架
3. 模仿话术风格，但不能照搬原话和金句
4. 选题要符合内容偏好中的方向
5. 每条文案控制在 40-90 秒口播时长（约 150-350 字）

请严格按以下 JSON 数组格式输出：
[
  {{
    "title": "选题标题",
    "hook": "开头钩子（前3秒，抓人眼球的一句）",
    "script": "完整口播文案（正文+结尾引导）",
    "hashtags": ["标签1", "标签2", "标签3", "标签4", "标签5"],
    "visual_suggestion": "画面建议（这段文案适合配什么画面/图片/素材）"
  }}
]

只输出 JSON，不要加任何额外文字。"""


def generate_scripts(
    value_positioning: dict,
    viral_techniques: dict,
    content_preferences: dict,
    language_style: dict,
    recent_titles: list[str] = None,
    count: int = 5,
) -> list[dict] | None:
    """
    Generate original scripts using Claude API.
    Returns list of script dicts, or None on failure.
    """
    prompt = SCRIPT_GEN_PROMPT.format(
        count=count,
        value_positioning=json.dumps(value_positioning, ensure_ascii=False, indent=2),
        viral_techniques=json.dumps(viral_techniques, ensure_ascii=False, indent=2),
        content_preferences=json.dumps(content_preferences, ensure_ascii=False, indent=2),
        language_style=json.dumps(language_style, ensure_ascii=False, indent=2),
        recent_titles="\n".join(f"- {t}" for t in (recent_titles or ["无"])) if recent_titles else "无",
    )

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, base_url=settings.ANTHROPIC_BASE_URL)
        message = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=8192,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response (handle thinking models that return multiple blocks)
        text_blocks = [b.text for b in message.content if b.type == "text"]
        response_text = text_blocks[0] if text_blocks else ""

        # Extract JSON array
        json_start = response_text.find("[")
        json_end = response_text.rfind("]") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response_text[json_start:json_end]
            return json.loads(json_str)
        return None
    except Exception as e:
        print(f"Script generation error: {e}")
        return None
