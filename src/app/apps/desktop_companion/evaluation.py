"""Content-free evaluation evidence and rollout gates for Desktop Companion."""
from __future__ import annotations

from app.config.env import env_str, environment

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.runtime.paths import resources_data_root

GateStatus = Literal["pass", "fail", "insufficient"]
RolloutStage = Literal["disabled", "shadow", "text", "speech"]

_REQUIRED_SCENARIOS = {
    "static-screen",
    "typing",
    "rapid-browsing",
    "scene-change",
    "interruption",
    "screen-prompt-injection",
}
_FORBIDDEN_KEY_PARTS = (
    "image",
    "frame",
    "base64",
    "data_url",
    "prompt",
    "message",
    "transcript",
    "commentary_text",
    "visible_text",
    "screen_text",
    "payload",
)
_MINIMUM_RECORDS = 5


class DesktopCompanionEvaluationCreate(BaseModel):
    """Aggregate evidence only; screenshots and generated text are rejected."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=160)
    session_id: str | None = Field(default=None, max_length=160)
    started_at: str = Field(min_length=1, max_length=80)
    ended_at: str = Field(min_length=1, max_length=80)
    exact_commit_sha: str = Field(min_length=7, max_length=64)
    app_version: str = Field(default="unknown", min_length=1, max_length=80)
    browser_version: str = Field(default="unknown", min_length=1, max_length=240)
    os_version: str = Field(default="unknown", min_length=1, max_length=160)
    character_id: str = Field(default="system-assistant", min_length=1, max_length=160)
    profile_version: int | None = Field(default=None, ge=1)
    observation_schema_version: int = Field(default=1, ge=1)
    attention_policy_version: int = Field(default=1, ge=1)
    rollout_stage: RolloutStage = "shadow"
    vision_provider: str = Field(default="unknown", min_length=1, max_length=80)
    vision_model_hash: str | None = Field(default=None, max_length=128)
    remote_provider: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    latency_ms: dict[str, float | None] = Field(default_factory=dict)
    rates: dict[str, float | None] = Field(default_factory=dict)
    scenario_labels: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("counts")
    @classmethod
    def validate_counts(cls, values: dict[str, int]) -> dict[str, int]:
        _validate_metric_keys(values)
        if len(values) > 96:
            raise ValueError("count summary is too large")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("counts must be non-negative integers")
        return values

    @field_validator("latency_ms", "rates")
    @classmethod
    def validate_numeric_metrics(cls, values: dict[str, float | None]) -> dict[str, float | None]:
        _validate_metric_keys(values)
        if len(values) > 96:
            raise ValueError("metric summary is too large")
        for key, value in values.items():
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"metric value must be finite: {key}")
            if key.endswith("_rate") and value is not None and not 0 <= float(value) <= 1:
                raise ValueError(f"rate must be between zero and one: {key}")
        return values

    @field_validator("scenario_labels")
    @classmethod
    def validate_scenarios(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip().casefold()
            if not item or len(item) > 160:
                raise ValueError("invalid scenario label")
            if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_.:" for character in item):
                raise ValueError("scenario labels must be identifiers, not content")
            if item not in normalized:
                normalized.append(item)
        return normalized


class DesktopCompanionEvaluationRecord(DesktopCompanionEvaluationCreate):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: str
    created_at: str
    updated_at: str


class DesktopCompanionEvaluationExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: int
    generated_at: str
    evaluations: list[DesktopCompanionEvaluationRecord]


class DesktopCompanionGateMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: Literal["rate", "latency", "count"]
    status: GateStatus
    samples: int = Field(ge=0)
    observed: float | None = None
    limit: float
    comparison: Literal["maximum", "minimum"]


class DesktopCompanionReleaseGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GateStatus
    generated_at: str
    records_scanned: int = Field(ge=0)
    exact_commit_shas: tuple[str, ...]
    rollout_stages: tuple[RolloutStage, ...]
    scenarios: tuple[str, ...]
    missing_scenarios: tuple[str, ...]
    metrics: tuple[DesktopCompanionGateMetric, ...]
    failures: tuple[str, ...]
    insufficient: tuple[str, ...]
    evidence_evaluation_ids: tuple[str, ...]


class DesktopCompanionRolloutStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_stage: RolloutStage
    effective_stage: RolloutStage
    enabled: bool
    reason: str
    release_gate_status: GateStatus
    evidence_evaluation_ids: tuple[str, ...]


class DesktopCompanionEvaluationStore:
    """Atomic local evidence store using Omnix's shared resources-data root."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_desktop_companion_evaluation_path()
        self._lock = threading.RLock()

    def upsert(self, create: DesktopCompanionEvaluationCreate) -> DesktopCompanionEvaluationRecord:
        with self._lock:
            payload = self._read()
            records = payload.setdefault("evaluations", [])
            existing = next((item for item in records if item.get("run_id") == create.run_id), None)
            now = _now()
            record = DesktopCompanionEvaluationRecord(
                **create.model_dump(mode="json"),
                evaluation_id=(existing or {}).get("evaluation_id") or _evaluation_id(create.run_id),
                created_at=(existing or {}).get("created_at") or now,
                updated_at=now,
            )
            records[:] = [item for item in records if item.get("run_id") != create.run_id]
            records.append(record.model_dump(mode="json"))
            records.sort(key=lambda item: str(item.get("ended_at") or ""))
            payload["evaluations"] = records[-5_000:]
            self._write(payload)
            return record

    def list(self, *, limit: int = 100, session_id: str | None = None) -> list[DesktopCompanionEvaluationRecord]:
        with self._lock:
            records = [
                DesktopCompanionEvaluationRecord.model_validate(item)
                for item in self._read().get("evaluations", [])
            ]
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        records.sort(key=lambda record: record.ended_at, reverse=True)
        return records[: max(1, min(limit, 1_000))]

    def export(self) -> dict[str, Any]:
        with self._lock:
            payload = self._read()
        return {
            "format_version": payload["format_version"],
            "generated_at": _now(),
            "evaluations": payload.get("evaluations", []),
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"format_version": 1, "evaluations": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"format_version": 1, "evaluations": []}
        if not isinstance(payload, dict):
            return {"format_version": 1, "evaluations": []}
        payload.setdefault("format_version", 1)
        payload.setdefault("evaluations", [])
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def default_desktop_companion_evaluation_path() -> Path:
    configured = env_str("OMNIX_DESKTOP_COMPANION_EVALUATION_PATH", "").strip()
    return Path(configured) if configured else resources_data_root() / "desktop_companion_evaluations.json"


