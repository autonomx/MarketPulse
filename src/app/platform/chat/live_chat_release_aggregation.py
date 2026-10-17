"""Convert durable Voice Session summaries into reproducible release-gate evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.platform.chat.evaluation_store import VoiceSessionEvaluationRecord
from .live_chat_release_gate import (
    LiveChatEvidenceBundle,
    LiveChatEvidenceEvent,
    LiveChatEvidenceMetadata,
    LiveChatMetricResult,
    LiveChatReleaseGateReport,
    LiveChatReleaseThresholds,
    evaluate_live_chat_release_gate_bundles,
)

_LATENCY_KEYS = {
    "stt_finalize_p95_ms": "stt_finalize_ms",
    "final_to_first_token_p95_ms": "final_to_first_token_ms",
    "first_token_to_first_audio_p95_ms": "first_token_to_first_audio_ms",
    "stt_request_to_first_playback_p95_ms": "stt_request_to_first_playback_ms",
    "interruption_to_silence_p95_ms": "interruption_to_silence_ms",
    "cancellation_p95_ms": "duck_to_cancel_ms",
    "rejected_candidate_restore_p95_ms": "rejected_candidate_restore_ms",
}
_QUALITY_KEYS = {
    "false_barge_in_rate": "false_barge_in_rate",
    "missed_barge_in_rate": "missed_barge_in_rate",
    "playback_echo_submission_rate": "playback_echo_submission_rate",
    "proactive_acceptance_rate": "proactive_prompt_acceptance_rate",
    "silence_fill_regret_rate": "proactive_prompt_regret_rate",
    "backchannel_collision_rate": "listener_backchannel_collision_rate",
    "repair_success_rate": "conversation_repair_success_rate",
    "repeated_topic_rate": "repeated_proactive_topic_rate",
    "unanswered_obligation_rate": "unanswered_obligation_rate",
    "perceived_listening_score": "perceived_listening_score",
    "perceived_pressure_score": "perceived_pressure_score",
}


def durable_record_to_bundle(record: VoiceSessionEvaluationRecord) -> LiveChatEvidenceBundle:
    metadata = LiveChatEvidenceMetadata(
        exact_commit_sha=record.exact_commit_sha,
        browser_version=record.browser_version,
        os_version=record.os_version,
        input_device_hash=record.input_device_hash or "unavailable",
        output_device_hash=record.output_device_hash or "unavailable",
        character_id=record.character_id,
        profile_version=record.profile_version or 1,
        presence_preset=record.presence_preset,
        configured_duplex_mode=record.configured_duplex_mode,
        resolved_duplex_mode=record.resolved_duplex_mode,
        calibration_version=record.calibration_version,
    )
    primary_scenario = record.scenario_labels[0] if record.scenario_labels else "unlabeled"
    events: list[LiveChatEvidenceEvent] = [
        _event(record, scenario, "scenario_coverage", 1.0)
        for scenario in record.scenario_labels
    ]
    for source, target in _LATENCY_KEYS.items():
        value = record.latency_summary.get(source)
        if isinstance(value, (int, float)):
            events.append(_event(record, primary_scenario, target, float(value)))
    for source, target in _QUALITY_KEYS.items():
        value = record.quality_metrics.get(source)
        if isinstance(value, (int, float)):
            events.append(_event(record, primary_scenario, target, float(value)))

    eos_total = sum(record.eos_termination_counts.values())
    if eos_total:
        events.extend([
            _event(
                record,
                primary_scenario,
                "natural_eos_rate",
                record.eos_termination_counts.get("natural_eos", 0) / eos_total,
            ),
            _event(
                record,
                primary_scenario,
                "forced_eos_rate",
                record.eos_termination_counts.get("forced_eos", 0) / eos_total,
            ),
            _event(
                record,
                primary_scenario,
                "token_limit_rate",
                record.eos_termination_counts.get("token_limit", 0) / eos_total,
            ),
        ])
    if not events:
        events.append(_event(record, primary_scenario, "scenario_coverage", 1.0))
    return LiveChatEvidenceBundle(metadata=metadata, events=events)


def evaluate_durable_live_chat_records(
    records: Iterable[VoiceSessionEvaluationRecord],
    *,
    thresholds: LiveChatReleaseThresholds | None = None,
) -> LiveChatReleaseGateReport:
    materialized = list(records)
    if not materialized:
        return empty_durable_release_report(thresholds=thresholds)
    return evaluate_live_chat_release_gate_bundles(
        [durable_record_to_bundle(record) for record in materialized],
        thresholds=thresholds,
    )


def empty_durable_release_report(
    *,
    thresholds: LiveChatReleaseThresholds | None = None,
) -> LiveChatReleaseGateReport:
    limits = thresholds or LiveChatReleaseThresholds()
    metadata = LiveChatEvidenceMetadata(
        exact_commit_sha="unknown0",
        browser_version="unknown",
        os_version="unknown",
        input_device_hash="unavailable",
        output_device_hash="unavailable",
    )
    metrics = [
        LiveChatMetricResult(
            name=name,
            kind=policy.kind,
            status="insufficient",
            samples=0,
            observed=None,
            limit=policy.limit,
            comparison=policy.comparison,
        )
        for name, policy in limits.metric_policies.items()
    ]
    return LiveChatReleaseGateReport(
        status="insufficient",
        generated_at=datetime.now(timezone.utc).isoformat(),
        metadata=metadata,
        metadata_records=[],
        records_scanned=0,
        traces=0,
        scenarios=[],
        missing_scenarios=list(limits.required_scenarios),
        character_modes=[],
        metrics=metrics,
        failures=[],
        insufficient=["no durable Voice Session evaluations are available"],
    )


def _event(
    record: VoiceSessionEvaluationRecord,
    scenario: str,
    metric_name: str,
    value: float,
) -> LiveChatEvidenceEvent:
    return LiveChatEvidenceEvent(
        timestamp_utc=record.ended_at,
        trace_id=record.evaluation_id,
        scenario=scenario,
        metric_name=metric_name,
        value=value,
        character_id=record.character_id,
    )
