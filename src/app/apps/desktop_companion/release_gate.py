"""Evidence partitioning and stricter release gates for Desktop Companion rollout."""
from __future__ import annotations
from app.config.env import environment as _environment

from dataclasses import dataclass
from typing import Mapping

from .evaluation import (
    DesktopCompanionEvaluationRecord,
    DesktopCompanionReleaseGateReport,
    GateStatus,
    build_desktop_companion_release_gate,
)

_MINIMUM_PARTITION_RECORDS = 12
_MINIMUM_SPEECH_RECORDS = 12
_MINIMUM_SPEECH_DELIVERIES = 12
_SPEECH_SCENARIOS = {"speech-completed", "interruption", "speech-stale"}


@dataclass(frozen=True, slots=True)
class DesktopCompanionEvidencePartition:
    exact_commit_sha: str
    observation_schema_version: int = 1
    attention_policy_version: int = 1
    vision_provider: str | None = None
    vision_model_hash: str | None = None
    remote_provider: bool | None = None


def select_desktop_companion_evidence(
    records: list[DesktopCompanionEvaluationRecord],
    partition: DesktopCompanionEvidencePartition,
) -> list[DesktopCompanionEvaluationRecord]:
    selected: list[DesktopCompanionEvaluationRecord] = []
    for record in records:
        if record.exact_commit_sha != partition.exact_commit_sha:
            continue
        if record.observation_schema_version != partition.observation_schema_version:
            continue
        if record.attention_policy_version != partition.attention_policy_version:
            continue
        if partition.vision_provider is not None and record.vision_provider != partition.vision_provider:
            continue
        if partition.vision_model_hash is not None and record.vision_model_hash != partition.vision_model_hash:
            continue
        if partition.remote_provider is not None and record.remote_provider != partition.remote_provider:
            continue
        selected.append(record)
    return selected


def build_partitioned_desktop_companion_release_gate(
    records: list[DesktopCompanionEvaluationRecord],
    partition: DesktopCompanionEvidencePartition,
) -> DesktopCompanionReleaseGateReport:
    selected = select_desktop_companion_evidence(records, partition)
    report = build_desktop_companion_release_gate(selected)
    insufficient = _without_legacy_minimum(report.insufficient)
    if len(selected) < _MINIMUM_PARTITION_RECORDS:
        insufficient.append(f"minimum_partition_records:{len(selected)}/{_MINIMUM_PARTITION_RECORDS}")
    if not partition.exact_commit_sha or partition.exact_commit_sha == "unknown-local-build":
        insufficient.append("exact_build_identity_required")
    return _with_status(report, insufficient)


def build_partitioned_desktop_companion_speech_gate(
    records: list[DesktopCompanionEvaluationRecord],
    partition: DesktopCompanionEvidencePartition,
) -> DesktopCompanionReleaseGateReport:
    selected = [
        record
        for record in select_desktop_companion_evidence(records, partition)
        if record.rollout_stage == "speech"
    ]
    report = build_desktop_companion_release_gate(selected)
    insufficient = _without_legacy_minimum(report.insufficient)
    if len(selected) < _MINIMUM_SPEECH_RECORDS:
        insufficient.append(f"minimum_speech_records:{len(selected)}/{_MINIMUM_SPEECH_RECORDS}")
    deliveries = sum(int(record.counts.get("deliveries", 0)) for record in selected)
    if deliveries < _MINIMUM_SPEECH_DELIVERIES:
        insufficient.append(f"minimum_speech_deliveries:{deliveries}/{_MINIMUM_SPEECH_DELIVERIES}")
    scenarios = {scenario for record in selected for scenario in record.scenario_labels}
    missing = sorted(_SPEECH_SCENARIOS - scenarios)
    if missing:
        insufficient.append("missing_speech_scenarios:" + ",".join(missing))
    if not partition.exact_commit_sha or partition.exact_commit_sha == "unknown-local-build":
        insufficient.append("exact_build_identity_required")
    return _with_status(report, insufficient)


def desktop_companion_speech_canary_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = environ if environ is not None else _environment()
    return str(values.get("OMNIX_DESKTOP_COMPANION_SPEECH_CANARY") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _without_legacy_minimum(values: tuple[str, ...]) -> list[str]:
    return [item for item in values if not item.startswith("minimum_records:")]


def _with_status(
    report: DesktopCompanionReleaseGateReport,
    insufficient: list[str],
) -> DesktopCompanionReleaseGateReport:
    status: GateStatus = "fail" if report.failures else "insufficient" if insufficient else "pass"
    return report.model_copy(
        update={
            "status": status,
            "insufficient": tuple(dict.fromkeys(insufficient)),
        }
    )


__all__ = [
    "DesktopCompanionEvidencePartition",
    "build_partitioned_desktop_companion_release_gate",
    "build_partitioned_desktop_companion_speech_gate",
    "desktop_companion_speech_canary_enabled",
    "select_desktop_companion_evidence",
]
