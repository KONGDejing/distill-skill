"""Login evidence must distinguish official QR pixels from placeholder logos."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.creator_adapters.douyin import Adapter, HOME_URL


def adapter(body="扫码登录"):
    page = Mock()
    page.url = HOME_URL
    page.locator.return_value.inner_text.return_value = body
    return Adapter(SimpleNamespace(pages=[page]))


def test_missing_qr_is_reported_as_network_error_not_authentication():
    client = adapter("创作者中心 视频管理 登录后使用丰富的作品体裁")
    with patch.object(client, "visible", return_value=None):
        state = client.inspect()
    assert state["status"] == "network_error"
    assert state["authenticated"] is False
    assert "尚未取得可扫描二维码" in state["error"]


@pytest.mark.parametrize("body,status", [
    ("扫码登录", "waiting_scan"),
    ("扫码成功 请在手机上确认", "needs_verification"),
    ("二维码已过期", "qr_expired"),
])
def test_real_qr_or_confirmation_does_not_mean_logged_in(body, status):
    client = adapter(body)
    panel = Mock()
    panel.inner_text.return_value = body
    with patch.object(client, "_qr_panel", return_value=panel), patch.object(client, "_qr_ready", return_value=True):
        state = client.inspect()
    assert state["status"] == status
    assert state["authenticated"] is False


def test_center_logo_is_not_a_scannable_code():
    client, panel, logo = adapter(), Mock(), Mock()
    panel.locator.return_value.all.return_value = [logo]
    logo.is_visible.return_value = True
    logo.bounding_box.return_value = {"width": 37, "height": 37}
    logo.evaluate.return_value = True
    assert client._qr_ready(panel) is False
    logo.evaluate.assert_not_called()
    logo.bounding_box.return_value = {"width": 180, "height": 180}
    logo.evaluate.return_value = False
    assert client._qr_ready(panel) is False


def test_diagnostics_are_bounded_and_unverified_publication_stays_disabled():
    client = adapter()
    for i in range(1000):
        client._network.append({"host": "creator.douyin.com", "status": 200, "sequence": i})
        client._errors.append("Error")
    assert len(client._network) == 100
    assert len(client._errors) == 20
    assert client._network[0]["sequence"] == 900
    assert not client.supports_prepare and not client.supports_publish
    with pytest.raises(ValueError, match="上传联调"):
        client.prepare({"item_id": "preview"})
    with pytest.raises(ValueError, match="未点击提交"):
        client.publication_control({}, {})


def test_untrusted_domain_cannot_report_authenticated():
    client = adapter("退出登录")
    client.page.url = "https://creator.douyin.com.invalid/"
    state = client.inspect()
    assert state["authenticated"] is False
    assert state["status"] == "unknown"


@pytest.mark.parametrize("panel_text,expected", [
    ("扫码登录 打开抖音APP", "waiting_scan"),
    ("二维码已过期 点击刷新", "qr_expired"),
])
def test_expiry_follows_active_qr_instead_of_background_landing_qr(panel_text, expected):
    client = adapter("首页：二维码已过期 弹窗：" + panel_text)
    panel = Mock()
    panel.inner_text.return_value = panel_text
    with patch.object(client, "_qr_panel", return_value=panel), patch.object(client, "_qr_ready", return_value=True):
        assert client.inspect()["status"] == expected


def test_official_verification_still_takes_priority_over_ready_modal_qr():
    client = adapter("请完成验证")
    panel = Mock()
    panel.inner_text.return_value = "扫码登录"
    with patch.object(client, "_qr_panel", return_value=panel), patch.object(client, "_qr_ready", return_value=True):
        assert client.inspect()["status"] == "needs_verification"
