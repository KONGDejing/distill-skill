"""Small UI-only contract shared by the official creator portal connectors."""
import base64
from pathlib import Path
from urllib.parse import urlsplit


def clean_url(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class BaseAdapter:
    supports_prepare = False
    supports_publish = False

    def __init__(self, context):
        self.context = context
        self.page = context.pages[0] if context.pages else context.new_page()
        self.page.set_default_timeout(15000)

    @staticmethod
    def visible(locators):
        for locator in locators:
            for match in locator.all():
                if match.is_visible():
                    return match
        return None

    def inspect(self):
        """Read official DOM; return authenticated/status/display_name/url/error."""
        raise NotImplementedError("尚未实现官方页面登录状态核验")

    def login(self, *, refresh=False):
        """Open/refresh official QR login without sending SMS or credentials."""
        raise NotImplementedError("尚未实现官方页面扫码登录")

    def capture_login_image(self, path: Path):
        """Save only the current unauthenticated login/QR view; return bool."""
        return False

    def save_login_region(self, locator, path: Path):
        """Read the official QR at high resolution, without unrelated page UI."""
        if locator is None or not locator.is_visible():
            return False
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if not box or min(box["width"], box["height"]) < 100:
            return False
        session = self.context.new_cdp_session(self.page)
        try:
            viewport = session.send("Page.getLayoutMetrics")["layoutViewport"]
            screenshot = session.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                "clip": {"x": box["x"] + viewport["pageX"], "y": box["y"] + viewport["pageY"],
                         "width": box["width"], "height": box["height"], "scale": 3}})
        finally:
            session.detach()
        temporary = Path(path).with_name(Path(path).name + ".tmp")
        temporary.write_bytes(base64.b64decode(screenshot["data"]))
        temporary.chmod(0o600)
        temporary.replace(path)
        return True

    def prepare(self, payload):
        raise ValueError("登录后仍需核验官方上传页面，当前尚未完成上传联调")

    def publication_control(self, payload, prepared):
        """Validate live item/editor/AI disclosure; return its actual publish UI."""
        raise ValueError("尚未核验官方发布控件，未点击提交")

    def require_publication_location(self, prepared):
        """A Beijing network route is not proof of an official work-location tag.

        Until the official location integration is verified, a requested city
        blocks submission rather than silently uploading without that tag.
        """
        if prepared.get("requested_publication_city"):
            raise ValueError("作品位置要求北京，官方地点标签尚未完成联调；网络出口验证不能代替作品位置，未点击提交")

    def start_submission_watch(self):
        """Optional read-only listener, started before the framework's UI click."""

    def stop_submission_watch(self):
        """Remove any read-only listeners installed for the submission."""

    def wait_for_submission(self, timeout=60):
        """Return official success-page/API-receipt evidence, never click success."""
        raise ValueError("尚未取得可核验的官方提交回执")
