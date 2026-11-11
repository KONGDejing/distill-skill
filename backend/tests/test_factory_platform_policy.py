"""Manual-only platforms must not start commands, browsers, or polling."""
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.factory import FactoryAnalyticsSnapshot
from services import factory_analytics as analytics, factory_operations as operations
from services import xiaohongshu_connector as connector
from services.factory_platform_policy import AutomationDisabled, automation_enabled, automation_state, require_automation


def test_only_douyin_upload_is_authorized_and_analytics_stays_manual():
    from services.factory_platform_policy import analytics_enabled
    assert automation_enabled(" DOUYIN ")
    assert automation_state("douyin")["mode"] == "automated"
    require_automation("douyin")
    assert "AI生成" in automation_state("douyin")["reason"]
    assert not any(analytics_enabled(p) for p in ["douyin", "kuaishou", "xiaohongshu", "bilibili"])


@pytest.mark.parametrize("platform", ["xiaohongshu", "kuaishou", "bilibili", "future_platform"])
def test_policy_cannot_be_enabled_by_environment_or_cached_login(monkeypatch, platform):
    monkeypatch.setenv(platform.upper() + "_AUTOMATION_ENABLED", "true")
    assert not automation_enabled(platform)
    assert not automation_enabled(" " + platform.upper() + " ")
    assert automation_state(platform)["mode"] == "manual"
    with pytest.raises(AutomationDisabled, match="本人手动"):
        require_automation(platform)
    assert issubclass(AutomationDisabled, ValueError)


@pytest.mark.parametrize("action", ["login", "inspect", "prepare", "publish"])
def test_xhs_submit_blocks_before_payload_queue_or_service_start(monkeypatch, action):
    guards = {name: Mock(side_effect=AssertionError("Disabled automation reached a side effect"))
              for name in ("_payload", "_directories", "_write", "_ensure_daemon")}
    for name, mock in guards.items():
        monkeypatch.setattr(connector, name, mock)
    with pytest.raises(AutomationDisabled):
        connector.submit_command(action, {"item_id": "fixture-item"})
    for mock in guards.values():
        mock.assert_not_called()


def test_all_xhs_start_paths_stop_before_importing_or_launching_browser(monkeypatch):
    directories = Mock(side_effect=AssertionError("Must not create runtime state"))
    launch = Mock(side_effect=AssertionError("Must not launch a browser"))
    command = Mock(side_effect=AssertionError("Must not start a process or service"))
    monkeypatch.setattr(connector, "_directories", directories)
    monkeypatch.setattr(connector.subprocess, "run", command)
    monkeypatch.setattr(connector.subprocess, "Popen", command)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    for invoke in (connector._ensure_daemon, connector._serve,
                   lambda: connector._Browser(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)))):
        with pytest.raises(AutomationDisabled):
            invoke()
    directories.assert_not_called(); launch.assert_not_called(); command.assert_not_called()


