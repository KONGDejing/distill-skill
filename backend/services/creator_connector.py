"""Independent, private UI workers for official creator portals.

The browser owns no publishing authority of its own: prepare and publish are
separate queued actions, and only a verified official receipt means submitted.
Account profiles, QR images, commands and submission ledgers live in .runtime.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
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
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "creator-connectors"
PLATFORMS = {
    "douyin": {"name": "抖音", "hosts": {"creator.douyin.com"}, "title_limit": 30, "caption_limit": 1000},
    "kuaishou": {"name": "快手", "hosts": {"cp.kuaishou.com"}, "title_limit": 50, "caption_limit": 500},
    "bilibili": {"name": "哔哩哔哩", "hosts": {"member.bilibili.com", "passport.bilibili.com", "www.bilibili.com"}, "title_limit": 80, "caption_limit": 2000},
}
ACTIONS = {"login", "inspect", "prepare", "publish"}
_STOP = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _platform(platform):
    if platform not in PLATFORMS:
        raise ValueError("不支持的创作平台")
    return platform


def platform_directory(platform):
    return RUNTIME / _platform(platform)


def login_image_path(platform):
    return platform_directory(platform) / "login.png"


def service_name(platform):
    return f"short-video-ai-factory-creator-{_platform(platform)}.service"


def _directories(platform):
    root = platform_directory(platform)
    for path in (RUNTIME, root, root / "profile", root / "commands", root / "results"):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return root


def _write(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ledger(platform):
    path = platform_directory(platform) / "submissions.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise ValueError("提交账本不可读，必须先核对平台状态，禁止自动重试") from None


def _clean_url(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def browser_proxy(platform=None):
    """Read deployment routing without placing proxy addresses in source/logs."""
    override = os.environ.get(f"CREATOR_BROWSER_PROXY_{_platform(platform).upper()}") if platform else None
    if override and override.lower() == "direct":
        return None
    value = (override or os.environ.get("FACTORY_CREATOR_BROWSER_PROXY") or os.environ.get("CREATOR_BROWSER_PROXY")
             or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https", "socks5"} or not parts.hostname:
        raise ValueError("浏览器代理配置格式无效")
    server = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    proxy = {"server": server}
    if parts.username:
        proxy["username"] = unquote(parts.username)
    if parts.password:
        proxy["password"] = unquote(parts.password)
    return proxy


def _alive(platform):
    try:
        pid = int((platform_directory(platform) / "daemon.pid").read_text())
        os.kill(pid, 0)
        proc = Path(f"/proc/{pid}")
        if (proc / "stat").read_text().split()[2] == "Z":
            return False
        args = (proc / "cmdline").read_bytes().split(b"\0")
        return (str(Path(__file__).resolve()).encode() in args and b"--platform" in args
                and args[args.index(b"--platform") + 1] == platform.encode())
    except (OSError, ValueError, IndexError):
        return False


def _recent(timestamp, seconds=45):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
        return 0 <= age <= seconds
    except (TypeError, ValueError):
        return False


def _latest_login(platform):
    identifier = _read(platform_directory(platform) / "login-request.json").get("command_id")
    if not identifier:
        return None
    try:
        result = command_result(platform, identifier)
    except ValueError:
        return None
    if result.get("action") != "login":
        return None
    return {key: result.get(key) for key in ("command_id", "status", "message", "created_at", "updated_at")}


def connection_state(platform) -> dict:
    directory = platform_directory(platform)
    state, active = _read(directory / "state.json"), _alive(platform)
    # A read-only snapshot lets API updates classify a newly observed checkpoint
    # without restarting an in-progress browser confirmation session.
    if platform == "douyin" and active and _recent(state.get("updated_at")) and state.get("state_code") in {"network_error", "needs_verification"}:
        snapshot_path = directory / "login-ui.json"
        try:
            current_session = snapshot_path.stat().st_mtime >= (directory / "daemon.pid").stat().st_mtime
        except OSError:
            current_session = False
        snapshot = _read(snapshot_path) if current_session else {}
        if snapshot.get("url") == state.get("url"):
            from services.creator_login_status import douyin_verification
            verification = douyin_verification(snapshot.get("body", ""))
            if verification:
                state.update(state_code="needs_verification", status="需要官方验证", qr_ready=False,
                             authenticated=False, verification=verification, error=verification["message"])
    login = _latest_login(platform)
    refreshing = bool(active and login and login["status"] in {"queued", "running"})
    qr_ready = (active and not refreshing and state.get("qr_ready") is True
                and state.get("state_code") == "waiting_scan" and _recent(state.get("updated_at"))
                and not state.get("authenticated") and (directory / "login.png").is_file())
    version = None
    if qr_ready:
        try:
            version = hashlib.sha256((directory / "login.png").read_bytes()).hexdigest()[:24]
        except OSError:
            qr_ready = False
    return {"platform": platform, "status": state.get("status", "未连接") if active else "连接器未运行",
            "state_code": state.get("state_code", "unknown") if active else "stopped",
            "authenticated": state.get("authenticated") is True and active,
            "session_active": active and state.get("session_active") is True,
            "display_name": state.get("display_name"), "url": state.get("url"),
            "error": state.get("error"), "updated_at": state.get("updated_at"),
            "qr_ready": bool(qr_ready), "qr_version": version, "login_command": login,
            "verification": state.get("verification") if active else None,
            "prepared_item_id": state.get("prepared_item_id") if active else None,
            "capabilities": state.get("capabilities", {"login": True, "prepare": False, "publish": False})}


def install_service(platform, *, start=False):
    """Create an independent user unit; API restarts cannot terminate this browser."""
    directory = _directories(platform)
    units = Path.home() / ".config" / "systemd" / "user"
    units.mkdir(parents=True, exist_ok=True)
    unit = units / service_name(platform)
    quoted = lambda path: json.dumps(str(path))
    unit.write_text(f"""[Unit]
