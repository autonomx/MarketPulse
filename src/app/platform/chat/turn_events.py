"""Events chat publishes for other modules to react to (ADR-0016, PA-3.4).

Delivered through the outbox after the producing transaction commits; a
consumer may see an event more than once and must be idempotent. The wire
contract is the aggregate type, the event type and the payload's fields.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

CHAT_SESSION_AGGREGATE = "chat_session"
CHAT_TURN_COMPLETED = "chat.turn.completed"


class ChatTurnCompleted(BaseModel):
    """A chat turn's reply was generated and stored.

    ``memory_writes_allowed`` is the session's policy: a character session
    writes memory only when the user turned it on.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    user_message_id: str
    user_id: str
    memory_writes_allowed: bool


def turn_completed_event_key(user_message_id: str) -> str:
    """One event per user message, however often the turn's completion is retried."""
    return f"{CHAT_TURN_COMPLETED}:{user_message_id}"


__all__ = ["CHAT_SESSION_AGGREGATE", "CHAT_TURN_COMPLETED", "ChatTurnCompleted", "turn_completed_event_key"]
