"""Browser-facing routes for per-session Chat memory snapshots and management."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException

from app.conversation.contracts import ChatSessionMutationPort
from .session import (
    RefreshSessionMemoryRequest,
    SessionMemoryConflictError,
    SessionMemoryState,
    get_session_memory_state,
    refresh_session_memory,
)

from .management_routes import register_memory_management_routes
from .owner_defaults import default_memory_service
from .service import MemoryService
from .settings_routes import register_memory_settings_routes
from .settings import AssistantMemorySettingsStore

_GET_ROUTE_NAME = "assistant_memory_session_state_endpoint"
_REFRESH_ROUTE_NAME = "assistant_memory_session_refresh_endpoint"


def register_assistant_memory_routes(
    router: APIRouter,
    *,
    chat_store_factory: Callable[[], ChatSessionMutationPort] | None = None,
    memory_service_factory: Callable[[], MemoryService] = default_memory_service,
    memory_settings_store_factory: Callable[[], AssistantMemorySettingsStore] | None = None,
) -> None:
    def get_chat_store() -> ChatSessionMutationPort:
        if chat_store_factory is None:
            raise HTTPException(status_code=503, detail="Chat session storage is unavailable")
        return chat_store_factory()

    route_names = {getattr(route, "name", "") for route in router.routes}
    if _GET_ROUTE_NAME not in route_names:

        @router.get(
            "/api/chat/sessions/{session_id}/memory",
            response_model=SessionMemoryState,
            tags=["chat-memory"],
            name=_GET_ROUTE_NAME,
        )
        def assistant_memory_session_state_endpoint(
            session_id: str,
        ) -> SessionMemoryState:
            state = get_session_memory_state(
                get_chat_store(),
                memory_service_factory(),
                session_id,
            )
            if state is None:
                raise HTTPException(status_code=404, detail="chat session not found")
            return state

    if _REFRESH_ROUTE_NAME not in route_names:

        @router.post(
            "/api/chat/sessions/{session_id}/memory/refresh",
            response_model=SessionMemoryState,
            tags=["chat-memory"],
            name=_REFRESH_ROUTE_NAME,
        )
        def assistant_memory_session_refresh_endpoint(
            session_id: str,
            request: RefreshSessionMemoryRequest,
        ) -> SessionMemoryState:
            try:
                state = refresh_session_memory(
                    get_chat_store(),
                    memory_service_factory(),
                    session_id,
                    request,
                )
            except SessionMemoryConflictError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "memory_snapshot_revision_conflict", "message": str(exc)},
                ) from exc
            if state is None:
                raise HTTPException(status_code=404, detail="chat session not found")
            return state

    register_memory_settings_routes(
        router,
        settings_store_factory=memory_settings_store_factory,
    )
    register_memory_management_routes(
        router,
        chat_store_factory=get_chat_store,
        memory_service_factory=memory_service_factory,
    )
