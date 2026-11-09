from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.creator_adapters.bilibili import Adapter as Bilibili
from services.creator_adapters.kuaishou import Adapter as Kuaishou


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
