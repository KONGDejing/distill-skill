"""Kuaishou official creator login, based on its observed passport DOM."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

from .base import BaseAdapter, clean_url
from .submission import SubmissionWatch

HOME_URL = "https://cp.kuaishou.com/"
OFFICIAL_HOSTS = {"cp.kuaishou.com", "passport.kuaishou.com"}
UPLOAD_FINISH_PATH = "/rest/cp/works/v2/video/pc/upload/finish"


class Adapter(SubmissionWatch, BaseAdapter):
    supports_prepare = True
    supports_publish = True
    portal_host = "cp.kuaishou.com"

    def _account_identity(self):
        address = urlsplit(self.page.url)
        if address.hostname != "cp.kuaishou.com" or address.path in {"", "/", "/login"}:
            return None
        # Observed signed-in header: account name and avatar inside its account
        # dropdown. Do not use names from comments or public creator examples.
        name = self.visible([self.page.locator(".header-bar .user-info-dpd .user-info-name")])
        avatar = self.visible([self.page.locator(".header-bar .user-info-dpd .user-info-avatar")])
        if name is None or avatar is None:
            return None
        value = name.inner_text().strip()
        if not value or len(value) > 60 or value in {"登录", "立即登录", "未登录"}:
            return None
        return {"name": value}

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
                identity = self._account_identity()
                logout = self.visible([self.page.get_by_text("退出登录", exact=True)])
                if identity is not None or logout is not None:
                    state.update(authenticated=True, status="authenticated",
                                 display_name=(identity or {}).get("name"))
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
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    current = self.inspect()
                    if current["authenticated"] or current["status"] == "needs_verification":
                        return current
                    if urlsplit(self.page.url).hostname == "passport.kuaishou.com":
                        break
                    entry = self.visible([self.page.get_by_text("立即登录", exact=True)])
                    if entry is not None:
                        entry.click()
                        self.page.wait_for_url("https://passport.kuaishou.com/**", timeout=30000)
                        break
                    self.page.wait_for_timeout(500)
                else:
                    return self.inspect()
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

    def _dismiss_editor_tour(self):
        for _ in range(5):
            control = self.visible([self.page.get_by_text("立刻体验", exact=True),
                                    self.page.get_by_text("下一步", exact=True)])
            if control is None:
                break
            control.click(timeout=5000)
            self.page.wait_for_timeout(300)

    def _observe_upload_finish(self, response):
        address = urlsplit(response.url)
        if (address.scheme != "https" or address.hostname != self.portal_host
                or address.path != UPLOAD_FINISH_PATH or response.request.method != "POST"
                or response.status != 200):
            return
        try:
            value = response.json()
        except Exception:
            return
        if not isinstance(value, dict) or type(value.get("result")) is not int:
            return
        if value["result"] != 1:
            self._upload_error = value["result"]
            return
        data = value.get("data")
        if (not isinstance(data, dict) or type(data.get("fileId")) is not int or data["fileId"] <= 0
                or type(data.get("videoDuration")) is not int or data["videoDuration"] <= 0):
            return
        self._upload_receipt = {"type": "upload_receipt", "success": True,
            "url": clean_url(response.url), "file_id": str(data["fileId"]), "duration_ms": data["videoDuration"]}

    def _wait_for_upload(self, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._upload_error is not None:
                raise ValueError(f"快手上传完成接口返回错误码 {self._upload_error}，未进入提交步骤")
            if self._upload_receipt:
                return self._upload_receipt
            self.page.wait_for_timeout(500)
        raise ValueError("尚未取得快手官方上传完成回执，已保留表单，未点击发布")

    def prepare(self, payload):
        # The home shortcut opens a new tab. Navigate to its observed official
        # target in this owned page so login inspection and publishing share it.
        self._upload_receipt, self._upload_error, self._verified_upload_item = None, None, None
        self.page.goto("https://cp.kuaishou.com/article/publish/video", wait_until="domcontentloaded", timeout=45000)
        self.page.on("response", self._observe_upload_finish)
        try:
            self.page.locator('input[type=file][accept*="video"]').set_input_files(payload["video_path"], timeout=30000)
            receipt = self._wait_for_upload()
        finally:
            self.page.remove_listener("response", self._observe_upload_finish)
        self._verified_upload_item = payload["item_id"]
        editor = self.page.locator('[contenteditable=true]')
        editor.wait_for(state="visible", timeout=90000)
        self._dismiss_editor_tour()
        caption = payload["title"] + "\n" + payload.get("caption", "") + "\n" + " ".join("#" + tag for tag in payload.get("tags", []))
        editor.fill(caption.strip())
        self.page.get_by_text("为作品添加补充说明", exact=True).locator("..").locator("input").click()
        self.page.locator(".ant-select-item-option").filter(has_text="内容为AI生成").click()
        if payload.get("cover_path"):
            self.page.locator('input[type=file][accept*="image"]').set_input_files(payload["cover_path"])
            self.page.wait_for_timeout(1500)
        self.publication_control(payload, payload)
        return {"status": "prepared", "uploaded": True, "upload_evidence": receipt, "ai_disclosure": "内容为AI生成",
                "message": "视频、文案、封面及AI声明已准备，等待定时提交"}

    def publication_control(self, payload, prepared):
        if (not getattr(self, "_upload_receipt", None)
                or getattr(self, "_verified_upload_item", None) != payload["item_id"]):
            raise ValueError("未取得本条视频的官方上传完成回执，未点击提交")
        address = urlsplit(self.page.url)
        if (address.scheme != "https" or address.hostname != self.portal_host
                or address.path != "/article/publish/video"):
            raise ValueError("当前不是这条作品的官方发布表单，未点击提交")
        self.require_publication_location(prepared)
        if not self.page.locator('[contenteditable=true]').inner_text().startswith(prepared["title"]):
            raise ValueError("发布表单的文案与已准备作品不一致")
        if self.visible([self.page.locator(".ant-select-selection-item").filter(has_text="内容为AI生成")]) is None:
            raise ValueError("未核验到AI内容声明，尚未提交")
        if self.visible([self.page.get_by_text("重新上传", exact=True)]) is None:
            raise ValueError("当前发布表单已没有已上传的视频，未点击提交")
        return self.page.get_by_text("发布", exact=True)
