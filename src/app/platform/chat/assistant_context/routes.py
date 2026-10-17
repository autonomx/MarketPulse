"""Browser-facing assistant context routes.

Research reaches these routes only through chat's ``CHAT_RESEARCH`` port
(ADR-0016); without the research feature every turn runs with research
disabled, and quick or deep requests are refused as unavailable.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.platform.chat import ChatSessionStore, SendChatMessageRequest, SendChatMessageResponse, default_chat_store
from app.platform.chat.contracts import CHAT_RESEARCH, ResearchTurn
from app.platform.chat.generation_jobs import start_chat_generation_job
from app.platform.chat.research_citations import validate_completed_research_reply
from app.platform.chat.research_release import apply_research_release_decision
from app.jobs import default_job_store
from app.runtime.ports import optional

from .models import AssistantContextChatRequest
from .service import AssistantContextService, default_assistant_context_service

_ROUTE_NAME = "assistant_context_chat_message_endpoint"
_STREAM_ROUTE_NAME = "assistant_context_stream_chat_message_endpoint"


def _bound_research():
    return optional(CHAT_RESEARCH)


def _resolve_turn(
    research: Any,
    request: AssistantContextChatRequest,
    session_id: str,
    *,
    settings_factory: Callable[[], Any] | None,
    release_policy_factory: Callable[[], Any] | None,
    policy_factory: Callable[[], Any] | None,
) -> ResearchTurn:
    if research is None:
        requested = request.web_research_mode
        if requested != "disabled":
            return ResearchTurn(
                requested_mode=requested,
                effective_mode="disabled",
                status="unavailable",
                reason="research_feature_disabled",
                unavailable={
                    "code": "research_mode_unavailable",
                    "requested_mode": requested,
                    "reason": "research_feature_disabled",
                    "available_modes": ["disabled"],
                    "downgrade_available": False,
                },
            )
        return ResearchTurn(
            requested_mode="disabled",
            effective_mode="disabled",
            status="allowed",
            reason="research_disabled_for_turn",
            release={
                "requested_mode": "disabled",
                "effective_mode": "disabled",
                "status": "allowed",
                "reason": "research_disabled_for_turn",
                "warnings": [],
                "use_hermes_planner": False,
            },
        )
    return research.resolve_turn(
        request,
        session_id,
        settings=settings_factory() if settings_factory is not None else None,
        release_policy=release_policy_factory() if release_policy_factory is not None else None,
        policy=policy_factory() if policy_factory is not None else None,
    )


def _release_diagnostics(turn: ResearchTurn) -> dict[str, Any]:
    return {
        "research_requested_mode": turn.requested_mode,
        "research_effective_mode": turn.effective_mode,
        "research_release_status": turn.status,
        "research_release_reason": turn.reason,
        "research_release_warnings": list(turn.warnings),
    }


def register_assistant_context_routes(
    router: APIRouter,
    *,
    chat_store_factory: Callable[[], ChatSessionStore] = default_chat_store,
    job_store_factory: Callable[[], Any] = default_job_store,
    context_service_factory: Callable[[], AssistantContextService] = default_assistant_context_service,
    policy_factory: Callable[[], Any] | None = None,
    settings_factory: Callable[[], Any] | None = None,
    release_policy_factory: Callable[[], Any] | None = None,
    research_factory: Callable[[], Any] = _bound_research,
) -> None:
    route_names = {getattr(route, "name", "") for route in router.routes}
    if _ROUTE_NAME in route_names:
        return

    @router.post(
        "/api/assistant/context/chat/sessions/{session_id}/messages",
        response_model=SendChatMessageResponse,
        name=_ROUTE_NAME,
    )
    def assistant_context_chat_message_endpoint(
        session_id: str,
        request: AssistantContextChatRequest,
    ) -> SendChatMessageResponse:
        research = research_factory()
        turn = _resolve_turn(
            research, request, session_id,
            settings_factory=settings_factory,
            release_policy_factory=release_policy_factory,
            policy_factory=policy_factory,
        )
        if turn.unavailable is not None:
            raise HTTPException(status_code=409, detail=turn.unavailable)
        if research is None:
            request.web_research_mode = "disabled"
        if request.web_research_mode == "deep":
            return research.begin_deep_research(
                session_id,
                request,
                chat_store=chat_store_factory(),
                job_store=job_store_factory(),
                turn=turn,
                send_request=_send_request(request),
            )

        chat_store = chat_store_factory()
        send_request = _send_request(request)
        queued_context_diagnostics = {
            "web_research_mode": request.web_research_mode,
            "context_status": "queued_for_chat_generation",
            **_release_diagnostics(turn),
        }
        job_store = job_store_factory()
        # Imported on first use: admission is not needed to compose the gateway.
        from app.platform.chat.admission import admit_chat_turn_for_http

        admission = admit_chat_turn_for_http(
            chat_store,
            job_store,
            session_id,
            send_request,
            begin_user_message=lambda turn_session_id, turn_request: chat_store.begin_user_message(
                turn_session_id, turn_request, context_diagnostics=queued_context_diagnostics,
            ),
            job_payload={
                "context_status": "queued",
                "research_release": turn.release,
                "research_compatibility_warnings": request.internal_research_warnings,
            },
            contract="assistant_context_chat_v1",
        )
        session, user_message, job = admission.session, admission.user_message, admission.job
        if admission.existing:
            return SendChatMessageResponse(session=session, user_message=user_message, job=job)

        def build_context() -> tuple[list[dict[str, Any]], dict[str, Any]]:
            context = context_service_factory().build(request)
            context_items = [item.model_dump(mode="json") for item in context.items]
            return context_items, {**context.diagnostics, **_release_diagnostics(turn)}

        def finalize_context_chat(
            store: Any,
            completed_session_id: str,
            completed_message_id: str,
            context_items: list[dict[str, Any]],
            _context_diagnostics: dict[str, Any],
        ) -> None:
            if research is not None:
                validate_completed_research_reply(
                    store,
                    completed_session_id,
                    completed_message_id,
                    context_items,
                    render=research.render_cited_reply,
                    show_diagnostics=turn.show_diagnostics,
                )
            apply_research_release_decision(
                store,
                completed_session_id,
                completed_message_id,
                turn,
            )

        job = start_chat_generation_job(
            chat_store=chat_store,
            job_store=job_store,
            job=job,
            request=send_request,
            context_builder=build_context,
            completion_hook=finalize_context_chat,
        )
        return SendChatMessageResponse(session=session, user_message=user_message, job=job)

    @router.post(
        "/api/assistant/context/chat/sessions/{session_id}/messages/stream",
        response_model=None,
        responses={
            200: {
                "description": "Context-assembled chat generation events as Server-Sent Events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
        name=_STREAM_ROUTE_NAME,
    )
    async def assistant_context_stream_chat_message_endpoint(
        session_id: str,
        request: AssistantContextChatRequest,
    ) -> StreamingResponse:
        research = research_factory()
        turn = _resolve_turn(
            research, request, session_id,
            settings_factory=settings_factory,
            release_policy_factory=release_policy_factory,
            policy_factory=policy_factory,
        )
        if turn.unavailable is not None:
            raise HTTPException(status_code=409, detail=turn.unavailable)
        if research is None:
            request.web_research_mode = "disabled"
        chat_store = chat_store_factory()
        if request.web_research_mode == "deep":
            response = research.begin_deep_research(
                session_id,
                request,
                chat_store=chat_store,
                job_store=job_store_factory(),
                turn=turn,
                send_request=_send_request(request),
            )

            def generate_deep_research_ack():
                yield _sse({"type": "user_message", "message": response.user_message.model_dump(mode="json")})
                yield _sse({"type": "session", "session": response.session.model_dump(mode="json")})
                yield _sse({"type": "done"})

            return StreamingResponse(generate_deep_research_ack(), media_type="text/event-stream")

        # Imported on first use: admission is not needed to compose the gateway.
        from app.platform.chat.admission import admit_chat_turn_for_http, stream_chat_turn
        from app.platform.chat.generation_jobs import find_chat_generation_job

        job_store = job_store_factory()
        send_request = _send_request(request)
        context_items: list[dict[str, Any]] = []
        context_sources: list[str] = []
        context_diagnostics: dict[str, Any] = {}
        # A repeated submission returns its accepted turn; do not research again.
        if await asyncio.to_thread(
            find_chat_generation_job, job_store, session_id=session_id, submission_id=send_request.user_turn_id,
        ) is None:
            context = await asyncio.to_thread(context_service_factory().build, request)
            context_items = [item.model_dump(mode="json") for item in context.items]
            context_sources = [item.source_id for item in context.items]
            context_diagnostics = {**context.diagnostics, **_release_diagnostics(turn)}
        admission = await asyncio.to_thread(
            admit_chat_turn_for_http,
            chat_store,
            job_store,
            session_id,
            send_request,
            begin_user_message=lambda turn_session_id, turn_request: chat_store.begin_user_message(
                turn_session_id,
                turn_request,
                context_items=context_items,
                context_diagnostics=context_diagnostics,
            ),
            job_payload={
                "research_release": turn.release,
                "research_compatibility_warnings": request.internal_research_warnings,
            },
            contract="assistant_context_chat_v1",
        )

        def annotate_reply(metadata: dict[str, Any]) -> None:
            if context_sources:
                metadata["context_sources"] = context_sources
            metadata["context_diagnostics"] = context_diagnostics

        def generate():
            yield _sse({"type": "user_message", "message": admission.user_message.model_dump(mode="json")})
            yield _sse({"type": "job", "job": admission.job.model_dump(mode="json")})
            if admission.existing:
                # A repeated submission reports the turn it already started.
                yield _sse({"type": "session", "session": admission.session.model_dump(mode="json")})
                yield _sse({"type": "done"})
                return
            try:
                for event in stream_chat_turn(
                    chat_store,
                    job_store,
                    admission,
                    send_request,
                    context_items=context_items,
                    annotate_reply=annotate_reply,
                ):
                    yield _sse(event)
                    if event.get("type") == "interrupted":
                        return
                yield _sse({"type": "done"})
            except Exception as exc:
                yield _sse({"type": "error", "message": str(exc) or "Chat stream failed."})

        return StreamingResponse(generate(), media_type="text/event-stream")



def _send_request(request: AssistantContextChatRequest) -> SendChatMessageRequest:
    return SendChatMessageRequest(
        content=request.content,
        user_turn_id=request.user_turn_id,
        image_data_url=request.image_data_url,
        image_data_urls=request.image_data_urls,
        text_attachment=request.text_attachment,
        provider_id=request.provider_id,
        model_id=request.model_id,
        agent_mode=request.agent_mode,
        coding_approval_policy=request.coding_approval_policy,
        dry_run=request.dry_run,
        workspace_root=request.workspace_root,
        research_mode=request.web_research_mode,
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, sort_keys=True)}\n\n"
