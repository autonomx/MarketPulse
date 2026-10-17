"""Deterministic release-gate evaluation for live conversation diagnostics."""
from __future__ import annotations

import logging

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

LatencyMetric = Literal[
    "stt_finalize_ms",
    "final_to_first_token_ms",
    "first_token_to_first_audio_ms",
    "interruption_to_silence_ms",
]
QualityMetric = Literal[
    "false_interruption",
    "missed_interruption",
    "backchannel_false_positive",
]
GateStatus = Literal["pass", "fail", "insufficient"]

REQUIRED_LIVE_VOICE_SCENARIOS = (
    "system-normal",
    "character-normal",
    "memory-off",
    "private-call",
    "hard-stop",
    "correction-overlap",
    "question-overlap",
    "sustained-overlap",
    "backchannel",
    "assistant-echo",
    "background-noise",
    "provider-reconnect",
    "stt-failure",
    "tts-failure",
    "browser-reload",
    "rapid-interruption-soak",
)


class LiveVoiceReleaseThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_latency_samples: int = Field(default=5, ge=1, le=10_000)
    minimum_quality_trials: int = Field(default=10, ge=1, le=10_000)
    required_scenarios: tuple[str, ...] = REQUIRED_LIVE_VOICE_SCENARIOS
    stt_finalize_p95_ms: float = Field(default=1_500.0, gt=0)
    final_to_first_token_p95_ms: float = Field(default=5_000.0, gt=0)
    first_token_to_first_audio_p95_ms: float = Field(default=3_500.0, gt=0)
    interruption_to_silence_p95_ms: float = Field(default=500.0, gt=0)
    false_interruption_rate: float = Field(default=0.05, ge=0, le=1)
    missed_interruption_rate: float = Field(default=0.10, ge=0, le=1)
    backchannel_false_positive_rate: float = Field(default=0.05, ge=0, le=1)


class LiveVoiceReleaseEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    timestamp_utc: str | None = None
    trace_id: str = "live-call-unscoped"
    event: str
    metric_name: str | None = None
    value_ms: float | None = None
    quality_name: str | None = None
    occurred: bool | None = None
    scenario: str | None = None


class LiveVoiceMetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: GateStatus
    samples: int
    observed: float | None = None
    limit: float
    unit: Literal["ms", "rate"]


class LiveVoiceReleaseGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: GateStatus
    generated_at: str
    window_start: str | None = None
    records_scanned: int = 0
    traces: int = 0
    scenarios: list[str] = Field(default_factory=list)
    missing_scenarios: list[str] = Field(default_factory=list)
    metrics: list[LiveVoiceMetricResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    insufficient: list[str] = Field(default_factory=list)


_LATENCY_LIMIT_FIELDS: dict[LatencyMetric, str] = {
    "stt_finalize_ms": "stt_finalize_p95_ms",
    "final_to_first_token_ms": "final_to_first_token_p95_ms",
    "first_token_to_first_audio_ms": "first_token_to_first_audio_p95_ms",
    "interruption_to_silence_ms": "interruption_to_silence_p95_ms",
}
_QUALITY_LIMIT_FIELDS: dict[QualityMetric, str] = {
    "false_interruption": "false_interruption_rate",
    "missed_interruption": "missed_interruption_rate",
    "backchannel_false_positive": "backchannel_false_positive_rate",
}


def evaluate_live_voice_release_gate(
    events: Iterable[dict[str, Any] | LiveVoiceReleaseEvent],
    *,
    thresholds: LiveVoiceReleaseThresholds | None = None,
    window_start: datetime | None = None,
) -> LiveVoiceReleaseGateReport:
    limits = thresholds or LiveVoiceReleaseThresholds()
    latency: dict[str, list[float]] = defaultdict(list)
    quality: dict[str, list[bool]] = defaultdict(list)
    traces: set[str] = set()
    scenarios: set[str] = set()
    scanned = 0

    for raw in events:
        try:
            event = raw if isinstance(raw, LiveVoiceReleaseEvent) else LiveVoiceReleaseEvent.model_validate(raw)
        except Exception:
            logger.debug("suppressed error in %s", "evaluate_live_voice_release_gate", exc_info=True)
            continue
        if window_start is not None and event.timestamp_utc:
            parsed = _parse_datetime(event.timestamp_utc)
            if parsed is not None and parsed < window_start:
                continue
        scanned += 1
        traces.add(event.trace_id)
        if event.scenario:
            scenarios.add(event.scenario)
        if event.event == "release_metric" and event.metric_name in _LATENCY_LIMIT_FIELDS:
            if event.value_ms is not None and math.isfinite(event.value_ms) and event.value_ms >= 0:
                latency[event.metric_name].append(float(event.value_ms))
        if event.event == "release_quality" and event.quality_name in _QUALITY_LIMIT_FIELDS:
            if event.occurred is not None:
                quality[event.quality_name].append(bool(event.occurred))

    results: list[LiveVoiceMetricResult] = []
    failures: list[str] = []
    insufficient: list[str] = []
    missing_scenarios = sorted(set(limits.required_scenarios) - scenarios)
    if missing_scenarios:
        insufficient.append("missing scenarios: " + ", ".join(missing_scenarios))

    for name, field in _LATENCY_LIMIT_FIELDS.items():
        values = latency[name]
        limit = float(getattr(limits, field))
        if len(values) < limits.minimum_latency_samples:
            status: GateStatus = "insufficient"
            observed = _percentile(values, 0.95) if values else None
            insufficient.append(f"{name}: {len(values)}/{limits.minimum_latency_samples} samples")
        else:
            observed = _percentile(values, 0.95)
            status = "pass" if observed <= limit else "fail"
            if status == "fail":
                failures.append(f"{name} p95 {observed:.1f} ms exceeds {limit:.1f} ms")
        results.append(LiveVoiceMetricResult(
            name=name,
            status=status,
            samples=len(values),
            observed=observed,
            limit=limit,
            unit="ms",
        ))

    for name, field in _QUALITY_LIMIT_FIELDS.items():
        values = quality[name]
        limit = float(getattr(limits, field))
        if len(values) < limits.minimum_quality_trials:
            status = "insufficient"
            observed = (sum(values) / len(values)) if values else None
            insufficient.append(f"{name}: {len(values)}/{limits.minimum_quality_trials} trials")
        else:
            observed = sum(values) / len(values)
            status = "pass" if observed <= limit else "fail"
            if status == "fail":
                failures.append(f"{name} rate {observed:.3f} exceeds {limit:.3f}")
        results.append(LiveVoiceMetricResult(
            name=name,
            status=status,
            samples=len(values),
            observed=observed,
            limit=limit,
            unit="rate",
        ))

    overall: GateStatus = "fail" if failures else "insufficient" if insufficient else "pass"
    return LiveVoiceReleaseGateReport(
        status=overall,
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_start=window_start.isoformat() if window_start else None,
        records_scanned=scanned,
        traces=len(traces),
        scenarios=sorted(scenarios),
        missing_scenarios=missing_scenarios,
        metrics=results,
        failures=failures,
        insufficient=insufficient,
    )


def evaluate_live_voice_log(
    path: str | Path,
    *,
    hours: int = 24,
    thresholds: LiveVoiceReleaseThresholds | None = None,
    max_records: int = 50_000,
) -> LiveVoiceReleaseGateReport:
    log_path = Path(path)
    window_start = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 30)))
    records: list[dict[str, Any]] = []
    paths = [log_path, *[Path(f"{log_path}.{index}") for index in range(1, 5)]]
    for candidate in reversed(paths):
        if not candidate.exists():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if len(records) >= max_records:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        if len(records) >= max_records:
            break
    return evaluate_live_voice_release_gate(records, thresholds=thresholds, window_start=window_start)


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
