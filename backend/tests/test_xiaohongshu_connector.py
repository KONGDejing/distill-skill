"""Browser-connector guards, without opening a browser or contacting a platform."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import xiaohongshu_connector as connector


def _browser(monkeypatch, tmp_path, button=None):
    monkeypatch.setattr(connector, "PRIVATE", tmp_path)
    browser = object.__new__(connector._Browser)
    browser.prepared = {"item_id": "item-1", "title": "测试标题", "caption": "测试正文",
                        "ai_disclosure": "笔记含AI合成内容"}
    browser.submission_attempted = None
    browser.page = Mock()
    browser.page.url = "https://creator.xiaohongshu.com/publish/publish"
    title = Mock()
    title.input_value.return_value = "测试标题"
    browser.page.get_by_placeholder.return_value = SimpleNamespace(first=title)
    declaration = Mock()
    declaration.is_visible.return_value = True
    browser.page.get_by_text.return_value = SimpleNamespace(first=declaration)
    editor = Mock()
    editor.inner_text.return_value = "测试正文"
    monkeypatch.setattr(browser, "_visible", lambda locators: editor)
    monkeypatch.setattr(browser, "_publish_button", Mock(return_value=button))
    monkeypatch.setattr(browser, "_wait_upload", Mock())
    return browser


def test_visible_locator_skips_hidden_first_match():
    hidden, visible = Mock(), Mock()
    hidden.is_visible.return_value = False
    visible.is_visible.return_value = True
    locator = Mock()
    locator.all.return_value = [hidden, visible]
    assert connector._Browser._visible([locator]) is visible


def test_publish_button_searches_visible_matches_in_child_frames():
    empty = Mock()
    empty.all.return_value = []
    hidden, visible = Mock(), Mock()
    hidden.is_visible.return_value = False
    visible.is_visible.return_value = True
    matches = Mock()
    matches.all.return_value = [hidden, visible]
    main, frame = Mock(), Mock()
    main.get_by_role.return_value = main.get_by_text.return_value = empty
    frame.get_by_role.return_value = empty
    frame.get_by_text.return_value = matches
    browser = object.__new__(connector._Browser)
    browser.page = SimpleNamespace(frames=[main, frame])
    assert browser._publish_button() is visible


def test_missing_control_is_proven_pre_submission_failure(monkeypatch, tmp_path):
    browser = _browser(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="未找到明确"):
        browser.publish({"item_id": "item-1"})
    assert connector._failure_fields("publish", browser) == {"submission_attempted": False}
    assert not (tmp_path / "submissions.json").exists()
    browser.page.on.assert_not_called()


def test_timeout_after_attempt_record_is_uncertain_and_cannot_retry(monkeypatch, tmp_path):
    button = Mock()
    button.is_enabled.return_value = True
    button.get_attribute.return_value = None
    button.click.side_effect = TimeoutError("Click result is unknown")
    browser = _browser(monkeypatch, tmp_path, button)
    with pytest.raises(TimeoutError):
        browser.publish({"item_id": "item-1"})
    assert connector._submission_ledger()["item-1"]["status"] == "attempting"
    assert connector._failure_fields("publish", browser) == {
        "submission_attempted": True, "submission_uncertain": True,
    }
    with pytest.raises(ValueError, match="不会重复发布"):
        browser.publish({"item_id": "item-1"})
    button.click.assert_called_once()


def test_corrupt_ledger_never_becomes_a_safe_retry(monkeypatch, tmp_path):
    browser = _browser(monkeypatch, tmp_path)
    (tmp_path / "submissions.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="记录不可读"):
        browser.publish({"item_id": "item-1"})
    assert connector._failure_fields("publish", browser) == {"submission_uncertain": True}
    browser._publish_button.assert_not_called()


def test_closed_shadow_button_reads_exact_publish_not_draft():
    def button(label, node_id):
        return {"nodeType": 1, "nodeName": "BUTTON", "backendNodeId": node_id,
                "children": [{"nodeType": 3, "nodeValue": label}]}
    session = Mock()
    session.send.return_value = {"root": {"children": [
        {"nodeName": "XHS-PUBLISH-BTN", "shadowRoots": [
            {"nodeName": "#document-fragment", "shadowRootType": "closed", "children": [
                button("暂存离开", 10), button("发布", 11), button("定时发布", 12),
            ]},
        ]},
    ]}}
    browser = object.__new__(connector._Browser)
    browser.context, browser.page = Mock(), Mock()
    browser.context.new_cdp_session.return_value = session
    matches = browser._shadow_publish_buttons()
    assert [match.node["backendNodeId"] for match in matches] == [11]
    browser.page.mouse.click.assert_not_called()


@pytest.mark.parametrize("guard", ["enabled", "visible", "unobstructed"])
def test_shadow_ui_click_never_bypasses_disabled_hidden_or_overlay(monkeypatch, guard):
    page, session = Mock(), Mock()
    button = connector._ShadowButton(page, session, {"backendNodeId": 1})
    state = {"text": "发布", "enabled": True, "visible": True, "unobstructed": True}
    state[guard] = False
    monkeypatch.setattr(button, "_state", lambda: state)
    with pytest.raises(ValueError, match="不可用或被遮挡"):
        button.click()
    page.mouse.click.assert_not_called()


def test_upload_wait_does_not_accept_editor_before_file_upload(monkeypatch):
    browser = object.__new__(connector._Browser)
    browser.page = Mock()
    browser.page.locator.return_value.inner_text.side_effect = ["视频文件 上传中 0%", "视频文件 重新上传"]
    available = iter([None, Mock()])
    monkeypatch.setattr(browser, "_visible", lambda locators: next(available))
    browser._wait_upload()
    browser.page.wait_for_timeout.assert_called_once_with(1000)
