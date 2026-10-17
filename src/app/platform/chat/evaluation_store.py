"""Durable, content-free Live Chat evaluation records and presence policies."""
from __future__ import annotations
import builtins

from app.config.env import env_str

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from contextlib import AbstractContextManager

PresencePreset = Literal["quiet", "natural", "engaged", "listener"]
GateStatus = Literal["pass", "fail", "insufficient"]
DuplexMode = Literal["automatic", "half_duplex", "echo_aware"]
ResolvedDuplexMode = Literal["half_duplex", "echo_aware"]

_FORBIDDEN_METRIC_KEY_PARTS = (
    "transcript",
    "prompt",
    "memory",
    "pcm",
    "raw_audio",
    "audio_bytes",
    "message_content",
    "utterance_text",
)
_UNKNOWN_RUNTIME_VALUES = {"", "unknown", "unknown0", "unavailable"}
_MINIMUM_POLICY_EVIDENCE = 5


class VoiceSessionEvaluationCreate(BaseModel):
    """Aggregate evaluation only; raw conversational content is not accepted."""

    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=160)
    session_id: str | None = Field(default=None, max_length=160)
    started_at: str = Field(min_length=1, max_length=80)
    ended_at: str = Field(min_length=1, max_length=80)
    exact_commit_sha: str = Field(min_length=7, max_length=64)
    app_version: str = Field(default="unknown", min_length=1, max_length=80)
    browser_version: str = Field(default="unknown", min_length=1, max_length=240)
    os_version: str = Field(default="unknown", min_length=1, max_length=160)
    character_id: str = Field(default="system-assistant", min_length=1, max_length=160)
    profile_version: int | None = Field(default=None, ge=1)
    presence_preset: PresencePreset = "natural"
    conversation_stance: str = Field(default="automatic", min_length=1, max_length=40)
    configured_duplex_mode: DuplexMode = "automatic"
    resolved_duplex_mode: ResolvedDuplexMode = "half_duplex"
    calibration_version: str | None = Field(default=None, max_length=80)
    input_device_hash: str | None = Field(default=None, max_length=128)
    output_device_hash: str | None = Field(default=None, max_length=128)
    environment_hash: str | None = Field(default=None, max_length=128)
    latency_summary: dict[str, float | None] = Field(default_factory=dict)
    quality_metrics: dict[str, float | int | None] = Field(default_factory=dict)
    eos_termination_counts: dict[str, int] = Field(default_factory=dict)
    scenario_labels: list[str] = Field(default_factory=list, max_length=64)
    release_gate_status: GateStatus = "insufficient"
    listening_score: float | None = Field(default=None, ge=1, le=5)
    pressure_score: float | None = Field(default=None, ge=1, le=5)

    @field_validator("latency_summary", "quality_metrics")
    @classmethod
    def validate_numeric_summary(cls, value: dict[str, float | int | None]) -> dict[str, float | int | None]:
        if len(value) > 96:
            raise ValueError("metric summary is too large")
        for key, number in value.items():
            normalized = key.casefold()
            if any(part in normalized for part in _FORBIDDEN_METRIC_KEY_PARTS):
                raise ValueError(f"content-bearing metric key is not allowed: {key}")
            if len(key) > 120:
                raise ValueError("metric key is too long")
            if number is not None and (not isinstance(number, (int, float)) or not math.isfinite(float(number))):
                raise ValueError(f"metric value must be finite: {key}")
        return value

    @field_validator("eos_termination_counts")
    @classmethod
    def validate_eos_counts(cls, value: dict[str, int]) -> dict[str, int]:
        allowed = {"natural_eos", "forced_eos", "token_limit", "sequence_limit", "model_stopped"}
        if set(value) - allowed:
            raise ValueError("unknown EOS termination reason")
        if any(not isinstance(count, int) or count < 0 for count in value.values()):
            raise ValueError("EOS counts must be non-negative integers")
        return value

    @field_validator("scenario_labels")
    @classmethod
    def validate_scenarios(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for scenario in value:
            item = scenario.strip().casefold()
            if not item or len(item) > 160:
                raise ValueError("invalid scenario label")
            if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_.:" for character in item):
                raise ValueError("scenario labels must be identifiers, not content")
            if item not in normalized:
                normalized.append(item)
        return normalized


class VoiceSessionEvaluationRecord(VoiceSessionEvaluationCreate):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: str
    created_at: str
    updated_at: str


class PresencePolicyValues(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    silence_tolerance_ms: int = Field(ge=5_000, le=120_000)
    initiative_threshold_ms: int = Field(ge=5_000, le=120_000)
    initiative_cooldown_ms: int = Field(ge=5_000, le=300_000)
    listener_backchannel_frequency: float = Field(ge=0, le=1)
    typical_turn_words: int = Field(ge=8, le=240)
    interruption_sensitivity: float = Field(ge=0, le=1)
    response_onset_ms: int = Field(ge=0, le=5_000)


class PresencePolicyVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: PresencePolicyValues
    reason: str = Field(min_length=1, max_length=240)
    evidence_evaluation_ids: list[str] = Field(default_factory=list, max_length=200)


class PresencePolicyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: PresencePreset
    version: int = Field(ge=1)
    values: PresencePolicyValues
    reason: str
    evidence_evaluation_ids: tuple[str, ...] = ()
    active: bool
    created_at: str


class LiveChatEvaluationExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: int
    generated_at: str
    evaluations: list[VoiceSessionEvaluationRecord]
    presence_policies: dict[str, PresencePolicyVersion]


_DEFAULT_POLICIES: dict[PresencePreset, PresencePolicyValues] = {
    "quiet": PresencePolicyValues(
        silence_tolerance_ms=30_000,
        initiative_threshold_ms=40_000,
        initiative_cooldown_ms=90_000,
        listener_backchannel_frequency=0.05,
        typical_turn_words=45,
        interruption_sensitivity=0.80,
        response_onset_ms=650,
    ),
    "natural": PresencePolicyValues(
        silence_tolerance_ms=15_000,
        initiative_threshold_ms=18_000,
        initiative_cooldown_ms=45_000,
        listener_backchannel_frequency=0.16,
        typical_turn_words=70,
        interruption_sensitivity=0.70,
        response_onset_ms=420,
    ),
    "engaged": PresencePolicyValues(
        silence_tolerance_ms=9_000,
        initiative_threshold_ms=11_000,
        initiative_cooldown_ms=30_000,
        listener_backchannel_frequency=0.24,
        typical_turn_words=85,
        interruption_sensitivity=0.75,
        response_onset_ms=300,
    ),
    "listener": PresencePolicyValues(
        silence_tolerance_ms=28_000,
        initiative_threshold_ms=38_000,
        initiative_cooldown_ms=75_000,
        listener_backchannel_frequency=0.12,
        typical_turn_words=35,
        interruption_sensitivity=0.90,
        response_onset_ms=700,
    ),
}


def default_live_chat_evaluation_path() -> Path:
    configured = (env_str("OMNIX_LIVE_CHAT_EVALUATION_PATH", "") or "").strip()
    return Path(configured) if configured else Path("resources/data/live_chat_evaluations.json")


class LiveChatEvaluationStore:
    """Atomic local store for content-free call evidence and versioned policies."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_live_chat_evaluation_path()
        # Any lock usable in ``with``; the PostgreSQL store uses a document lock.
        self._lock: AbstractContextManager[Any] = threading.RLock()

    def upsert(self, create: VoiceSessionEvaluationCreate) -> VoiceSessionEvaluationRecord:
        with self._lock:
            payload = self._read()
            now = _now()
            records = payload.setdefault("evaluations", [])
            existing = next((item for item in records if item.get("call_id") == create.call_id), None)
            evaluation_id = existing.get("evaluation_id") if existing else _evaluation_id(create.call_id)
            created_at = existing.get("created_at") if existing else now
            record = VoiceSessionEvaluationRecord(
                **create.model_dump(mode="json"),
                evaluation_id=evaluation_id,
                created_at=created_at,
                updated_at=now,
            )
            records[:] = [item for item in records if item.get("call_id") != create.call_id]
            records.append(record.model_dump(mode="json"))
            records.sort(key=lambda item: str(item.get("ended_at", "")))
            payload["evaluations"] = records[-5_000:]
            self._write(payload)
            return record

    def update_release_gate_status(self, evaluation_id: str, status: GateStatus) -> VoiceSessionEvaluationRecord:
        with self._lock:
            payload = self._read()
            records = payload.setdefault("evaluations", [])
            target = next((item for item in records if item.get("evaluation_id") == evaluation_id), None)
            if target is None:
                raise KeyError(f"voice session evaluation {evaluation_id} does not exist")
            target["release_gate_status"] = status
            target["updated_at"] = _now()
            self._write(payload)
            return VoiceSessionEvaluationRecord.model_validate(target)

    def get(self, evaluation_id: str) -> VoiceSessionEvaluationRecord | None:
        with self._lock:
            for item in self._read().get("evaluations", []):
                if item.get("evaluation_id") == evaluation_id:
                    return VoiceSessionEvaluationRecord.model_validate(item)
        return None

    def list(
        self,
        *,
        session_id: str | None = None,
        presence_preset: PresencePreset | None = None,
        limit: int = 100,
    ) -> list[VoiceSessionEvaluationRecord]:
        with self._lock:
            records = [VoiceSessionEvaluationRecord.model_validate(item) for item in self._read().get("evaluations", [])]
        if session_id is not None:
            records = [record for record in records if record.session_id == session_id]
        if presence_preset is not None:
            records = [record for record in records if record.presence_preset == presence_preset]
        records.sort(key=lambda record: record.ended_at, reverse=True)
        return records[: max(1, min(limit, 1_000))]

    def export(self) -> dict:
        with self._lock:
            payload = self._read()
        active_policies = {
            str(preset): active
            for preset, versions in payload.get("presence_policies", {}).items()
            if isinstance(versions, list)
            and (active := next(
                (version for version in versions if version.get("active")), None
            )) is not None
        }
        return {
            "format_version": payload["format_version"],
            "generated_at": _now(),
            "evaluations": payload.get("evaluations", []),
            "presence_policies": active_policies,
        }

    def list_policy_versions(self, preset: PresencePreset | None = None) -> builtins.list[PresencePolicyVersion]:
        with self._lock:
            payload = self._read()
            rows = payload.get("presence_policies", {})
            presets = [preset] if preset else list(_DEFAULT_POLICIES)
            versions = [
                PresencePolicyVersion.model_validate(item)
                for name in presets
                for item in rows.get(name, [])
            ]
        return sorted(versions, key=lambda item: (item.preset, item.version))

    def active_policies(self) -> dict[PresencePreset, PresencePolicyVersion]:
        result: dict[PresencePreset, PresencePolicyVersion] = {}
        for version in self.list_policy_versions():
            if version.active:
                result[version.preset] = version
        return result

    def create_policy_version(
        self,
        preset: PresencePreset,
        create: PresencePolicyVersionCreate,
    ) -> PresencePolicyVersion:
        with self._lock:
            payload = self._read()
            evidence_ids = tuple(dict.fromkeys(create.evidence_evaluation_ids))
            self._validate_policy_evidence(payload, preset, evidence_ids)
            rows = payload.setdefault("presence_policies", {}).setdefault(preset, [])
            next_version = max((int(item.get("version", 0)) for item in rows), default=0) + 1
            record = PresencePolicyVersion(
                preset=preset,
                version=next_version,
                values=create.values,
                reason=create.reason,
                evidence_evaluation_ids=evidence_ids,
                active=False,
                created_at=_now(),
            )
            rows.append(record.model_dump(mode="json"))
            self._write(payload)
            return record

    def activate_policy(self, preset: PresencePreset, version: int) -> PresencePolicyVersion:
        with self._lock:
            payload = self._read()
            rows = payload.setdefault("presence_policies", {}).setdefault(preset, [])
            match = next((item for item in rows if int(item.get("version", 0)) == version), None)
            if match is None:
                raise KeyError(f"presence policy {preset} v{version} does not exist")
            if version > 1:
                self._validate_policy_evidence(
                    payload,
                    preset,
                    tuple(match.get("evidence_evaluation_ids", ())),
                )
            selected: PresencePolicyVersion | None = None
            for item in rows:
                item["active"] = int(item.get("version", 0)) == version
                if item["active"]:
                    selected = PresencePolicyVersion.model_validate(item)
            self._write(payload)
            assert selected is not None
            return selected

    def rollback_policy(self, preset: PresencePreset) -> PresencePolicyVersion:
        versions = self.list_policy_versions(preset)
        active = next((item for item in versions if item.active), None)
        previous = [item for item in versions if active is not None and item.version < active.version]
        if not previous:
            raise KeyError(f"presence policy {preset} has no previous version")
        return self.activate_policy(preset, previous[-1].version)

    @staticmethod
    def _validate_policy_evidence(payload: dict, preset: PresencePreset, evidence_ids: tuple[str, ...]) -> None:
        if len(evidence_ids) < _MINIMUM_POLICY_EVIDENCE:
            raise ValueError(f"at least {_MINIMUM_POLICY_EVIDENCE} labelled evaluations are required")
        records_by_id = {
            str(item.get("evaluation_id")): VoiceSessionEvaluationRecord.model_validate(item)
            for item in payload.get("evaluations", [])
        }
        missing = [evaluation_id for evaluation_id in evidence_ids if evaluation_id not in records_by_id]
        if missing:
            raise ValueError("presence policy evidence contains unknown evaluation IDs")
        evidence = [records_by_id[evaluation_id] for evaluation_id in evidence_ids]
        if any(record.presence_preset != preset for record in evidence):
            raise ValueError("presence policy evidence must match the selected preset")
        if any(not record.scenario_labels for record in evidence):
            raise ValueError("presence policy evidence must be scenario-labelled")
        if any(record.release_gate_status == "fail" for record in evidence):
            raise ValueError("failed release evidence cannot tune a presence policy")
        if any(record.exact_commit_sha.casefold() in _UNKNOWN_RUNTIME_VALUES for record in evidence):
            raise ValueError("presence policy evidence requires an exact commit SHA")
        if any(record.browser_version.casefold() in _UNKNOWN_RUNTIME_VALUES for record in evidence):
            raise ValueError("presence policy evidence requires browser metadata")
        if any(record.os_version.casefold() in _UNKNOWN_RUNTIME_VALUES for record in evidence):
            raise ValueError("presence policy evidence requires OS metadata")

    def _read(self) -> dict:
        if not self.path.is_file():
            return self._fresh_payload()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._fresh_payload()
        if not isinstance(payload, dict):
            return self._fresh_payload()
        payload.setdefault("format_version", 2)
        payload.setdefault("evaluations", [])
        policies = payload.setdefault("presence_policies", {})
        self._ensure_default_policies(policies)
        return payload

    def _fresh_payload(self) -> dict:
        policies: dict[str, list[dict]] = {}
        self._ensure_default_policies(policies)
        return {"format_version": 2, "evaluations": [], "presence_policies": policies}

    @staticmethod
    def _ensure_default_policies(policies: dict) -> None:
        for preset, values in _DEFAULT_POLICIES.items():
            if policies.get(preset):
                continue
            policies[preset] = [PresencePolicyVersion(
                preset=preset,
                version=1,
                values=values,
                reason="initial_server_policy",
                active=True,
                created_at="2026-07-11T00:00:00+00:00",
            ).model_dump(mode="json")]

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


_default_store: LiveChatEvaluationStore | None = None
_default_store_path: Path | None = None


def default_live_chat_evaluation_store() -> LiveChatEvaluationStore:
    global _default_store, _default_store_path
    from app.persistence.runtime import uses_postgresql_runtime
    if uses_postgresql_runtime():
        from app.platform.chat.persistence.evaluation_store import PostgresLiveChatEvaluationStore
        return PostgresLiveChatEvaluationStore()
    path = default_live_chat_evaluation_path()
    if _default_store is None or _default_store_path != path:
        _default_store = LiveChatEvaluationStore(path)
        _default_store_path = path
    return _default_store


def _evaluation_id(call_id: str) -> str:
    digest = hashlib.sha256(call_id.encode("utf-8")).hexdigest()[:24]
    return f"live-evaluation:{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
