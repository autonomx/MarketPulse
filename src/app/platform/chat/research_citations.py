"""Citation validation for completed research-backed chat replies."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .concurrency import serialized_chat_mutation
from .models import ChatSession
from .store import ChatSessionStore


@serialized_chat_mutation
def validate_completed_research_reply(
    store: ChatSessionStore,
    session_id: str,
    user_message_id: str,
    context_items: list[dict[str, Any]],
    *,
    render: Callable[[str, list[dict[str, Any]]], tuple[str, dict[str, Any]] | None],
    show_diagnostics: bool = True,
) -> ChatSession | None:
    """Apply research's citation rendering to the reply; ``render`` returns None without citations."""
    if render("", context_items) is None:
        return store.get_session(session_id)
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
    rendered = render(assistant.content, context_items)
    if rendered is None:
        return session
    assistant.content, citation_metadata = rendered
    assistant.metadata.update(
        {
            "research_mode": "quick",
            "research_status": "completed",
            "research_diagnostics_enabled": show_diagnostics,
            **citation_metadata,
        }
    )
    session.updated_at = assistant.created_at
    store._save_session(session)  # noqa: SLF001 - one targeted session mutation
    return session
