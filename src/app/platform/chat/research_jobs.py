"""Chat persistence helpers for durable research jobs."""
from __future__ import annotations

from .models import ChatMessage, ChatSession
from .store import ChatSessionStore


def link_user_message_to_research_job(
    store: ChatSessionStore,
    session_id: str,
    message_id: str,
    job_id: str,
) -> tuple[ChatSession, ChatMessage] | None:
    metadata = {
        "research_mode": "deep",
        "research_status": "queued",
        "research_job_id": job_id,
    }
    targeted_update = getattr(store, "update_user_message_metadata", None)
    if callable(targeted_update):
        changed = targeted_update(
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
        )
        if not changed:
            return None
        session = store.get_session(session_id)
        if session is None:
            return None
        message = next((value for value in session.messages if value.id == message_id), None)
        return (session, message) if message is not None else None

    session = store.get_session(session_id)
    if session is None:
        return None
    message = next((value for value in session.messages if value.id == message_id), None)
    if message is None:
        return None
    message.metadata.update(metadata)
    store._save_session(session)  # noqa: SLF001 - one targeted session mutation
    return session, message
