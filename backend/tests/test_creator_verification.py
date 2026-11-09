import os
from unittest.mock import Mock

import pytest

from services import creator_connector as connector
from services import creator_verification as verification
from services.creator_login_status import douyin_verification


@pytest.mark.parametrize("body,kind", [
    ("需在手机上进行确认", "phone"),
    ("需在手机上进行确认 身份验证 为保障账号安全，请先完成身份验证", "identity"),
    ("接收短信验证码 验证登录密码", "identity"),
    ("安全验证", "identity"),
    ("扫码登录 二维码失效 点击刷新", None),
])
def test_observed_confirmation_and_identity_are_distinct(body, kind):
    result = douyin_verification(body)
    assert (result or {}).get("kind") == kind


def test_only_current_browser_snapshot_can_correct_network_error(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    monkeypatch.setattr(connector, "_alive", lambda _: True)
    directory = connector._directories("douyin")
    pid = directory / "daemon.pid"
    pid.write_text("test-owner")
    state = {"state_code": "network_error", "status": "官方页面连接失败", "url": "https://creator.douyin.com/",
             "authenticated": False, "updated_at": connector._now()}
    connector._write(directory / "state.json", state)
    snapshot = directory / "login-ui.json"
    connector._write(snapshot, {"url": state["url"], "body": "需在手机上进行确认 身份验证"})
    assert connector.connection_state("douyin")["verification"]["kind"] == "identity"
    assert connector.connection_state("douyin")["state_code"] == "needs_verification"
    assert not connector.connection_state("douyin")["authenticated"]
    monkeypatch.setattr(connector, "_ensure_daemon", lambda _: None)
    with pytest.raises(ValueError, match="未刷新二维码"):
        connector.submit_command("douyin", "login")
    assert not list((directory / "commands").iterdir())
    os.utime(snapshot, (pid.stat().st_mtime - 10, pid.stat().st_mtime - 10))
    assert connector.connection_state("douyin")["state_code"] == "network_error"


def test_verification_token_and_origin_are_both_required(monkeypatch, tmp_path):
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
    connect = Mock()
    monkeypatch.setattr(verification, "_official_page", connect)
    with pytest.raises(ValueError):
        verification.interact("douyin", action, "frame", **extra)
    connect.assert_not_called()


def test_code_is_not_queued_or_echoed_in_error_responses(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.creator_platforms import router
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    token = verification.page_token("douyin")
    application = FastAPI()
    application.include_router(router, prefix="/api/factory")
    handler = Mock(side_effect=RuntimeError("must not echo request input"))
    monkeypatch.setattr(verification, "interact", handler)
    code = "451239"
    with TestClient(application) as client:
        response = client.post("/api/factory/platforms/douyin/verification-input",
                               headers={"X-Factory-Verification": token, "Origin": "http://testserver"},
                               json={"action": "fill_code", "frame_id": "test", "code": code})
        assert response.status_code == 503 and code not in response.text
        assert "must not echo" not in response.text
        handler.assert_called_once_with("douyin", "fill_code", "test", None, None, code)
        assert client.post("/api/factory/platforms/douyin/verification-input", json={"code": code}).status_code == 403
    assert not list((connector.platform_directory("douyin") / "commands").iterdir())
    assert not list((connector.platform_directory("douyin") / "results").iterdir())
    assert all(code not in path.read_text() for path in tmp_path.rglob("*.json"))
