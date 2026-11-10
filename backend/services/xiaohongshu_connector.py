"""Private, persistent browser connector for the official creator portal.

Commands are queued to one profile-owning process. ``prepare`` uploads and fills
the editor; only a separate matching ``publish`` command can submit it. A click
alone is never reported as successful publication.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from services.factory_platform_policy import AutomationDisabled, automation_enabled, automation_state, require_automation
else:
    # The historical systemd unit executes this file directly, without PYTHONPATH.
    from factory_platform_policy import AutomationDisabled, automation_enabled, automation_state, require_automation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT_ROOT / ".runtime"
PRIVATE = RUNTIME / "xiaohongshu-connector"
PROFILE = RUNTIME / "xiaohongshu-session"
QA = RUNTIME / "qa"
STATE_FILE = QA / "xiaohongshu-state.json"
LOGIN_IMAGE = QA / "xiaohongshu-login.png"
PID_FILE = PRIVATE / "daemon.pid"
SERVICE_NAME = "short-video-ai-factory-xiaohongshu.service"
SERVICE_FILE = Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
OFFICIAL_HOME = "https://creator.xiaohongshu.com/new/home"
OFFICIAL_LOGIN = "https://creator.xiaohongshu.com/login"
ALLOWED_ACTIONS = {"login", "inspect", "prepare", "publish"}
_STOP = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _directories():
    for path in (PRIVATE, PRIVATE / "commands", PRIVATE / "results", PROFILE):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    QA.mkdir(parents=True, exist_ok=True)


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def _submission_ledger():
    """A corrupt audit ledger must never be treated as an empty ledger."""
    path = PRIVATE / "submissions.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise ValueError("提交记录不可读，需先核对平台后台；禁止自动重复提交") from None


def _failure_fields(action, browser):
    if action != "publish":
        return {}
    attempted = getattr(browser, "submission_attempted", None)
    if attempted is False:
        return {"submission_attempted": False}
    fields = {"submission_uncertain": True}
    if attempted is True:
        fields["submission_attempted"] = True
    return fields


def _clean_url(url):
    address = urlsplit(url)
    return f"{address.scheme}://{address.netloc}{address.path}"


def _alive():
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        proc = Path(f"/proc/{pid}")
        if proc.exists():
            if (proc / "stat").read_text().split()[2] == "Z":
                return False
            return str(Path(__file__).resolve()).encode() in (proc / "cmdline").read_bytes()
        return True
    except (OSError, ValueError, IndexError):
        return False


def connection_state() -> dict:
    state = _read(STATE_FILE)
    state = state if isinstance(state, dict) else {}
    if not automation_enabled("xiaohongshu"):
        policy = automation_state("xiaohongshu")
        return {**policy, "status": "manual", "state_code": "manual", "message": policy["reason"],
                "automation_enabled": False, "cache_only": True,
                "display_name": state.get("display_name"), "account_id": state.get("account_id"),
                "authenticated": False, "session_active": False,
                "cached_authenticated": bool(state.get("authenticated")),
                "updated_at": state.get("updated_at"), "cached_updated_at": state.get("updated_at"),
                "prepared_item_id": state.get("prepared_item_id"),
                "capabilities": {name: False for name in ("login", "inspect", "prepare", "publish", "analytics")}}
    active = _alive()
    return {
        "status": state.get("status", "未连接") if active else "连接器未运行",
        "display_name": state.get("display_name"),
        "account_id": state.get("account_id"),
        "authenticated": bool(state.get("authenticated")) and active,
        "session_active": active and bool(state.get("session_active")),
        "url": state.get("url"),
        "error": state.get("error"),
        "updated_at": state.get("updated_at"),
        "prepared_item_id": state.get("prepared_item_id"),
    }


def _ensure_daemon():
    require_automation("xiaohongshu")
    _directories()
    with (PRIVATE / "start.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _alive():
            return
        if SERVICE_FILE.is_file():
            # A new session does not escape a systemd cgroup. Delegate ownership
            # to a separate service so an API/worker restart cannot kill Chromium.
            result = subprocess.run(["systemctl", "--user", "start", SERVICE_NAME],
                                    capture_output=True, text=True, timeout=20)
            if result.returncode:
                raise RuntimeError("无法启动独立小红书连接器服务，请检查用户服务状态")
            return
        log_path = PRIVATE / "daemon.log"
        with log_path.open("ab") as log:
            log_path.chmod(0o600)
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve"],
                cwd=str(PROJECT_ROOT / "backend"), stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True,
            )
        PID_FILE.write_text(str(process.pid))
        PID_FILE.chmod(0o600)


def _media_path(value, suffixes):
    if not isinstance(value, str) or not value:
        raise ValueError("缺少素材文件路径")
    path = Path(value).expanduser().resolve()
    storage = (PROJECT_ROOT / "backend" / "storage").resolve()
    if not path.is_relative_to(storage) or not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError("只能上传本工程 storage 内的有效媒体文件")
    return str(path)


def _payload(action, payload):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload 必须为对象")
    if action in {"login", "inspect"}:
        return {}
    item_id = payload.get("item_id")
    if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("需要有效的 item_id")
    if action == "publish":
        return {"item_id": item_id}
    title, caption = payload.get("title"), payload.get("caption", "")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 20:
        raise ValueError("小红书标题需为 1–20 个字符")
    if not isinstance(caption, str):
        raise ValueError("正文需为字符串")
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("标签需为字符串数组")
    body = caption.strip()
    if tags:
        body += "\n\n" + " ".join("#" + tag.lstrip("#").strip() for tag in tags[:10] if tag.strip())
    if len(body) > 1000:
        raise ValueError("正文和标签合计不能超过 1000 个字符")
    value = {"item_id": item_id, "title": title.strip(), "caption": body,
             "video_path": _media_path(payload.get("video_path"), {".mp4", ".mov"})}
    if payload.get("cover_path"):
        value["cover_path"] = _media_path(payload["cover_path"], {".png", ".jpg", ".jpeg"})
    return value


def submit_command(action: str, payload: dict | None = None) -> dict:
    require_automation("xiaohongshu")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("不支持的连接器操作")
    clean = _payload(action, payload)
    _directories()
    command_id = str(uuid.uuid4())
    result = {"command_id": command_id, "action": action, "status": "queued",
              "message": "已加入小红书连接器队列", "created_at": _now(), "updated_at": _now()}
    _write(PRIVATE / "results" / f"{command_id}.json", result)
    _write(PRIVATE / "commands" / f"{command_id}.json", {**result, "payload": clean})
    _ensure_daemon()
    return result


def command_result(command_id: str) -> dict:
    try:
        valid = str(uuid.UUID(command_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("无效的 command_id") from None
    result = _read(PRIVATE / "results" / f"{valid}.json")
    if not result:
        return {"command_id": valid, "status": "failed", "message": "命令不存在"}
    return result


class _ShadowButton:
    """A real, labelled UI element inside the portal's closed shadow root.

    CDP is used only to read DOM state/bounds. Submission remains a browser mouse
    click, with upload, disabled-state and overlay checks still enforced.
    """
    def __init__(self, page, session, node):
        self.page, self.session, self.node = page, session, node

    def _state(self):
        resolved = self.session.send("DOM.resolveNode", {"backendNodeId": self.node["backendNodeId"]})
        object_id = resolved["object"]["objectId"]
        try:
            result = self.session.send("Runtime.callFunctionOn", {
                "objectId": object_id, "returnByValue": True,
                "functionDeclaration": """function() {
                    let enabled=true, visible=true;
                    for(let e=this;e&&e.nodeType===1;e=e.parentElement) {
                        const style=getComputedStyle(e);
                        if(style.display==='none'||style.visibility==='hidden'||style.opacity==='0') visible=false;
                        if(e.disabled||e.getAttribute('aria-disabled')==='true'||/(^|[ _-])disabled([ _-]|$)/i.test(e.className||'')) enabled=false;
                    }
                    const rect=this.getBoundingClientRect();
                    const root=this.getRootNode(), host=root.host;
                    const x=rect.x+rect.width/2, y=rect.y+rect.height/2;
                    const hit=this.ownerDocument.elementFromPoint(x,y);
                    const unobstructed=!!hit&&(hit===host||hit===this||this.contains(hit));
                    return {enabled,visible:visible&&rect.width>0&&rect.height>0,unobstructed,
                        text:(this.textContent||'').trim(),tag:this.tagName.toLowerCase(),
                        attrs:Object.fromEntries([...this.attributes].filter(a=>['class','role','disabled','aria-disabled'].includes(a.name)).map(a=>[a.name,a.value]))};
                }""",
            })
            state = result.get("result", {}).get("value")
            if not isinstance(state, dict):
                raise ValueError("无法读取官方发布控件状态，尚未发布")
            return state
        finally:
            self.session.send("Runtime.releaseObject", {"objectId": object_id})

    def is_visible(self):
        return self._state()["visible"]

    def is_enabled(self):
        state = self._state()
        return state["enabled"] and state["visible"]

    def get_attribute(self, name):
        return self._state()["attrs"].get(name)

    def click(self):
        self.session.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": self.node["backendNodeId"]})
        state = self._state()
        if state["text"] != "发布" or not all(state[key] for key in ("enabled", "visible", "unobstructed")):
            raise ValueError("官方发布按钮不可用或被遮挡，未执行点击")
        model = self.session.send("DOM.getBoxModel", {"backendNodeId": self.node["backendNodeId"]})["model"]
        quad = model["content"]
        self.page.mouse.click(sum(quad[0::2]) / 4, sum(quad[1::2]) / 4)


class _Browser:
    def __init__(self, playwright):
        require_automation("xiaohongshu")
        self.context = playwright.chromium.launch_persistent_context(
            str(PROFILE), headless=True, viewport={"width": 1440, "height": 1000},
            locale="zh-CN", args=["--disable-dev-shm-usage",
                                  "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"],
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        self.prepared = None
        self.submission_attempted = None
        self.page.goto(OFFICIAL_HOME, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(4000)

    def inspect(self):
        page = self.page
        address = urlsplit(page.url)
        body = page.locator("body").inner_text()
        authenticated = (address.hostname == "creator.xiaohongshu.com"
                         and address.path not in ("/", "/login", "")
                         and sum(word in body for word in ("发布笔记", "笔记管理", "数据中心", "首页", "内容管理")) >= 2)
        old = _read(STATE_FILE)
        name = None
        if authenticated:
            for locator in page.locator("[class*=user-name], [class*=userName], [class*=nickname], [class*=nick-name], [class*=account-name], [class*=user_name]").all():
                if locator.is_visible():
                    value = locator.inner_text().strip()
                    if value and len(value) <= 40 and not re.search(r"1[3-9]\d{9}", value):
                        name = value
                        break
            previous_name = old.get("display_name")
            if not name and previous_name and previous_name in body[:1500]:
                name = previous_name
        status = "已登录" if authenticated else "等待扫码"
        if not authenticated and any(s in body for s in ("扫码成功", "手机确认", "确认登录")):
            status = "需要手机确认"
        if not authenticated and any(s in body for s in ("二维码已失效", "二维码已过期", "二维码过期", "二维码失效")):
            status = "二维码已过期"
        state = {"status": status, "display_name": name, "account_id": old.get("account_id") if authenticated else None,
                 "authenticated": authenticated, "session_active": True, "error": None,
                 "url": _clean_url(page.url), "updated_at": _now(),
                 "prepared_item_id": self.prepared["item_id"] if self.prepared else None}
        _write(STATE_FILE, state)
        # Only the official login page can become the publicly displayed image.
        if address.path == "/login":
            page.screenshot(path=str(LOGIN_IMAGE), full_page=True)
            LOGIN_IMAGE.chmod(0o600)
        return state

    def login(self):
        if self.inspect()["authenticated"]:
            return {"status": "prepared", "message": "账号已登录，无需重复扫码", "connection": self.inspect()}
        self.prepared = None
        self.page.goto(OFFICIAL_LOGIN, wait_until="domcontentloaded", timeout=45000)
        self.page.wait_for_timeout(1500)
        try:
            self.page.get_by_text("请稍候...", exact=True).wait_for(state="hidden", timeout=30000)
        except Exception:
            pass
        if "短信登录" in self.page.locator("body").inner_text():
            self.page.mouse.click(1280, 350)
            self.page.get_by_text("APP扫一扫登录", exact=True).wait_for(timeout=15000)
        self.page.wait_for_timeout(1000)
        self.inspect()
        return {"status": "prepared", "message": "官方二维码已更新，请用小红书 App 扫码并确认"}

    def _editor(self):
        self.page.goto(OFFICIAL_HOME, wait_until="domcontentloaded", timeout=45000)
        entry = self.page.get_by_text("发布笔记", exact=True).first
        entry.wait_for(state="visible", timeout=45000)
        previous = len(self.context.pages)
        entry.click()
        self.page.wait_for_timeout(1500)
        if len(self.context.pages) > previous:
            self.page = self.context.pages[-1]
        self.page.locator("input[type=file].upload-input").first.wait_for(state="attached", timeout=45000)
        # The SPA inserts its file input before the upload event handler is ready.
        self.page.get_by_role("button", name="上传视频", exact=True).wait_for(state="visible", timeout=15000)
        self.page.wait_for_timeout(2000)

    @staticmethod
    def _visible(locators):
        for locator in locators:
            for match in locator.all():
                if match.is_visible():
                    return match
        return None

    def _publish_button(self):
        # The portal may render a hidden mobile copy before the visible footer.
        # Inspect all exact matches and include child frames, without coordinates.
        locators = []
        for frame in self.page.frames:
            locators.extend([frame.get_by_role("button", name="发布", exact=True),
                             frame.get_by_text("发布", exact=True)])
        button = self._visible(locators)
        if button is not None:
            return button
        return next((button for button in self._shadow_publish_buttons() if button.is_visible()), None)

    def _shadow_publish_buttons(self):
        # The live portal renders its footer in <xhs-publish-btn> with a closed
        # shadow root. Read only that component, retaining exact label matching.
        cached = getattr(self, "_cdp", None)
        if not cached or cached[0] is not self.page:
            cached = (self.page, self.context.new_cdp_session(self.page))
            self._cdp = cached
        session = cached[1]
        document = session.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        def descendants(node):
            yield node
            for key in ("children", "shadowRoots"):
                for child in node.get(key, []):
                    yield from descendants(child)
        def text_content(node):
            if node.get("nodeType") == 3:
                return node.get("nodeValue", "")
            return "".join(text_content(child) for child in node.get("children", []))
        candidates = []
        for host in descendants(document):
            if host.get("nodeName") != "XHS-PUBLISH-BTN":
                continue
            for node in descendants(host):
                if node.get("nodeType") == 1 and text_content(node).strip() == "发布":
                    candidates.append(node)
        candidates.sort(key=lambda node: node.get("nodeName") != "BUTTON")
        return [_ShadowButton(self.page, session, node) for node in candidates]

    def _wait_upload(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            text = self.page.locator("body").inner_text()
            if any(message in text for message in ("上传失败", "上传出错", "视频处理失败")):
                raise ValueError("官方页面提示视频上传失败，尚未发布")
            if (self._visible([self.page.get_by_text("重新上传", exact=True)]) is not None
                    and not re.search(r"上传中\s*\d*\s*%?", text)):
                return
            self.page.wait_for_timeout(1000)
        raise ValueError("视频仍未上传完成，尚未发布")

    def _wait_publish_ready(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            button = self._publish_button()
            if button is not None and button.is_enabled() and button.get_attribute("aria-disabled") != "true":
                return
            self.page.wait_for_timeout(1000)
        self._record_publish_controls()
        raise ValueError("上传后发布按钮仍不可用，请检查官方页面提示，尚未发布")

    def _record_publish_controls(self):
        controls = []
        for frame in self.page.frames:
            for kind, locator in (("role", frame.get_by_role("button", name="发布", exact=True)),
                                  ("text", frame.get_by_text("发布", exact=True))):
                for match in locator.all():
                    controls.append({"kind": kind, "frame_url": _clean_url(frame.url),
                                     "visible": match.is_visible(),
                                     "tag": match.evaluate("e => e.tagName.toLowerCase()"),
                                     "class": match.get_attribute("class"),
                                     "role": match.get_attribute("role")})
        for button in self._shadow_publish_buttons():
            controls.append({"kind": "closed_shadow_ui", "host": "xhs-publish-btn", **button._state()})
        # Keep diagnostic editor evidence private; the login endpoint only serves
        # the separate QR image. Read DOM metadata, never session storage/cookies.
        diagnostics = self.page.evaluate("""() => {
            const describe = e => ({tag:e.tagName, class:e.className?.toString().slice(0,200),
                text:(e.innerText||'').slice(0,160), role:e.getAttribute('role'),
                before:getComputedStyle(e,'::before').content,
                after:getComputedStyle(e,'::after').content, shadow:!!e.shadowRoot});
            const points = [550, 750, 950].map(x => document.elementFromPoint(x,innerHeight-45));
            return {bottom: points.map(e => {const a=[]; for(let i=0;e&&i<5;i++,e=e.parentElement) a.push(describe(e));return a;}),
                tail:[...document.querySelectorAll('body *')].slice(-80).map(describe),
                custom:[...document.querySelectorAll('body *')].filter(e=>e.tagName.includes('-')).map(describe)};
        }""")
        diagnostics["title"] = self.page.get_by_placeholder(re.compile("标题")).first.input_value()
        _write(PRIVATE / "editor-controls.json", {"checked_at": _now(), "controls": controls, "diagnostics": diagnostics})
        screenshot = PRIVATE / "editor-current.png"
        self.page.screenshot(path=str(screenshot), full_page=True)
        screenshot.chmod(0o600)
        button = self._publish_button()
        return button is not None and button.is_enabled()

    def prepare(self, payload):
        ledger = _submission_ledger()
        if payload["item_id"] in ledger:
            raise ValueError("该条内容已有提交记录，不能自动重复准备或发布")
        self.prepared = None
        self._editor()
        self.page.locator("input[type=file].upload-input").first.set_input_files(payload["video_path"])
        title = self.page.get_by_placeholder(re.compile("标题"))
        title.first.wait_for(state="visible", timeout=180000)
        self._wait_upload()
        title.first.fill(payload["title"])
        body = self._visible([
            self.page.locator(".tiptap[contenteditable=true]"),
            self.page.locator(".ql-editor[contenteditable=true]"),
            self.page.locator("[contenteditable=true]"),
            self.page.get_by_placeholder(re.compile("正文|内容|描述")),
        ])
        if body is None:
            raise ValueError("未找到正文编辑器，尚未发布")
        body.fill(payload["caption"])
        declaration = "笔记含AI合成内容"
        declaration_control = self.page.get_by_text("添加内容类型声明", exact=True)
        if declaration_control.count() and declaration_control.first.is_visible():
            declaration_control.first.click()
            option = self.page.get_by_text(declaration, exact=True).first
            option.wait_for(state="visible", timeout=10000)
            option.click()
            self.page.keyboard.press("Escape")
        self.page.get_by_text(declaration, exact=True).first.wait_for(state="visible", timeout=10000)
        self._wait_publish_ready()
        warnings = []
        if payload.get("cover_path"):
            warnings.append("本次使用平台视频帧封面，尚未应用素材包中的独立封面")
        # Leave the editor open. A separate command must explicitly name this item.
        self.prepared = {**payload, "prepared_at": _now(), "ai_disclosure": declaration}
        publish_control_available = self._record_publish_controls()
        self.inspect()
        return {"status": "prepared", "message": "视频已上传并填写标题正文，尚未点击发布",
                "item_id": payload["item_id"], "title": payload["title"], "warnings": warnings,
                "uploaded": True, "published": False, "ai_disclosure": declaration,
                "publish_control_available": publish_control_available}

    def publish(self, payload):
        self.submission_attempted = None
        item_id = payload["item_id"]
        ledger = _submission_ledger()
        prior = ledger.get(item_id)
        if prior:
            self.submission_attempted = True
            if prior.get("status") == "submitted":
                return {"status": "submitted", "message": "该条已提交，未重复点击发布", "item_id": item_id,
                        "evidence": prior.get("evidence", {}), "submission_attempted": True}
            raise ValueError("该条已有提交尝试且状态尚未确认，请核对平台后台；连接器不会重复发布")
        # From this point until the durable attempting record, no click has run.
        self.submission_attempted = False
        if not self.prepared or self.prepared["item_id"] != item_id:
            raise ValueError("必须先在当前会话准备同一 item_id，重启后需重新准备")
        if urlsplit(self.page.url).path != "/publish/publish":
            raise ValueError("当前页面不是已准备的编辑页，尚未发布")
        title = self.page.get_by_placeholder(re.compile("标题")).first
        if title.input_value().strip() != self.prepared["title"]:
            raise ValueError("编辑页标题与准备记录不一致，尚未发布")
        editor = self._visible([self.page.locator(".tiptap[contenteditable=true]"),
                                self.page.locator(".ql-editor[contenteditable=true]"),
                                self.page.locator("[contenteditable=true]")])
        if editor is None or re.sub(r"\s+", "", editor.inner_text()) != re.sub(r"\s+", "", self.prepared["caption"]):
            raise ValueError("编辑页正文与准备记录不一致，尚未发布")
        if not self.page.get_by_text(self.prepared["ai_disclosure"], exact=True).first.is_visible():
            raise ValueError("AI 合成内容声明未保留，尚未发布")
        self._wait_upload()
        button = self._publish_button()
        if button is None:
            raise ValueError("未找到明确的发布控件，尚未发布")
        if (not button.is_enabled() or button.get_attribute("aria-disabled") == "true"
                or "disabled" in (button.get_attribute("class") or "").lower()):
            raise ValueError("发布按钮尚不可用，可能仍在上传或需要补充信息")
        receipts = []

        def on_response(response):
            address = urlsplit(response.url)
            if response.request.method != "POST" or "publish" not in address.path.lower():
                return
            if not address.hostname or not (address.hostname == "xiaohongshu.com" or address.hostname.endswith(".xiaohongshu.com")):
                return
            try:
                value = response.json()
                data = value.get("data") or {}
                note_id = data.get("note_id") or data.get("noteId") or data.get("id")
                success = value.get("success") is True or value.get("code") == 0
                if success and isinstance(note_id, (str, int)) and str(note_id):
                    receipts.append({"type": "api_receipt", "endpoint": address.path, "note_id": str(note_id)})
            except Exception:
                pass

        self.page.on("response", on_response)
        # Set the in-memory flag before persisting: even a write failure must not
        # accidentally advertise a safe retry after an ambiguous side effect.
        self.submission_attempted = True
        ledger[item_id] = {"status": "attempting", "started_at": _now()}
        try:
            _write(PRIVATE / "submissions.json", ledger)
            button.click()
            deadline = time.monotonic() + 60
            evidence = None
            while time.monotonic() < deadline:
                self.page.wait_for_timeout(1000)
                if receipts:
                    evidence = receipts[0]
                    break
                text = self.page.locator("body").inner_text()
                if "发布成功" in text or "笔记发布成功" in text:
                    evidence = {"type": "success_page", "text": "发布成功", "url": _clean_url(self.page.url)}
                    break
                if any(word in text for word in ("安全验证", "请完成验证", "短信验证", "滑块验证")):
                    raise ValueError("平台要求额外验证，请在官方页面完成；尚无成功回执")
            if not evidence:
                raise ValueError("已点击提交，但没有取得成功页或明确回执，需要核对平台后台，禁止自动重发")
            ledger[item_id] = {"status": "submitted", "submitted_at": _now(), "evidence": evidence}
            _write(PRIVATE / "submissions.json", ledger)
            self.prepared = None
            self.inspect()
            return {"status": "submitted", "message": "平台已确认提交，后续审核状态以小红书后台为准",
                    "item_id": item_id, "evidence": evidence, "submission_attempted": True}
        finally:
            self.page.remove_listener("response", on_response)


def _serve():
    require_automation("xiaohongshu")
    from playwright.sync_api import sync_playwright

    _directories()
    def stop_handler(_signum, _frame):
        global _STOP
        _STOP = True
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    with (RUNTIME / "xiaohongshu-profile.lock").open("a") as profile_lock:
        os.chmod(profile_lock.name, 0o600)
        try:
            fcntl.flock(profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(2)
        PID_FILE.write_text(str(os.getpid()))
        PID_FILE.chmod(0o600)
        # Never silently replay a command interrupted after an upload or click.
        for path in (PRIVATE / "results").glob("*.json"):
            result = _read(path)
            if result.get("status") == "running":
                result.update(status="failed", message="连接器曾中断，请核对平台状态；未自动重放", updated_at=_now())
                if result.get("action") == "publish":
                    result["submission_uncertain"] = True
                _write(path, result)
        browser = None
        with sync_playwright() as playwright:
            try:
                browser = _Browser(playwright)
                browser.inspect()
                last_inspect = time.monotonic()
                while not _STOP:
                    for path in sorted((PRIVATE / "commands").glob("*.json"), key=lambda p: p.stat().st_mtime):
                        command = _read(path)
                        result_path = PRIVATE / "results" / path.name
                        result = _read(result_path)
                        if result.get("status") != "queued":
                            continue
                        result.update(status="running", message="连接器正在执行", updated_at=_now())
                        _write(result_path, result)
                        try:
                            action = command["action"]
                            if action == "inspect":
                                value = {"status": "prepared", "message": "登录状态已检查", "connection": browser.inspect()}
                                if browser.prepared:
                                    value["publish_control_available"] = browser._record_publish_controls()
                            else:
                                value = getattr(browser, action)(command.get("payload", {})) if action in {"prepare", "publish"} else browser.login()
                            result.update(value, updated_at=_now())
                        except Exception as exc:
                            message = str(exc) if isinstance(exc, ValueError) else f"官方页面操作未完成（{type(exc).__name__}），请检查登录状态后重试"
                            result.update(status="failed", message=message[:500], updated_at=_now(),
                                          **_failure_fields(command.get("action"), browser))
                        _write(result_path, result)
                    if time.monotonic() - last_inspect >= 15:
                        browser.inspect()
                        last_inspect = time.monotonic()
                    browser.page.wait_for_timeout(1000)
            except Exception as exc:
                old = _read(STATE_FILE)
                old.update(status="连接器中断", authenticated=False, session_active=False,
                           error=f"{type(exc).__name__}：请重新检查连接", updated_at=_now())
                _write(STATE_FILE, old)
                for path in (PRIVATE / "results").glob("*.json"):
                    result = _read(path)
                    if result.get("status") in {"queued", "running"}:
                        result.update(status="failed", message="连接器启动或会话中断，请检查连接后重试", updated_at=_now())
                        if result.get("action") == "publish":
                            result["submission_uncertain"] = True
                        _write(path, result)
                print(type(exc).__name__, flush=True)
                raise SystemExit(1) from None
            finally:
                if browser:
                    try:
                        browser.context.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    if "--serve" in sys.argv:
        try:
            _serve()
        except AutomationDisabled as exc:
            # An old enabled unit must exit cleanly, not enter a restart loop.
            print(str(exc), flush=True)
