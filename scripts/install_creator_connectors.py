#!/usr/bin/env python3
"""Install isolated creator-portal browser services; sessions remain private."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.creator_connector import PLATFORMS, install_service
from services.factory_platform_policy import automation_enabled


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", nargs="+", choices=list(PLATFORMS), default=[p for p in PLATFORMS if automation_enabled(p)])
    args = parser.parse_args()
    for platform in args.platform:
        result = install_service(platform, start=True)
        print(f"Installed independent service: {result['service']}")
