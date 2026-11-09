"""Publication safety and platform isolation; no browser/network is started."""
import json
from unittest.mock import Mock

import pytest

from services import creator_connector as connector


def _browser(monkeypatch, tmp_path):
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
