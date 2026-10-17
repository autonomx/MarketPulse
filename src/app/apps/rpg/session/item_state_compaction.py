"""Deterministic item-state compaction helpers for long RPG runs."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_state_compaction_v1"
DEFAULT_BUCKET_LIMIT = 50
COMPACTION_TRACE_LIMIT = 20
ITEM_TRACE_LIMIT = 50
TRACE_BUCKETS = (
    "item_traces",
    "inventory_traces",
    "item_use_traces",
    "salvage_traces",
    "crafting_traces",
    "pickup_traces",
    "modification_traces",
    "market_traces",
    "item_combat_traces",
    "item_effect_traces",
    "item_report_sections",
    "item_report_session_traces",
    "item_command_traces",
    "item_scenario_traces",
    "recipe_discovery_traces",
    "item_state_audit_traces",
)


def _limit(value: Any, fallback: int = DEFAULT_BUCKET_LIMIT) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return fallback


def _compact_list(entries: list[Any], limit: int) -> tuple[list[Any], int]:
    if limit <= 0:
        return [], len(entries)
    if len(entries) <= limit:
        return deepcopy(entries), 0
    return deepcopy(entries[:limit]), len(entries) - limit


def _bucket_summary(name: str, before: int, after: int, dropped: int) -> dict[str, Any]:
    return {
        "bucket": name,
        "before": before,
        "after": after,
        "dropped": dropped,
        "changed": dropped > 0,
    }


def build_item_state_compaction(
    state: dict[str, Any],
    *,
    bucket_limit: int = DEFAULT_BUCKET_LIMIT,
    include_unchanged: bool = False,
) -> dict[str, Any]:
    """Return a deterministic compaction plan for item mechanics buckets."""
    mechanics = _safe_dict(_safe_dict(state).get("mechanics"))
    limit = _limit(bucket_limit)
    summaries: list[dict[str, Any]] = []
    invalid_buckets: list[dict[str, Any]] = []
    total_before = 0
    total_after = 0
    total_dropped = 0

    for bucket in TRACE_BUCKETS:
        raw_entries = mechanics.get(bucket)
        if raw_entries is None:
            before = after = dropped = 0
        elif not isinstance(raw_entries, list):
            invalid_buckets.append(
                {
                    "bucket": bucket,
                    "reason": "not_list",
                    "value_type": type(raw_entries).__name__,
                }
            )
            before = after = dropped = 0
        else:
            before = len(raw_entries)
            after = min(before, limit)
            dropped = max(0, before - after)
        total_before += before
        total_after += after
        total_dropped += dropped
        if include_unchanged or dropped > 0:
            summaries.append(_bucket_summary(bucket, before, after, dropped))

    return {
        "ok": not invalid_buckets,
        "changed": total_dropped > 0,
        "bucket_limit": limit,
        "buckets": summaries,
        "invalid_buckets": invalid_buckets,
        "summary": {
            "bucket_count": len(TRACE_BUCKETS),
            "total_before": total_before,
            "total_after": total_after,
            "total_dropped": total_dropped,
            "changed_buckets": sum(1 for summary in summaries if summary.get("changed") is True),
            "invalid_buckets": len(invalid_buckets),
        },
        "mechanics_source": MECHANICS_SOURCE,
    }


def apply_item_state_compaction(
    state: dict[str, Any],
    *,
    bucket_limit: int = DEFAULT_BUCKET_LIMIT,
    record_trace: bool = True,
) -> dict[str, Any]:
    """Compact item mechanics buckets in-place and return a traceable result."""
    mutable_state = state if isinstance(state, dict) else {}
    mechanics = _safe_dict(mutable_state.get("mechanics"))
    limit = _limit(bucket_limit)
    bucket_results: list[dict[str, Any]] = []
    invalid_buckets: list[dict[str, Any]] = []

    for bucket in TRACE_BUCKETS:
        raw_entries = mechanics.get(bucket)
        if raw_entries is None:
            bucket_results.append(_bucket_summary(bucket, 0, 0, 0))
            continue
        if not isinstance(raw_entries, list):
            invalid_buckets.append(
                {
                    "bucket": bucket,
                    "reason": "not_list",
                    "value_type": type(raw_entries).__name__,
                }
            )
            bucket_results.append(_bucket_summary(bucket, 0, 0, 0))
            continue
        compacted, dropped = _compact_list(raw_entries, limit)
        mechanics[bucket] = compacted
        bucket_results.append(_bucket_summary(bucket, len(raw_entries), len(compacted), dropped))

    changed_buckets = [result for result in bucket_results if result.get("changed") is True]
    total_before = sum(int(result.get("before") or 0) for result in bucket_results)
    total_after = sum(int(result.get("after") or 0) for result in bucket_results)
    total_dropped = sum(int(result.get("dropped") or 0) for result in bucket_results)
    trace = {
        "event": "item_state_compacted",
        "bucket_limit": limit,
        "changed": total_dropped > 0,
        "changed_buckets": deepcopy(changed_buckets),
        "total_before": total_before,
        "total_after": total_after,
        "total_dropped": total_dropped,
        "invalid_buckets": deepcopy(invalid_buckets),
        "mechanics_source": MECHANICS_SOURCE,
    }
    if record_trace:
        mechanics["item_state_compaction_traces"] = [
            deepcopy(trace),
            *_safe_list(mechanics.get("item_state_compaction_traces")),
        ][:COMPACTION_TRACE_LIMIT]
        mechanics["item_traces"] = [deepcopy(trace), *_safe_list(mechanics.get("item_traces"))][:ITEM_TRACE_LIMIT]
    mutable_state["mechanics"] = mechanics
    return {
        "ok": not invalid_buckets,
        "changed": total_dropped > 0,
        "bucket_limit": limit,
        "buckets": bucket_results,
        "invalid_buckets": invalid_buckets,
        "summary": {
            "bucket_count": len(TRACE_BUCKETS),
            "total_before": total_before,
            "total_after": total_after,
            "total_dropped": total_dropped,
            "changed_buckets": len(changed_buckets),
            "invalid_buckets": len(invalid_buckets),
        },
        "trace": trace,
        "mechanics_source": MECHANICS_SOURCE,
    }