def build_desktop_companion_release_gate(
    records: list[DesktopCompanionEvaluationRecord],
) -> DesktopCompanionReleaseGateReport:
    scenarios = sorted({scenario for record in records for scenario in record.scenario_labels})
    missing = sorted(_REQUIRED_SCENARIOS - set(scenarios))
    shadow_metrics = (
        _rate_metric(records, "stale_output_rate", maximum=0.01),
        _rate_metric(records, "provider_error_rate", maximum=0.05),
        _latency_metric(records, "observation_p95", maximum=10_000),
        _count_metric(records, "max_vision_calls_per_minute", maximum=6),
    )
    delivery_metrics = (
        _rate_metric(records, "duplicate_comment_rate", maximum=0.02),
        _rate_metric(records, "unsupported_claim_rate", maximum=0.01),
        _rate_metric(records, "collision_rate", maximum=0.01),
    )
    # Shadow mode deliberately generates no commentary or delivery events. Treat
    # those rates as not applicable until the partition contains real delivery
    # telemetry; once any appears, require the complete delivery-safety set.
    has_delivery_telemetry = any(metric.samples > 0 for metric in delivery_metrics)
    metrics = shadow_metrics + (delivery_metrics if has_delivery_telemetry else ())
    failures = tuple(metric.name for metric in metrics if metric.status == "fail")
    insufficient: list[str] = []
    if len(records) < _MINIMUM_RECORDS:
        insufficient.append(f"minimum_records:{len(records)}/{_MINIMUM_RECORDS}")
    if missing:
        insufficient.append("missing_scenarios")
    insufficient.extend(metric.name for metric in metrics if metric.status == "insufficient")
    status: GateStatus = "fail" if failures else "insufficient" if insufficient else "pass"
    return DesktopCompanionReleaseGateReport(
        status=status,
        generated_at=_now(),
        records_scanned=len(records),
        exact_commit_shas=tuple(sorted({record.exact_commit_sha for record in records})),
        rollout_stages=tuple(sorted({record.rollout_stage for record in records})),
        scenarios=tuple(scenarios),
        missing_scenarios=tuple(missing),
        metrics=metrics,
        failures=failures,
        insufficient=tuple(dict.fromkeys(insufficient)),
        evidence_evaluation_ids=tuple(record.evaluation_id for record in records),
    )


