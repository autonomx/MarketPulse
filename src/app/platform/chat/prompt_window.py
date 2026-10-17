"""Bound ordinary Chat prompts without losing older conversation context.

A stateless local LLM must receive conversational state on every request, but
replaying an unbounded transcript makes prompt processing progressively slower
and can exceed a small model's loaded context.  Keep a configurable recent tail
and replace older eligible turns with an exact deterministic summary.

Ordinary Chat applies this feature-owned stage before canonical prompt
rendering. The live-voice profile keeps its separate 12-message latency policy.
"""
from __future__ import annotations

from app.config.env import environment
from collections.abc import Callable
from typing import Any

from .compaction import build_deterministic_summary
from .models import ChatMessage, ChatSession
from .prompt_assembly import PromptAssembly

from app.observability.tts_stream_diagnostics import stream_log

_DEFAULT_RECENT_MESSAGE_LIMIT = 24
_MIN_RECENT_MESSAGE_LIMIT = 2
_MAX_RECENT_MESSAGE_LIMIT = 200
# The longest recent tail any prompt can use; a transcript window loads at
# least this many eligible messages (WP-5.7).
MAX_RECENT_MESSAGE_LIMIT = _MAX_RECENT_MESSAGE_LIMIT


def _boolean_setting(name: str, fallback: bool) -> bool:
    raw = environment().get(name)
    if raw is None or not raw.strip():
        return fallback
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _integer_setting(
    name: str,
    fallback: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (environment().get(name) or "").strip()
    try:
        value = int(raw) if raw else fallback
    except ValueError:
        value = fallback
    return min(maximum, max(minimum, value))


def normal_chat_prompt_window_enabled() -> bool:
    """Return whether ordinary Chat should use bounded rolling context."""
    return _boolean_setting("OMNIX_CHAT_PROMPT_WINDOW_ENABLED", True)


def normal_chat_recent_message_limit() -> int:
    """Return the maximum raw user/assistant tail sent to ordinary Chat."""
    return _integer_setting(
        "OMNIX_CHAT_PROMPT_RECENT_MESSAGE_LIMIT",
        _DEFAULT_RECENT_MESSAGE_LIMIT,
        minimum=_MIN_RECENT_MESSAGE_LIMIT,
        maximum=_MAX_RECENT_MESSAGE_LIMIT,
    )


def _eligible_prompt_messages(
    session: ChatSession,
    user_message: ChatMessage,
) -> list[ChatMessage]:
    active_segment_id = session.active_segment_id
    return [
        message
        for message in session.messages
        if message.id != user_message.id
        and message.role in {"user", "assistant"}
        and (
            not active_segment_id
            or message.metadata.get("segment_id") == active_segment_id
        )
    ]


def _effective_recent_limit(existing_limit: Any) -> int:
    configured = normal_chat_recent_message_limit()
    if isinstance(existing_limit, bool):
        return configured
    try:
        requested = int(existing_limit) if existing_limit is not None else configured
    except (TypeError, ValueError):
        requested = configured
    return max(_MIN_RECENT_MESSAGE_LIMIT, min(configured, requested))


def _exact_window_summary(
    session: ChatSession,
    eligible_messages: list[ChatMessage],
    *,
    recent_message_limit: int,
):
    if len(eligible_messages) <= recent_message_limit:
        return None
    bounded_session = session.model_copy(
        update={
            "messages": eligible_messages,
            "message_count": len(eligible_messages),
        }
    )
    return build_deterministic_summary(
        bounded_session,
        recent_message_limit=recent_message_limit,
    )


def build_prompt_assembly_with_window(
    original_build: Callable[..., PromptAssembly],
    session: ChatSession,
    user_message: ChatMessage,
    **kwargs: Any,
) -> PromptAssembly:
    """Apply an exact rolling window before canonical prompt rendering."""
    if not normal_chat_prompt_window_enabled():
        assembly = original_build(session, user_message, **kwargs)
        assembly.diagnostics["prompt_window"] = {
            "enabled": False,
            "reason": "disabled_by_configuration",
            "eligible_message_count": len(
                _eligible_prompt_messages(session, user_message)
            ),
        }
        return assembly

    eligible_messages = _eligible_prompt_messages(session, user_message)
    recent_message_limit = _effective_recent_limit(
        kwargs.get("recent_message_limit")
    )
    summarized_message_count = max(
        0,
        len(eligible_messages) - recent_message_limit,
    )
    supplied_summary = str(kwargs.get("session_summary") or "").strip()
    summary = None
    summary_error_type: str | None = None
    if summarized_message_count:
        try:
            summary = _exact_window_summary(
                session,
                eligible_messages,
                recent_message_limit=recent_message_limit,
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            summary_error_type = type(exc).__name__

    if summary is not None:
        if supplied_summary and supplied_summary == summary.summary.strip():
            selected_summary = supplied_summary
            summary_source = "persisted_exact"
        else:
            selected_summary = summary.summary
            summary_source = "ephemeral_exact"
    elif summarized_message_count:
        selected_summary = None
        summary_source = (
            "summary_failed_recent_only"
            if summary_error_type
            else "omitted_by_retention_policy"
        )
    else:
        selected_summary = None
        summary_source = "not_needed"

    kwargs["session_summary"] = selected_summary
    kwargs["recent_message_limit"] = recent_message_limit
    assembly = original_build(session, user_message, **kwargs)
    diagnostics = {
        "enabled": True,
        "recent_message_limit": recent_message_limit,
        "eligible_message_count": len(eligible_messages),
        "recent_message_count": len(assembly.recent_messages),
        "summarized_message_count": summarized_message_count,
        "bounded": bool(summarized_message_count),
        "summary_source": summary_source,
        "summary_through_message_id": (
            summary.through_message_id if summary is not None else None
        ),
        "persisted_summary_supplied": bool(supplied_summary),
        "persisted_summary_reused": summary_source == "persisted_exact",
        "summary_error_type": summary_error_type,
    }
    assembly.diagnostics["prompt_window"] = diagnostics
    if summarized_message_count:
        stream_log(
            "gateway-live-chat-first-token",
            "runtime",
            "live_chat_prompt_window_applied",
            session_message_count=len(session.messages),
            active_segment_id=session.active_segment_id,
            **diagnostics,
        )
    return assembly


__all__ = [
    "build_prompt_assembly_with_window",
    "normal_chat_prompt_window_enabled",
    "normal_chat_recent_message_limit",
]
