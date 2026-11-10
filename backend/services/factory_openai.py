"""Responses transport; no chat-session credentials or cross-provider fallback."""
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx

from config import settings


class ProviderError(Exception):
    def __init__(self, status_code=None, body=None):
        # Provider bodies may contain private request information. Never stringify.
        super().__init__("OpenAI provider request failed")
        self.status_code, self.body = status_code, body


def create_response(*, model, system, messages, max_tokens):
    if not settings.OPENAI_API_KEY:
        raise ProviderError(401, {"error": {"message": "API key not configured"}})
    url = settings.OPENAI_BASE_URL.rstrip("/")
    address = urlsplit(url)
    if address.scheme != "https" or not address.hostname or address.username or address.password or address.query or address.fragment:
        raise ValueError("OpenAI服务地址必须为不含凭据或查询参数的HTTPS地址")
    payload = {"model": model, "instructions": system, "input": messages,
               "max_output_tokens": max_tokens + 2048,
               "reasoning": {"effort": settings.FACTORY_OPENAI_REASONING},
               "text": {"format": {"type": "json_object"}}, "store": False}
    try:
        with httpx.Client(timeout=httpx.Timeout(150, connect=10), follow_redirects=False) as client:
            response = client.post(url + "/responses", json=payload,
                                   headers={"Authorization": "Bearer " + settings.OPENAI_API_KEY})
    except httpx.RequestError:
        raise ProviderError() from None
    try:
        data = response.json()
    except ValueError:
        raise ProviderError(response.status_code) from None
    if response.status_code != 200 or not isinstance(data, dict):
        raise ProviderError(response.status_code, data)
    usage = data.get("usage") or {}
    if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
        raise ValueError("OpenAI响应缺少实际用量，不能伪造调用记录")
    # An incomplete response is still accounted for, then rejected by the caller.
    text = "\n".join(c["text"] for block in data.get("output", []) if block.get("type") == "message"
                     for c in block.get("content", []) if c.get("type") == "output_text" and isinstance(c.get("text"), str))
    if data.get("status") != "completed":
        text = ""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"]),
        stop_reason=data.get("status"))
