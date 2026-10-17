"""Typed, trust-separated provider prompt assembly."""
from __future__ import annotations

import logging

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import PromptMemoryItem

from .context_budget import PromptBudget, prompt_budget_for_model, prompt_budget_from_env
from .models import ChatMessage, ChatSession, MessageContentPurpose, project_message_content
from .session_identity import neutralize_legacy_system_prompt, resolve_system_session_identity

logger = logging.getLogger(__name__)

PromptRole = Literal["system", "user", "assistant"]


class PromptTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: PromptRole
    content: str
    message_id: str | None = None


class PromptHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str | None = None


class PromptExternalContextItem(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    source_id: str = "context"
    title: str = "Context"
    content: str
    url: str | None = None


class PromptAssembly(BaseModel):
    """Canonical provider input before provider-specific message conversion."""

    model_config = ConfigDict(extra="forbid")
    system_instructions: list[str] = Field(default_factory=list)
    assistant_identity: list[str] = Field(default_factory=list)
    approved_memory: list[PromptMemoryItem] = Field(default_factory=list)
    session_summary: str | None = None
    recent_messages: list[PromptTurn] = Field(default_factory=list)
    retrieved_history: list[PromptHistoryItem] = Field(default_factory=list)
    external_context: list[PromptExternalContextItem] = Field(default_factory=list)
    current_user_message: PromptTurn
    budget: PromptBudget = Field(default_factory=prompt_budget_from_env)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


def _external_item(payload: dict[str, Any], index: int) -> PromptExternalContextItem:
    source_id = str(payload.get("source_id") or "context").strip() or "context"
    title = str(payload.get("title") or source_id or f"Context {index}").strip()
    content = str(payload.get("content") or "").strip()
    url = str(payload.get("url") or "").strip() or None
    return PromptExternalContextItem(source_id=source_id, title=title or f"Context {index}", content=content, url=url)


def _active_segment_summary(session: ChatSession) -> str | None:
    if not session.active_segment_id:
        return None
    try:
        from app.platform.chat.segments import conversation_segments

        segments = conversation_segments().segments(session.id)
    except Exception:
        logger.debug("suppressed error in %s", "_active_segment_summary", exc_info=True)
        return None
    segment = next((item for item in segments if item.id == session.active_segment_id), None)
    return segment.carryover_summary if segment else None


def build_prompt_assembly(
    session: ChatSession,
    user_message: ChatMessage,
    *,
    global_system_prompt: str,
    context_items: list[dict[str, Any]] | None = None,
    approved_memory: list[PromptMemoryItem] | None = None,
    session_summary: str | None = None,
    retrieved_history: list[PromptHistoryItem] | None = None,
    assistant_identity: list[str] | None = None,
    budget: PromptBudget | None = None,
    recent_message_limit: int | None = None,
    identity_resolver: Callable[[ChatSession], Any] | None = None,
) -> PromptAssembly:
    """Build one stable structure for streaming and non-streaming generation."""

    interaction = (identity_resolver or resolve_system_session_identity)(session)
    session_system_messages = [
        neutralize_legacy_system_prompt(message.content)
        for message in session.messages
        if message.role == "system"
        and (not session.active_segment_id or message.metadata.get("segment_id") == session.active_segment_id)
    ]
    if interaction.interaction_mode == "character":
        # Character Mode has one authoritative persona source. Do not combine it
        # with the global System Assistant prompt, legacy Maya prompt, session
        # system prompts, or a caller-supplied assistant identity override.
        system_instructions: list[str] = []
        resolved_identity = interaction.assistant_identity
    else:
        system_instructions = session_system_messages or [
            neutralize_legacy_system_prompt(global_system_prompt)
        ]
        resolved_identity = assistant_identity if assistant_identity is not None else []
    eligible_recent_messages = [
        message
        for message in session.messages
        if message.id != user_message.id
        and message.role != "system"
        and (not session.active_segment_id or message.metadata.get("segment_id") == session.active_segment_id)
    ]
    if recent_message_limit is not None:
        eligible_recent_messages = eligible_recent_messages[-max(0, recent_message_limit):]
    recent_messages: list[PromptTurn] = []
    for message in eligible_recent_messages:
        content = project_message_content(message, MessageContentPurpose.MODEL)
        if content:
            recent_messages.append(PromptTurn(role=message.role, content=content, message_id=message.id))
    external_context = [_external_item(payload, index) for index, payload in enumerate(context_items or [], start=1)]
    resolved_summary = session_summary if session_summary is not None else _active_segment_summary(session)
    character_personality_only = interaction.interaction_mode == "character"
    return PromptAssembly(
        system_instructions=system_instructions,
        assistant_identity=resolved_identity,
        approved_memory=approved_memory or [],
        session_summary=resolved_summary,
        recent_messages=recent_messages,
        retrieved_history=retrieved_history or [],
        external_context=external_context,
        current_user_message=PromptTurn(role="user", content=user_message.content, message_id=user_message.id),
        budget=budget or prompt_budget_for_model(session.provider_id, session.model_id),
        diagnostics={
            "session_id": session.id,
            "active_segment_id": session.active_segment_id,
            "system_instruction_count": len(system_instructions),
            "assistant_identity_count": len(resolved_identity),
            "assistant_identity_chars": sum(len(value) for value in resolved_identity),
            "approved_memory_count": len(approved_memory or []),
            "recent_message_count": len(recent_messages),
            "retrieved_history_count": len(retrieved_history or []),
            "external_context_count": len(external_context),
            "character_personality_only": character_personality_only,
            "global_system_prompt_suppressed": character_personality_only,
            "session_system_prompt_count_suppressed": (
                len(session_system_messages) if character_personality_only else 0
            ),
            "caller_identity_override_suppressed": (
                character_personality_only and assistant_identity is not None
            ),
            "interaction": interaction.model_dump(mode="json"),
        },
    )
