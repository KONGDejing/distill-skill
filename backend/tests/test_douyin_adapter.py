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
    client = Adapter(SimpleNamespace(pages=[page]))
    client._save_pending_dom = Mock()
    return client


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


def test_diagnostics_are_bounded():
    client = adapter()
    for i in range(1000):
        client._network.append({"host": "creator.douyin.com", "status": 200, "sequence": i})
        client._errors.append("Error")
    assert len(client._network) == 100
    assert len(client._errors) == 20
    assert client._network[0]["sequence"] == 900
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


def test_signed_in_portal_checks_observed_avatar_logout_without_clicking_it():
    client = adapter("作品发布 首页 内容管理 数据中心")
    client.page.url = "https://creator.douyin.com/creator-micro/home"
    avatar = Mock()
    with patch.object(client, "_profile_identity", return_value=None), patch.object(client, "visible", side_effect=[None, avatar]):
        assert client._account_logged_in()
    avatar.hover.assert_called_once()
    client.page.get_by_text.return_value.click.assert_not_called()


def test_creator_route_alone_does_not_prove_login():
    client = adapter("作品发布 首页 内容管理 数据中心")
    client.page.url = "https://creator.douyin.com/creator-micro/home"
    with patch.object(client, "visible", return_value=None):
        assert not client._account_logged_in()


def test_own_profile_card_proves_login_without_hovering_an_obscured_menu():
    client = adapter()
    client.page.url = "https://creator.douyin.com/creator-micro/home"
    with patch.object(client, "_profile_identity", return_value={"name": "测试作者"}):
        assert client._account_logged_in()
    client.page.get_by_text.assert_not_called()


def test_restored_session_does_not_wait_for_a_logged_out_login_button():
    client = adapter()
    client.page.url = "about:blank"
    client.inspect = Mock(side_effect=[{"authenticated": False, "status": "unknown"},
                                       {"authenticated": True, "status": "authenticated"}])
    with patch.object(client, "visible", return_value=None):
        assert client.login()["authenticated"]
    client.page.goto.assert_called_once()
    client.page.get_by_role.assert_not_called()


@pytest.mark.parametrize('declaration_visible,ai_generated', [(False, True), (True, False)])
def test_publish_requires_both_ai_flag_and_official_declaration(declaration_visible, ai_generated):
    client = adapter()
    client.page.url = 'https://creator.douyin.com/creator-micro/content/post/video'
    client.page.get_by_placeholder.return_value.input_value.return_value = '定稿标题'
    with patch.object(client, 'visible', return_value=Mock() if declaration_visible else None):
        with pytest.raises(ValueError, match='AI'):
            client.publication_control({}, {'title': '定稿标题', 'ai_generated': ai_generated})
    client.page.get_by_role.assert_not_called()


@pytest.mark.parametrize('selection_confirmed', [True, False])
def test_prepare_waits_for_ai_radio_and_never_confirms_an_unselected_declaration(selection_confirmed):
    client = adapter()
    client._dismiss_editor_tips = Mock()
    client.set_publication_location = Mock(return_value=None)
    client.publication_control = Mock()
    payload = {'video_path': 'approved.mp4', 'title': '定稿标题', 'ai_generated': True}
    with patch('services.creator_adapters.douyin.expect') as verify:
        if not selection_confirmed:
            verify.return_value.to_have_class.side_effect = AssertionError('timed out')
            with pytest.raises(ValueError, match='AI生成声明未选中'):
                client.prepare(payload)
            client.page.get_by_role.assert_not_called()
            client.publication_control.assert_not_called()
        else:
            assert client.prepare(payload)['ai_disclosure'] == '内容由AI生成'
            client.page.get_by_role.assert_called_once_with('button', name='确定', exact=True)
            client.publication_control.assert_called_once()
        verify.return_value.to_have_class.assert_called_once()
        assert verify.return_value.to_have_class.call_args.kwargs['timeout'] == 5000
        selected_class = verify.return_value.to_have_class.call_args.args[0]
        assert selected_class.search('semi-radio semi-radio-checked')
        assert not selected_class.search('semi-radio semi-radio-unchecked')


def test_beijing_proxy_does_not_imply_a_verified_work_location():
    client = adapter()
    client.page.url = 'https://creator.douyin.com/creator-micro/content/post/video'
    with pytest.raises(ValueError, match='北京尚未在官方表单核验'):
        client.publication_control({}, {'title': '定稿标题', 'ai_generated': True, 'requested_publication_city': '北京'})
    client.page.get_by_role.assert_not_called()


def test_city_selection_records_real_official_control_and_is_rechecked_before_submit():
    client = adapter()
    control = Mock()
    control.get_attribute.return_value = 'semi-select-open'
    control.locator.return_value.inner_text.return_value = '北京市'
    option = client.page.get_by_role.return_value.filter.return_value
    option.count.return_value = 1
    option.is_visible.return_value = True
    option.inner_text.return_value = '北京市\n中国北京市'
    with patch.object(client, '_location_control', return_value=control):
        evidence = client.set_publication_location({'requested_publication_city': '北京'})
        assert evidence['city'] == '北京' and evidence['country'] == 'CN'
        option.click.assert_called_once()
        client.require_publication_location({'requested_publication_city': '北京'})
        control.locator.return_value.inner_text.return_value = '洛杉矶'
        with pytest.raises(ValueError, match='发生变化'):
            client.require_publication_location({'requested_publication_city': '北京'})
