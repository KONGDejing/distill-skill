from unittest.mock import Mock

import pytest

from services import creator_connector as connector
from services import creator_verification as verification
from services.creator_login_status import douyin_verification
from services.factory_platform_policy import AutomationDisabled


@pytest.mark.parametrize("body,kind", [
    ("需在手机上进行确认", "phone"),
    ("需在手机上进行确认 身份验证 为保障账号安全，请先完成身份验证", "identity"),
    ("接收短信验证码 验证登录密码", "identity"),
    ("需在手机上进行确认 接收短信验证码 短信已发送至 重新发送 取消 验证", "identity"),
    ("安全验证", "identity"),
    ("扫码登录 二维码失效 点击刷新", None),
])
def test_observed_confirmation_and_identity_are_distinct(body, kind):
    result = douyin_verification(body)
    assert (result or {}).get("kind") == kind


def test_manual_state_does_not_resume_cached_identity_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    directory = connector._directories("kuaishou")
    connector._write(directory / "state.json", {"state_code": "needs_verification", "authenticated": False,
                     "verification": {"kind": "identity"}, "updated_at": connector._now()})
    connector._write(directory / "login-ui.json", {"body": "身份验证"})
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    state = connector.connection_state("kuaishou")
    assert state["state_code"] == "manual" and state["verification"] is None
    with pytest.raises(AutomationDisabled):
        connector.submit_command("kuaishou", "login")
    assert not list((directory / "commands").iterdir())
    assert all(path.read_bytes() == data for path, data in before.items())


def test_verification_token_and_origin_are_both_required(monkeypatch, tmp_path):
    # Retain unit coverage of token validation without authorizing production access.
    monkeypatch.setattr(verification, "require_automation", lambda _: None)
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    token = verification.page_token("douyin")
    verification.authorize("douyin", token, "http://factory.test", "http://factory.test")
    with pytest.raises(ValueError):
        verification.authorize("douyin", "invalid", "http://factory.test", "http://factory.test")
    with pytest.raises(ValueError):
        verification.authorize("douyin", token, "https://untrusted.test", "http://factory.test")
    with pytest.raises(ValueError):
        verification.authorize("douyin", token, None, "http://factory.test", "cross-site")
    path = connector.platform_directory("douyin") / "verification-token.json"
    assert path.stat().st_mode & 0o777 == 0o600
    connector._write(path, {"token": token, "expires_at": 0})
    with pytest.raises(ValueError):
        verification.authorize("douyin", token, "http://factory.test", "http://factory.test")


@pytest.mark.parametrize("action,extra", [
    ("navigate", {}), ("click", {"x": -0.1, "y": 0.2}),
    ("click", {"x": float("nan"), "y": 0.2}),
    ("fill_code", {"code": "password-not-a-code"}),
])
def test_invalid_input_never_reaches_the_browser(monkeypatch, action, extra):
    monkeypatch.setattr(verification, "require_automation", lambda _: None)
    connect = Mock()
    monkeypatch.setattr(verification, "_official_page", connect)
    with pytest.raises(ValueError):
        verification.interact("douyin", action, "frame", **extra)
    connect.assert_not_called()


def test_disabled_verification_request_never_reads_or_echoes_code(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.creator_platforms import router
    monkeypatch.setattr(connector, "RUNTIME", tmp_path / "untouched")
    application = FastAPI()
    application.include_router(router, prefix="/api/factory")
    handler = Mock(side_effect=AssertionError("Must not operate the official page"))
    monkeypatch.setattr(verification, "interact", handler)
    code = "451239"
    with TestClient(application) as client:
        response = client.post("/api/factory/platforms/kuaishou/verification-input",
                               json={"action": "fill_code", "frame_id": "test", "code": code})
        assert response.status_code == 409 and code not in response.text
        assert "自动化已停用" in response.text
        handler.assert_not_called()
    assert not connector.RUNTIME.exists()
