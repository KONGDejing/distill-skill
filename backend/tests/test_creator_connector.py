"""Publication safety and platform isolation; no browser/network is started."""
import json
from unittest.mock import Mock

import pytest

from services import creator_connector as connector
from services.factory_platform_policy import AutomationDisabled


def test_kuaishou_hashtag_limit_includes_caption_and_deduplicates(monkeypatch):
    monkeypatch.setattr(connector, "_media", lambda value, suffixes: value)
    payload = {"item_id": "item", "title": "演示", "caption": "内容 #人工智能", "video_path": "video.mp4",
               "tags": ["科技", "科技", "演示", "工具", "模型"]}
    assert connector._payload("kuaishou", "prepare", payload)["tags"] == ["科技", "演示", "工具"]
    with pytest.raises(ValueError, match="4个"):
        connector._payload("kuaishou", "prepare", {**payload, "caption": "#a #b #c #d #e"})


def _browser(monkeypatch, tmp_path):
    # Isolate the retained receipt/audit algorithm; default-policy rejection is
    # exercised separately without this test-only boundary override.
    monkeypatch.setattr(connector, "require_automation", lambda _: None)
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    browser = object.__new__(connector._Browser)
    browser.platform = "douyin"
    browser.directory = connector._directories("douyin")
    browser.prepared = {"item_id": "item-1", "title": "Demo"}
    browser.submission_attempted = None
    browser.inspect = Mock(return_value={"authenticated": True})
    browser.adapter = Mock()
    browser.adapter.supports_publish = True
    button = browser.adapter.publication_control.return_value
    button.is_visible.return_value = button.is_enabled.return_value = True
    button.get_attribute.return_value = None
    return browser, button


def test_missing_prepared_item_proves_no_submission_attempt(monkeypatch, tmp_path):
    browser, button = _browser(monkeypatch, tmp_path)
    browser.prepared = None
    with pytest.raises(ValueError, match="先准备同一"):
        browser.publish({"item_id": "item-1"})
    assert connector._failure_fields("publish", browser) == {"submission_attempted": False}
    assert not (browser.directory / "submissions.json").exists()
    button.click.assert_not_called()


def test_disabled_official_control_is_not_clicked_or_recorded(monkeypatch, tmp_path):
    browser, button = _browser(monkeypatch, tmp_path)
    button.is_enabled.return_value = False
    with pytest.raises(ValueError, match="未点击"):
        browser.publish({"item_id": "item-1"})
    assert connector._failure_fields("publish", browser) == {"submission_attempted": False}
    assert not (browser.directory / "submissions.json").exists()
    button.click.assert_not_called()


def test_click_timeout_is_uncertain_and_cannot_be_replayed(monkeypatch, tmp_path):
    browser, button = _browser(monkeypatch, tmp_path)
    button.click.side_effect = TimeoutError("Unknown click outcome")
    with pytest.raises(TimeoutError):
        browser.publish({"item_id": "item-1"})
    assert connector._ledger("douyin")["item-1"]["status"] == "attempting"
    assert connector._failure_fields("publish", browser) == {
        "submission_attempted": True, "submission_uncertain": True}
    with pytest.raises(ValueError, match="禁止自动重试"):
        browser.publish({"item_id": "item-1"})
    button.click.assert_called_once()


def test_official_receipt_records_submission_without_claiming_publication(monkeypatch, tmp_path):
    browser, button = _browser(monkeypatch, tmp_path)
    evidence = {"type": "success_page", "url": "https://creator.douyin.com/success", "text": "发布成功"}
    browser.adapter.wait_for_submission.return_value = evidence
    result = browser.publish({"item_id": "item-1"})
    assert result["status"] == "submitted" and result["published"] is False
    assert connector._ledger("douyin")["item-1"]["evidence"] == evidence
    assert browser.publish({"item_id": "item-1"})["status"] == "submitted"
    button.click.assert_called_once()


@pytest.mark.parametrize("evidence", [
    {"type": "clicked", "url": "https://creator.douyin.com/upload"},
    {"type": "success_page", "url": "https://example.com/success", "text": "发布成功"},
    {"type": "success_page", "url": "https://creator.douyin.com/upload", "text": "未发布成功"},
    {"type": "api_receipt", "url": "https://creator.douyin.com/api/publish", "item_id": "id"},
])
def test_unverified_evidence_never_becomes_submitted(monkeypatch, tmp_path, evidence):
    browser, _ = _browser(monkeypatch, tmp_path)
    browser.adapter.wait_for_submission.return_value = evidence
    with pytest.raises(ValueError, match="缺少官方成功回执"):
        browser.publish({"item_id": "item-1"})
    assert connector._ledger("douyin")["item-1"]["status"] == "attempting"
    assert connector._failure_fields("publish", browser)["submission_uncertain"] is True


def test_corrupt_ledger_does_not_authorize_safe_retry(monkeypatch, tmp_path):
    browser, button = _browser(monkeypatch, tmp_path)
    (browser.directory / "submissions.json").write_text("{broken")
    with pytest.raises(ValueError, match="账本不可读"):
        browser.publish({"item_id": "item-1"})
    assert connector._failure_fields("publish", browser) == {"submission_uncertain": True}
    button.click.assert_not_called()


