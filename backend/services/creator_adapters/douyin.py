"""Observed Douyin creator login UI; upload/publish require later live testing."""
import base64
from collections import deque
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from .base import BaseAdapter, clean_url

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

    def inspect(self):
        state = {"authenticated": False, "status": "unknown", "display_name": None,
                 "url": clean_url(self.page.url), "error": None}
        if urlsplit(self.page.url).hostname != "creator.douyin.com":
            state["error"] = "当前页面不是已核验的抖音官方创作后台"
            return state
        try:
            body = self.page.locator("body").inner_text(timeout=5000)
        except Exception:
            state.update(status="network_error", error="抖音官方页面尚未加载完成")
            return state
        if any(text in body for text in ("请完成验证", "安全验证", "扫码成功", "请在手机上确认", "请在抖音上确认")):
            state.update(status="needs_verification", error="请按抖音官方页面或手机提示完成确认")
        elif any(text in body for text in ("二维码已失效", "二维码已过期", "二维码过期", "二维码失效")):
            state["status"] = "qr_expired"
        elif self._qr_ready(self._qr_panel()):
            state["status"] = "waiting_scan"
        elif self.visible([self.page.get_by_text("退出登录", exact=True)]) is not None:
            state.update(status="authenticated", authenticated=True)
        else:
            state.update(status="network_error", error="官方登录界面已打开，但尚未取得可扫描二维码；请刷新或检查网络")
        return state

    def login(self):
        current = self.inspect()
        if current["authenticated"] or current["status"] in {"waiting_scan", "needs_verification"}:
            return current
        if urlsplit(self.page.url).hostname != "creator.douyin.com" or current["status"] == "qr_expired":
            self.page.goto(HOME_URL, wait_until="commit", timeout=45000)
        if self.visible([self.page.locator(".douyin-creator-master-modal-content")]) is None:
            entry = self.page.get_by_role("button", name="登录后使用丰富的作品体裁", exact=True)
            try:
                entry.wait_for(state="visible", timeout=90000)
                entry.click()
            except Exception:
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
        if state["authenticated"] or not self._qr_ready(panel):
            return False
        panel.scroll_into_view_if_needed()
        box = panel.bounding_box()
        if not box:
            return False
        # Read pixels through the browser, avoiding screenshot font-loading waits
        # on unrelated marketing resources. Only the QR column is exposed.
        session = self.context.new_cdp_session(self.page)
        try:
            viewport = session.send("Page.getLayoutMetrics")["layoutViewport"]
            screenshot = session.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                "clip": {"x": box["x"] + viewport["pageX"], "y": box["y"] + viewport["pageY"],
                         "width": box["width"], "height": box["height"], "scale": 1}})
        finally:
            session.detach()
        temporary = Path(path).with_name(Path(path).name + ".tmp")
        temporary.write_bytes(base64.b64decode(screenshot["data"]))
        temporary.chmod(0o600)
        temporary.replace(path)
        return True
