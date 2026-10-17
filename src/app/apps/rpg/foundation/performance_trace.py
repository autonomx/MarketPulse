"""End-to-end RPG request tracing with bounded structured stage metrics."""
from __future__ import annotations

import logging

from app.config.env import env_str

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter, process_time
from typing import Any, Iterator


from app.observability.tracing import annotate
from app.observability.tracing import span as tracing_span
from app.apps.rpg.foundation.debug_logging import log_rpg_event, new_rpg_trace_id

logger = logging.getLogger(__name__)

_WARNING_THRESHOLD_ENV = "OMNIX_RPG_SLOW_SPAN_MS"
_DEFAULT_WARNING_THRESHOLD_MS = 500.0
_ATTRIBUTION_TARGET_PERCENT = 95.0
_CURRENT_TRACE: ContextVar["RpgPipelineTrace | None"] = ContextVar("omnix_rpg_pipeline_trace", default=None)
_SPAN_DEPTH: ContextVar[int] = ContextVar("omnix_rpg_pipeline_span_depth", default=0)

_REPORTED_STAGE_NAMES = {
    "manual_turn_ms": "turn.manual_total",
    "pre_runtime_intent_llm_ms": "provider.intent_request",
    "provider_queue_ms": "provider.queue",
    "provider_ms": "provider.request",
    "provider_decode_ms": "provider.decode",
    "prompt_build_ms": "provider.prompt_build",
    "deterministic_runtime_apply_ms": "turn.runtime_resolution",
    "grounding_validation_ms": "dialogue.grounding_validation",
    "repair_ms": "dialogue.quality_repair",
    "state_snapshot_ms": "session.snapshot_write",
    "deferred_enqueue_ms": "narration.deferred_enqueue",
    "persistence_ms": "session.persistence",
    "serialization_ms": "turn.serialization",
}


@dataclass
class RpgPipelineTrace:
    trace_id: str
    operation: str
    session_id: str | None
    started_at: float = field(default_factory=perf_counter)
    cpu_started_ms: float = field(default_factory=lambda: process_time() * 1000.0)
    rss_started_bytes: int | None = field(default_factory=lambda: _rss_bytes())
    spans: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    reported_stages: dict[str, float] = field(default_factory=dict)

    @property
    def elapsed_ms(self) -> float:
        return round((perf_counter() - self.started_at) * 1000.0, 3)

    @property
    def measured_span_duration_ms(self) -> float:
        return round(sum(float(span.get("duration_ms") or 0.0) for span in self.spans), 3)

    @property
    def child_duration_ms(self) -> float:
        """Return non-overlapping top-level span time for backwards compatibility."""

        return round(
            sum(
                float(span.get("duration_ms") or 0.0)
                for span in self.spans
                if int(span.get("depth") or 0) == 0
            ),
            3,
        )

    def add_reported_stages(self, result: Any) -> None:
        for source in _result_sources(result):
            for timing_key in ("manual_turn_stage_timing", "stage_timing", "timing"):
                timing = source.get(timing_key)
                if not isinstance(timing, dict):
                    continue
                for key, value in timing.items():
                    if key not in _REPORTED_STAGE_NAMES or isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    self.reported_stages.setdefault(_REPORTED_STAGE_NAMES[key], round(float(value), 3))
        if isinstance(result, dict):
            self.fields["provider_called"] = _first_bool(_result_sources(result), "llm_called")
            self.fields["interaction_id"] = _first_text(_result_sources(result), "interaction_id") or None
            self.fields["turn_id"] = _first_text(_result_sources(result), "turn_id") or None

    def summary(self) -> dict[str, Any]:
        total_ms = self.elapsed_ms
        attributed_ms = min(total_ms, self.child_duration_ms)
        unattributed_ms = round(max(0.0, total_ms - attributed_ms), 3)
        attribution_percent = round((attributed_ms / total_ms) * 100.0, 2) if total_ms > 0 else 100.0
        cpu_ms = round(max(0.0, process_time() * 1000.0 - self.cpu_started_ms), 3)
        rss_end = _rss_bytes()
        rss_delta = None
        if self.rss_started_bytes is not None and rss_end is not None:
            rss_delta = rss_end - self.rss_started_bytes
        return {
            "operation": self.operation,
            "trace_id": self.trace_id,
            "total_ms": total_ms,
            "child_duration_ms": attributed_ms,
            "measured_span_duration_ms": self.measured_span_duration_ms,
            "unattributed_ms": unattributed_ms,
            "attribution_percent": attribution_percent,
            "attribution_target_percent": _ATTRIBUTION_TARGET_PERCENT,
            "attribution_target_met": attribution_percent >= _ATTRIBUTION_TARGET_PERCENT,
            "cpu_ms": cpu_ms,
            "rss_started_bytes": self.rss_started_bytes,
            "rss_finished_bytes": rss_end,
            "rss_delta_bytes": rss_delta,
            "span_count": len(self.spans),
            "spans": list(self.spans),
            "reported_stage_ms": dict(self.reported_stages),
            **self.fields,
        }

    def public_summary(self) -> dict[str, Any]:
        summary = self.summary()
        return {
            key: summary[key]
            for key in (
                "trace_id",
                "total_ms",
                "unattributed_ms",
                "attribution_percent",
                "attribution_target_met",
                "cpu_ms",
                "rss_delta_bytes",
                "reported_stage_ms",
            )
        }


