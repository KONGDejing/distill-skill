#!/usr/bin/env python3
"""Install and manage this checkout's persistent user services (Linux/systemd)."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "short-video-ai-factory"
CREATOR_PLATFORMS = ("douyin", "kuaishou", "bilibili")
UNITS = [f"{PREFIX}-{role}.service" for role in
         ("api", "worker", "scheduler", "xiaohongshu", *(f"creator-{platform}" for platform in CREATOR_PLATFORMS))]


def run(*args, check=True):
    return subprocess.run(args, check=check)


def install(port):
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True, mode=0o700)
    runtime.chmod(0o700)
    network_file = runtime / "service-network.env"
    network = [key + "=" + json.dumps(os.environ[key]) for key in
               ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy") if os.environ.get(key)]
    if network or not network_file.exists():
        network_file.write_text("\n".join(network) + "\n")
        network_file.chmod(0o600)
    directory = Path.home() / ".config" / "systemd" / "user"
    directory.mkdir(parents=True, exist_ok=True)
    commands = {
        "api": f"{sys.executable} -m uvicorn main:app --host 0.0.0.0 --port {port}",
        "worker": f"{sys.executable} -m celery -A tasks.celery_app worker -l info -c 1 -Q short_video_ai_factory --hostname=factory@%h",
        "scheduler": f"{sys.executable} -m services.factory_scheduler",
        "xiaohongshu": f"{sys.executable} {ROOT / 'backend/services/xiaohongshu_connector.py'} --serve",
    }
    commands.update({f"creator-{platform}": f"{sys.executable} {ROOT / 'backend/services/creator_connector.py'} --serve --platform {platform}"
                     for platform in CREATOR_PLATFORMS})
    for role, command in commands.items():
        unit = directory / f"{PREFIX}-{role}.service"
        creator_environment = f"EnvironmentFile=-{runtime / 'creator-browser.env'}\n" if role.startswith("creator-") else ""
        unit.write_text(f"""[Unit]
Description=Short Video AI Factory / {role}
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-{network_file}
{creator_environment}WorkingDirectory={ROOT / 'backend'}
ExecStart={command}
Environment=PYTHONUNBUFFERED=1
Environment=OMP_NUM_THREADS=4
Environment=MKL_NUM_THREADS=4
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=mixed
UMask=0077
StandardOutput=append:{runtime}/{role}.log
StandardError=append:{runtime}/{role}.log

[Install]
WantedBy=default.target
""", encoding="utf-8")
    run("systemctl", "--user", "daemon-reload")
    print(f"Installed services for {ROOT}; API and web UI: http://localhost:{port}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "start", "stop", "restart", "status"])
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    if args.action == "install":
        install(args.port)
    elif args.action == "start":
        run("systemctl", "--user", "enable", "--now", *UNITS)
    elif args.action == "status":
        run("systemctl", "--user", "status", "--no-pager", *UNITS, check=False)
    else:
        run("systemctl", "--user", args.action, *UNITS)
