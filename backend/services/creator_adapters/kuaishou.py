"""Kuaishou official creator login, based on its observed passport DOM."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

from .base import BaseAdapter, clean_url

HOME_URL = "https://cp.kuaishou.com/"
OFFICIAL_HOSTS = {"cp.kuaishou.com", "passport.kuaishou.com"}


class Adapter(BaseAdapter):
    supports_prepare = False
    supports_publish = False

    def _qr_panel(self):
        qr = self.visible([self.page.locator("#login-form .qr-login")])
        return self.visible([self.page.locator("#login-form")]) if qr is not None else None

    def _save_pending_dom(self, body):
        directory = Path(__file__).resolve().parents[3] / ".runtime/creator-connectors/kuaishou"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        destination = directory / "dom-after-login.json"
        controls = self.page.locator("button,a,input,[role=button],[contenteditable=true]").evaluate_all(
            "els=>els.map(e=>({tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,120),"
            "type:e.getAttribute('type'),placeholder:e.getAttribute('placeholder'),classes:e.className})).slice(0,160)")
        destination.write_text(json.dumps({"url": clean_url(self.page.url), "body": body[:14000], "controls": controls}, ensure_ascii=False, indent=2))
        destination.chmod(0o600)

    def inspect(self):
        host = urlsplit(self.page.url).hostname
        state = {"authenticated": False, "status": "unknown", "display_name": None, "url": clean_url(self.page.url)}
        if host not in OFFICIAL_HOSTS:
            state["error"] = "当前页面不是已核验的快手创作后台或官方登录域名"
            return state
        try:
            body = self.page.locator("body").inner_text(timeout=5000)
        except BrowserTimeout:
            state.update(status="network_error", error="快手官方页面尚未加载完成")
            return state
        if any(word in body for word in ("安全验证", "身份验证", "验证身份", "请完成验证", "异常访问", "请在手机上确认", "扫码成功", "扫描成功", "登录确认")):
            state.update(status="needs_verification", error="请在官方页面或快手客户端完成验证/登录确认")
        elif any(word in body for word in ("二维码已失效", "二维码已过期", "二维码过期", "二维码失效", "点击刷新")):
            state["status"] = "qr_expired"
        elif self._qr_panel() is not None:
            image = self.visible([self.page.locator("#login-form .qr-login .qrcode img")])
            if image is not None and image.evaluate("img=>img.complete && img.naturalWidth>=100"):
                state["status"] = "waiting_scan"
            else:
                state.update(status="network_error", error="官方扫码区域已加载，但二维码图像尚未就绪")
        elif host == "cp.kuaishou.com":
            login_button = self.visible([self.page.get_by_text("立即登录", exact=True)])
            if login_button is not None:
                state.update(status="unknown", error="官方首页尚未切换到扫码登录")
            elif body.strip():
                logout = self.visible([self.page.get_by_text("退出登录", exact=True)])
                if logout is not None:
                    state.update(authenticated=True, status="authenticated")
                else:
                    state["error"] = "页面已进入创作后台，仍需核验真实账号状态与上传控件"
                self._save_pending_dom(body)
            else:
                state.update(status="network_error", error="快手创作后台内容尚未加载")
        else:
            state.update(status="network_error", error="官方登录组件尚未生成可扫描二维码")
        return state

    def login(self, *, refresh=False):
        current = self.inspect()
        if current["authenticated"] or current["status"] == "needs_verification":
            return current
        if current["status"] == "waiting_scan" and not refresh:
            return current
        if current["status"] == "qr_expired":
            button = self.visible([self.page.get_by_text("点击刷新", exact=True)])
            if button is not None:
                button.click()
                for _ in range(30):
                    self.page.wait_for_timeout(500)
                    current = self.inspect()
                    if current["status"] in {"waiting_scan", "needs_verification", "authenticated"}:
                        return current
        host = urlsplit(self.page.url).hostname
        if host != "passport.kuaishou.com":
            self.page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                entry = self.page.get_by_text("立即登录", exact=True)
                entry.wait_for(state="visible", timeout=20000)
                entry.click()
                self.page.wait_for_url("https://passport.kuaishou.com/**", timeout=30000)
            except BrowserTimeout:
                return self.inspect()
        elif refresh or current["status"] == "qr_expired":
            self.page.reload(wait_until="domcontentloaded", timeout=45000)
        if self._qr_panel() is None:
            try:
                switch = self.page.get_by_text("扫码登录", exact=True)
                switch.wait_for(state="visible", timeout=30000)
                switch.click()
            except BrowserTimeout:
                return self.inspect()
        try:
            self.page.locator("#login-form .qr-login .qrcode img").wait_for(state="visible", timeout=20000)
            self.page.wait_for_timeout(1000)
        except BrowserTimeout:
            pass
        return self.inspect()

    def capture_login_image(self, path: Path):
        state = self.inspect()
        if state["authenticated"] or state["status"] != "waiting_scan":
            return False
        region = self.visible([self.page.locator("#login-form .qr-login .qrcode")])
        return self.save_login_region(region, path)
