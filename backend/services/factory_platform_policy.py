"""Platform automation boundaries, independent of UI switches and credentials.

The factory delivers complete media packages for the owner to upload manually.
Account automation stays disabled for every platform; a login, stored cookie,
environment flag, or ordinary settings update cannot opt in.
"""


class AutomationDisabled(ValueError):
    """The requested platform supports manual operation only."""


def automation_enabled(platform):
    # Fail closed for future platform names as well as the four current ones.
    return False


def automation_state(platform):
    platform = str(platform).strip().lower()
    enabled = automation_enabled(platform)
    return {
        "platform": platform,
        "mode": "automated" if enabled else "manual",
        "enabled": enabled,
        "reason": "账号自动化已停用；工厂仅交付素材包，由本人手动上传、查看数据及核对发布结果。",
    }


def require_automation(platform):
    state = automation_state(platform)
    if not state["enabled"]:
        raise AutomationDisabled(state["reason"])
