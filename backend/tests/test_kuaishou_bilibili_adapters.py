from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.creator_adapters.bilibili import Adapter as Bilibili
from services.creator_adapters.kuaishou import Adapter as Kuaishou
from services.creator_adapters.douyin import Adapter as Douyin


class Locator:
    def __init__(self, visible=False, text="", image_loaded=True):
        self.present, self.text, self.image_loaded = visible, text, image_loaded

    def all(self):
        return [self] if self.present else []

    def is_visible(self):
        return self.present

    def inner_text(self, **kwargs):
        return self.text

    def evaluate(self, _expression):
        return self.image_loaded


class Page:
    def __init__(self, url, body, visible=(), image_loaded=True):
        self.url, self.body = url, body
        self.visible, self.image_loaded = set(visible), image_loaded

    def set_default_timeout(self, _timeout):
        pass

    def locator(self, selector):
        return Locator(selector == "body" or selector in self.visible,
                       self.body if selector == "body" else "", self.image_loaded)

    def get_by_text(self, text, **kwargs):
        return Locator("text:" + text in self.visible, text)


CONFIG = [
    (Bilibili, "https://passport.bilibili.com/login", "https://member.bilibili.com/platform/home",
     [".login-scan", ".login-scan__qrcode img"]),
    (Kuaishou, "https://passport.kuaishou.com/pc/account/login/", "https://cp.kuaishou.com/profile",
     ["#login-form", "#login-form .qr-login", "#login-form .qr-login .qrcode img"]),
]


@pytest.mark.parametrize("adapter_type,login_url,creator_url,qr_selectors", CONFIG)
def test_public_creator_words_do_not_prove_authentication(adapter_type, login_url, creator_url, qr_selectors):
    adapter = adapter_type(SimpleNamespace(pages=[Page(creator_url, "创作中心 投稿 视频上传 数据统计")]))
    with patch.object(adapter, "_save_pending_dom"):
        state = adapter.inspect()
    assert state["authenticated"] is False
    assert state["status"] == "unknown"


@pytest.mark.parametrize("adapter_type,login_url,creator_url,qr_selectors", CONFIG)
def test_real_qr_waits_for_user_and_missing_image_is_not_reported_ready(adapter_type, login_url, creator_url, qr_selectors):
    page = Page(login_url, "扫码登录", qr_selectors)
    adapter = adapter_type(SimpleNamespace(pages=[page]))
    state = adapter.inspect()
    assert state["status"] == "waiting_scan" and state["authenticated"] is False
    page.image_loaded = False
    assert adapter.inspect()["status"] == "network_error"


@pytest.mark.parametrize("adapter_type,login_url,creator_url,qr_selectors", CONFIG)
def test_phone_confirmation_and_expired_qr_never_count_as_authenticated(adapter_type, login_url, creator_url, qr_selectors):
    page = Page(login_url, "扫码成功 请在手机上确认", qr_selectors)
    adapter = adapter_type(SimpleNamespace(pages=[page]))
    state = adapter.inspect()
    assert state["status"] == "needs_verification" and state["authenticated"] is False
    page.body = "二维码已过期"
    state = adapter.inspect()
    assert state["status"] == "qr_expired" and state["authenticated"] is False


@pytest.mark.parametrize("adapter_type", [Bilibili, Kuaishou])
def test_unverified_upload_and_publish_controls_remain_disabled(adapter_type):
    adapter = adapter_type(SimpleNamespace(pages=[Page("about:blank", "")]))
    assert not adapter.supports_prepare and not adapter.supports_publish
    with pytest.raises(ValueError, match="上传联调"):
        adapter.prepare({"item_id": "preview"})
    with pytest.raises(ValueError, match="未点击提交"):
        adapter.publication_control({"item_id": "preview"}, {})
    with pytest.raises(ValueError, match="官方提交回执"):
        adapter.wait_for_submission()


