"""Internal Desktop Companion observation, preflight, evaluation, and rollout endpoints."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import BackgroundTasks, APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.platform.chat.contracts import ChatSessionStore, default_chat_store

from .activity_bridge import (
    DesktopCompanionActivityBridge,
    DesktopCompanionActivitySnapshot,
    default_desktop_companion_activity_bridge,
)
from .build_identity import (
    DesktopCompanionBuildIdentity,
    resolve_desktop_companion_build_identity,
)
from .context import (
    DesktopCompanionContextSnapshot,
    DesktopCompanionContextStore,
    default_desktop_companion_context_store,
)
from .evaluation import (
    DesktopCompanionEvaluationCreate,
    DesktopCompanionEvaluationExport,
    DesktopCompanionEvaluationRecord,
    DesktopCompanionEvaluationStore,
    DesktopCompanionReleaseGateReport,
    DesktopCompanionRolloutStatus,
    RolloutStage,
    default_desktop_companion_evaluation_store,
    resolve_desktop_companion_rollout,
)
from .memory_bridge import (
    DesktopCompanionMemoryBridge,
    default_desktop_companion_memory_bridge,
)
from .operations import (
    DesktopCompanionOperationalStatus,
    desktop_companion_operational_status,
)
from .preflight import (
    DesktopCompanionPreflightRequest,
    DesktopCompanionPreflightResult,
    DesktopCompanionPreflightService,
    default_desktop_companion_preflight_service,
)
from .release_gate import (
    DesktopCompanionEvidencePartition,
    build_partitioned_desktop_companion_release_gate,
    build_partitioned_desktop_companion_speech_gate,
    desktop_companion_speech_canary_enabled,
)
from .runtime import (
    DesktopCompanionObserveRequest,
    DesktopCompanionObserveResponse,
    DesktopCompanionOrchestrator,
    default_desktop_companion_orchestrator,
)


class DesktopCompanionResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=160)
    capture_generation: str | None = Field(default=None, max_length=160)


class DesktopCompanionResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reset: bool = True
    session_id: str


IdentityResolutionStatus = Literal[
    "resolved_system",
    "resolved_character",
    "session_missing",
    "character_missing",
]


@dataclass(frozen=True, slots=True)
class AuthoritativeIdentityResolution:
    status: IdentityResolutionStatus
    character_id: str | None = None


def _resolve_authoritative_identity(
    request: DesktopCompanionObserveRequest,
    chat_store: ChatSessionStore,
) -> AuthoritativeIdentityResolution:
    """Resolve identity from authoritative Chat state; lookup failures propagate closed."""

    session = chat_store.get_session(request.session_id)
    if session is None:
        return AuthoritativeIdentityResolution(status="session_missing")
    if session.interaction_mode != "character":
        return AuthoritativeIdentityResolution(status="resolved_system")
    character_id = str(session.character_id or "").strip()
    if not character_id:
        return AuthoritativeIdentityResolution(status="character_missing")
    return AuthoritativeIdentityResolution(
        status="resolved_character",
        character_id=character_id,
    )


def register_desktop_companion_routes(
    router: APIRouter,
    *,
    evaluation_store_factory: Callable[[], DesktopCompanionEvaluationStore] = (
        default_desktop_companion_evaluation_store
    ),
    orchestrator_factory: Callable[[], DesktopCompanionOrchestrator] = (
        default_desktop_companion_orchestrator
    ),
    preflight_service_factory: Callable[[], DesktopCompanionPreflightService] = (
        default_desktop_companion_preflight_service
    ),
    build_identity_factory: Callable[[], DesktopCompanionBuildIdentity] = (
        resolve_desktop_companion_build_identity
    ),
    speech_canary_factory: Callable[[], bool] = desktop_companion_speech_canary_enabled,
    operational_status_factory: Callable[[], DesktopCompanionOperationalStatus] = (
        desktop_companion_operational_status
    ),
    chat_store_factory: Callable[[], ChatSessionStore] = default_chat_store,
    context_store_factory: Callable[[], DesktopCompanionContextStore] = (
        default_desktop_companion_context_store
    ),
    memory_bridge_factory: Callable[[], DesktopCompanionMemoryBridge] = (
        default_desktop_companion_memory_bridge
    ),
    activity_bridge_factory: Callable[[], DesktopCompanionActivityBridge] = (
        default_desktop_companion_activity_bridge
    ),
) -> None:
    @router.get(
        "/api/desktop-companion/operational-status",
        response_model=DesktopCompanionOperationalStatus,
        tags=["desktop-companion"],
    )
    def desktop_companion_operations() -> DesktopCompanionOperationalStatus:
        return operational_status_factory()

    @router.get(
        "/api/desktop-companion/build-identity",
        response_model=DesktopCompanionBuildIdentity,
        tags=["desktop-companion"],
    )
    def desktop_companion_build_identity() -> DesktopCompanionBuildIdentity:
        return build_identity_factory()

    @router.post(
        "/api/desktop-companion/preflight",
        response_model=DesktopCompanionPreflightResult,
        tags=["desktop-companion"],
    )
    def preflight_desktop_companion(
        request: DesktopCompanionPreflightRequest,
    ) -> DesktopCompanionPreflightResult:
        operations = operational_status_factory()
        if not operations.available:
            return DesktopCompanionPreflightResult(
                ready=False,
                model_id=request.vision_model_id,
                reason=operations.reason,
            )
        return preflight_service_factory().check(request)

    @router.post(
        "/api/desktop-companion/observe",
        response_model=DesktopCompanionObserveResponse,
        tags=["desktop-companion"],
    )
    def observe_desktop_companion(
        request: DesktopCompanionObserveRequest,
        background_tasks: BackgroundTasks,
    ) -> DesktopCompanionObserveResponse:
        operations = operational_status_factory()
        if not operations.available:
            return DesktopCompanionObserveResponse(status="suppressed", reason=operations.reason)

        identity = _resolve_authoritative_identity(request, chat_store_factory())
        if identity.status == "session_missing":
            return DesktopCompanionObserveResponse(
                status="suppressed",
                reason="authoritative_session_missing",
            )
        if identity.status == "character_missing":
            return DesktopCompanionObserveResponse(
                status="suppressed",
                reason="authoritative_character_missing",
            )

        authoritative_request = request.model_copy(
            update={"character_id": identity.character_id}
        )
        result = orchestrator_factory().observe(authoritative_request)
        if result.status == "completed" and result.observation is not None:
            context_store_factory().record(
                result.observation,
                scene_summary=result.scene_summary,
            )
            activity = activity_bridge_factory().record(result.observation)
            intent = activity.cognition.delivery_intent
            typed_summary = activity.activity_summary or result.scene_summary
            result = result.model_copy(
                update={
                    "scene_summary": typed_summary,
                    "activity_summary": activity.activity_summary,
                    "activity_intent": intent.kind,
                    "activity_grounding_ids": list(intent.grounding_proposition_ids),
                    "activity_confidence": intent.confidence,
                    "activity_salience": intent.salience,
                    "delivery_eligible": result.delivery_eligible and intent.kind != "IGNORE",
                }
            )
            background_tasks.add_task(memory_bridge_factory().record, result.observation)
        return result

    @router.get(
        "/api/desktop-companion/context",
        response_model=DesktopCompanionContextSnapshot | None,
        tags=["desktop-companion"],
    )
    def desktop_companion_context(
        session_id: str = Query(min_length=1, max_length=160),
    ) -> DesktopCompanionContextSnapshot | None:
        return context_store_factory().snapshot(session_id)

    @router.get(
        "/api/desktop-companion/activity",
        response_model=DesktopCompanionActivitySnapshot | None,
        tags=["desktop-companion"],
    )
    def desktop_companion_activity(
        session_id: str = Query(min_length=1, max_length=160),
    ) -> DesktopCompanionActivitySnapshot | None:
        return activity_bridge_factory().snapshot(session_id)

    @router.post(
        "/api/desktop-companion/reset",
        response_model=DesktopCompanionResetResponse,
        tags=["desktop-companion"],
    )
    def reset_desktop_companion(
        request: DesktopCompanionResetRequest,
    ) -> DesktopCompanionResetResponse:
        orchestrator_factory().reset(request.session_id, request.capture_generation)
        context_store_factory().clear(request.session_id)
        activity_bridge_factory().clear(request.session_id, request.capture_generation)
        return DesktopCompanionResetResponse(session_id=request.session_id)

    @router.post(
        "/api/desktop-companion/evaluations",
        response_model=DesktopCompanionEvaluationRecord,
        tags=["desktop-companion"],
    )
    def upsert_desktop_companion_evaluation(
        request: DesktopCompanionEvaluationCreate,
    ) -> DesktopCompanionEvaluationRecord:
        return evaluation_store_factory().upsert(request)

    @router.get(
        "/api/desktop-companion/evaluations",
        response_model=list[DesktopCompanionEvaluationRecord],
        tags=["desktop-companion"],
    )
    def list_desktop_companion_evaluations(
        limit: int = Query(default=100, ge=1, le=1_000),
        session_id: str | None = Query(default=None, max_length=160),
    ) -> list[DesktopCompanionEvaluationRecord]:
        return evaluation_store_factory().list(limit=limit, session_id=session_id)

    @router.get(
        "/api/desktop-companion/evaluations/export",
        response_model=DesktopCompanionEvaluationExport,
        tags=["desktop-companion"],
    )
    def export_desktop_companion_evaluations() -> DesktopCompanionEvaluationExport:
        return DesktopCompanionEvaluationExport.model_validate(
            evaluation_store_factory().export()
        )

    def evidence_partition(
        *,
        exact_commit_sha: str | None,
        observation_schema_version: int,
        attention_policy_version: int,
        vision_provider: str | None,
        vision_model_hash: str | None,
        remote_provider: bool | None,
    ) -> DesktopCompanionEvidencePartition:
        identity = build_identity_factory()
        return DesktopCompanionEvidencePartition(
            exact_commit_sha=exact_commit_sha or identity.exact_commit_sha,
            observation_schema_version=observation_schema_version,
            attention_policy_version=attention_policy_version,
            vision_provider=vision_provider,
            vision_model_hash=vision_model_hash,
            remote_provider=remote_provider,
        )

    @router.get(
        "/api/desktop-companion/release-gate",
        response_model=DesktopCompanionReleaseGateReport,
        tags=["desktop-companion"],
    )
    def desktop_companion_release_gate(
        stage: RolloutStage = "text",
        exact_commit_sha: str | None = Query(default=None, min_length=7, max_length=64),
        observation_schema_version: int = Query(default=1, ge=1),
        attention_policy_version: int = Query(default=1, ge=1),
        vision_provider: str | None = Query(default=None, max_length=80),
        vision_model_hash: str | None = Query(default=None, max_length=128),
        remote_provider: bool | None = Query(default=None),
        limit: int = Query(default=1_000, ge=1, le=5_000),
    ) -> DesktopCompanionReleaseGateReport:
        partition = evidence_partition(
            exact_commit_sha=exact_commit_sha,
            observation_schema_version=observation_schema_version,
            attention_policy_version=attention_policy_version,
            vision_provider=vision_provider,
            vision_model_hash=vision_model_hash,
            remote_provider=remote_provider,
        )
        records = evaluation_store_factory().list(limit=limit)
        if stage == "speech":
            return build_partitioned_desktop_companion_speech_gate(records, partition)
        return build_partitioned_desktop_companion_release_gate(records, partition)

    @router.get(
        "/api/desktop-companion/rollout-status",
        response_model=DesktopCompanionRolloutStatus,
        tags=["desktop-companion"],
    )
    def desktop_companion_rollout_status(
        requested_stage: RolloutStage = "disabled",
        exact_commit_sha: str | None = Query(default=None, min_length=7, max_length=64),
        observation_schema_version: int = Query(default=1, ge=1),
        attention_policy_version: int = Query(default=1, ge=1),
        vision_provider: str | None = Query(default=None, max_length=80),
        vision_model_hash: str | None = Query(default=None, max_length=128),
        remote_provider: bool | None = Query(default=None),
        limit: int = Query(default=1_000, ge=1, le=5_000),
    ) -> DesktopCompanionRolloutStatus:
        operations = operational_status_factory()
        if not operations.available:
            return DesktopCompanionRolloutStatus(
                requested_stage=requested_stage,
                effective_stage="disabled",
                enabled=False,
                reason=operations.reason,
                release_gate_status="insufficient",
                evidence_evaluation_ids=(),
            )
        partition = evidence_partition(
            exact_commit_sha=exact_commit_sha,
            observation_schema_version=observation_schema_version,
            attention_policy_version=attention_policy_version,
            vision_provider=vision_provider,
            vision_model_hash=vision_model_hash,
            remote_provider=remote_provider,
        )
        records = evaluation_store_factory().list(limit=limit)
        text_report = build_partitioned_desktop_companion_release_gate(records, partition)
        if requested_stage != "speech":
            return resolve_desktop_companion_rollout(requested_stage, text_report)
        if text_report.status != "pass":
            return resolve_desktop_companion_rollout(requested_stage, text_report)
        speech_report = build_partitioned_desktop_companion_speech_gate(records, partition)
        if speech_report.status == "pass":
            return DesktopCompanionRolloutStatus(
                requested_stage="speech",
                effective_stage="speech",
                enabled=True,
                reason="speech_rollout_gate_passed",
                release_gate_status="pass",
                evidence_evaluation_ids=speech_report.evidence_evaluation_ids,
            )
        if speech_canary_factory():
            return DesktopCompanionRolloutStatus(
                requested_stage="speech",
                effective_stage="speech",
                enabled=True,
                reason="speech_validation_canary",
                release_gate_status=speech_report.status,
                evidence_evaluation_ids=speech_report.evidence_evaluation_ids,
            )
        return DesktopCompanionRolloutStatus(
            requested_stage="speech",
            effective_stage="text",
            enabled=True,
            reason="speech_evidence_missing",
            release_gate_status=speech_report.status,
            evidence_evaluation_ids=text_report.evidence_evaluation_ids,
        )


__all__ = ["register_desktop_companion_routes"]
