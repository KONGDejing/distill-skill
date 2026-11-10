from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services.factory_usage import item_usage, batch_usage, usage_context, summarize_usage


def test_nested_and_concurrent_items_do_not_leak_accounting_scope():
    @item_usage("editing")
    def broken(run_id, item_id):
        assert usage_context(run_id)["item_id"] == item_id
        raise ValueError("render failed")

    @item_usage("script")
    def work(run_id, item_id):
        with pytest.raises(ValueError):
            broken(run_id, "nested")
        assert usage_context("other-run")["usage_scope"] == "unattributed"
        return usage_context(run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda identifier: work("run", identifier), ["first", "second"]))
    assert [result["item_id"] for result in results] == ["first", "second"]
    assert all(result["production_stage"] == "script" for result in results)
    assert usage_context("run")["usage_scope"] == "unattributed"

    @batch_usage("planning")
    def planning(run_id):
        assert work(run_id, "video")["usage_scope"] == "item"
        return usage_context(run_id)
    assert planning("run")["usage_scope"] == "batch"
    assert usage_context("run")["usage_scope"] == "unattributed"


def test_totals_separate_legacy_shared_rework_and_missing_usage():
    def event(detail, stage="model_call"):
        return SimpleNamespace(stage=stage, detail=detail)
    events = [
        event({"input_tokens": 100, "output_tokens": 10}),
        event({"usage_scope": "batch", "input_tokens": 20, "output_tokens": 5, "cached_input_tokens": 10}),
        event({"usage_scope": "item", "item_id": "video", "work_kind": "initial", "production_stage": "script",
               "input_tokens": 200, "output_tokens": 40, "cached_input_tokens": 150}),
        event({"usage_scope": "item", "item_id": "video", "work_kind": "rework", "production_stage": "script",
               "input_tokens": 50, "output_tokens": 10, "cached_input_tokens": 30}),
        event({"usage_scope": "item", "item_id": "video", "usage_available": False}, "model_error"),
    ]
    result = summarize_usage(events, ["video", "unstarted"])
    assert result["total"]["total_tokens"] == 435
    assert result["total"]["cached_input_tokens"] == 190
    assert result["unattributed"]["total_tokens"] == 110
    assert result["shared"]["total_tokens"] == 25
    item = result["items"]["video"]
    assert item["initial_tokens"] == 240 and item["rework_tokens"] == 60
    assert item["stages"] == {"script": 300}
    assert item["failed_attempts_without_usage"] == 1 and item["calls"] == 2
    assert result["items"]["unstarted"]["calls"] == 0
    assert result["has_unattributed_history"] is True
