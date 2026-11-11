"""The owner authorizes Douyin publishing; other accounts remain manual.

Credentials and generic switches cannot enable another platform. The runtime
publish switch separately pauses scheduled uploads without changing this scope.
"""


class AutomationDisabled(ValueError):
    """The requested platform supports manual operation only."""


def automation_enabled(platform):
    return str(platform).strip().lower() == "douyin"


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
        "reason": ("抖音按排期自动上传最终成片，必须选择官方“内容由AI生成”声明；需要本人验证时暂停并提示。"
                   if enabled else "账号自动化已停用；工厂仅交付素材包，由本人手动上传、查看数据及核对发布结果。"),
    }


def require_automation(platform):
    state = automation_state(platform)
    if not state["enabled"]:
        raise AutomationDisabled(state["reason"])
