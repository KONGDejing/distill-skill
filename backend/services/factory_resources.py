"""Read Linux memory pressure without inspecting command arguments or secrets.

Admission checks stop new work at stage boundaries. They do not kill a running
encoder, close authenticated browsers, or stop unrelated processes.
"""
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config import settings

ROOT = Path(__file__).resolve().parents[2]
PROC = Path("/proc")
GIB = 1024 ** 3
_lock = threading.Lock()
_cached = None
_cached_at = 0


class MemoryPressure(RuntimeError):
    def __init__(self, snapshot):
        self.snapshot = snapshot
        super().__init__(snapshot["reason"])


def _sample():
    memory = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.split()[0]) * 1024
    total, available = memory["MemTotal"], memory["MemAvailable"]
    if total <= 0 or not 0 <= available <= total:
        raise ValueError("invalid memory counters")
    project, processes, unreadable = 0, 0, 0
    for process in PROC.iterdir():
        if not process.name.isdigit():
            continue
        try:
            if not (process / "cwd").resolve(strict=True).is_relative_to(ROOT):
                continue
        except (OSError, RuntimeError):
            continue
        try:
            pss = next(line for line in (process / "smaps_rollup").read_text().splitlines()
                       if line.startswith("Pss:"))
            project += int(pss.split()[1]) * 1024
            processes += 1
        except (OSError, ValueError, StopIteration):
            if process.exists():
                unreadable += 1
    stall = None
    try:
        line = next(line for line in (PROC / "pressure/memory").read_text().splitlines()
                    if line.startswith("full "))
        stall = float(dict(field.split("=", 1) for field in line.split()[1:])["avg10"])
    except (OSError, ValueError, KeyError, StopIteration):
        pass
    return {"total": total, "available": available, "project": project,
            "processes": processes, "unreadable_processes": unreadable, "stall": stall}


def memory_state(*, fresh=False):
    global _cached, _cached_at
    with _lock:
        now = time.monotonic()
        if not fresh and _cached is not None and now - _cached_at < 3:
            return dict(_cached)
        state = {"checked_at": datetime.now(timezone.utc).isoformat(),
                 "max_project_gib": settings.FACTORY_MAX_MEMORY_GIB,
                 "min_available_percent": settings.FACTORY_MIN_AVAILABLE_PERCENT,
                 "max_stall_percent": settings.FACTORY_MAX_MEMORY_STALL_PERCENT}
        try:
            sample = _sample()
            reserve = max(settings.FACTORY_MIN_AVAILABLE_GIB * GIB,
                          sample["total"] * settings.FACTORY_MIN_AVAILABLE_PERCENT / 100)
            reasons = []
            if sample["available"] < reserve:
                reasons.append(f"机器可用内存 {sample['available'] / GIB:.1f} GB，低于保留余量 {reserve / GIB:.1f} GB")
            if sample["project"] >= settings.FACTORY_MAX_MEMORY_GIB * GIB:
                reasons.append(f"工程内存 {sample['project'] / GIB:.1f} GB，达到 {settings.FACTORY_MAX_MEMORY_GIB:g} GB 上限")
            if sample["stall"] is not None and sample["stall"] >= settings.FACTORY_MAX_MEMORY_STALL_PERCENT:
                reasons.append("机器出现持续内存等待")
            if sample["unreadable_processes"]:
                reasons.append("部分工程进程内存暂时无法核验")
            state.update(status="high" if reasons else "normal", blocked=bool(reasons),
                         reason="；".join(reasons) if reasons else "内存余量充足",
                         project_gib=round(sample["project"] / GIB, 2),
                         available_gib=round(sample["available"] / GIB, 2),
                         total_gib=round(sample["total"] / GIB, 2),
                         min_available_gib=round(reserve / GIB, 2),
                         full_stall_avg10=sample["stall"], process_count=sample["processes"])
        except (OSError, ValueError, KeyError):
            state.update(status="unavailable", blocked=True, reason="暂时无法读取机器内存，停止启动新的生产任务")
        _cached, _cached_at = state, time.monotonic()
        return dict(state)


def ensure_memory_available():
    state = memory_state()
    if state["blocked"]:
        raise MemoryPressure(state)
    return state
