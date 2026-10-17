"""Browser diagnostics ingestion for live-call streaming."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.observability.content_free_diagnostics import sanitize_content_free_details
from app.platform.live_voice.release_gate import (
    LiveVoiceReleaseEvent,
    LiveVoiceReleaseGateReport,
    LiveVoiceReleaseThresholds,
    evaluate_live_voice_log,
    evaluate_live_voice_release_gate,
)
from app.platform.live_voice.diagnostics import (
    diagnostics_log_path,
    live_voice_log,
    normalize_trace_id,
)
from app.platform.live_voice.capacity import live_call_capacity_snapshot

_ROUTE_SENTINEL = "_omnix_live_voice_diagnostics_registered"
LIVE_VOICE_DIAGNOSTICS_PATH = "/api/tts/live-call/diagnostics"
_LOG_ENVELOPE_FIELDS = {
    "event",
    "monotonic_ms",
    "process_id",
    "sequence",
    "source",
    "thread_id",
    "thread_name",
    "timestamp_utc",
    "trace_id",
}


class LiveVoiceDiagnosticEvent(BaseModel):
    source: str = Field(default="browser", max_length=80)
    event: str = Field(min_length=1, max_length=160)
    details: dict[str, Any] = Field(default_factory=dict)


class LiveVoiceDiagnosticBatch(BaseModel):
    trace_id: str = Field(min_length=1, max_length=160)
    events: list[LiveVoiceDiagnosticEvent] = Field(min_length=1, max_length=200)


class LiveVoiceReleaseGateEvaluationRequest(BaseModel):
    events: list[LiveVoiceReleaseEvent] = Field(min_length=1, max_length=100_000)
    thresholds: LiveVoiceReleaseThresholds = Field(default_factory=LiveVoiceReleaseThresholds)


def register_live_voice_diagnostics_routes(router: APIRouter, state: Any) -> None:
    """Register live-call diagnostics ingestion once."""
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.post(LIVE_VOICE_DIAGNOSTICS_PATH)
    def ingest_live_voice_diagnostics(batch: LiveVoiceDiagnosticBatch) -> dict[str, Any]:
        trace_id = normalize_trace_id(batch.trace_id)
        for item in batch.events:
            details = sanitize_content_free_details(item.details)
            live_voice_log(
                trace_id,
                item.source,
                item.event,
                **{key: value for key, value in details.items() if key not in _LOG_ENVELOPE_FIELDS},
            )
        return {
            "accepted": len(batch.events),
            "trace_id": trace_id,
            "log_path": diagnostics_log_path(),
        }

    @router.get(f"{LIVE_VOICE_DIAGNOSTICS_PATH}/status")
    def live_voice_diagnostics_status() -> dict[str, Any]:
        return {
            "ready": True,
            "log_path": diagnostics_log_path(),
            "capacity": live_call_capacity_snapshot(),
        }

    @router.get(
        f"{LIVE_VOICE_DIAGNOSTICS_PATH}/release-gate",
        response_model=LiveVoiceReleaseGateReport,
    )
    def live_voice_release_gate(
        hours: int = Query(default=24, ge=1, le=24 * 30),
        minimum_latency_samples: int = Query(default=5, ge=1, le=10_000),
        minimum_quality_trials: int = Query(default=10, ge=1, le=10_000),
    ) -> LiveVoiceReleaseGateReport:
        thresholds = LiveVoiceReleaseThresholds(
            minimum_latency_samples=minimum_latency_samples,
            minimum_quality_trials=minimum_quality_trials,
        )
        return evaluate_live_voice_log(
            diagnostics_log_path(),
            hours=hours,
            thresholds=thresholds,
        )

    @router.post(
        f"{LIVE_VOICE_DIAGNOSTICS_PATH}/release-gate/evaluate",
        response_model=LiveVoiceReleaseGateReport,
    )
    def evaluate_live_voice_release_gate_payload(
        request: LiveVoiceReleaseGateEvaluationRequest,
    ) -> LiveVoiceReleaseGateReport:
        return evaluate_live_voice_release_gate(
            request.events,
            thresholds=request.thresholds,
        )
