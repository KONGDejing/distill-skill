"""Trusted local Codex jobs using saved ChatGPT sign-in, never exported tokens."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace

from config import settings
from services.factory_openai import ProviderError

ENV_KEYS = {"HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR",
            "CODEX_HOME", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"}
DISABLED = ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks", "image_generation",
            "computer_use", "browser_use", "view_image", "sleep_tool", "code_mode_host", "skill_search", "memories")


def subscription_environment():
    # In particular, API keys and gateway credentials cannot override saved login.
    return {k: v for k, v in os.environ.items() if k in ENV_KEYS}


def parse_events(stdout):
    message, usage, failure = None, None, None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            message = event["item"].get("text")
        elif kind == "turn.completed":
            usage = event.get("usage")
        elif kind in {"turn.failed", "error"}:
            failure = event
    if failure and usage is None:
        text = json.dumps(failure, ensure_ascii=False).lower()
        if any(term in text for term in ("usage limit", "usage_limit", "quota", "额度", "rate_limit")):
            raise ProviderError(429, {"error": {"code": "insufficient_quota"}})
        if any(term in text for term in ("unauthorized", "authentication", "login", "401", "403")):
            raise ProviderError(401)
        raise ProviderError(400)  # bounded stop; never echo private CLI error bodies
    if not isinstance(message, str) or not isinstance(usage, dict) or not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
        raise ProviderError(400)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=message)],
        usage=SimpleNamespace(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                              cached_input_tokens=usage.get("cached_input_tokens", 0)), stop_reason="completed")


def create_response(*, model, system, messages, max_tokens):
    environment = subscription_environment()
    try:
        login = subprocess.run([settings.FACTORY_CODEX_BIN, "login", "status"], env=environment,
                               capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError(401) from None
    if login.returncode or "logged in using chatgpt" not in (login.stdout + login.stderr).lower():
        raise ProviderError(401)
    # Isolate each role from repo instructions and this interactive chat history.
    with tempfile.TemporaryDirectory(prefix="factory-codex-role-") as directory:
        instructions = Path(directory) / "role.txt"
        instructions.write_text(system + "\n只完成当前内容岗位任务，返回JSON。不得调用工具、读写文件或执行命令。", encoding="utf-8")
        command = [settings.FACTORY_CODEX_BIN, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--model", model, "--json",
                   "-c", "approval_policy=\"never\"", "-c", "web_search=\"disabled\"",
                   "-c", "model_reasoning_effort=\"low\"", "-c", "skills.include_instructions=false",
                   "-c", "project_doc_max_bytes=0", "-c", "model_instructions_file=" + json.dumps(str(instructions))]
        for feature in DISABLED:
            command += ["--disable", feature]
        command += ["-"]
        prompt = "\n\n".join(m["content"] for m in messages) + f"\n只输出所需JSON，避免重复材料，目标输出不超过{max_tokens} tokens。"
        try:
            process = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError:
            raise ProviderError(401) from None
        try:
            stdout, stderr = process.communicate(prompt, timeout=settings.FACTORY_CODEX_TIMEOUT_SECONDS)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            # A lost answer may have consumed usage. Do not automatically replay.
            raise ProviderError(408) from None
        if process.returncode and not stdout:
            raise ProviderError(400)
        return parse_events(stdout)