Description=Short Video AI Factory / {platform} creator connector
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-{ROOT / '.runtime/service-network.env'}
EnvironmentFile=-{ROOT / '.runtime/creator-browser.env'}
Environment=PYTHONUNBUFFERED=1
WorkingDirectory={ROOT / 'backend'}
ExecStart={quoted(sys.executable)} {quoted(Path(__file__).resolve())} --serve --platform {platform}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=mixed
UMask=0077
StandardOutput=append:{directory / 'daemon.log'}
StandardError=append:{directory / 'daemon.log'}

[Install]
WantedBy=default.target
""", encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, timeout=20)
    if start:
        subprocess.run(["systemctl", "--user", "enable", "--now", service_name(platform)],
                       check=True, capture_output=True, timeout=30)
    return {"platform": platform, "service": service_name(platform), "installed": True}


def _ensure_daemon(platform):
    directory = _directories(platform)
    with (directory / "start.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _alive(platform):
            return
        if not (Path.home() / ".config/systemd/user" / service_name(platform)).is_file():
            install_service(platform)
        result = subprocess.run(["systemctl", "--user", "start", service_name(platform)],
                                capture_output=True, timeout=25)
        if result.returncode:
            raise RuntimeError("无法启动平台的独立浏览器服务，请检查用户服务状态")


def _media(value, suffixes):
    if not isinstance(value, str) or not value:
        raise ValueError("缺少有效素材路径")
    path = Path(value).expanduser().resolve()
    if not path.is_relative_to((ROOT / "backend/storage").resolve()) or not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError("只能使用本工程 storage 内的有效素材")
    return str(path)


def _payload(platform, action, payload):
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload 必须为对象")
    if action in {"login", "inspect"}:
        return {}
    item_id = payload.get("item_id")
    if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item_id):
        raise ValueError("需要有效 item_id")
    if action == "publish":
        return {"item_id": item_id}
    title, caption, tags = payload.get("title"), payload.get("caption", ""), payload.get("tags", [])
    config = PLATFORMS[platform]
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= config["title_limit"]:
        raise ValueError(f"标题长度须为 1–{config['title_limit']} 个字符")
    if not isinstance(caption, str) or not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("正文或标签格式无效")
    body = caption.strip()
    clean_tags = [tag.lstrip("#").strip() for tag in tags[:10] if tag.strip()]
    if platform == "kuaishou":
        # Observed official submit response rejects more than four hashtags.
        in_caption = len(re.findall(r"(?<!\S)#[^\s#]+", body))
        if in_caption > 4:
            raise ValueError("快手正文中的话题不能超过4个，请精简后上传")
        clean_tags = list(dict.fromkeys(clean_tags))[:4 - in_caption]
    if len(body) + sum(len(tag) + 2 for tag in clean_tags) > config["caption_limit"]:
        raise ValueError("正文与标签超过平台连接器长度上限")
    result = {"item_id": item_id, "title": title.strip(), "caption": body, "tags": clean_tags,
              "video_path": _media(payload.get("video_path"), {".mp4", ".mov"}), "ai_generated": True}
    if payload.get("cover_path"):
        result["cover_path"] = _media(payload["cover_path"], {".png", ".jpg", ".jpeg"})
    return result


def submit_command(platform, action, payload=None) -> dict:
    _platform(platform)
    if action not in ACTIONS:
        raise ValueError("不支持的平台操作")
    clean = _payload(platform, action, payload)
    _ensure_daemon(platform)
    directory = _directories(platform)
    if action == "login":
        with (directory / "login-request.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = _latest_login(platform)
            if previous and previous["status"] in {"queued", "running"}:
                return command_result(platform, previous["command_id"])
            if connection_state(platform).get("state_code") == "needs_verification":
                raise ValueError("当前会话正在等待官方验证，请完成验证；未刷新二维码")
            result = _enqueue_command(platform, action, clean)
            _write(directory / "login-request.json", {"command_id": result["command_id"]})
            return result
    return _enqueue_command(platform, action, clean)


def _enqueue_command(platform, action, clean):
    directory = _directories(platform)
    identifier = str(uuid.uuid4())
    result = {"platform": platform, "command_id": identifier, "action": action,
              "status": "queued", "message": "已加入官方创作后台操作队列",
              "created_at": _now(), "updated_at": _now()}
    _write(directory / "results" / f"{identifier}.json", result)
    _write(directory / "commands" / f"{identifier}.json", {**result, "payload": clean})
    return result


def command_result(platform, command_id) -> dict:
    directory = platform_directory(platform)
    try:
        identifier = str(uuid.UUID(command_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("无效 command_id") from None
    return _read(directory / "results" / f"{identifier}.json") or {
        "platform": platform, "command_id": identifier, "status": "failed", "message": "命令不存在"}


def _valid_evidence(platform, evidence):
    if not isinstance(evidence, dict):
        return False
    parts = urlsplit(evidence.get("url", ""))
    if parts.scheme != "https" or parts.hostname not in PLATFORMS[platform]["hosts"]:
        return False
    if evidence.get("type") == "api_acceptance":
        return (platform == "kuaishou" and parts.hostname == "cp.kuaishou.com"
                and parts.path == "/rest/cp/works/v2/video/pc/submit"
                and evidence.get("success") is True and type(evidence.get("code")) is int and evidence["code"] == 1)
    if evidence.get("type") == "success_page":
        return str(evidence.get("text", "")).strip(" 。!！") in {
            "发布成功", "作品发布成功", "视频发布成功", "投稿成功", "视频投稿成功", "发表成功", "提交成功"}
    return (evidence.get("type") == "api_receipt" and evidence.get("success") is True
            and bool(evidence.get("item_id") or evidence.get("video_id") or evidence.get("note_id")))


class _Browser:
    def __init__(self, platform, playwright):
        self.platform, self.directory = platform, _directories(platform)
        module = importlib.import_module(f"services.creator_adapters.{platform}")
        options = {"headless": True, "viewport": {"width": 1440, "height": 1000},
                   "locale": "zh-CN", "args": ["--disable-dev-shm-usage", *getattr(module, "BROWSER_ARGS", [])]}
        if platform in {"douyin", "kuaishou"}:
            options["args"].extend(["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0"])
        proxy = browser_proxy(platform)
        if proxy:
            options["proxy"] = proxy
        self.context = playwright.chromium.launch_persistent_context(str(self.directory / "profile"), **options)
        self.adapter = module.Adapter(self.context)
        self.prepared, self.submission_attempted = None, None
        # Each portal has its own observed login entry. Some authenticated-home
        # URLs stall when logged out; let the adapter choose its official route.
        queued_login = any(_read(path).get("action") == "login" and _read(path).get("status") == "queued"
                           for path in (self.directory / "results").glob("*.json"))
        if not queued_login:
            self.adapter.login()

    def login(self):
        current = self.adapter.inspect()
        if not current.get("authenticated") and current.get("status") != "needs_verification":
            login_image_path(self.platform).unlink(missing_ok=True)
            state = _read(self.directory / "state.json")
            state.update(status="正在刷新官方二维码", state_code="refreshing", qr_ready=False,
                         authenticated=False, session_active=True, error=None, updated_at=_now())
            _write(self.directory / "state.json", state)
        self.adapter.login(refresh=True)
        deadline = time.monotonic() + 25
        while True:
            state = self.inspect()
            if state["authenticated"]:
                return {"status": "completed", "message": "账号已登录，无需重新扫码"}
            if state["state_code"] == "needs_verification":
                return {"status": "completed", "message": "请在对应 App 或官方页面完成登录确认"}
            if state["state_code"] == "waiting_scan" and state["qr_ready"]:
                return {"status": "completed", "message": "新的官方二维码已就绪，请立即扫码"}
            if time.monotonic() >= deadline:
                return {"status": "failed", "message": state.get("error") or "官方页面未返回可扫描的新二维码，请稍后重试"}
            self.adapter.page.wait_for_timeout(500)

    def inspect(self):
        value = self.adapter.inspect()
        authenticated = value.get("authenticated") is True
        name = value.get("display_name")
        if not isinstance(name, str) or len(name) > 60 or re.search(r"1[3-9]\d{9}", name):
            name = None
        qr_ready = False
        if authenticated or value.get("status") != "waiting_scan":
            login_image_path(self.platform).unlink(missing_ok=True)
        else:
            qr_ready = self.adapter.capture_login_image(login_image_path(self.platform)) is True
            if qr_ready and login_image_path(self.platform).is_file():
                login_image_path(self.platform).chmod(0o600)
            else:
                qr_ready = False
                login_image_path(self.platform).unlink(missing_ok=True)
        code = value.get("status", "authenticated" if authenticated else "waiting_scan")
        labels = {"authenticated": "已登录", "waiting_scan": "等待扫码", "qr_expired": "二维码已过期",
                  "needs_verification": "需要官方验证", "network_error": "官方页面连接失败", "unknown": "等待核验", "refreshing": "正在刷新官方二维码"}
        state = {"platform": self.platform, "status": labels.get(code, code), "state_code": code,
                 "authenticated": authenticated, "session_active": True, "display_name": name,
                 "url": _clean_url(value.get("url") or self.adapter.page.url), "error": value.get("error"),
                 "qr_ready": qr_ready, "updated_at": _now(),
                 "verification": value.get("verification"),
                 "prepared_item_id": self.prepared["item_id"] if self.prepared else None,
                 "capabilities": {"login": True, "prepare": self.adapter.supports_prepare,
                                  "publish": self.adapter.supports_publish}}
        _write(self.directory / "state.json", state)
        return state

    def prepare(self, payload):
        if payload["item_id"] in _ledger(self.platform):
            raise ValueError("该作品已有提交记录，禁止自动重新准备")
        if not self.inspect()["authenticated"]:
            raise ValueError("请先在官方页面扫码登录，尚未上传")
        if not self.adapter.supports_prepare:
            raise ValueError("账号登录后仍需核验真实上传页面；当前尚未完成上传联调")
        self.prepared = None
        value = self.adapter.prepare(payload)
        if not isinstance(value, dict) or value.get("status") != "prepared":
            raise ValueError("官方上传页面未确认准备完成")
        self.prepared = dict(payload)
        self.inspect()
        return {**value, "status": "prepared", "item_id": payload["item_id"], "published": False}

    def publish(self, payload):
        self.submission_attempted = None
        ledger = _ledger(self.platform)
        item_id = payload["item_id"]
        if item_id in ledger:
            self.submission_attempted = True
            prior = ledger[item_id]
            if isinstance(prior, dict) and prior.get("status") == "submitted" and _valid_evidence(self.platform, prior.get("evidence")):
                return {"status": "submitted", "item_id": item_id, "message": "已有官方提交回执，未重复点击",
                        "evidence": prior["evidence"], "submission_attempted": True, "published": False}
            raise ValueError("已有提交尝试，需要核对平台结果，禁止自动重试")
        self.submission_attempted = False
        if not self.prepared or self.prepared["item_id"] != item_id:
            raise ValueError("必须在当前会话先准备同一作品，尚未提交")
        if not self.inspect()["authenticated"] or not self.adapter.supports_publish:
            raise ValueError("登录或发布联调尚未完成，未点击提交")
        control = self.adapter.publication_control(payload, self.prepared)
        if (control is None or not control.is_visible() or not control.is_enabled()
                or control.get_attribute("aria-disabled") == "true"
                or "disabled" in (control.get_attribute("class") or "").lower()):
            raise ValueError("未找到可用的官方发布控件，未点击提交")
        self.adapter.start_submission_watch()
        self.submission_attempted = True
        ledger[item_id] = {"status": "attempting", "started_at": _now()}
        try:
            _write(self.directory / "submissions.json", ledger)
            control.click()
            evidence = self.adapter.wait_for_submission(timeout=60)
            if not _valid_evidence(self.platform, evidence):
                raise ValueError("已点击但缺少官方成功回执，请人工核对，禁止自动重试")
            evidence = {**evidence, "url": _clean_url(evidence["url"])}
            ledger[item_id] = {"status": "submitted", "submitted_at": _now(), "evidence": evidence}
            _write(self.directory / "submissions.json", ledger)
            self.prepared = None
            self.inspect()
            return {"status": "submitted", "item_id": item_id, "evidence": evidence,
                    "message": "官方已确认提交，公开可见与审核状态仍以平台为准",
                    "submission_attempted": True, "published": False}
        finally:
            self.adapter.stop_submission_watch()


def _failure_fields(action, browser):
    if action != "publish":
        return {}
    attempted = getattr(browser, "submission_attempted", None)
    if attempted is False:
        return {"submission_attempted": False}
    return {"submission_uncertain": True, **({"submission_attempted": True} if attempted is True else {})}


def _serve(platform):
    from playwright.sync_api import sync_playwright
    directory = _directories(platform)
    def stop(_signum, _frame):
        global _STOP
        _STOP = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (directory / "profile.lock").open("a") as lock:
        os.chmod(lock.name, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(2)
        (directory / "daemon.pid").write_text(str(os.getpid()))
        (directory / "daemon.pid").chmod(0o600)
        for path in (directory / "results").glob("*.json"):
            result = _read(path)
            if result.get("status") == "running":
                result.update(status="failed", message="连接器曾中断，请核对平台结果；未自动重放", updated_at=_now())
                if result.get("action") == "publish":
                    result["submission_uncertain"] = True
                _write(path, result)
        browser = None
        with sync_playwright() as playwright:
            try:
                browser = _Browser(platform, playwright)
                browser.inspect()
                checked = time.monotonic()
                while not _STOP:
                    for path in sorted((directory / "commands").glob("*.json"), key=lambda path: path.stat().st_mtime):
                        command, result_path = _read(path), directory / "results" / path.name
                        result = _read(result_path)
                        if result.get("status") != "queued":
                            continue
                        result.update(status="running", message="正在操作官方创作后台", updated_at=_now())
                        _write(result_path, result)
                        try:
                            action = command["action"]
                            if action == "login":
                                value = browser.login()
                            elif action == "inspect":
                                value = {"status": "prepared", "message": "官方账号状态已检查", "connection": browser.inspect()}
                            else:
                                value = getattr(browser, action)(command["payload"])
                            result.update(value, updated_at=_now())
                        except Exception as exc:
                            message = str(exc) if isinstance(exc, ValueError) else f"官方页面操作未完成（{type(exc).__name__}），请检查登录或网络状态"
                            result.update(status="failed", message=message[:400], updated_at=_now(),
                                          **_failure_fields(command.get("action"), browser))
                        _write(result_path, result)
                    if time.monotonic() - checked >= 3:
                        browser.inspect()
                        checked = time.monotonic()
                    browser.adapter.page.wait_for_timeout(1000)
            except Exception as exc:
                diagnostic = re.sub(r"https?://[^\s]+", lambda match: _clean_url(match.group(0)), str(exc))
                _write(directory / "last-error.json", {"type": type(exc).__name__, "message": diagnostic[:1500], "at": _now()})
                state = _read(directory / "state.json")
                state.update(platform=platform, status="连接器中断", authenticated=False, session_active=False,
                             qr_ready=False, prepared_item_id=None, error=f"{type(exc).__name__}：请检查官方页面或网络", updated_at=_now())
                login_image_path(platform).unlink(missing_ok=True)
                _write(directory / "state.json", state)
                for path in (directory / "results").glob("*.json"):
                    result = _read(path)
                    if result.get("status") in {"queued", "running"}:
                        result.update(status="failed", message="浏览器启动或会话中断，未自动重放", updated_at=_now())
                        if result.get("action") == "publish":
                            result["submission_uncertain"] = True
                        _write(path, result)
                raise SystemExit(1) from None
            finally:
                if browser:
                    try:
                        browser.context.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "backend"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", required=True, choices=list(PLATFORMS))
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--install", action="store_true")
    arguments = parser.parse_args()
    if arguments.install:
        install_service(arguments.platform, start=True)
    elif arguments.serve:
        _serve(arguments.platform)
