"""A visible re-upload button is not evidence that Kuaishou finished uploading."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from services.creator_adapters.kuaishou import Adapter, UPLOAD_FINISH_PATH


FINISH_URL = "https://cp.kuaishou.com" + UPLOAD_FINISH_PATH
SUCCESS = {"result": 1, "data": {"fileId": 42, "videoDuration": 157000}}
PAYLOAD = {"item_id": "video-a", "title": "机器人演示", "video_path": "fixture.mp4"}


def response(value=SUCCESS, *, url=FINISH_URL, method="POST", status=200):
    return SimpleNamespace(url=url, status=status, request=SimpleNamespace(method=method),
                           json=Mock(return_value=value))


def adapter():
    client = object.__new__(Adapter)
    client.page = Mock(url="https://cp.kuaishou.com/article/publish/video")
    client._upload_receipt = client._upload_error = client._verified_upload_item = None
    return client


@pytest.mark.parametrize("override", [
    {"url": "https://cp.kuaishou.com.invalid" + UPLOAD_FINISH_PATH},
    {"url": "http://cp.kuaishou.com" + UPLOAD_FINISH_PATH},
    {"url": "https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit"},
    {"method": "GET"}, {"status": 500},
])
def test_only_successful_official_upload_finish_response_is_accepted(override):
    client = adapter()
    reply = response(**override)
    client._observe_upload_finish(reply)
    assert client._upload_receipt is None
    reply.json.assert_not_called()


@pytest.mark.parametrize("value", [
    [], {"result": True, "data": SUCCESS["data"]}, {"result": 1, "data": {}},
    {"result": 1, "data": {"fileId": True, "videoDuration": 157000}},
    {"result": 1, "data": {"fileId": 42, "videoDuration": 0}},
    {"result": 1, "data": {"fileId": 0, "videoDuration": 157000}},
])
def test_incomplete_or_malformed_upload_receipt_never_unlocks_publication(value):
    client = adapter()
    client._observe_upload_finish(response(value))
    with pytest.raises(ValueError, match="上传完成回执"):
        client.publication_control(PAYLOAD, PAYLOAD)
    client.page.get_by_text.assert_not_called()


def test_receipt_excludes_signed_query_and_does_not_authorize_another_video():
    client = adapter()
    client._observe_upload_finish(response(url=FINISH_URL + "?signature=private-fixture"))
    assert client._upload_receipt == {"type": "upload_receipt", "success": True,
        "url": FINISH_URL, "file_id": "42", "duration_ms": 157000}
    client._verified_upload_item = "video-b"
    with pytest.raises(ValueError, match="本条视频"):
        client.publication_control(PAYLOAD, PAYLOAD)


def test_visible_reupload_button_alone_never_authorizes_publish():
    client = adapter()
    client.visible = Mock(return_value=Mock())
    client.page.locator.return_value.inner_text.return_value = PAYLOAD["title"]
    with pytest.raises(ValueError, match="上传完成回执"):
        client.publication_control(PAYLOAD, PAYLOAD)
    client.page.get_by_text.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "rejected", "timeout"])
def test_prepare_observes_upload_before_selecting_file_and_waits_before_editing(outcome):
    client = adapter()
    # A preceding prepared item must not unlock this new upload.
    client._upload_receipt, client._verified_upload_item = {"success": True}, "old-item"
    client._dismiss_editor_tour = Mock()
    client.publication_control = Mock()
    events, listeners = [], []
    client.page.on.side_effect = lambda event, listener: listeners.append(listener)

    def select_file(*args, **kwargs):
        assert listeners and client._upload_receipt is None and client._verified_upload_item is None
        events.append("file_selected")

    def pump_response(_milliseconds):
        assert events == ["file_selected"]
        events.append("upload_finished")
        if outcome != "timeout":
            listeners[0](response(SUCCESS if outcome == "success" else {"result": 8}))

    client.page.locator.return_value.set_input_files.side_effect = select_file
    client.page.wait_for_timeout.side_effect = pump_response
    client.page.locator.return_value.fill.side_effect = lambda *args: events.append("caption_filled")
    with patch("services.creator_adapters.kuaishou.time.monotonic", side_effect=[0, 1, 601] if outcome == "timeout" else [0, 1, 2]):
        if outcome == "success":
            result = client.prepare(PAYLOAD)
            assert result["status"] == "prepared" and result["upload_evidence"]["duration_ms"] == 157000
            assert client._verified_upload_item == PAYLOAD["item_id"]
            assert events == ["file_selected", "upload_finished", "caption_filled"]
            client.publication_control.assert_called_once_with(PAYLOAD, PAYLOAD)
        else:
            with pytest.raises(ValueError, match="错误码 8" if outcome == "rejected" else "未点击发布"):
                client.prepare(PAYLOAD)
            client.page.locator.return_value.fill.assert_not_called()
            client.publication_control.assert_not_called()
            assert client._verified_upload_item is None
    client.page.remove_listener.assert_called_once_with("response", client._observe_upload_finish)


def test_verified_upload_still_requires_matching_official_form_and_disclosure():
    client = adapter()
    client._observe_upload_finish(response())
    client._verified_upload_item = PAYLOAD["item_id"]
    client.visible = Mock(return_value=Mock())
    client.page.locator.return_value.inner_text.return_value = PAYLOAD["title"]
    assert client.publication_control(PAYLOAD, PAYLOAD) is client.page.get_by_text.return_value
    client.visible.return_value = None
    with pytest.raises(ValueError, match="AI内容声明"):
        client.publication_control(PAYLOAD, PAYLOAD)
    client.page.url = "https://cp.kuaishou.com.invalid/article/publish/video"
    with pytest.raises(ValueError, match="官方发布表单"):
        client.publication_control(PAYLOAD, PAYLOAD)
