"""Memory thresholds and exact process attribution without allocating RAM."""
from pathlib import Path

import pytest
from services import factory_resources as resources


@pytest.fixture
def normal(monkeypatch):
    monkeypatch.setattr(resources, "_cached", None)
    for key, value in {"FACTORY_MAX_MEMORY_GIB": 8, "FACTORY_MIN_AVAILABLE_GIB": 2,
                       "FACTORY_MIN_AVAILABLE_PERCENT": 8, "FACTORY_MAX_MEMORY_STALL_PERCENT": 5}.items():
        monkeypatch.setattr(resources.settings, key, value)
    sample = {"total": 100 * resources.GIB, "available": 25 * resources.GIB,
              "project": 2 * resources.GIB, "processes": 7, "unreadable_processes": 0, "stall": 0}
    monkeypatch.setattr(resources, "_sample", lambda: sample)
    return sample


@pytest.mark.parametrize("changes,reason", [
    ({"project": 8 * resources.GIB}, "工程内存"),
    ({"available": 7 * resources.GIB}, "可用内存"),
    ({"total": 16 * resources.GIB, "available": 1.9 * resources.GIB}, "保留余量 2.0"),
    ({"stall": 5.1}, "内存等待"),
    ({"unreadable_processes": 1}, "无法核验"),
])
def test_independent_thresholds_stop_work_without_allocating_memory(normal, changes, reason):
    normal.update(changes)
    state = resources.memory_state(fresh=True)
    assert state["blocked"] and reason in state["reason"]
    with pytest.raises(resources.MemoryPressure, match=reason):
        resources.ensure_memory_available()


def test_healthy_sample_and_unavailable_metrics(normal, monkeypatch):
    state = resources.memory_state(fresh=True)
    assert not state["blocked"] and state["project_gib"] == 2
    def fail():
        raise OSError("unavailable")
    monkeypatch.setattr(resources, "_sample", fail)
    state = resources.memory_state(fresh=True)
    assert state["blocked"] and state["status"] == "unavailable"
    assert "unavailable" not in state["reason"]  # No raw exception details.


def test_cache_is_bounded_and_does_not_expose_a_mutable_internal_result(normal, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(resources.time, "monotonic", lambda: clock[0])
    state = resources.memory_state()
    state["blocked"] = True
    normal["project"] = 10 * resources.GIB
    assert not resources.memory_state()["blocked"]
    clock[0] += 3.1
    assert resources.memory_state()["blocked"]


def test_project_pss_excludes_similarly_named_projects_and_shared_rss(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    root = tmp_path / "factory"
    (root / "backend").mkdir(parents=True)
    outside = tmp_path / "factory-other"
    outside.mkdir()
    (proc / "meminfo").write_text("MemTotal: 100000 kB\nMemAvailable: 40000 kB\n")
    (proc / "pressure").mkdir()
    (proc / "pressure/memory").write_text("some avg10=0.5 avg60=0.1 avg300=0.1 total=1\nfull avg10=0.2 avg60=0 avg300=0 total=1\n")
    for pid, cwd, pss in (("1", root, 100), ("2", root / "backend", 200), ("3", outside, 900)):
        directory = proc / pid
        directory.mkdir()
        (directory / "cwd").symlink_to(cwd, target_is_directory=True)
        (directory / "smaps_rollup").write_text(f"Rss: 5000 kB\nPss: {pss} kB\n")
    monkeypatch.setattr(resources, "PROC", proc)
    monkeypatch.setattr(resources, "ROOT", root)
    sample = resources._sample()
    assert sample["project"] == 300 * 1024
    assert sample["processes"] == 2 and sample["stall"] == 0.2


@pytest.mark.parametrize('state,rss,expected,unreadable', [('S',400,400,0),('Z',None,0,0),('S',None,0,1)])
def test_missing_smaps_counts_live_rss_or_blocks_unverifiable_process(tmp_path,monkeypatch,state,rss,expected,unreadable):
    proc=tmp_path/'proc';proc.mkdir()
    root=tmp_path/'factory';root.mkdir()
    (proc/'meminfo').write_text('MemTotal: 100000 kB\nMemAvailable: 40000 kB\n')
    process=proc/'20';process.mkdir()
    (process/'cwd').symlink_to(root,target_is_directory=True)
    (process/'stat').write_text(f'20 (encoder) {state} 1')
    (process/'status').write_text(f'State:\t{state} (state)\n'+(f'VmRSS:\t{rss} kB\n' if rss is not None else ''))
    monkeypatch.setattr(resources,'PROC',proc);monkeypatch.setattr(resources,'ROOT',root)
    sample=resources._sample()
    assert sample['project']==expected*1024
    assert sample['unreadable_processes']==unreadable
    assert sample['rss_fallback_processes']==(1 if rss is not None else 0)


def test_transient_unreadable_process_is_confirmed_before_pausing(normal,monkeypatch):
    first={**normal,'unreadable_processes':1}
    samples=iter([first,normal])
    monkeypatch.setattr(resources,'_sample',lambda:next(samples))
    monkeypatch.setattr(resources.time,'sleep',lambda seconds:None)
    assert resources.ensure_memory_available()['blocked'] is False
