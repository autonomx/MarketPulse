"""Persist visible research release and downgrade metadata on chat replies."""
from __future__ import annotations

from typing import Any

from .concurrency import serialized_chat_mutation
from .models import ChatSession
from .store import ChatSessionStore


@serialized_chat_mutation
def apply_research_release_decision(
    store: ChatSessionStore,
    session_id: str,
    user_message_id: str,
    decision: Any,
) -> ChatSession | None:
    session = store.get_session(session_id)
    if session is None:
        return None
    assistant = next(
        (
            message
            for message in session.messages
            if message.role == "assistant"
            and message.metadata.get("reply_to_message_id") == user_message_id
        ),
        None,
    )
    if assistant is None:
        user_index = next(
            (
                index
                for index, message in enumerate(session.messages)
                if message.id == user_message_id
            ),
            None,
        )
        if user_index is None:
            return session
        assistant = next(
            (
                message
                for message in session.messages[user_index + 1 :]
                if message.role == "assistant"
            ),
            None,
        )
    if assistant is None:
        return session
    notice = getattr(decision, "notice", None)
    if notice and notice not in assistant.content:
        assistant.content = f"{assistant.content}\n\n> Research mode notice: {notice}".strip()
    assistant.metadata.update(
        {
            "research_requested_mode": decision.requested_mode,
            "research_effective_mode": decision.effective_mode,
            "research_release_status": decision.status,
            "research_release_reason": decision.reason,
            "research_release_warnings": list(decision.warnings),
        }
    )
    store._save_session(session)  # noqa: SLF001 - one targeted session mutation
    return session
