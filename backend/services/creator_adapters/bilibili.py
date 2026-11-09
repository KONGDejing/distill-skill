"""Observed official Bilibili QR login; publishing remains deliberately gated."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from .base import BaseAdapter, clean_url

HOME_URL = "https://member.bilibili.com/platform/home"
LOGIN_URL = "https://passport.bilibili.com/login?gourl=https%3A%2F%2Fmember.bilibili.com%2Fplatform%2Fhome"
OFFICIAL_HOSTS = {"member.bilibili.com", "passport.bilibili.com", "www.bilibili.com"}


class Adapter(BaseAdapter):
    # Upload and final-submit controls will only be enabled after a real logged
    # in editor has been inspected. A QR scan is not an upload/publish receipt.
    supports_prepare = False
    supports_publish = False

    def _qr_panel(self):
        return self.visible([self.page.locator(".login-scan")])

    def _save_pending_dom(self, body):
        """Keep the post-login DOM available for private editor integration."""
        directory = Path(__file__).resolve().parents[3] / ".runtime/creator-connectors/bilibili"
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
            state["error"] = "当前页面不是已核验的 B 站官方后台或登录域名"
            return state
        try:
            body = self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            state.update(status="network_error", error="B站官方页面尚未加载完成")
            return state
        if any(word in body for word in ("安全验证", "身份验证", "验证身份", "请完成验证", "异常访问", "访问被拒绝", "请在手机上确认", "扫码成功", "扫描成功")):
            state.update(status="needs_verification", error="请在官方页面或 B站客户端完成验证/登录确认")
        elif any(word in body for word in ("二维码已失效", "二维码已过期", "二维码过期", "二维码失效", "点击刷新")):
            state["status"] = "qr_expired"
        elif self._qr_panel() is not None:
            qr = self.visible([self.page.locator(".login-scan__qrcode img"), self.page.locator(".login-scan__qrcode canvas")])
            ready = qr is not None and qr.evaluate(
                "node=>node.tagName==='IMG' ? node.complete && node.naturalWidth>=100 : node.width>=100 && node.height>=100")
            if ready:
                state["status"] = "waiting_scan"
            else:
                state.update(status="network_error", error="官方扫码区域已加载，但二维码图像尚未就绪")
        elif host == "member.bilibili.com" and body.strip():
            # Require an explicit authenticated control, not public marketing
            # text such as 创作中心/投稿 which also appears before login.
            logout = self.visible([self.page.get_by_text("退出登录", exact=True)])
            if logout is not None:
                state.update(authenticated=True, status="authenticated")
            else:
                state["error"] = "页面已进入创作后台，仍需核验真实账号状态与上传控件"
            self._save_pending_dom(body)
        elif not body.strip():
            state.update(status="network_error", error="B站创作页内容为空；可打开官方账号扫码登录页继续")
        else:
            state["error"] = "尚未观察到可确认的二维码或已登录账号控件"
        return state

    def login(self):
        current = self.inspect()
        if current["authenticated"] or current["status"] in {"waiting_scan", "needs_verification"}:
            return current
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            self.page.locator(".login-scan__qrcode").wait_for(state="visible", timeout=20000)
            self.page.wait_for_timeout(1000)
        except Exception:
            pass
        return self.inspect()

    def capture_login_image(self, path: Path):
        state = self.inspect()
        if state["authenticated"]:
            return False
        panel = self._qr_panel()
        if panel is None:
            return False
        temporary = Path(path).with_name(Path(path).name + ".tmp")
        panel.screenshot(path=str(temporary), type="png")
        temporary.chmod(0o600)
        temporary.replace(path)
        return state["status"] in {"waiting_scan", "qr_expired", "needs_verification"}
