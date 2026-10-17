"""Backend-owned conversation research-mode persistence."""
from __future__ import annotations

from app.conversation.contracts import ResearchMode

from .models import ChatSession
from .store import ChatSessionStore


def update_conversation_research_mode(
    store: ChatSessionStore,
    session_id: str,
    mode: ResearchMode | None,
) -> ChatSession | None:
    """Persist a conversation override without changing message history."""

    session = store.get_session(session_id)
    if session is None:
        return None
    session.research_mode_override = mode
    store._save_session(session)  # noqa: SLF001 - one targeted session mutation
    return session
