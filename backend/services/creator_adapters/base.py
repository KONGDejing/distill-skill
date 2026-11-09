"""Small UI-only contract shared by the official creator portal connectors."""
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

    def login(self):
        """Open/refresh official QR login without sending SMS or credentials."""
        raise NotImplementedError("尚未实现官方页面扫码登录")

    def capture_login_image(self, path: Path):
        """Save only the current unauthenticated login/QR view; return bool."""
        return False

    def prepare(self, payload):
        raise ValueError("登录后仍需核验官方上传页面，当前尚未完成上传联调")

    def publication_control(self, payload, prepared):
        """Validate live item/editor/AI disclosure; return its actual publish UI."""
        raise ValueError("尚未核验官方发布控件，未点击提交")

    def start_submission_watch(self):
        """Optional read-only listener, started before the framework's UI click."""

    def stop_submission_watch(self):
        """Remove any read-only listeners installed for the submission."""

    def wait_for_submission(self, timeout=60):
        """Return official success-page/API-receipt evidence, never click success."""
        raise ValueError("尚未取得可核验的官方提交回执")
