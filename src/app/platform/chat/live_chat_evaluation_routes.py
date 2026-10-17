"""Hidden local APIs for durable content-free Live Chat evaluation."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.platform.chat.evaluation_store import (
    LiveChatEvaluationExport,
    LiveChatEvaluationStore,
    PresencePolicyVersion,
    PresencePolicyVersionCreate,
    VoiceSessionEvaluationCreate,
    VoiceSessionEvaluationRecord,
    default_live_chat_evaluation_store,
)
from .live_chat_release_aggregation import evaluate_durable_live_chat_records
from .live_chat_release_gate import (
    LiveChatReleaseGateEvaluationRequest,
    LiveChatReleaseGateReport,
    evaluate_live_chat_release_gate,
)


def create_live_chat_evaluation_router(
    *,
    store: LiveChatEvaluationStore | None = None,
) -> APIRouter:
    def get_store():
        return store if store is not None else default_live_chat_evaluation_store()
    router = APIRouter(prefix="/api/tts/live-call")

    @router.post(
        "/diagnostics/release-gate/v2/evaluate",
        response_model=LiveChatReleaseGateReport,
    )
    def evaluate_live_chat_release_gate_payload(
        request: LiveChatReleaseGateEvaluationRequest,
    ) -> LiveChatReleaseGateReport:
        return evaluate_live_chat_release_gate(
            request.metadata,
            request.events,
            thresholds=request.thresholds,
        )

    @router.post("/evaluations", response_model=VoiceSessionEvaluationRecord)
    def upsert_voice_session_evaluation(
        request: VoiceSessionEvaluationCreate,
    ) -> VoiceSessionEvaluationRecord:
        return get_store().upsert(request)

    @router.get("/evaluations", response_model=list[VoiceSessionEvaluationRecord])
    def list_voice_session_evaluations(
        session_id: str | None = None,
        presence_preset: str | None = None,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
    ) -> list[VoiceSessionEvaluationRecord]:
        if presence_preset not in {None, "quiet", "natural", "engaged", "listener"}:
            raise HTTPException(status_code=422, detail="unknown presence preset")
        return get_store().list(
            session_id=session_id,
            presence_preset=presence_preset,
            limit=limit,
        )

    @router.get("/evaluations/release-gate", response_model=LiveChatReleaseGateReport)
    def evaluate_durable_voice_session_evidence(
        limit: Annotated[int, Query(ge=1, le=1_000)] = 1_000,
        persist_status: bool = True,
    ) -> LiveChatReleaseGateReport:
        records = get_store().list(limit=limit)
        report = evaluate_durable_live_chat_records(records)
        if persist_status:
            for record in records:
                get_store().update_release_gate_status(record.evaluation_id, report.status)
        return report

    @router.get("/evaluations/export", response_model=LiveChatEvaluationExport)
    def export_voice_session_evaluations() -> LiveChatEvaluationExport:
        return LiveChatEvaluationExport.model_validate(get_store().export())

    @router.get("/evaluations/{evaluation_id}", response_model=VoiceSessionEvaluationRecord)
    def get_voice_session_evaluation(evaluation_id: str) -> VoiceSessionEvaluationRecord:
        record = get_store().get(evaluation_id)
        if record is None:
            raise HTTPException(status_code=404, detail="voice session evaluation not found")
        return record

    @router.get("/presence-presets", response_model=dict[str, PresencePolicyVersion])
    def active_presence_policies() -> dict[str, PresencePolicyVersion]:
        return get_store().active_policies()

    @router.get("/presence-presets/versions", response_model=list[PresencePolicyVersion])
    def list_presence_policy_versions(
        preset: str | None = None,
    ) -> list[PresencePolicyVersion]:
        if preset not in {None, "quiet", "natural", "engaged", "listener"}:
            raise HTTPException(status_code=422, detail="unknown presence preset")
        return get_store().list_policy_versions(preset)

    @router.post("/presence-presets/{preset}/versions", response_model=PresencePolicyVersion)
    def create_presence_policy_version(
        preset: str,
        request: PresencePolicyVersionCreate,
    ) -> PresencePolicyVersion:
        if preset not in {"quiet", "natural", "engaged", "listener"}:
            raise HTTPException(status_code=422, detail="unknown presence preset")
        try:
            return get_store().create_policy_version(preset, request)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/presence-presets/{preset}/activate/{version}", response_model=PresencePolicyVersion)
    def activate_presence_policy(preset: str, version: int) -> PresencePolicyVersion:
        if preset not in {"quiet", "natural", "engaged", "listener"}:
            raise HTTPException(status_code=422, detail="unknown presence preset")
        try:
            return get_store().activate_policy(preset, version)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/presence-presets/{preset}/rollback", response_model=PresencePolicyVersion)
    def rollback_presence_policy(preset: str) -> PresencePolicyVersion:
        if preset not in {"quiet", "natural", "engaged", "listener"}:
            raise HTTPException(status_code=422, detail="unknown presence preset")
        try:
            return get_store().rollback_policy(preset)
        except KeyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