def test_xhs_state_is_read_only_historical_cache_even_if_authenticated(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    cached = {"authenticated": True, "session_active": True, "display_name": "fixture-account",
              "updated_at": "2026-01-01T00:00:00+00:00", "prepared_item_id": "historical-item"}
    path.write_text(json.dumps(cached)); before = path.read_bytes()
    monkeypatch.setattr(connector, "STATE_FILE", path)
    alive = Mock(side_effect=AssertionError("Manual state must not inspect or start the daemon"))
    monkeypatch.setattr(connector, "_alive", alive)
    state = connector.connection_state()
    assert state["status"] == state["mode"] == "manual" and state["cache_only"]
    assert not state["authenticated"] and not state["session_active"]
    assert state["cached_authenticated"] and state["cached_updated_at"] == cached["updated_at"]
    assert state["display_name"] == "fixture-account" and not any(state["capabilities"].values())
    assert path.read_bytes() == before
    alive.assert_not_called()


def test_direct_script_entry_exits_cleanly_without_pythonpath(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(Path(connector.__file__).resolve()), "--serve"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "自动化已停用" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.fixture
def sessions():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_settings_enable_only_douyin_without_enabling_other_accounts(sessions):
    from routers.factory import SettingsUpdate, update_settings
    with sessions() as db:
        result = update_settings(SettingsUpdate(publish_enabled=True, manual_delivery_enabled=True), db)
        assert result['publish_enabled'] and result['manual_delivery_enabled']
        assert result['platform_automation']['douyin']['enabled']
        assert not result['auto_publish_xiaohongshu']
        assert all(not result['platform_automation'][p]['enabled'] for p in ['kuaishou', 'xiaohongshu'])


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu", "kuaishou", "bilibili"])
def test_manual_analytics_never_imports_browser_or_overwrites_historical_metrics(sessions, monkeypatch, platform):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with sessions() as db:
        old = FactoryAnalyticsSnapshot(platform=platform, status="connected", collected_at=datetime.utcnow()-timedelta(days=2),
            data={"status": "connected", "metrics": {"followers": 17, "views": 101}, "period": {"label": "historical fixture"}})
        db.add(old); db.commit()
        result = analytics.capture(platform)
        assert result["status"] == "manual" and "metrics" not in result
        assert analytics.save_snapshot(db, platform, result).id == old.id
        assert db.query(FactoryAnalyticsSnapshot).count() == 1
        view = next(row for row in analytics.overview(db)["platforms"] if row["platform"] == platform)
        assert view["status"] == "manual" and view["historical_only"]
        assert view["metrics"] == {"followers": 17, "views": 101}
        assert view["period"]["label"] == "historical fixture" and "自动采集已停止" in view["message"]
        assert view["collected_at"].startswith(old.collected_at.isoformat())


def test_operations_loop_skips_all_account_capture_and_snapshots(sessions, tmp_path, monkeypatch):
    class EndLoop(BaseException):
        pass
    monkeypatch.setattr(operations, "init_db", lambda: None)
    monkeypatch.setattr(operations, "SessionLocal", sessions)
    monkeypatch.setattr(operations, "PRIVATE", tmp_path)
    monkeypatch.setattr(operations, "patrol", lambda db: None)
    monkeypatch.setattr(operations, "memory_state", lambda: {"blocked": False})
    capture = Mock(return_value={"status": "connected"})
    save = Mock()
    monkeypatch.setattr(operations.factory_analytics, "capture", capture)
    monkeypatch.setattr(operations.factory_analytics, "save_snapshot", save)
    monkeypatch.setattr(operations.time, "sleep", lambda _: (_ for _ in ()).throw(EndLoop()))
    with pytest.raises(EndLoop):
        operations.serve()
    capture.assert_not_called()
    save.assert_not_called()


def load_factoryctl():
    path = Path(__file__).resolve().parents[2] / "scripts/factoryctl.py"
    spec = importlib.util.spec_from_file_location("factoryctl_policy_test", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("action", ["start", "restart", "stop", "status"])
def test_factoryctl_never_starts_accounts_but_can_stop_or_report_legacy_units(monkeypatch, action):
    control = load_factoryctl(); command = Mock(); monkeypatch.setattr(control, "run", command)
    control.main([action])
    args = command.call_args.args
    for unit in control.LEGACY_UNITS:
        assert (unit in args) is (action in {"stop", "status"})
    assert len(control.UNITS) == len(control.LEGACY_UNITS) == 4
    assert "short-video-ai-factory-api.service" in args


def test_factoryctl_install_does_not_create_or_replace_account_units(tmp_path, monkeypatch):
    control = load_factoryctl()
    root, home = tmp_path / "checkout", tmp_path / "test-user"
    root.mkdir(); directory = home / ".config/systemd/user"; directory.mkdir(parents=True)
    old_units = [directory / name for name in control.LEGACY_UNITS]
    for unit in old_units:
        unit.write_text("retained historical unit")
    monkeypatch.setattr(control, "ROOT", root)
    monkeypatch.setattr(control.Path, "home", lambda: home)
    command = Mock(); monkeypatch.setattr(control, "run", command)
    control.install(3000)
    assert all(unit.read_text() == "retained historical unit" for unit in old_units)
    assert len(list(directory.glob("*.service"))) == len(control.UNITS) + len(control.LEGACY_UNITS)
    command.assert_called_once_with("systemctl", "--user", "daemon-reload")
