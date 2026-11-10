"""Attribute model usage without guessing ownership of historical calls."""
from contextvars import ContextVar
from functools import wraps

_scope = ContextVar("factory_item_usage", default=None)


def item_usage(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(run_id, item_id, *args, **kwargs):
            token = _scope.set({"run_id": run_id, "usage_scope": "item", "item_id": item_id, "production_stage": stage})
            try:
                return function(run_id, item_id, *args, **kwargs)
            finally:
                _scope.reset(token)
        return wrapped
    return decorate


def batch_usage(stage):
    def decorate(function):
        @wraps(function)
        def wrapped(run_id, *args, **kwargs):
            token = _scope.set({"run_id": run_id, "usage_scope": "batch", "item_id": None, "production_stage": stage})
            try:
                return function(run_id, *args, **kwargs)
            finally:
                _scope.reset(token)
        return wrapped
    return decorate


def usage_context(run_id):
    scope = _scope.get()
    if scope and scope["run_id"] == run_id:
        return {"usage_scope": scope["usage_scope"], "item_id": scope["item_id"],
                "production_stage": scope["production_stage"]}
    return {"usage_scope": "unattributed", "item_id": None, "production_stage": "unknown"}


def _empty():
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cached_input_tokens": 0, "cache_unknown_calls": 0,
            "failed_attempts_without_usage": 0, "initial_tokens": 0,
            "rework_tokens": 0, "unclassified_tokens": 0, "stages": {}}


def summarize_usage(events, item_ids):
    result = {"items": {identifier: _empty() for identifier in item_ids},
              "shared": _empty(), "unattributed": _empty(), "total": _empty()}
    for event in events:
        if event.stage not in {"model_call", "model_error"}:
            continue
        detail = event.detail or {}
        identifier = detail.get("item_id")
        if identifier in result["items"]:
            bucket = result["items"][identifier]
        elif detail.get("usage_scope") == "batch" and not identifier:
            bucket = result["shared"]
        else:
            bucket = result["unattributed"]
        for target in (bucket, result["total"]):
            if event.stage == "model_error":
                target["failed_attempts_without_usage"] += 1
                continue
            incoming, outgoing = int(detail.get("input_tokens") or 0), int(detail.get("output_tokens") or 0)
            total = incoming + outgoing
            target["calls"] += 1
            target["input_tokens"] += incoming
            target["output_tokens"] += outgoing
            target["total_tokens"] += total
            cached = detail.get("cached_input_tokens")
            if cached is None:
                target["cache_unknown_calls"] += 1
            else:
                # Cached input is a subset, never added to the token total.
                target["cached_input_tokens"] += int(cached)
            kind = detail.get("work_kind", "unclassified")
            target[(kind if kind in {"initial", "rework"} else "unclassified") + "_tokens"] += total
            stage = detail.get("production_stage", "unknown")
            target["stages"][stage] = target["stages"].get(stage, 0) + total
    result["has_unattributed_history"] = bool(result["unattributed"]["calls"])
    return result
