"""The owner authorizes Douyin/Kuaishou publishing; other accounts remain manual.

Credentials and generic switches cannot enable another platform. The runtime
publish switch separately pauses scheduled uploads without changing this scope.
"""
import json
from pathlib import Path

TRANSPORT_STATE = Path(__file__).resolve().parents[2] / '.runtime/creator-transport-status.json'


class AutomationDisabled(ValueError):
    """The requested platform supports manual operation only."""


AUTOMATED_PLATFORMS = frozenset({"douyin", "kuaishou"})


def automation_enabled(platform):
    return str(platform).strip().lower() in AUTOMATED_PLATFORMS


def publication_transport_state(platform):
    try:
        value = json.loads(TRANSPORT_STATE.read_text()).get(platform, {})
        return {key: value[key] for key in ('ready', 'reason', 'checked_at') if key in value}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def effective_automation_enabled(platform):
    return automation_enabled(platform) and publication_transport_state(platform).get('ready') is not False


def analytics_enabled(platform):
    # Restoring upload authorization does not turn on background account browsing.
    return False


def analytics_state(platform):
    return {"platform": str(platform).strip().lower(), "mode": "manual", "enabled": False,
            "reason": "账号数据自动采集保持关闭；由本人提供官方数据，历史统计及采集时间保留。"}


def automation_state(platform):
    platform = str(platform).strip().lower()
    enabled = automation_enabled(platform)
    return {
        "platform": platform,
        "mode": "automated" if enabled else "manual",
        "enabled": enabled,
        "reason": (("抖音" if platform == "douyin" else "快手") + "按排期自动上传最终成片，每天最多2条，必须选择官方AI生成声明；需要本人验证时暂停并提示。"
                   if enabled else "账号自动化已停用；工厂仅交付素材包，由本人手动上传、查看数据及核对发布结果。"),
    }


def require_automation(platform):
    state = automation_state(platform)
    if not state["enabled"]:
        raise AutomationDisabled(state["reason"])
