"""Read-only receipt watch around the connector's one authorized submit click."""
import re
import time
from urllib.parse import urlsplit

from .base import clean_url


class SubmissionWatch:
    def start_submission_watch(self):
        self._submission_receipt = None
        self._submission_error = None
        self.page.on("response", self._observe_submission)

    def stop_submission_watch(self):
        self.page.remove_listener("response", self._observe_submission)

    def _observe_submission(self, response):
        address = urlsplit(response.url)
        submission_path = (address.path == "/rest/cp/works/v2/video/pc/submit" if self.portal_host == "cp.kuaishou.com"
                           else bool(re.search(r"/aweme/(?:create|publish)/?$", address.path)))
        if (address.hostname != self.portal_host or response.request.method != "POST"
                or not submission_path
                or response.status != 200):
            return
        try:
            data = response.json()
        except Exception:
            return
        if not isinstance(data, dict):
            return
        code = data.get("status_code") if self.portal_host == "creator.douyin.com" else data.get("result")
        success = type(code) is int and code == (0 if self.portal_host == "creator.douyin.com" else 1)
        if not success:
            if type(code) is int:
                self._submission_error = code
            return
        objects = [data, data.get("data"), data.get("aweme")]
        if isinstance(data.get("data"), dict):
            objects.append(data["data"].get("aweme"))
        identifier = next((obj[key] for obj in objects if isinstance(obj, dict)
            for key in ("aweme_id", "photoId", "photo_id", "videoId", "video_id") if obj.get(key)), None)
        if identifier:
            self._submission_receipt = {"type": "api_receipt", "success": True,
                "video_id": str(identifier), "url": clean_url(response.url)}

    def wait_for_submission(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._submission_receipt:
                return self._submission_receipt
            if self._submission_error is not None:
                raise ValueError(f"官方提交接口返回错误码 {self._submission_error}，未确认发布；不会自动重复提交")
            for text in ("发布成功", "作品发布成功", "视频发布成功", "提交成功"):
                visible = self.visible([self.page.get_by_text(text, exact=True)])
                if visible is not None and urlsplit(self.page.url).hostname == self.portal_host:
                    return {"type": "success_page", "text": text, "url": clean_url(self.page.url)}
            self.page.wait_for_timeout(500)
        raise ValueError("已经点击提交但未取得官方成功回执，需人工核对；不会自动重试")
