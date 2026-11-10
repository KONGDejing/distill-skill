"""Platform automation boundaries, independent of UI switches and credentials.

Xiaohongshu has no implemented official authorization integration here. Browser
automation stays disabled until a real authorized capability replaces it; a
login, stored cookie, environment flag, or ordinary settings update cannot opt in.
"""


class AutomationDisabled(ValueError):
    """The requested platform supports manual operation only."""


def automation_enabled(platform):
    return str(platform).strip().lower() != "xiaohongshu"


def automation_state(platform):
    platform = str(platform).strip().lower()
    enabled = automation_enabled(platform)
    return {
        "platform": platform,
        "mode": "automated" if enabled else "manual",
        "enabled": enabled,
        "reason": None if enabled else "小红书浏览器自动化已停用；未接入官方授权能力，仅支持手动发布与查看。",
    }


def require_automation(platform):
    state = automation_state(platform)
    if not state["enabled"]:
        raise AutomationDisabled(state["reason"])