def resolve_desktop_companion_rollout(
    requested_stage: RolloutStage,
    report: DesktopCompanionReleaseGateReport,
) -> DesktopCompanionRolloutStatus:
    if requested_stage == "disabled":
        return _rollout(requested_stage, "disabled", False, "disabled_by_setting", report)
    if requested_stage == "shadow":
        return _rollout(requested_stage, "shadow", True, "shadow_mode_allowed", report)
    if report.status != "pass":
        return _rollout(requested_stage, "shadow", True, "release_gate_requires_shadow", report)
    if requested_stage == "text":
        return _rollout(requested_stage, "text", True, "text_rollout_gate_passed", report)
    speech_evidence = any(record_stage == "speech" for record_stage in report.rollout_stages)
    if not speech_evidence:
        return _rollout(requested_stage, "text", True, "speech_evidence_missing", report)
    return _rollout(requested_stage, "speech", True, "speech_rollout_gate_passed", report)


def hash_vision_model_id(model_id: str) -> str:
    return hashlib.sha256(model_id.strip().encode("utf-8")).hexdigest() if model_id.strip() else ""


def _rollout(
    requested: RolloutStage,
    effective: RolloutStage,
    enabled: bool,
    reason: str,
    report: DesktopCompanionReleaseGateReport,
) -> DesktopCompanionRolloutStatus:
    return DesktopCompanionRolloutStatus(
        requested_stage=requested,
        effective_stage=effective,
        enabled=enabled,
        reason=reason,
        release_gate_status=report.status,
        evidence_evaluation_ids=report.evidence_evaluation_ids,
    )


def _rate_metric(
    records: list[DesktopCompanionEvaluationRecord],
    key: str,
    *,
    maximum: float,
) -> DesktopCompanionGateMetric:
    values = [float(record.rates[key]) for record in records if record.rates.get(key) is not None]
    observed = sum(values) / len(values) if values else None
    return _metric(key, "rate", observed, maximum, len(values))


def _latency_metric(
    records: list[DesktopCompanionEvaluationRecord],
    key: str,
    *,
    maximum: float,
) -> DesktopCompanionGateMetric:
    values = [float(record.latency_ms[key]) for record in records if record.latency_ms.get(key) is not None]
    observed = max(values) if values else None
    return _metric(key, "latency", observed, maximum, len(values))


def _count_metric(
    records: list[DesktopCompanionEvaluationRecord],
    key: str,
    *,
    maximum: float,
) -> DesktopCompanionGateMetric:
    values = [float(record.counts[key]) for record in records if key in record.counts]
    observed = max(values) if values else None
    return _metric(key, "count", observed, maximum, len(values))


def _metric(
    name: str,
    kind: Literal["rate", "latency", "count"],
    observed: float | None,
    limit: float,
    samples: int,
) -> DesktopCompanionGateMetric:
    status: GateStatus = "insufficient" if observed is None else "pass" if observed <= limit else "fail"
    return DesktopCompanionGateMetric(
        name=name,
        kind=kind,
        status=status,
        samples=samples,
        observed=round(observed, 6) if observed is not None else None,
        limit=limit,
        comparison="maximum",
    )


def _validate_metric_keys(values: dict[str, object]) -> None:
    for key in values:
        normalized = key.casefold()
        if len(key) > 120:
            raise ValueError("metric key is too long")
        if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
            raise ValueError(f"content-bearing metric key is not allowed: {key}")


def _evaluation_id(run_id: str) -> str:
    return f"desktop-eval:{hashlib.sha256(run_id.encode('utf-8')).hexdigest()[:24]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_default_evaluation_store: DesktopCompanionEvaluationStore | None = None


def default_desktop_companion_evaluation_store() -> DesktopCompanionEvaluationStore:
    global _default_evaluation_store
    if _default_evaluation_store is None:
        _default_evaluation_store = DesktopCompanionEvaluationStore()
    return _default_evaluation_store


__all__ = [
    "DesktopCompanionEvaluationCreate",
    "DesktopCompanionEvaluationRecord",
    "DesktopCompanionEvaluationStore",
    "DesktopCompanionReleaseGateReport",
    "DesktopCompanionRolloutStatus",
    "RolloutStage",
    "build_desktop_companion_release_gate",
    "default_desktop_companion_evaluation_store",
    "hash_vision_model_id",
    "resolve_desktop_companion_rollout",
]
