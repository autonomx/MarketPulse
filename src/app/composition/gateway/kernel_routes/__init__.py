"""Kernel-owned browser routes composed into an APIRouter."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from app.assets import AssetType
from app.platform.assistant_tools import (
    AssistantToolRegistryPayload,
    assistant_tool_registry_payload,
)
from app.jobs import JobRecord, ModelResidencyDiagnostics, ModelResidencyRecord
from app.composition.gateway.diagnostics import (
    DiagnosticsPayload,
)
from app.platform.chat.legacy_session_api import (
    LegacyGenerateTitleRequest,
    LegacyGenerateTitleResponse,
    LegacySessionCreateResponse,
    LegacySessionListResponse,
    LegacySessionResponse,
    LegacySessionUpdateRequest,
    LegacySuccessResponse,
    create_legacy_session,
    delete_legacy_session,
    generate_legacy_session_title,
    get_legacy_session,
    list_legacy_sessions,
    update_legacy_session,
)
from app.observability.reports import (
    ReportListResponse,
    list_report_artifacts,
)
from app.composition.gateway.settings_control import (
    SettingsPayload,
    SettingsSaveResponse,
    get_settings_payload,
    save_settings_payload,
)
from app.prompts import (
    PromptRenderError,
    PromptRenderRequest,
    PromptTemplateRenderer,
    RenderedPrompt,
)
from app.providers.cache_status import (
    ProviderModelRefreshRequest,
    create_provider_model_refresh_job_request,
)
from app.providers.chatgpt_codex_provider import ChatGPTCodexProvider
from app.providers.facade import ProviderFacadePayload
from app.apps.rpg.edge.replay import (
    CheckpointBundleRequest,
    CheckpointEnvelope,
    PersistenceInventory,
    ReplayPrimitiveList,
    StateHashRequest,
    StateHashResponse,
)
from app.runtime.worker_health import (
    WorkerHealthPayload,
    WorkerPayloadPolicy,
    get_worker_health_payload,
    get_worker_payload_policy,
)
from app.settings.api import settings_payload, save_settings_patch
from app.settings.models import SettingsProfileSaveRequest
from app.persistence.document_store import DocumentRevisionConflict
from app.settings.service import SettingRevisionConflict, SettingsPatch
from app.settings.access import load_settings
from app.settings.profile_repository import load_settings_profile
from app.platform.voice.legacy_clone_files import delete_legacy_voice_clone_files

from .core_assets_routes import _asset_by_id
from ..schemas import (
    CodexAuthStatus,
    CompatibilityHandoffPayload,
    GatewayHealth,
    GatewayReadinessPayload,
    RuntimeStatusPayload,
)


def create_kernel_router(
    state,
    *,
    readiness_check,
    get_job_store,
    get_provider_facade,
    get_asset_store,
    get_chat_store,
    get_replay_adapter,
    get_model_residency_store,
    allow_offline_model_residency_store: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", response_model=GatewayHealth, tags=["gateway"])
    def health() -> GatewayHealth:
        return GatewayHealth()

    @router.get("/api/health", response_model=GatewayHealth, tags=["gateway"])
    def api_health() -> GatewayHealth:
        return GatewayHealth()

    from .client_errors_routes import register_client_error_routes

    register_client_error_routes(router)

    @router.get("/ready", response_model=GatewayReadinessPayload, tags=["gateway"])
    def readiness() -> JSONResponse:
        from app.runtime.drain import process_drain

        if process_drain().draining:
            runtime_config = getattr(state, "runtime_config", None)
            return JSONResponse(
                {
                    "ready": False,
                    "reason": "draining",
                    "build_revision": getattr(runtime_config, "build_revision", None),
                },
                status_code=503,
            )
        if not state.runtime_started or readiness_check is None:
            return JSONResponse(
                {"ready": False, "reason": "runtime_not_started"}, status_code=503
            )
        try:
            payload = readiness_check()
        except Exception:
            # Database errors can include connection credentials and SQL details.
            return JSONResponse(
                {"ready": False, "reason": "persistence_unavailable"}, status_code=503
            )
        return JSONResponse(
            payload, status_code=200 if payload.get("ready") is True else 503
        )

    @router.get(
        "/api/runtime/status", response_model=RuntimeStatusPayload, tags=["runtime"]
    )
    def runtime_status() -> RuntimeStatusPayload:
        return _runtime_status()

    @router.get(
        "/api/workers/health", response_model=WorkerHealthPayload, tags=["workers"]
    )
    def worker_health() -> WorkerHealthPayload:
        return get_worker_health_payload()

    @router.get(
        "/api/workers/payload-policy",
        response_model=WorkerPayloadPolicy,
        tags=["workers"],
    )
    def worker_payload_policy() -> WorkerPayloadPolicy:
        return get_worker_payload_policy()

    @router.get(
        "/api/compatibility/legacy",
        response_model=CompatibilityHandoffPayload,
        tags=["compatibility"],
    )
    def compatibility_handoff() -> CompatibilityHandoffPayload:
        return _compatibility_handoff()

    @router.get(
        "/api/assistant/tools",
        response_model=AssistantToolRegistryPayload,
        tags=["assistant-tools"],
    )
    def assistant_tools() -> AssistantToolRegistryPayload:
        return assistant_tool_registry_payload()

    from .core_chat_routes import register_core_chat_routes

    register_core_chat_routes(
        router, get_chat_store=get_chat_store, get_job_store=get_job_store
    )

    @router.get(
        "/api/providers", response_model=ProviderFacadePayload, tags=["providers"]
    )
    async def providers() -> ProviderFacadePayload:
        return await asyncio.to_thread(lambda: get_provider_facade().payload())

    @router.get(
        "/api/models", response_model=ProviderFacadePayload, tags=["providers"]
    )
    async def models() -> ProviderFacadePayload:
        return await asyncio.to_thread(lambda: get_provider_facade().payload())

    @router.get(
        "/api/providers/chatgpt-codex/auth",
        response_model=CodexAuthStatus,
        tags=["providers"],
    )
    def chatgpt_codex_auth_status() -> CodexAuthStatus:
        return CodexAuthStatus.model_validate(ChatGPTCodexProvider.auth_status(_configured_codex_path()))

    @router.post(
        "/api/providers/chatgpt-codex/login",
        response_model=CodexAuthStatus,
        tags=["providers"],
    )
    def chatgpt_codex_login() -> CodexAuthStatus:
        return CodexAuthStatus.model_validate(ChatGPTCodexProvider.start_login(_configured_codex_path()))

    @router.post(
        "/api/providers/refresh", response_model=JobRecord, tags=["providers"]
    )
    def refresh_providers(request: ProviderModelRefreshRequest) -> JobRecord:
        return get_job_store().create_job(
            create_provider_model_refresh_job_request(request)
        )

    @router.post("/api/models/refresh", response_model=JobRecord, tags=["providers"])
    def refresh_models(request: ProviderModelRefreshRequest) -> JobRecord:
        return get_job_store().create_job(
            create_provider_model_refresh_job_request(request)
        )

    @router.get("/api/settings", response_model=SettingsPayload, tags=["settings"])
    def settings() -> SettingsPayload:
        service = getattr(getattr(state, "runtime_services", None), "settings", None)
        return settings_payload(service)

    @router.post(
        "/api/settings", response_model=SettingsSaveResponse, tags=["settings"]
    )
    def save_settings(request: SettingsPatch) -> SettingsSaveResponse:
        service = getattr(getattr(state, "runtime_services", None), "settings", None)
        if service is None:
            raise HTTPException(status_code=503, detail="settings_service_unavailable")
        try:
            return save_settings_patch(service, request)
        except SettingRevisionConflict as exc:
            raise HTTPException(status_code=409, detail="settings_revision_conflict") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # The Settings Control Center works on the whole profile document, with API
    # keys masked on read and routed to the secret store on save.
    @router.get("/api/settings/profile", response_model=SettingsPayload, tags=["settings"])
    def settings_profile() -> SettingsPayload:
        return get_settings_payload()

    @router.post("/api/settings/profile", response_model=SettingsSaveResponse, tags=["settings"])
    def save_settings_profile(request: SettingsProfileSaveRequest) -> SettingsSaveResponse:
        return save_settings_payload(request.model_dump(by_alias=True, exclude_none=True))

    @router.get(
        "/api/sessions",
        response_model=LegacySessionListResponse,
        tags=["legacy-sessions"],
    )
    def legacy_sessions() -> LegacySessionListResponse:
        return list_legacy_sessions()

    @router.post(
        "/api/sessions",
        response_model=LegacySessionCreateResponse,
        tags=["legacy-sessions"],
    )
    def create_session() -> LegacySessionCreateResponse:
        return create_legacy_session()

    @router.post(
        "/api/sessions/generate-title",
        response_model=LegacyGenerateTitleResponse,
        tags=["legacy-sessions"],
    )
    def generate_session_title(
        request: LegacyGenerateTitleRequest,
    ) -> LegacyGenerateTitleResponse:
        return generate_legacy_session_title(request)

    @router.get(
        "/api/sessions/{session_id}",
        response_model=LegacySessionResponse,
        tags=["legacy-sessions"],
    )
    def legacy_session(session_id: str) -> LegacySessionResponse:
        session = get_legacy_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Not found")
        return session

    @router.put(
        "/api/sessions/{session_id}",
        response_model=LegacySuccessResponse,
        tags=["legacy-sessions"],
    )
    def update_session(
        session_id: str, request: LegacySessionUpdateRequest
    ) -> LegacySuccessResponse:
        try:
            result = update_legacy_session(session_id, request)
        except DocumentRevisionConflict as exc:
            raise HTTPException(status_code=409, detail="legacy_session_changed") from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Not found")
        return result

    @router.delete(
        "/api/sessions/{session_id}",
        response_model=LegacySuccessResponse,
        tags=["legacy-sessions"],
    )
    def delete_session(session_id: str) -> LegacySuccessResponse:
        result = delete_legacy_session(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Not found")
        return result

    @router.get("/api/reports", response_model=ReportListResponse, tags=["reports"])
    def reports() -> ReportListResponse:
        return list_report_artifacts()

    @router.get(
        "/api/diagnostics", response_model=DiagnosticsPayload, tags=["diagnostics"]
    )
    def diagnostics() -> DiagnosticsPayload:
        from app.composition.gateway.diagnostics import get_runtime_diagnostics_payload
        return get_runtime_diagnostics_payload(
            state, model_residency_store_factory=get_model_residency_store,
            allow_offline_store=allow_offline_model_residency_store,
        )

    @router.get("/metrics", response_class=Response, tags=["diagnostics"])
    def metrics() -> Response:
        """Prometheus metrics (WP-10.3); needs ``admin:metrics``."""
        from app.observability.metrics import (
            CapacityCollector, DurableStateCollector, PoolCollector, SchedulerCollector, TtsStreamCollector,
            exposition,
        )
        from app.persistence.device_permits import default_device_permit_service

        services = getattr(state, "runtime_services", None)
        database = getattr(getattr(services, "jobs", None), "database", None)
        collectors: list[object] = []
        tts_snapshot = getattr(state, "tts_stream_snapshot", None)
        if callable(tts_snapshot):
            collectors.append(TtsStreamCollector(tts_snapshot))
        scheduler = getattr(state, "scheduler_runtime", None)
        if scheduler is not None:
            collectors.append(SchedulerCollector(scheduler.diagnostics))
        permits = default_device_permit_service()
        if permits is not None:
            collectors.append(CapacityCollector(permits.metrics_snapshot))
        if database is not None:
            # In-memory runtimes (tests, benchmarks) have no pool or durable queue.
            from app.composition.gateway.runtime_diagnostics import durable_metrics_snapshot

            collectors.append(PoolCollector(database.pool_statistics))
            collectors.append(DurableStateCollector(lambda: durable_metrics_snapshot(services)))
        body, content_type = exposition(*collectors)
        return Response(body, media_type=content_type)

    @router.get(
        "/api/model-residency",
        response_model=ModelResidencyDiagnostics,
        tags=["models"],
    )
    def model_residency() -> ModelResidencyDiagnostics:
        return get_model_residency_store().diagnostics()

    @router.post(
        "/api/model-residency",
        response_model=ModelResidencyDiagnostics,
        tags=["models"],
    )
    def report_model_residency(
        record: ModelResidencyRecord,
    ) -> ModelResidencyDiagnostics:
        store = get_model_residency_store()
        store.upsert_record(record)
        return store.diagnostics()

    @router.delete(
        "/api/model-residency/{model_id}",
        response_model=ModelResidencyDiagnostics,
        tags=["models"],
    )
    def delete_model_residency(model_id: str) -> ModelResidencyDiagnostics:
        store = get_model_residency_store()
        store.delete_record(model_id)
        return store.diagnostics()

    from .core_assets_routes import register_core_assets_routes

    register_core_assets_routes(router, get_asset_store=get_asset_store)

    @router.delete("/api/voice-cloning/assets/{asset_id}")
    def delete_voice_clone_asset(asset_id: str) -> dict[str, Any]:
        store = get_asset_store()
        asset = _asset_by_id(store, asset_id)
        if asset is None and asset_id.startswith("voice-cloning:"):
            from app.assets.canonical_voice_clones import (
                discover_canonical_voice_clone_assets,
            )

            asset = next(
                (
                    candidate
                    for candidate in discover_canonical_voice_clone_assets()
                    if candidate.id == asset_id
                ),
                None,
            )
        if asset is None:
            raise HTTPException(status_code=404, detail="asset_not_found")
        if asset.type != AssetType.VOICE_PROFILE or asset.module != "voice-cloning":
            raise HTTPException(status_code=409, detail="asset_not_voice_clone")
        shared_result = store.delete_asset(asset_id)
        legacy_result = delete_legacy_voice_clone_files(asset)
        deleted = (
            bool(shared_result.get("deleted"))
            or bool(legacy_result.get("manifest_deleted"))
            or bool(legacy_result.get("file_deleted"))
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="asset_not_deletable")
        return {
            "ok": True,
            "asset_id": asset_id,
            "deleted": True,
            "file_deleted": bool(shared_result.get("file_deleted"))
            or bool(legacy_result.get("file_deleted")),
        }

    @router.post(
        "/api/prompts/render", response_model=RenderedPrompt, tags=["prompts"]
    )
    def render_prompt(request: PromptRenderRequest) -> RenderedPrompt:
        try:
            return PromptTemplateRenderer().render(request)
        except PromptRenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/api/replay/primitives", response_model=ReplayPrimitiveList, tags=["replay"]
    )
    def replay_primitives() -> ReplayPrimitiveList:
        return get_replay_adapter().list_primitives()

    @router.post(
        "/api/replay/state-hash", response_model=StateHashResponse, tags=["replay"]
    )
    def replay_state_hash(request: StateHashRequest) -> StateHashResponse:
        return get_replay_adapter().state_hash(request.state)

    @router.post(
        "/api/replay/checkpoints", response_model=CheckpointEnvelope, tags=["replay"]
    )
    def replay_checkpoint(bundle: CheckpointBundleRequest) -> CheckpointEnvelope:
        return get_replay_adapter().create_checkpoint(
            bundle.model_dump(exclude_unset=True)
        )

    @router.get(
        "/api/replay/persistence/inventory",
        response_model=PersistenceInventory,
        tags=["replay"],
    )
    def replay_persistence_inventory() -> PersistenceInventory:
        return get_replay_adapter().list_sessions()

    from .core_jobs_routes import register_core_jobs_routes

    register_core_jobs_routes(
        router, state, get_chat_store=get_chat_store, get_job_store=get_job_store
    )

    return router


def _configured_codex_path() -> str:
    profile = load_settings_profile(load_settings())
    return profile.provider_configs.chatgpt_codex.codex_path


def _compatibility_handoff() -> CompatibilityHandoffPayload:
    return CompatibilityHandoffPayload(
        handoff_targets=[
            {
                "namespace": "/api/rpg",
                "current_owner": "app.composition.gateway RPG routes",
                "gateway_phase": "current",
            },
            {
                "namespace": "/api/image-generation",
                "current_owner": "app.composition.gateway image workspace and image service",
                "gateway_phase": "current",
            },
            {
                "namespace": "/api/jobs",
                "current_owner": "app.jobs feature execution and speech workers",
                "gateway_phase": "current",
            },
            {
                "namespace": "/api/assets/{asset_id}/file",
                "current_owner": "app.composition.gateway.image_asset_routes",
                "gateway_phase": "current",
            },
        ]
    )


def _runtime_status() -> RuntimeStatusPayload:
    workers = get_worker_health_payload()
    return RuntimeStatusPayload(
        ok=workers.ok,
        status="ready" if workers.ok else "degraded",
        gateway=GatewayHealth(),
        workers=workers,
        compatibility={
            "legacy_ui_status": "retired",
            "existing_fastapi_app": "app.composition.gateway.main:app",
            "domain_logic_policy": "delegate_to_existing_service_modules",
        },
    )
