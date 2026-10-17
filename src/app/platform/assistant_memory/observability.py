"""Content-free aggregate observability for companion memory runtime behavior."""
from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_LOCK = threading.RLock()
_COUNTERS: Counter[str] = Counter()
_TOTALS: defaultdict[str, float] = defaultdict(float)
_MAXIMA: dict[str, float] = {}
_LATEST_USAGE: dict[str, "MemoryUsageResponse"] = {}
_MAX_METRIC_KEYS = 2048
_METRIC_TTL_SECONDS = 3600.0
_METRIC_TOUCHED: dict[str, float] = {}
_MAX_USAGE_SESSIONS = 1024
_USAGE_TTL_SECONDS = 3600.0
_USAGE_TOUCHED: dict[str, float] = {}
_ALLOWED_REASON_FIELDS = {
    "action",
    "reason",
    "disabled_reason",
}


class CompanionMemoryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    turns: int = 0
    counters: dict[str, int] = Field(default_factory=dict)
    totals: dict[str, float] = Field(default_factory=dict)
    maxima: dict[str, float] = Field(default_factory=dict)
    diagnostics_policy: str = "content_free"


class MemoryUsageItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    selection_reason: str
    activation_score: int
    section: str
    source_revision: int = Field(ge=1)


class MemoryUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    recorded_at: str
    items: tuple[MemoryUsageItem, ...] = ()
    diagnostics_policy: str = "content_free"


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _metrics_now() -> float:
    return time.monotonic()


def _prune_metric_keys_locked(now: float) -> None:
    for key, touched_at in list(_METRIC_TOUCHED.items()):
        if now - touched_at > _METRIC_TTL_SECONDS:
            _METRIC_TOUCHED.pop(key, None)
            _COUNTERS.pop(key, None)
            _TOTALS.pop(key, None)
            _MAXIMA.pop(key, None)


def _touch_metric_locked(key: str, now: float) -> None:
    _prune_metric_keys_locked(now)
    if key not in _METRIC_TOUCHED and len(_METRIC_TOUCHED) >= _MAX_METRIC_KEYS:
        oldest = min(_METRIC_TOUCHED, key=_METRIC_TOUCHED.__getitem__)
        _METRIC_TOUCHED.pop(oldest, None)
        _COUNTERS.pop(oldest, None)
        _TOTALS.pop(oldest, None)
        _MAXIMA.pop(oldest, None)
    _METRIC_TOUCHED[key] = now


def _prune_usage_locked(now: float) -> None:
    for session_id, touched_at in list(_USAGE_TOUCHED.items()):
        if now - touched_at > _USAGE_TTL_SECONDS:
            _USAGE_TOUCHED.pop(session_id, None)
            _LATEST_USAGE.pop(session_id, None)


def _count_reason(prefix: str, payload: dict[str, Any]) -> None:
    for field in _ALLOWED_REASON_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value and len(value) <= 80:
            metric = f"{prefix}.{field}.{value}"
            _touch_metric_locked(metric, _metrics_now())
            _COUNTERS[metric] += 1


def _record_section(prefix: str, payload: object) -> None:
    if not isinstance(payload, dict):
        return
    _count_reason(prefix, payload)
    for key in (
        "candidate_count",
        "selected_count",
        "excluded_count",
        "signal_count",
        "record_count",
        "token_estimate",
        "packet_tokens",
        "preload_ms",
        "rank_ms",
        "build_ms",
        "packet_build_ms",
        "deadline_ms",
    ):
        numeric = _number(payload.get(key))
        if numeric is None:
            continue
        metric = f"{prefix}.{key}"
        now = _metrics_now()
        _touch_metric_locked(metric, now)
        _TOTALS[metric] += numeric
        _MAXIMA[metric] = max(_MAXIMA.get(metric, numeric), numeric)
    for key in (
        "cache_hit",
        "preload_cache_hit",
        "preload_timed_out",
        "truncated",
        "proactive",
        "private_mode",
        "durable_candidate_created",
    ):
        value = payload.get(key)
        if isinstance(value, bool):
            metric = f"{prefix}.{key}.{str(value).lower()}"
            _touch_metric_locked(metric, _metrics_now())
            _COUNTERS[metric] += 1


def record_companion_diagnostics(diagnostics: dict[str, Any]) -> None:
    """Aggregate only an allowlisted numeric/boolean/reason projection."""

    with _LOCK:
        _touch_metric_locked("turns", _metrics_now())
        _COUNTERS["turns"] += 1
        for section in (
            "companion_context",
            "temporal_retrieval",
            "initiative",
            "paralinguistic_state",
            "rollout",
        ):
            _record_section(section, diagnostics.get(section))


def record_memory_usage(session_id: str, items: list[dict[str, object]]) -> None:
    """Store the latest content-free selection explanation for one Chat."""

    validated = tuple(MemoryUsageItem.model_validate(item) for item in items[:50])
    with _LOCK:
        now = _metrics_now()
        _prune_usage_locked(now)
        if session_id not in _LATEST_USAGE and len(_LATEST_USAGE) >= _MAX_USAGE_SESSIONS:
            oldest = min(_USAGE_TOUCHED, key=_USAGE_TOUCHED.__getitem__)
            _USAGE_TOUCHED.pop(oldest, None)
            _LATEST_USAGE.pop(oldest, None)
        _LATEST_USAGE[session_id] = MemoryUsageResponse(
            session_id=session_id,
            recorded_at=datetime.now(timezone.utc).isoformat(),
            items=validated,
        )
        _USAGE_TOUCHED[session_id] = now


def memory_usage_snapshot(session_id: str) -> MemoryUsageResponse:
    with _LOCK:
        now = _metrics_now()
        _prune_usage_locked(now)
        current = _LATEST_USAGE.get(session_id)
        if current is not None:
            _USAGE_TOUCHED[session_id] = now
            return current
    return MemoryUsageResponse(
        session_id=session_id,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )


def companion_metrics_snapshot() -> CompanionMemoryMetrics:
    with _LOCK:
        _prune_metric_keys_locked(_metrics_now())
        return CompanionMemoryMetrics(
            turns=int(_COUNTERS.get("turns", 0)),
            counters={
                key: int(value)
                for key, value in sorted(_COUNTERS.items())
                if key != "turns"
            },
            totals={key: round(float(value), 3) for key, value in sorted(_TOTALS.items())},
            maxima={key: round(float(value), 3) for key, value in sorted(_MAXIMA.items())},
        )


def reset_companion_metrics() -> None:
    with _LOCK:
        _COUNTERS.clear()
        _TOTALS.clear()
        _MAXIMA.clear()
        _LATEST_USAGE.clear()
        _METRIC_TOUCHED.clear()
        _USAGE_TOUCHED.clear()


__all__ = [
    "CompanionMemoryMetrics",
    "MemoryUsageItem",
    "MemoryUsageResponse",
    "companion_metrics_snapshot",
    "memory_usage_snapshot",
    "record_companion_diagnostics",
    "record_memory_usage",
    "reset_companion_metrics",
]
