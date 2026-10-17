from __future__ import annotations

"""Reproducible session evidence manifests and reconciliation state."""

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .strategy_repository import StrategyEvent


ReconciliationState = Literal[
    "PENDING_DATA",
    "RETRYING",
    "FINAL",
    "PERMANENTLY_UNSCORABLE",
]


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: Literal[
        "strategy_event_stream",
        "universe_snapshot",
        "input_file",
        "provider_response",
    ]
    source_id: str
    sha256: str = Field(min_length=64, max_length=64)
    record_count: int = Field(default=0, ge=0)
    start_time: datetime | None = None
    end_time: datetime | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("evidence input timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class AggregationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timezone: str = "America/New_York"
    included_event_types: tuple[str, ...] = ()
    excluded_event_types: tuple[str, ...] = ()
    event_deduplication: str = "event_id_then_idempotency_key"
    event_ordering: str = "observed_at_then_event_id"
    price_authority: str = "consolidated_sip_trade_events"
    formal_bar_authority: str = "single_provider_raw_finalized_sip_5m"
    feature_qualification_policy: str = "feature-local-coverage-v1"
    execution_capture_policy: str = "first-causal-post-actionable-quote-v1"


class SessionEvidenceFrozenScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_version: str = "session-evidence-manifest-v1"
    session_date: date
    strategy_ids: tuple[str, ...]
    event_ids: tuple[str, ...] = ()
    arm_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    universe_ids: tuple[str, ...] = ()
    aggregation_start: datetime
    aggregation_end: datetime
    evidence_inputs: tuple[EvidenceInput, ...]
    aggregation_rule: AggregationRule
    expected_candidate_count: int = Field(default=0, ge=0)
    observed_candidate_count: int = Field(default=0, ge=0)
    frozen_at: datetime

    @field_validator("aggregation_start", "aggregation_end", "frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("session manifest timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class SessionEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    manifest_id: str
    strategy_id: str
    frozen_scope: SessionEvidenceFrozenScope
    reconciliation_state: ReconciliationState = "PENDING_DATA"
    attempt_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    finalized_at: datetime | None = None
    reconciliation_payload: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    updated_at: datetime | None = None

    @field_validator("next_retry_at", "finalized_at", "updated_at")
    @classmethod
    def aware_optional(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("session reconciliation timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @property
    def immutable_fingerprint(self) -> str:
        return self.frozen_scope.immutable_fingerprint


def _event_payload(event: StrategyEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "instrument_id": event.instrument_id,
        "event_type": event.event_type,
        "state": event.state,
        "reason_code": event.reason_code,
        "observed_at": event.observed_at.astimezone(timezone.utc).isoformat(),
        "idempotency_key": event.idempotency_key,
        "payload": event.payload,
    }


def event_stream_input(
    strategy_id: str,
    events: list[StrategyEvent] | tuple[StrategyEvent, ...],
) -> EvidenceInput:
    ordered = sorted(
        events,
        key=lambda event: (
            event.observed_at.astimezone(timezone.utc),
            event.event_id,
        ),
    )
    payload = [_event_payload(event) for event in ordered]
    return EvidenceInput(
        source_type="strategy_event_stream",
        source_id=f"strategy:{strategy_id}",
        sha256=_hash(payload),
        record_count=len(ordered),
        start_time=ordered[0].observed_at if ordered else None,
        end_time=ordered[-1].observed_at if ordered else None,
    )


def universe_input(universe) -> EvidenceInput:
    payload = universe.model_dump(mode="json")
    return EvidenceInput(
        source_type="universe_snapshot",
        source_id=str(universe.universe_id),
        sha256=_hash(payload),
        record_count=len(universe.candidates),
        start_time=universe.evaluation_time,
        end_time=universe.evaluation_time,
    )


def provider_evidence_input(
    *,
    source_id: str,
    payload: object,
    record_count: int,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> EvidenceInput:
    return EvidenceInput(
        source_type="provider_response",
        source_id=source_id,
        sha256=_hash(payload),
        record_count=record_count,
        start_time=start_time,
        end_time=end_time,
    )


def file_evidence_input(
    *,
    source_id: str,
    content: bytes,
    record_count: int = 0,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> EvidenceInput:
    return EvidenceInput(
        source_type="input_file",
        source_id=source_id,
        sha256=hashlib.sha256(content).hexdigest(),
        record_count=record_count,
        start_time=start_time,
        end_time=end_time,
    )


def build_session_evidence_manifest(
    *,
    strategy_id: str,
    session_date: date,
    events: list[StrategyEvent] | tuple[StrategyEvent, ...],
    universe,
    aggregation_start: datetime,
    aggregation_end: datetime,
    frozen_at: datetime,
    extra_inputs: tuple[EvidenceInput, ...] = (),
    excluded_event_types: tuple[str, ...] = (),
) -> SessionEvidenceManifest:
    ordered = sorted(
        events,
        key=lambda event: (
            event.observed_at.astimezone(timezone.utc),
            event.event_id,
        ),
    )
    run_ids = tuple(
        sorted({str(event.run_id) for event in ordered if event.run_id})
    )
    arm_ids: set[str] = set()
    universe_ids: set[str] = {str(universe.universe_id)}
    for event in ordered:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key in ("arm", "policy", "policy_version"):
            value = payload.get(key)
            if value:
                arm_ids.add(str(value))
        universe_id = payload.get("universe_id")
        if universe_id:
            universe_ids.add(str(universe_id))
    event_types = tuple(
        sorted(
            {
                event.event_type
                for event in ordered
                if event.event_type not in set(excluded_event_types)
            }
        )
    )
    inputs = (
        event_stream_input(strategy_id, ordered),
        universe_input(universe),
        *extra_inputs,
    )
    scope = SessionEvidenceFrozenScope(
        session_date=session_date,
        strategy_ids=(strategy_id,),
        event_ids=tuple(event.event_id for event in ordered),
        arm_ids=tuple(sorted(arm_ids)),
        run_ids=run_ids,
        universe_ids=tuple(sorted(universe_ids)),
        aggregation_start=aggregation_start,
        aggregation_end=aggregation_end,
        evidence_inputs=inputs,
        aggregation_rule=AggregationRule(
            included_event_types=event_types,
            excluded_event_types=tuple(sorted(excluded_event_types)),
        ),
        expected_candidate_count=len(universe.candidates),
        observed_candidate_count=len(
            {
                event.instrument_id
                for event in ordered
                if event.instrument_id != "__universe__"
            }
        ),
        frozen_at=frozen_at,
    )
    manifest_id = _hash(
        {
            "strategy_id": strategy_id,
            "session_date": session_date.isoformat(),
            "frozen_scope": scope.model_dump(mode="json"),
        }
    )[:40]
    return SessionEvidenceManifest(
        manifest_id=manifest_id,
        strategy_id=strategy_id,
        frozen_scope=scope,
    )


def begin_reconciliation(
    manifest: SessionEvidenceManifest,
    *,
    observed_at: datetime,
    lease_until: datetime,
) -> SessionEvidenceManifest:
    """Claim the manifest for one attempt.

    ``lease_until`` is stored as ``next_retry_at`` so an attempt that never
    records its outcome (process exit, cycle timeout) is retried only after the
    lease lapses, not on every restart.
    """
    if manifest.reconciliation_state in {"FINAL", "PERMANENTLY_UNSCORABLE"}:
        raise ValueError(
            f"terminal_manifest_cannot_retry:{manifest.reconciliation_state}"
        )
    if lease_until <= observed_at:
        raise ValueError("reconciliation_lease_must_follow_observation")
    return manifest.model_copy(
        update={
            "reconciliation_state": "RETRYING",
            "attempt_count": manifest.attempt_count + 1,
            "next_retry_at": lease_until,
            "revision": manifest.revision + 1,
            "updated_at": observed_at,
        }
    )


def checkpoint_reconciliation(
    manifest: SessionEvidenceManifest,
    *,
    observed_at: datetime,
    payload: dict[str, Any],
) -> SessionEvidenceManifest:
    """Record an in-flight attempt's progress; the state and lease are unchanged."""
    if manifest.reconciliation_state != "RETRYING":
        raise ValueError("manifest_must_be_retrying_to_checkpoint")
    return manifest.model_copy(
        update={
            "reconciliation_payload": payload,
            "revision": manifest.revision + 1,
            "updated_at": observed_at,
        }
    )


def defer_reconciliation(
    manifest: SessionEvidenceManifest,
    *,
    observed_at: datetime,
    next_retry_at: datetime,
    payload: dict[str, Any],
) -> SessionEvidenceManifest:
    if manifest.reconciliation_state != "RETRYING":
        raise ValueError("manifest_must_be_retrying_to_defer")
    return manifest.model_copy(
        update={
            "next_retry_at": next_retry_at,
            "reconciliation_payload": payload,
            "revision": manifest.revision + 1,
            "updated_at": observed_at,
        }
    )


def finalize_reconciliation(
    manifest: SessionEvidenceManifest,
    *,
    observed_at: datetime,
    payload: dict[str, Any],
) -> SessionEvidenceManifest:
    if manifest.reconciliation_state != "RETRYING":
        raise ValueError("manifest_must_be_retrying_to_finalize")
    return manifest.model_copy(
        update={
            "reconciliation_state": "FINAL",
            "next_retry_at": None,
            "finalized_at": observed_at,
            "reconciliation_payload": payload,
            "revision": manifest.revision + 1,
            "updated_at": observed_at,
        }
    )


def permanently_unscorable(
    manifest: SessionEvidenceManifest,
    *,
    observed_at: datetime,
    payload: dict[str, Any],
) -> SessionEvidenceManifest:
    if manifest.reconciliation_state != "RETRYING":
        raise ValueError("manifest_must_be_retrying_to_close")
    return manifest.model_copy(
        update={
            "reconciliation_state": "PERMANENTLY_UNSCORABLE",
            "next_retry_at": None,
            "finalized_at": observed_at,
            "reconciliation_payload": payload,
            "revision": manifest.revision + 1,
            "updated_at": observed_at,
        }
    )


_COLUMNS = """
manifest_id, session_date, strategy_id, reconciliation_state, payload,
attempt_count, next_retry_at, frozen_at, finalized_at, revision, updated_at
"""


def _row_to_manifest(row) -> SessionEvidenceManifest:
    payload = dict(row[4] or {})
    return SessionEvidenceManifest(
        manifest_id=str(row[0]),
        strategy_id=str(row[2]),
        frozen_scope=SessionEvidenceFrozenScope.model_validate(
            payload["frozen_scope"]
        ),
        reconciliation_state=cast(Any, str(row[3])),
        attempt_count=int(row[5]),
        next_retry_at=row[6],
        finalized_at=row[8],
        reconciliation_payload=dict(
            payload.get("reconciliation_payload") or {}
        ),
        revision=int(row[9]),
        updated_at=row[10],
    )


class SessionEvidenceRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory=unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def create(self, manifest: SessionEvidenceManifest) -> SessionEvidenceManifest:
        payload = {
            "frozen_scope": manifest.frozen_scope.model_dump(mode="json"),
            "reconciliation_payload": manifest.reconciliation_payload,
        }
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_session_evidence_manifests (
                    workspace_id, manifest_id, session_date, strategy_id,
                    reconciliation_state, payload, attempt_count,
                    next_retry_at, frozen_at, finalized_at, revision
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
                )
                ON CONFLICT (workspace_id, strategy_id, session_date)
                DO NOTHING
                RETURNING {_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    manifest.manifest_id,
                    manifest.frozen_scope.session_date,
                    manifest.strategy_id,
                    manifest.reconciliation_state,
                    json.dumps(payload, default=str),
                    manifest.attempt_count,
                    manifest.next_retry_at,
                    manifest.frozen_scope.frozen_at,
                    manifest.finalized_at,
                    manifest.revision,
                ),
            ).fetchone()
            if row is None:
                row = uow.connection.execute(
                    f"""
                    SELECT {_COLUMNS}
                      FROM omnix_trading_session_evidence_manifests
                     WHERE workspace_id = %s
                       AND strategy_id = %s
                       AND session_date = %s
                    """,
                    (
                        self.context.workspace_id,
                        manifest.strategy_id,
                        manifest.frozen_scope.session_date,
                    ),
                ).fetchone()
            uow.commit()
        return _row_to_manifest(row)

    def get_for_session(
        self,
        strategy_id: str,
        session_date: date,
    ) -> SessionEvidenceManifest | None:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_session_evidence_manifests
                 WHERE workspace_id = %s
                   AND strategy_id = %s
                   AND session_date = %s
                """,
                (self.context.workspace_id, strategy_id, session_date),
            ).fetchone()
        return _row_to_manifest(row) if row is not None else None

    def pending(
        self,
        *,
        as_of: datetime,
        limit: int = 20,
    ) -> list[SessionEvidenceManifest]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_session_evidence_manifests
                 WHERE workspace_id = %s
                   AND reconciliation_state IN ('PENDING_DATA', 'RETRYING')
                   AND (next_retry_at IS NULL OR next_retry_at <= %s)
                 ORDER BY session_date, updated_at, manifest_id
                 LIMIT %s
                """,
                (self.context.workspace_id, as_of, limit),
            ).fetchall()
        return [_row_to_manifest(row) for row in rows]

    def save_transition(
        self,
        prior: SessionEvidenceManifest,
        updated: SessionEvidenceManifest,
    ) -> SessionEvidenceManifest:
        if prior.immutable_fingerprint != updated.immutable_fingerprint:
            raise ValueError("session_manifest_frozen_scope_mutation")
        if updated.revision != prior.revision + 1:
            raise ValueError("session_manifest_revision_must_advance_once")
        payload = {
            "frozen_scope": prior.frozen_scope.model_dump(mode="json"),
            "reconciliation_payload": updated.reconciliation_payload,
        }
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                UPDATE omnix_trading_session_evidence_manifests
                   SET reconciliation_state = %s,
                       payload = %s::jsonb,
                       attempt_count = %s,
                       next_retry_at = %s,
                       finalized_at = %s,
                       revision = %s,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND manifest_id = %s
                   AND revision = %s
                RETURNING {_COLUMNS}
                """,
                (
                    updated.reconciliation_state,
                    json.dumps(payload, default=str),
                    updated.attempt_count,
                    updated.next_retry_at,
                    updated.finalized_at,
                    updated.revision,
                    updated.updated_at,
                    self.context.workspace_id,
                    prior.manifest_id,
                    prior.revision,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("session_manifest_revision_conflict")
            uow.commit()
        return _row_to_manifest(row)


def default_session_evidence_repository() -> SessionEvidenceRepository:
    return SessionEvidenceRepository()


__all__ = [
    "AggregationRule",
    "EvidenceInput",
    "ReconciliationState",
    "SessionEvidenceFrozenScope",
    "SessionEvidenceManifest",
    "SessionEvidenceRepository",
    "begin_reconciliation",
    "build_session_evidence_manifest",
    "checkpoint_reconciliation",
    "default_session_evidence_repository",
    "defer_reconciliation",
    "event_stream_input",
    "file_evidence_input",
    "finalize_reconciliation",
    "permanently_unscorable",
    "provider_evidence_input",
    "universe_input",
]