def test_platform_directories_are_private_and_never_cross_read_results(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    first = connector._directories("douyin")
    second = connector._directories("bilibili")
    assert first != second
    assert first.stat().st_mode & 0o777 == 0o700
    assert (first / "profile").stat().st_mode & 0o777 == 0o700
    identifier = "44065963-9bb7-4e3d-bc3b-8434d68c61d8"
    connector._write(first / "results" / f"{identifier}.json", {"status": "prepared"})
    assert connector.command_result("bilibili", identifier)["status"] == "failed"
    with pytest.raises(ValueError):
        connector.command_result("douyin", "../state")
    with pytest.raises(ValueError):
        connector.platform_directory("wechat_channels")


def test_payload_drops_credentials_and_rejects_media_outside_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "ROOT", tmp_path)
    storage = tmp_path / "backend/storage"
    storage.mkdir(parents=True)
    video = storage / "demo.mp4"
    video.write_bytes(b"fixture")
    payload = {"item_id": "demo", "title": "Demo", "caption": "Text", "video_path": str(video),
               "password": "never-store-this", "cookies": "never-store-this"}
    cleaned = connector._payload("douyin", "prepare", payload)
    assert "password" not in cleaned and "cookies" not in cleaned
    assert cleaned["ai_generated"] is True
    (storage / "escape.mp4").symlink_to(tmp_path / "outside.mp4")
    (tmp_path / "outside.mp4").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="storage"):
        connector._payload("douyin", "prepare", {**payload, "video_path": str(storage / "escape.mp4")})


def test_stopped_daemon_never_looks_authenticated(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    directory = connector._directories("douyin")
    connector._write(directory / "state.json", {"authenticated": True, "session_active": True, "qr_ready": True})
    monkeypatch.setattr(connector, "_alive", lambda platform: False)
    state = connector.connection_state("douyin")
    assert not state["authenticated"] and not state["session_active"] and not state["qr_ready"]


def test_restore_quarantines_old_pending_commands_but_preserves_receipts_and_new_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    directory = connector._directories('douyin')
    original = {}
    for name, status, policy in [('old', 'queued', None), ('receipt', 'submitted', None),
                                  ('uncertain', 'failed', None), ('new', 'queued', connector.AUTOMATION_POLICY_VERSION)]:
        command = {'action': 'publish', 'automation_policy_version': policy}
        result = {'status': status, 'evidence': {'fixture': name}}
        connector._write(directory / 'commands' / (name + '.json'), command)
        connector._write(directory / 'results' / (name + '.json'), result)
        original[name] = (directory / 'results' / (name + '.json')).read_bytes()
    connector._retire_legacy_commands('douyin')
    retired = connector._read(directory / 'results' / 'old.json')
    assert retired['status'] == 'failed' and retired['submission_uncertain'] is True
    for name in ['receipt', 'uncertain', 'new']:
        assert (directory / 'results' / (name + '.json')).read_bytes() == original[name]


def test_manual_connection_never_exposes_even_fresh_cached_qr(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    alive = Mock(side_effect=AssertionError("Manual status cannot inspect a process"))
    monkeypatch.setattr(connector, "_alive", alive)
    directory = connector._directories("bilibili")
    picture = directory / "login.png"
    picture.write_bytes(b"retained-image-fixture")
    connector._write(directory / "state.json", {"authenticated": True, "session_active": True, "qr_ready": True,
                     "state_code": "waiting_scan", "updated_at": connector._now()})
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    state = connector.connection_state("bilibili")
    assert state["status"] == "manual" and state["cache_only"]
    assert not state["qr_ready"] and state["qr_version"] is None
    assert state["login_command"] is None and not state["authenticated"]
    assert state["cached_authenticated"] and not any(state["capabilities"].values())
    assert all(path.read_bytes() == data for path, data in before.items())
    alive.assert_not_called()


def test_refresh_requests_never_queue_or_mutate_cached_login(monkeypatch, tmp_path):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path / "untouched")
    for _ in range(2):
        with pytest.raises(AutomationDisabled):
            connector.submit_command("kuaishou", "login")
    assert not connector.RUNTIME.exists()


@pytest.mark.parametrize("action", ["login", "inspect", "prepare", "publish"])
def test_retained_browser_object_cannot_operate_or_delete_cached_files(monkeypatch, tmp_path, action):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    browser = object.__new__(connector._Browser)
    browser.platform, browser.directory = "kuaishou", connector._directories("kuaishou")
    browser.adapter = Mock()
    picture = browser.directory / "login.png"
    picture.write_bytes(b"retained-image")
    args = ({"item_id": "fixture-item"},) if action in {"prepare", "publish"} else ()
    with pytest.raises(AutomationDisabled):
        getattr(browser, action)(*args)
    assert not browser.adapter.mock_calls
    assert picture.read_bytes() == b"retained-image"


def test_login_routes_never_expose_upload_payload_or_expired_image(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.creator_platforms import router
    app = FastAPI()
    app.include_router(router, prefix='/api/factory')
    monkeypatch.setattr(connector, 'connection_state', lambda _: {'qr_ready': False, 'state_code': 'qr_expired'})
    with TestClient(app) as client:
        response = client.get('/api/factory/platforms/bilibili/login-state')
        assert response.status_code == 200 and 'no-store' in response.headers['cache-control']
        assert client.get('/api/factory/platforms/bilibili/login-image').status_code == 409
        result = {'action': 'login', 'command_id': 'test', 'status': 'running', 'payload': {'private': 'must-not-be-returned'}}
        monkeypatch.setattr(connector, 'command_result', lambda *args: result)
        response = client.get('/api/factory/platforms/bilibili/login-commands/test')
        assert response.status_code == 200 and 'payload' not in response.json()
        result['action'] = 'publish'
        assert client.get('/api/factory/platforms/bilibili/login-commands/test').status_code == 404
