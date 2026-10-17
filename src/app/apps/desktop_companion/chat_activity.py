"""Best-effort bridge from accepted Chat user turns into Companion Activity state."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.platform.chat.contracts import ChatMessage, ChatSession

from .activity_bridge import (
    CompanionActivityUserTurnUpdate,
    default_desktop_companion_activity_bridge,
)

logger = logging.getLogger(__name__)


def record_accepted_chat_activity(
    session: ChatSession,
    user_message: ChatMessage,
) -> CompanionActivityUserTurnUpdate | None:
    """Record explicit user activity semantics after Chat has accepted the turn.

    Chat persistence/generation remains authoritative for the user request. Companion
    activity is enrichment: an unavailable checkpoint store or malformed legacy timestamp
    may degrade the companion projection, but must not reject an otherwise accepted turn.
    """

    if user_message.role != "user":
        return None
    try:
        observed_at = _parse_timestamp(user_message.created_at)
        character_id = (
            session.character_id if session.interaction_mode == "character" else None
        )
        return default_desktop_companion_activity_bridge().record_user_turn(
            session_id=session.id,
            character_id=character_id,
            message_id=user_message.id,
            content=user_message.content,
            observed_at=observed_at,
        )
    except Exception:
        logger.warning(
            "Companion Activity user-turn enrichment failed for session=%s message=%s",
            session.id,
            user_message.id,
            exc_info=True,
        )
        return None


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


__all__ = ["record_accepted_chat_activity"]
