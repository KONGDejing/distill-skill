"""Observed Douyin creator login UI; upload/publish require later live testing."""
from collections import deque
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

from .base import BaseAdapter, clean_url
from services.creator_login_status import douyin_verification

HOME_URL = "https://creator.douyin.com/"
# The portal's document/resource transfer stalled with the older browser's HTTP2
# stack in this environment. These standard network flags do not alter auth UI.
BROWSER_ARGS = ["--disable-http2", "--disable-quic"]


class Adapter(BaseAdapter):
    supports_prepare = False
    supports_publish = False

    def __init__(self, context):
        super().__init__(context)
        self._network, self._errors = deque(maxlen=100), deque(maxlen=20)
        self.page.on("response", lambda response: self._network.append({
            "host": urlsplit(response.url).hostname, "status": response.status,
            "type": response.request.resource_type}))
        self.page.on("requestfailed", lambda request: self._network.append({
            "host": urlsplit(request.url).hostname, "error": request.failure,
            "type": request.resource_type}))
        self.page.on("pageerror", lambda error: self._errors.append(type(error).__name__))

    def _qr_panel(self):
        # Both the landing card and modal may exist; prefer the visible modal.
        return self.visible([
            self.page.locator(".douyin-creator-master-modal-content .ZKVC3H8u"),
            self.page.locator("[class*=douyin-login-container] .ZKVC3H8u"),
        ])

    def _qr_ready(self, panel):
        if panel is None:
            return False
        for image in panel.locator(".YJz68tAT img, .YJz68tAT canvas, .YJz68tAT svg").all():
            if not image.is_visible():
                continue
            box = image.bounding_box()
            if not box or min(box["width"], box["height"]) < 100:
                continue
            # A logo inside an empty QR box is not a scannable login code.
            if image.evaluate("""e => {
                const tag=e.tagName.toLowerCase();
                if(tag==='img') return e.complete&&e.naturalWidth>=100;
                if(tag==='svg') return [...e.querySelectorAll('path')].reduce((n,p)=>n+(p.getAttribute('d')||'').length,0)>1000;
                if(tag==='canvas') {
                    try {const d=e.getContext('2d').getImageData(0,0,e.width,e.height).data;
                        let dark=0; for(let i=0;i<d.length;i+=4) if(d[i+3]>200&&d[i]+d[i+1]+d[i+2]<300) dark++;
                        return dark>e.width*e.height*.12;} catch(_) {return false;}
                }
                return false;
            }"""):
                return True
        return False

    def _diagnostics(self):
        directory = Path(__file__).resolve().parents[3] / ".runtime/creator-connectors/douyin"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "login-diagnostics.json"
        path.write_text(json.dumps({"url": clean_url(self.page.url), "resources": list(self._network),
                                    "console_error_types": list(self._errors)}, ensure_ascii=False, indent=2))
        path.chmod(0o600)

    def _save_pending_dom(self, body):
        """Keep unknown/confirmed login UI private for account integration."""
        signature = (clean_url(self.page.url), body)
        if signature == getattr(self, "_last_ui_snapshot", None):
            return
        directory = Path(__file__).resolve().parents[3] / ".runtime/creator-connectors/douyin"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        controls = self.page.locator("button,a,input,[role=button],[role=dialog]").evaluate_all(
            "es=>es.slice(0,160).map(e=>({tag:e.tagName,classes:e.className,"
            "text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,200),"
            "type:e.getAttribute('type'),placeholder:e.getAttribute('placeholder')}))")
        temporary = directory / "login-ui.json.tmp"
        temporary.write_text(json.dumps({"url": signature[0], "body": body[:14000], "controls": controls},
                                        ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(directory / "login-ui.json")
        self._last_ui_snapshot = signature

    def _profile_identity(self):
        if not urlsplit(self.page.url).path.startswith("/creator-micro/"):
            return None
        identifier = self.visible([self.page.locator("[class*=unique_id-]")])
        if identifier is None or not re.fullmatch(r"抖音号[：:]\s*\S{1,64}", identifier.inner_text().strip()):
            return None
        name = self.visible([identifier.locator("..").locator("[class*=left-]")])
        return {"name": name.inner_text().strip() if name is not None else None}

    def _account_logged_in(self):
        # The signed-in home exposes the account's own identity card even when
        # an onboarding tooltip obscures the avatar menu.
        if self._profile_identity() is not None:
            return True
        logout = self.visible([self.page.get_by_text("退出账号", exact=True),
                               self.page.get_by_text("退出登录", exact=True)])
        if logout is not None:
            return True
        # The observed signed-in portal hides its logout control in the avatar
        # dropdown. Opening that menu is read-only; never click the logout item.
        if not urlsplit(self.page.url).path.startswith("/creator-micro/"):
            return False
        avatar = self.visible([self.page.locator("[class*=avatar-wrapper-]")])
        if avatar is None:
            return False
        try:
            avatar.hover(timeout=3000)
            self.page.get_by_text("退出账号", exact=True).wait_for(state="visible", timeout=2000)
            return True
        except BrowserTimeout:
            return False

    def inspect(self):
        state = {"authenticated": False, "status": "unknown", "display_name": None,
                 "url": clean_url(self.page.url), "error": None}
        if urlsplit(self.page.url).hostname != "creator.douyin.com":
            state["error"] = "当前页面不是已核验的抖音官方创作后台"
            return state
        try:
            body = self.page.locator("body").inner_text(timeout=5000)
            panel = self._qr_panel()
            # The landing QR remains visible in the DOM behind the modal and
            # can expire independently. Classify the same QR that we capture,
            # rather than treating another panel's expiry as this QR's expiry.
            qr_text = panel.inner_text(timeout=5000) if panel is not None else body
        except BrowserTimeout:
            state.update(status="network_error", error="抖音官方页面尚未加载完成")
            return state
        verification = douyin_verification(body)
        if verification:
            state.update(status="needs_verification", error=verification["message"], verification=verification)
        elif any(text in qr_text for text in ("二维码已失效", "二维码已过期", "二维码过期", "二维码失效")):
            state["status"] = "qr_expired"
        elif self._qr_ready(panel):
            state["status"] = "waiting_scan"
        elif self._account_logged_in():
            state.update(status="authenticated", authenticated=True,
                         display_name=(self._profile_identity() or {}).get("name"))
        else:
            state.update(status="network_error", error="官方登录界面已打开，但尚未取得可扫描二维码；请刷新或检查网络")
        if state["status"] in {"needs_verification", "authenticated", "network_error"}:
            self._save_pending_dom(body)
        return state

    def login(self, *, refresh=False):
        current = self.inspect()
        if current["authenticated"] or current["status"] == "needs_verification":
            return current
        if current["status"] == "waiting_scan" and not refresh:
            return current
        reset = refresh or current["status"] == "qr_expired"
        modal = self.visible([self.page.locator(".douyin-creator-master-modal-content")])
        if reset and modal is not None:
            # The observed official dialog has no close button. Clicking its
            # surrounding mask dismisses it; reopening requests a fresh QR
            # without reloading the entire creator portal and login SDK.
            self.page.locator(".douyin-creator-master-modal-wrap").click(position={"x": 10, "y": 10})
            modal.wait_for(state="hidden", timeout=10000)
        elif reset or urlsplit(self.page.url).hostname != "creator.douyin.com":
            self.page.goto(HOME_URL, wait_until="commit", timeout=45000)
        if self.visible([self.page.locator(".douyin-creator-master-modal-content")]) is None:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                current = self.inspect()
                if current["authenticated"] or current["status"] == "needs_verification":
                    return current
                entry = self.visible([self.page.get_by_role("button", name="登录后使用丰富的作品体裁", exact=True)])
                if entry is not None:
                    entry.click()
                    break
                self.page.wait_for_timeout(500)
            else:
                self._diagnostics()
                return self.inspect()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            current = self.inspect()
            if current["status"] in {"waiting_scan", "needs_verification", "authenticated", "qr_expired"}:
                break
            self.page.wait_for_timeout(1500)
        self._diagnostics()
        return current

    def capture_login_image(self, path: Path):
        state = self.inspect()
        panel = self._qr_panel()
        if state["authenticated"] or state["status"] != "waiting_scan" or not self._qr_ready(panel):
            return False
        region = self.visible([panel.locator(".YJz68tAT")])
        return self.save_login_region(region, path)