@pytest.mark.parametrize("adapter_type,login_url,creator_url,qr_selectors", CONFIG)
def test_lookalike_domain_cannot_prove_login(adapter_type, login_url, creator_url, qr_selectors):
    page = Page(creator_url.replace(".com/", ".com.invalid/"), "退出登录", ["text:退出登录"])
    state = adapter_type(SimpleNamespace(pages=[page])).inspect()
    assert state["authenticated"] is False
    assert state["status"] == "unknown"


@pytest.mark.parametrize('adapter_type', [Bilibili, Kuaishou, Douyin])
@pytest.mark.parametrize('state', [
    {'authenticated': True, 'status': 'authenticated'},
    {'authenticated': False, 'status': 'needs_verification'},
])
def test_refresh_preserves_logged_in_or_phone_confirmation_session(adapter_type, state):
    from unittest.mock import Mock
    adapter = object.__new__(adapter_type)
    adapter.page = Mock()
    adapter.inspect = Mock(return_value=state)
    assert adapter.login(refresh=True) == state
    adapter.page.goto.assert_not_called()
    adapter.page.reload.assert_not_called()


@pytest.mark.parametrize('adapter_type,url', [
    (Bilibili, 'https://passport.bilibili.com/login'),
    (Kuaishou, 'https://passport.kuaishou.com/pc/account/login/'),
    (Douyin, 'https://creator.douyin.com/'),
])
def test_browser_crash_is_not_swallowed_as_loading_timeout(adapter_type, url):
    from unittest.mock import Mock
    from playwright.sync_api import Error, TimeoutError
    adapter = object.__new__(adapter_type)
    adapter.page = Mock(url=url)
    adapter.page.locator.return_value.inner_text.side_effect = TimeoutError('loading')
    assert adapter.inspect()['status'] == 'network_error'
    adapter.page.locator.return_value.inner_text.side_effect = Error('Target crashed')
    with pytest.raises(Error, match='Target crashed'):
        adapter.inspect()


def test_douyin_refresh_reopens_official_modal_without_reloading_portal():
    from unittest.mock import Mock
    adapter = object.__new__(Douyin)
    adapter.page = Mock(url='https://creator.douyin.com/')
    adapter.inspect = Mock(return_value={'authenticated': False, 'status': 'waiting_scan'})
    modal = Mock()
    adapter.visible = Mock(side_effect=[modal, None])
    adapter._diagnostics = Mock()
    assert adapter.login(refresh=True)['status'] == 'waiting_scan'
    adapter.page.goto.assert_not_called()
    modal.wait_for.assert_called_once_with(state='hidden', timeout=10000)
    adapter.page.get_by_role.return_value.click.assert_called_once()


@pytest.mark.parametrize('next_state', ['qr_expired', 'needs_verification', 'authenticated'])
def test_bilibili_stalled_expired_refresh_reopens_only_without_phone_confirmation(next_state):
    from unittest.mock import Mock
    from playwright.sync_api import TimeoutError
    adapter = object.__new__(Bilibili)
    adapter.page = Mock()
    expired = {'authenticated': False, 'status': 'qr_expired'}
    after = {'authenticated': next_state == 'authenticated', 'status': next_state}
    adapter.inspect = Mock(side_effect=[expired, after, {'authenticated': False, 'status': 'waiting_scan'}])
    button = Mock()
    button.click.side_effect = TimeoutError('Official refresh overlay did not accept click')
    adapter.visible = Mock(return_value=button)
    adapter._save_pending_dom = Mock()
    result = adapter.login(refresh=True)
    if next_state == 'qr_expired':
        adapter.page.goto.assert_called_once()
        assert result['status'] == 'waiting_scan'
    else:
        adapter.page.goto.assert_not_called()
        assert result == after


@pytest.mark.parametrize('adapter_type', [Bilibili, Kuaishou])
def test_expired_qr_is_not_captured_as_scannable(adapter_type):
    from unittest.mock import Mock
    adapter = object.__new__(adapter_type)
    adapter.inspect = Mock(return_value={'authenticated': False, 'status': 'qr_expired'})
    adapter.save_login_region = Mock()
    assert adapter.capture_login_image(Path('unused.png')) is False
    adapter.save_login_region.assert_not_called()
