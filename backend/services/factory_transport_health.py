"""Bounded, unauthenticated public connectivity checks for the restored Kuaishou route.

No account cookie, browser, upload or generic overseas proxy is used. A healthy
network is separate from account login, the official location control and a
successful upload receipt.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
STATUS_FILE = ROOT / '.runtime/creator-transport-status.json'
PROXY_FILE = ROOT / '.runtime/creator-browser.env'
INTERVAL_SECONDS = 300
_last_session_attempt = float('-inf')


def restore_saved_session(state):
    """Resume the owned profile after recovery, without uploading or submitting."""
    from services.factory_platform_policy import automation_enabled
    if not state or not state.get('ready') or not automation_enabled('kuaishou'):
        return
    from services.creator_connector import _alive, _ensure_daemon
    if _alive('kuaishou'):
        return
    global _last_session_attempt
    now = time.monotonic()
    if now - _last_session_attempt < INTERVAL_SECONDS:
        return
    _last_session_attempt = now
    _ensure_daemon('kuaishou')


def _probe(proxy):
    try:
        with httpx.Client(proxy=proxy, trust_env=False, timeout=8, follow_redirects=False) as client:
            with client.stream('GET', 'https://cp.kuaishou.com/') as response:
                if 200 <= response.status_code < 400:
                    return True, '快手官方公共入口HTTPS已连通；登录、北京地点控件及发布回执分别核验'
                return False, f'快手官方公共入口返回HTTP {response.status_code}，自动上传继续等待'
    except (httpx.HTTPError, ValueError, ImportError) as error:
        return False, f'专用北京代理连接快手HTTPS失败（{type(error).__name__}）；自动上传保留排期，不回退其他线路'


def refresh_kuaishou_transport(now=None):
    from services.factory_platform_policy import automation_enabled
    if not automation_enabled('kuaishou') or not STATUS_FILE.is_file():
        return None
    current = now or datetime.now(timezone.utc)
    lock_path = STATUS_FILE.with_suffix('.health.lock')
    with lock_path.open('a') as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        try:
            states = json.loads(STATUS_FILE.read_text())
            if not isinstance(states, dict):
                states = {}
        except (OSError, ValueError):
            states = {}
        try:
            prior = states.get('kuaishou', {})
            checked = datetime.fromisoformat(prior.get('checked_at', ''))
            age = (current - checked).total_seconds()
            if 0 <= age < INTERVAL_SECONDS:
                return prior
        except (ValueError, TypeError, AttributeError):
            pass
        private = dotenv_values(PROXY_FILE) if PROXY_FILE.is_file() else {}
        # Only the creator's explicitly configured route is acceptable. Never
        # inherit HTTPS_PROXY or interpret "direct" as a fallback permission.
        proxy = (private.get('CREATOR_BROWSER_PROXY_KUAISHOU') or os.environ.get('CREATOR_BROWSER_PROXY_KUAISHOU')
                 or private.get('FACTORY_CREATOR_BROWSER_PROXY') or os.environ.get('FACTORY_CREATOR_BROWSER_PROXY')
                 or private.get('CREATOR_BROWSER_PROXY') or os.environ.get('CREATOR_BROWSER_PROXY'))
        if not proxy or not proxy.startswith(('http://', 'https://', 'socks5://')):
            ready, reason = False, '缺少专用北京代理配置；未使用直连或通用代理自动上传快手'
        else:
            ready, reason = _probe(proxy)
        value = {'ready': ready, 'reason': reason, 'checked_at': current.isoformat(),
                 'check_scope': 'unauthenticated_public_homepage_headers_only', 'retry_seconds': INTERVAL_SECONDS}
        states['kuaishou'] = value
        temporary = STATUS_FILE.with_suffix('.health.tmp')
        temporary.write_text(json.dumps(states, ensure_ascii=False, indent=2))
        temporary.chmod(0o600); temporary.replace(STATUS_FILE)
        return value