def current_rpg_pipeline_trace() -> RpgPipelineTrace | None:
    return _CURRENT_TRACE.get()


def attach_rpg_result_timing(result: Any) -> None:
    trace = current_rpg_pipeline_trace()
    if trace is not None:
        trace.add_reported_stages(result)


@contextmanager
def rpg_pipeline_trace(
    operation: str,
    *,
    session_id: str | None = None,
    trace_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Iterator[RpgPipelineTrace]:
    """Measure an RPG request; with tracing on, it is also an OpenTelemetry span (WP-10.4)."""
    name = str(operation or "rpg.pipeline")
    with tracing_span(f"rpg.{name}", session_id=session_id) as otel_span, _measured_pipeline_trace(
        name, session_id=session_id, trace_id=trace_id, fields=fields,
    ) as trace:
        try:
            yield trace
        finally:
            annotate(otel_span, {"rpg_trace_id": trace.trace_id, **trace.fields})


@contextmanager
def _measured_pipeline_trace(
    operation: str,
    *,
    session_id: str | None = None,
    trace_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> Iterator[RpgPipelineTrace]:
    trace = RpgPipelineTrace(
        trace_id=trace_id or new_rpg_trace_id("pipeline"),
        operation=str(operation or "rpg.pipeline"),
        session_id=str(session_id) if session_id else None,
        fields=dict(fields or {}),
    )
    token = _CURRENT_TRACE.set(trace)
    depth_token = _SPAN_DEPTH.set(0)
    log_rpg_event(
        f"{trace.operation}.started",
        category="performance",
        session_id=trace.session_id,
        trace_id=trace.trace_id,
        fields=trace.fields,
    )
    # Startup logging is instrumentation overhead, not pipeline work. Start the
    # attribution window after emitting that lifecycle event so top-level spans
    # are compared against the operation they actually cover.
    trace.started_at = perf_counter()
    trace.cpu_started_ms = process_time() * 1000.0
    trace.rss_started_bytes = _rss_bytes()
    try:
        yield trace
    except Exception as exc:
        log_rpg_event(
            f"{trace.operation}.failed",
            category="performance",
            level="error",
            session_id=trace.session_id,
            trace_id=trace.trace_id,
            duration_ms=trace.elapsed_ms,
            fields=trace.summary(),
            error=exc,
            include_traceback=True,
        )
        raise
    else:
        log_rpg_event(
            f"{trace.operation}.completed",
            category="performance",
            session_id=trace.session_id,
            trace_id=trace.trace_id,
            duration_ms=trace.elapsed_ms,
            fields=trace.summary(),
        )
    finally:
        _SPAN_DEPTH.reset(depth_token)
        _CURRENT_TRACE.reset(token)


@contextmanager
def rpg_pipeline_span(
    name: str,
    *,
    fields: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Measure one stage; with tracing on, it is also a child OpenTelemetry span."""
    with tracing_span(f"rpg.{name}") as otel_span, _measured_pipeline_span(name, fields=fields) as stage:
        try:
            yield stage
        finally:
            annotate(otel_span, stage)


@contextmanager
def _measured_pipeline_span(
    name: str,
    *,
    fields: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    trace = current_rpg_pipeline_trace()
    started_at = perf_counter()
    cpu_started = process_time()
    rss_started = _rss_bytes()
    depth = _SPAN_DEPTH.get(0)
    depth_token = _SPAN_DEPTH.set(depth + 1)
    mutable_fields = dict(fields or {})
    try:
        yield mutable_fields
    except Exception as exc:
        duration_ms = (perf_counter() - started_at) * 1000.0
        _attach_span_resources(mutable_fields, cpu_started, rss_started)
        _record_span(trace, name, duration_ms, mutable_fields, failed=True, depth=depth)
        log_rpg_event(
            f"{name}.failed",
            category="performance",
            level="error",
            session_id=trace.session_id if trace else None,
            trace_id=trace.trace_id if trace else None,
            duration_ms=duration_ms,
            fields=mutable_fields,
            error=exc,
            include_traceback=True,
        )
        raise
    else:
        duration_ms = (perf_counter() - started_at) * 1000.0
        _attach_span_resources(mutable_fields, cpu_started, rss_started)
        slow = duration_ms >= slow_span_threshold_ms()
        _record_span(trace, name, duration_ms, mutable_fields, failed=False, depth=depth)
        # Within a trace, the trace's completion event lists every span; a span
        # gets its own line only when it is slow (WP-5.6: a turn has ~60 spans).
        if trace is None or slow:
            log_rpg_event(
                f"{name}.completed",
                category="performance",
                level="warning" if slow else "info",
                session_id=trace.session_id if trace else None,
                trace_id=trace.trace_id if trace else None,
                duration_ms=duration_ms,
                fields=mutable_fields,
            )
    finally:
        _SPAN_DEPTH.reset(depth_token)


@contextmanager
def rpg_pipeline_span_if_active(
    name: str,
    *,
    fields: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any] | None]:
    """Measure an internal operation only when a request trace is already active."""

    if current_rpg_pipeline_trace() is None:
        yield None
        return
    with rpg_pipeline_span(name, fields=fields) as span:
        yield span


def slow_span_threshold_ms() -> float:
    try:
        return max(0.0, float(env_str(_WARNING_THRESHOLD_ENV, str(_DEFAULT_WARNING_THRESHOLD_MS))))
    except (TypeError, ValueError):
        return _DEFAULT_WARNING_THRESHOLD_MS


def _record_span(
    trace: RpgPipelineTrace | None,
    name: str,
    duration_ms: float,
    fields: dict[str, Any],
    *,
    failed: bool,
    depth: int,
) -> None:
    if trace is None:
        return
    trace.spans.append(
        {
            "name": str(name),
            "duration_ms": round(float(duration_ms), 3),
            "depth": int(depth),
            "failed": bool(failed),
            **fields,
        }
    )


def _attach_span_resources(fields: dict[str, Any], cpu_started: float, rss_started: int | None) -> None:
    fields["cpu_ms"] = round(max(0.0, (process_time() - cpu_started) * 1000.0), 3)
    rss_finished = _rss_bytes()
    if rss_started is not None and rss_finished is not None:
        fields["rss_delta_bytes"] = rss_finished - rss_started


def _rss_bytes() -> int | None:
    if sys.platform != "win32":
        try:
            import resource

            rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return rss if sys.platform == "darwin" else rss * 1024
        except Exception:
            logger.debug("suppressed error in %s", "_rss_bytes", exc_info=True)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
        except Exception:
            logger.debug("suppressed error in %s", "_rss_bytes", exc_info=True)
            return None
    return None


def server_timing_header(spans: list[dict[str, Any]]) -> str:
    parts = []
    for index, span in enumerate(spans):
        if int(span.get("depth") or 0) != 0:
            continue
        name = "".join(character if character.isalnum() else "_" for character in str(span.get("name") or "span"))
        parts.append(f"rpg_{index}_{name};dur={float(span.get('duration_ms') or 0.0):.3f}")
    return ", ".join(parts)[:4_000]


def _result_sources(result: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(result, dict):
        return ()
    sources = [result]
    for key in ("result", "authoritative", "resolved_result"):
        value = result.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return tuple(sources)


def _first_text(sources: tuple[dict[str, Any], ...], key: str) -> str:
    for source in sources:
        value = source.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _first_bool(sources: tuple[dict[str, Any], ...], key: str) -> bool | None:
    for source in sources:
        value = source.get(key)
        if isinstance(value, bool):
            return value
    return None
