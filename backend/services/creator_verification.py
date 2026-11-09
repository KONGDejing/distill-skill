"""User-operated official identity dialog; codes never enter the file queue."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import urlsplit

from services import creator_connector as connector
from services.creator_login_status import douyin_verification


def _directory(platform):
    if platform != "douyin":
        raise ValueError("该平台尚未启用官方身份验证窗口")
    return connector._directories(platform)


def page_token(platform):
    if platform != "douyin":
        return ""
    directory = _directory(platform)
    with (directory / "verification-token.lock").open("a") as lock:
        lock_path = directory / "verification-token.lock"
        lock_path.chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "verification-token.json"
        value = connector._read(path)
        if value.get("expires_at", 0) <= time.time():
            value = {"token": secrets.token_urlsafe(32), "expires_at": time.time() + 3600}
            connector._write(path, value)
        return value["token"]


def authorize(platform, token, origin, expected_origin, fetch_site=None):
    value = connector._read(_directory(platform) / "verification-token.json")
    if (not token or value.get("expires_at", 0) <= time.time()
            or not secrets.compare_digest(str(value.get("token", "")), token)):
        raise ValueError("验证窗口已失效，请重新打开登录页")
    if (origin and origin.rstrip("/") != expected_origin.rstrip("/")) or fetch_site == "cross-site":
        raise ValueError("请从当前工厂登录页操作验证窗口")


@contextmanager
def _official_page(platform):
    from playwright.sync_api import sync_playwright
    directory = _directory(platform)
    if not connector._alive(platform):
        raise ValueError("官方浏览器尚未启动")
    try:
        port_text = (directory / "profile/DevToolsActivePort").read_text().splitlines()[0]
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
    except (OSError, IndexError, ValueError):
        raise ValueError("当前浏览器还未接入官方验证窗口，请稍候") from None
    with (directory / "verification-control.lock").open("a") as lock:
        (directory / "verification-control.lock").chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=10000)
            # Stopping this short-lived CDP client disconnects it; never close
            # the browser or its persistent context owned by the connector.
            for context in browser.contexts:
                for page in reversed(context.pages):
                    parts = urlsplit(page.url)
                    if parts.scheme != "https" or parts.hostname != "creator.douyin.com":
                        continue
                    body = page.locator("body").inner_text(timeout=5000)
                    verification = douyin_verification(body)
                    if verification and verification["kind"] == "identity":
                        yield page
                        return
            raise ValueError("当前没有待处理的官方身份验证窗口；请查看登录状态")


def _panel(page):
    # Observed official second-verification component keeps this panel while
    # its heading changes from identity selection to SMS-code entry.
    for element in page.locator("[class*=second_verify_panel_new-]").element_handles():
        box = element.bounding_box()
        if element.is_visible() and box and 280 <= box["width"] <= 1000 and 160 <= box["height"] <= 900:
            return element, box
        element.dispose()
    for title in ("身份验证", "安全验证", "验证身份", "接收短信验证码"):
        for label in page.get_by_text(title, exact=True).all():
            if not label.is_visible():
                continue
            handle = label.evaluate_handle("""e => {
                for(let n=e;n&&n!==document.body;n=n.parentElement || n.getRootNode().host) {
                    const r=n.getBoundingClientRect(), t=n.innerText||'';
                    if(r.width>=280&&r.width<=1000&&r.height>=160&&r.height<=900
                       && /验证码|短信|验证登录密码/.test(t)) return n;
                }
                return null;
            }""")
            element = handle.as_element()
            if element is not None:
                box = element.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    return element, box
            handle.dispose()
    raise ValueError("尚未定位官方身份验证窗口，请稍候再试")


def _frame_id(page, box):
    geometry = {key: round(box[key], 1) for key in ("x", "y", "width", "height")}
    return hashlib.sha256((page.url.split("?")[0] + json.dumps(geometry, sort_keys=True)).encode()).hexdigest()[:24]


def frame(platform):
    with _official_page(platform) as page:
        panel, box = _panel(page)
        try:
            session = page.context.new_cdp_session(page)
            try:
                viewport = session.send("Page.getLayoutMetrics")["layoutViewport"]
                result = session.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                    "clip": {"x": box["x"] + viewport["pageX"], "y": box["y"] + viewport["pageY"],
                             "width": box["width"], "height": box["height"], "scale": 1.5}})
            finally:
                session.detach()
            return base64.b64decode(result["data"]), _frame_id(page, box)
        finally:
            panel.dispose()


def interact(platform, action, frame_id, x=None, y=None, code=None):
    if action not in {"click", "fill_code"}:
        raise ValueError("不支持的验证操作")
    if action == "click" and (not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                              or not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1):
        raise ValueError("请点击官方验证窗口中的控件")
    if action == "fill_code" and (not isinstance(code, str) or not re.fullmatch(r"[0-9]{4,8}", code)):
        raise ValueError("请输入收到的 4–8 位数字验证码")
    with _official_page(platform) as page:
        panel, box = _panel(page)
        try:
            if not secrets.compare_digest(_frame_id(page, box), frame_id or ""):
                raise ValueError("官方验证窗口已变化，请等待画面更新后再操作")
            if action == "click":
                page.mouse.click(box["x"] + box["width"] * x, box["y"] + box["height"] * y)
            else:
                # Limit text entry to an explicit SMS-code field in this dialog.
                candidates = panel.query_selector_all("input")
                target = next((field for field in candidates if field.is_visible()
                               and field.get_attribute("type") != "password"
                               and "验证码" in (field.get_attribute("placeholder") or "")), None)
                if target is None:
                    raise ValueError("请先在官方窗口选择短信验证，等待验证码输入框出现")
                target.fill(code)
            page.wait_for_timeout(400)
            return {"ok": True, "message": "已操作官方验证窗口，请查看页面结果"}
        finally:
            panel.dispose()
