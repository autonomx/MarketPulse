"""Feature-owned conversation research-mode routes."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from .models import UpdateChatResearchModeRequest
from .research_mode import update_conversation_research_mode


def create_research_mode_router(*, chat_store_factory: Callable[[], Any]) -> APIRouter:
    router = APIRouter()

    @router.post("/api/chat/sessions/{session_id}/research-mode")
    def set_conversation_research_mode(
        session_id: str,
        request: UpdateChatResearchModeRequest,
    ) -> dict[str, Any]:
        session = update_conversation_research_mode(
            chat_store_factory(),
            session_id,
            request.research_mode_override,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="chat_session_not_found")
        return {
            "ok": True,
            "session_id": session.id,
            "research_mode_override": session.research_mode_override,
        }

    return router
