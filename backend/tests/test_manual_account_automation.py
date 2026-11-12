"""Manual package delivery never touches account browsers or durable queues."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services import creator_connector as connector, creator_verification as verification
from services.factory_platform_policy import AutomationDisabled

PLATFORMS = ("bilibili",)


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("action", ["login", "inspect", "prepare", "publish"])
def test_submit_blocked_before_payload_files_or_start(monkeypatch, platform, action):
    forbidden = Mock(side_effect=AssertionError("Disabled automation reached a side effect"))
    for name in ("_payload", "_write", "_directories", "_ensure_daemon", "_enqueue_command"):
        monkeypatch.setattr(connector, name, forbidden)
    with pytest.raises(AutomationDisabled):
        connector.submit_command(platform, action, {"item_id": "fixture-item"})
    forbidden.assert_not_called()


@pytest.mark.parametrize("platform", PLATFORMS)
def test_all_direct_start_and_queue_paths_block_before_import_or_process(monkeypatch, platform):
    forbidden = Mock(side_effect=AssertionError("Disabled automation reached a side effect"))
    for name in ("_directories", "_write"):
        monkeypatch.setattr(connector, name, forbidden)
    monkeypatch.setattr(connector.subprocess, "run", forbidden)
    monkeypatch.setattr(connector.subprocess, "Popen", forbidden)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    browser = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=forbidden))
    for invoke in (lambda: connector.install_service(platform, start=True),
                   lambda: connector._ensure_daemon(platform), lambda: connector._serve(platform),
                   lambda: connector._Browser(platform, browser),
                   lambda: connector._enqueue_command(platform, "login", {})):
        with pytest.raises(AutomationDisabled):
            invoke()
    forbidden.assert_not_called()


@pytest.mark.parametrize("platform", PLATFORMS)
def test_state_and_receipts_remain_local_read_only_history(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(connector, "RUNTIME", tmp_path)
    directory = tmp_path / platform
    (directory / "results").mkdir(parents=True)
    (directory / "profile").mkdir()
    identifier = "44065963-9bb7-4e3d-bc3b-8434d68c61d8"
    history = {"status": "failed", "submission_uncertain": True, "submission_attempted": True}
    (directory / "results" / (identifier + ".json")).write_text(json.dumps(history))
    (directory / "state.json").write_text(json.dumps({"authenticated": True, "updated_at": "2026-01-01"}))
    (directory / "submissions.json").write_text(json.dumps({"fixture-item": {"status": "attempting"}}))
    (directory / "profile" / "Cookies").write_bytes(b"fixture-profile-unchanged")
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    forbidden = Mock(side_effect=AssertionError("Manual cache read must not inspect or start services"))
    monkeypatch.setattr(connector, "_alive", forbidden)
    monkeypatch.setattr(connector, "_latest_login", forbidden)
    state = connector.connection_state(platform)
    assert state["automation"]["enabled"] is False and state["status"] == "manual"
    assert state["cache_only"] and state["cached_authenticated"]
    assert state["cached_updated_at"] == "2026-01-01"
    assert not state["authenticated"] and not any(state["capabilities"].values())
    assert connector.command_result(platform, identifier) == history
    assert {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()} == before
    forbidden.assert_not_called()


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("action", ["--serve", "--install"])
def test_direct_script_paths_exit_cleanly_without_pythonpath(tmp_path, platform, action):
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(Path(connector.__file__).resolve()), "--platform", platform, action],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "自动化已停用" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("platform", (*PLATFORMS, "xiaohongshu"))
def test_identity_token_capture_and_input_blocked_before_files_or_browser(monkeypatch, platform):
    forbidden = Mock(side_effect=AssertionError("Manual mode must not reach the browser or token files"))
    monkeypatch.setattr(connector, "_directories", forbidden)
    monkeypatch.setattr(connector, "_read", forbidden)
    monkeypatch.setattr(connector, "_write", forbidden)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with ExitStack() as stack:
        for invoke in (lambda: verification._directory(platform), lambda: verification.page_token(platform),
                       lambda: verification.authorize(platform, "fixture", None, "http://factory.test"),
                       lambda: verification.frame(platform),
                       lambda: verification.interact(platform, "click", "fixture", x=.5, y=.5),
                       lambda: verification.interact(platform, "fill_code", "fixture", code="123456"),
                       lambda: stack.enter_context(verification._official_page(platform))):
            with pytest.raises(AutomationDisabled):
                invoke()
    forbidden.assert_not_called()
