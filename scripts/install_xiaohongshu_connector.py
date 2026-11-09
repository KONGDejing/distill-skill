#!/usr/bin/env python3
"""Install the browser connector in a service independent of API/worker cgroups."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = "short-video-ai-factory-xiaohongshu.service"


def main():
    directory = Path.home() / ".config" / "systemd" / "user"
    directory.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True, mode=0o700)
    unit = directory / NAME
    unit.write_text(f"""[Unit]
Description=Short Video AI Factory / Xiaohongshu browser connector
After=network-online.target

[Service]
Type=simple
EnvironmentFile=-{runtime / 'service-network.env'}
Environment=PYTHONUNBUFFERED=1
WorkingDirectory={ROOT / 'backend'}
ExecStart={sys.executable} {ROOT / 'backend/services/xiaohongshu_connector.py'} --serve
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=mixed
UMask=0077
StandardOutput=append:{runtime / 'xiaohongshu-connector-service.log'}
StandardError=append:{runtime / 'xiaohongshu-connector-service.log'}

[Install]
WantedBy=default.target
""", encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", NAME], check=True)
    print(f"Installed and started {NAME}")


if __name__ == "__main__":
    main()
