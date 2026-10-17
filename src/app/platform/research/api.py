"""Research's HTTP routes and its side of assistant-context chat (ADR-0016).

- Credential-safe routes for API-backed web research providers.
- Research status and deep-research plan routes (moved from chat in PA-1.3,
  same paths and permissions).
- ``ChatResearchAdapter``: research's implementation of chat's ``CHAT_RESEARCH``
  port. Chat never imports research, so chat works when research is off.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.errors import LegacyPersistenceRetired
from app.security import provider_secret_store as secret_store
from app.platform.chat.contracts import ResearchTurn, SendChatMessageResponse, link_user_message_to_research_job
from app.jobs import JobRecord

from .evidence import citation_labels, prepare_evidence_context_items, render_answer_with_compatibility_fallback, source_manifest_id
from .extraction import ReadablePageExtractor
from .jobs import DeepResearchJobInput, create_deep_research_job_request, start_research_job
from .planner import ResearchPlanner, ResearchPlanningBudget, ResearchPlanningRequest
from .policy import ResearchPolicy, research_policy_from_env
from .provider_chain import ProviderFallbackSearchClient, normalize_provider_chain
from .quick_search import QuickSearchService
from .release_policy import (
    ResearchReleaseDecision,
    ResearchReleasePolicy,
    research_release_availability,
    research_release_notice,
    research_release_policy_from_env,
    resolve_research_release,
)
from .settings import ResearchRuntimeSettings, load_research_runtime_settings
from .status import ResearchRuntimeStatus, research_runtime_status
from .web_search import WebSearchClient
from .contracts import RESEARCH_JOB_TYPE

_RESEARCH_CREDENTIAL_PROVIDERS = ("brave", "tavily")
_GET_ROUTE_NAME = "assistant_research_credentials_status_endpoint"
_UPDATE_ROUTE_NAME = "assistant_research_credentials_update_endpoint"


class ResearchCredentialUpdate(BaseModel):
    provider: Literal["brave", "tavily"]
    api_key: str = Field(default="", max_length=4096)


def _provider_status(provider: str) -> dict[str, object]:
    api_key = secret_store.load_research_provider_secrets().get(provider, "")
    source = secret_store.research_provider_credential_source(provider)
    return {
        "provider": provider,
        "configured": bool(api_key),
        "source": source,
        "editable": secret_store.research_provider_credential_editable(provider),
        "key_suffix": api_key[-4:] if api_key else None,
    }


def research_credentials_status() -> dict[str, object]:
    return {
        "providers": [_provider_status(provider) for provider in _RESEARCH_CREDENTIAL_PROVIDERS],
        "legacy_environment_key": any(
            secret_store.research_provider_credential_source(provider) == "legacy_environment"
            for provider in _RESEARCH_CREDENTIAL_PROVIDERS
        ),
    }


def create_research_credential_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/assistant/research/credentials",
        name=_GET_ROUTE_NAME,
    )
    def assistant_research_credentials_status_endpoint() -> dict[str, object]:
        return research_credentials_status()

    @router.post(
        "/api/assistant/research/credentials",
        name=_UPDATE_ROUTE_NAME,
    )
    def assistant_research_credentials_update_endpoint(
        request: ResearchCredentialUpdate,
    ) -> dict[str, object]:
        source = secret_store.research_provider_credential_source(request.provider)
        if source in {"environment", "legacy_environment"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "research_credential_environment_owned",
                    "provider": request.provider,
                    "source": source,
                },
            )
        try:
            secret_store.save_research_provider_secret(request.provider, request.api_key)
        except LegacyPersistenceRetired as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "research_credential_store_unavailable",
                    "provider": request.provider,
                    "message": str(exc),
                },
            ) from exc
        return research_credentials_status()

    return router


_STATUS_ROUTE_NAME = "assistant_research_runtime_status_endpoint"
_PLAN_UPDATE_ROUTE_NAME = "assistant_deep_research_plan_update_endpoint"
_PLAN_START_ROUTE_NAME = "assistant_deep_research_plan_start_endpoint"


class ChatResearchAdapter:
    """Research's implementation of chat's ``ChatResearch`` port."""

    def resolve_turn(
        self,
        request: Any,
        session_id: str,
        *,
        settings: ResearchRuntimeSettings | None = None,
        release_policy: ResearchReleasePolicy | None = None,
        policy: ResearchPolicy | None = None,
    ) -> ResearchTurn:
        settings = settings if settings is not None else load_research_runtime_settings()
        release_policy = release_policy if release_policy is not None else research_release_policy_from_env()
        decision = resolve_research_release(
            request.web_research_mode,
            settings,
            release_policy,
            identity=session_id,
            allow_downgrade=request.allow_research_downgrade,
        )
        if decision.status == "unavailable":
            availability = research_release_availability(settings, release_policy, identity=session_id)
            return _turn(decision, settings, policy, unavailable={
                "code": "research_mode_unavailable",
                "requested_mode": decision.requested_mode,
                "reason": decision.reason,
                "available_modes": [
                    mode
                    for mode, available in (
                        ("disabled", availability.disabled),
                        ("quick", availability.quick),
                        ("deep", availability.deep),
                    )
                    if available
                ],
                "downgrade_available": decision.requested_mode == "deep" and availability.quick,
            })
        request.web_research_mode = decision.effective_mode
        request.internal_research_warnings = [*request.internal_research_warnings, *decision.warnings]
        policy = policy if policy is not None else settings.policy
        request.internal_research_identity = session_id
        request.internal_research_provider = settings.effective_provider
        request.internal_research_provider_chain = list(settings.effective_provider_chain)
        request.internal_research_policy = {
            "search_cache_ttl_seconds": policy.search_cache_ttl_seconds,
            "extraction_cache_ttl_seconds": policy.extraction_cache_ttl_seconds,
            "raw_snapshot_retention_days": policy.raw_snapshot_retention_days,
            "source_manifest_retention_days": policy.source_manifest_retention_days,
            "planner_receives_conversation_history": False,
            "synthesis_receives_raw_page_bodies": False,
        }
        request.web_search_max_results = settings.max_results
        return _turn(decision, settings, policy)

    def begin_deep_research(
        self,
        session_id: str,
        request: Any,
        *,
        chat_store: Any,
        job_store: Any,
        turn: ResearchTurn,
        send_request: Any,
    ) -> SendChatMessageResponse:
        decision, settings, policy = turn.state
        research_provider = settings.effective_provider
        research_provider_chain = list(settings.effective_provider_chain)
        max_pages = _deep_research_page_limit(request, settings)
        appended = chat_store.begin_user_message(
            session_id,
            send_request,
            context_diagnostics={
                "web_research_mode": "deep",
                "web_search_status": "queued_as_durable_research_job",
                "research_provider": research_provider,
                "research_provider_chain": research_provider_chain,
                "research_requested_mode": decision.requested_mode,
                "research_effective_mode": decision.effective_mode,
                "research_release_status": decision.status,
                "research_release_reason": decision.reason,
                "research_compatibility_warnings": request.internal_research_warnings,
            },
        )
        if appended is None:
            raise HTTPException(status_code=404, detail="chat session not found")
        session, user_message = appended
        research_input = _with_research_plan(
            DeepResearchJobInput(
                session_id=session.id,
                user_message_id=user_message.id,
                question=request.content,
                provider_id=request.provider_id or session.provider_id,
                model_id=request.model_id or session.model_id,
                research_provider=research_provider,
                research_provider_chain=research_provider_chain,
                max_steps=settings.max_steps,
                max_queries=min(max_pages, settings.max_queries),
                max_sources=max_pages,
                max_extracts=min(max_pages, settings.max_extracts),
                search_cache_ttl_seconds=policy.search_cache_ttl_seconds,
                extraction_cache_ttl_seconds=policy.extraction_cache_ttl_seconds,
                hermes_planner_enabled=decision.use_hermes_planner,
                awaiting_plan_approval=True,
                metadata={
                    "agent_mode": request.agent_mode,
                    "dry_run": request.dry_run,
                    "diagnostics_enabled": settings.show_diagnostics,
                    "research_release": decision.model_dump(mode="json"),
                    "research_compatibility_warnings": request.internal_research_warnings,
                },
            )
        )
        job = job_store.create_job(create_deep_research_job_request(research_input))
        linked = link_user_message_to_research_job(chat_store, session.id, user_message.id, job.id)
        if linked is not None:
            session, user_message = linked
        return SendChatMessageResponse(session=session, user_message=user_message, job=job)

    def quick_context(
        self,
        request: Any,
        *,
        web_search_factory: Callable[..., Any] | None = None,
        quick_search_factory: Callable[[], QuickSearchService] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        service = (
            quick_search_factory()
            if quick_search_factory is not None
            else _quick_search_for(request, web_search_factory or WebSearchClient)
        )
        execution = service.search(
            request.content,
            request.web_search_max_results,
            identity=request.internal_research_identity or "anonymous",
        )
        items = prepare_evidence_context_items([item.model_dump(mode="json") for item in execution.items])
        diagnostics: dict[str, Any] = {f"web_search_{key}": value for key, value in execution.diagnostics.items()}
        diagnostics["web_search_warnings"] = execution.warnings
        return items, diagnostics

    def render_cited_reply(
        self, content: str, context_items: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]] | None:
        labels = citation_labels(context_items)
        if not labels:
            return None
        rendered = render_answer_with_compatibility_fallback(content, labels)
        return rendered.content, {
            "source_manifest_id": source_manifest_id(context_items),
            "citation_validation": rendered.validation.model_dump(mode="json"),
        }


def _turn(
    decision: ResearchReleaseDecision,
    settings: ResearchRuntimeSettings,
    policy: ResearchPolicy | None,
    *,
    unavailable: dict[str, Any] | None = None,
) -> ResearchTurn:
    return ResearchTurn(
        requested_mode=decision.requested_mode,
        effective_mode=decision.effective_mode,
        status=decision.status,
        reason=decision.reason,
        warnings=list(decision.warnings),
        release=decision.model_dump(mode="json"),
        notice=research_release_notice(decision),
        show_diagnostics=settings.show_diagnostics,
        unavailable=unavailable,
        state=(decision, settings, policy),
    )


def _quick_search_for(request: Any, web_search_factory: Callable[..., Any]) -> QuickSearchService:
    policy = (
        ResearchPolicy(**request.internal_research_policy)
        if request.internal_research_policy
        else research_policy_from_env()
    )
    provider_chain = normalize_provider_chain(
        request.internal_research_provider,
        request.internal_research_provider_chain,
    )

    def create_client(timeout_seconds: float):
        if len(provider_chain) > 1:
            return ProviderFallbackSearchClient(
                providers=provider_chain,
                timeout_seconds=timeout_seconds,
                client_factory=web_search_factory,
            )
        try:
            return web_search_factory(provider=provider_chain[0], timeout_seconds=timeout_seconds)
        except TypeError:
            try:
                return web_search_factory(timeout_seconds=timeout_seconds)
            except TypeError:
                return web_search_factory()

    return QuickSearchService(
        client_factory=create_client,
        research_policy=policy,
        extractor_factory=lambda: ReadablePageExtractor(research_policy=policy),
    )


def _deep_research_page_limit(request: Any, settings: ResearchRuntimeSettings) -> int:
    selected = request.deep_research_max_pages
    value = settings.max_sources if selected is None else selected
    return max(1, min(100, int(value)))


def _with_research_plan(input_payload: DeepResearchJobInput) -> DeepResearchJobInput:
    budget = ResearchPlanningBudget(
        max_steps=input_payload.max_steps,
        max_queries=input_payload.max_queries,
        max_sources=input_payload.max_sources,
        max_extracts=input_payload.max_extracts,
    )
    decision = ResearchPlanner(
        prefer_hermes=input_payload.hermes_planner_enabled,
        provider_id=input_payload.provider_id,
        model_id=input_payload.model_id,
        use_provider=True,
    ).plan(ResearchPlanningRequest(question=input_payload.question, budget=budget))
    metadata = {**input_payload.metadata, "planner_warnings": decision.warnings}
    return input_payload.model_copy(
        update={"research_plan": decision.plan, "planner_backend": decision.backend, "metadata": metadata}
    )


def _start_research_execution(job_store: Any, job: JobRecord) -> JobRecord:
    """Release durable research to the PostgreSQL worker, preserving local test compatibility."""
    from app.persistence.runtime import uses_postgresql_runtime

    if uses_postgresql_runtime():
        return job
    return start_research_job(job_store, job)


def _update_job_input(
    job_store: Any,
    job: JobRecord,
    input_payload: DeepResearchJobInput,
    *,
    compat: dict[str, Any] | None = None,
) -> JobRecord | None:
    update_awaiting_plan = getattr(job_store, "update_awaiting_plan_input", None)
    if callable(update_awaiting_plan):
        return update_awaiting_plan(job.id, input_payload.model_dump(mode="json"), compat=compat)
    update = getattr(job_store, "update_job_input", None)
    if not callable(update):
        return None
    return update(job.id, input_payload.model_dump(mode="json"), compat=compat)


class DeepResearchPlanUpdateRequest(BaseModel):
    max_pages: int = Field(ge=1, le=100)


def register_research_job_routes(
    router: APIRouter,
    *,
    job_store_factory: Callable[[], Any],
    settings_factory: Callable[[], ResearchRuntimeSettings] = load_research_runtime_settings,
    release_policy_factory: Callable[[], ResearchReleasePolicy] = research_release_policy_from_env,
) -> None:
    """Research status and deep-research plan routes (moved from chat; same paths and permissions)."""
    route_names = {getattr(route, "name", "") for route in router.routes}
    if _STATUS_ROUTE_NAME not in route_names:

        @router.get(
            "/api/assistant/research/status",
            response_model=ResearchRuntimeStatus,
            name=_STATUS_ROUTE_NAME,
        )
        def assistant_research_runtime_status_endpoint(
            session_id: str = "status-preview",
        ) -> ResearchRuntimeStatus:
            return research_runtime_status(
                settings_factory(),
                release_policy_factory(),
                identity=session_id,
            )

    if _PLAN_UPDATE_ROUTE_NAME not in route_names:

        @router.patch(
            "/api/assistant/context/research/jobs/{job_id}/plan",
            response_model=JobRecord,
            name=_PLAN_UPDATE_ROUTE_NAME,
        )
        def update_deep_research_plan_endpoint(
            job_id: str,
            request: DeepResearchPlanUpdateRequest,
        ) -> JobRecord:
            job_store = job_store_factory()
            job = job_store.get_job(job_id)
            if job is None or job.type != RESEARCH_JOB_TYPE:
                raise HTTPException(status_code=404, detail="deep research job not found")
            try:
                input_payload = DeepResearchJobInput.model_validate(job.input_payload or {})
            except Exception as exc:
                raise HTTPException(status_code=409, detail="deep research plan is invalid") from exc
            if not input_payload.awaiting_plan_approval:
                raise HTTPException(status_code=409, detail="deep research has already started")
            settings = settings_factory()
            updated_input = input_payload.model_copy(
                update={
                    "max_sources": request.max_pages,
                    "max_queries": min(request.max_pages, settings.max_queries),
                    "max_extracts": min(request.max_pages, settings.max_extracts),
                }
            )
            updated_input = _with_research_plan(updated_input)
            updated = _update_job_input(job_store, job, updated_input)
            if updated is None:
                raise HTTPException(status_code=409, detail="deep research plan could not be updated")
            return updated

    if _PLAN_START_ROUTE_NAME not in route_names:

        @router.post(
            "/api/assistant/context/research/jobs/{job_id}/start",
            response_model=JobRecord,
            name=_PLAN_START_ROUTE_NAME,
        )
        def start_deep_research_plan_endpoint(job_id: str) -> JobRecord:
            job_store = job_store_factory()
            job = job_store.get_job(job_id)
            if job is None or job.type != RESEARCH_JOB_TYPE:
                raise HTTPException(status_code=404, detail="deep research job not found")
            try:
                input_payload = DeepResearchJobInput.model_validate(job.input_payload or {})
            except Exception as exc:
                raise HTTPException(status_code=409, detail="deep research plan is invalid") from exc
            if not input_payload.awaiting_plan_approval:
                return _start_research_execution(job_store, job)
            approved_input = input_payload.model_copy(update={"awaiting_plan_approval": False})
            updated = _update_job_input(job_store, job, approved_input, compat=dict(job.compat))
            if updated is None:
                raise HTTPException(status_code=409, detail="deep research plan could not be started")
            return _start_research_execution(job_store, updated)


__all__ = [
    "ChatResearchAdapter",
    "DeepResearchPlanUpdateRequest",
    "create_research_credential_router",
    "register_research_job_routes",
]
