"""Conversation segments: a chat session's provider-context identity boundaries (PA-1.3, PA-2.2).

A session starts a new segment when its interaction (system or character,
memory and transcript policy) changes. Chat owns segments; characters reads
them through the chat store it is given.
"""
from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import InteractionMode, SharedMemoryAccess, TranscriptPolicy

# Segments of one session returned by a read; the newest are kept (WP-5.5).
MAX_SESSION_SEGMENTS = 1000


class ConversationSegment(BaseModel):
    """Persisted provider-context identity boundary."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=200)
    interaction_mode: InteractionMode
    character_id: str | None = Field(default=None, max_length=160)
    profile_version: int | None = Field(default=None, ge=1)
    transcript_policy: TranscriptPolicy = "persistent"
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: SharedMemoryAccess = "none"
    carryover_summary: str | None = Field(default=None, max_length=16_000)
    started_at: str
    ended_at: str | None = None


def check_segment_identity(interaction_mode: str, character_id: str | None) -> None:
    if interaction_mode == "character" and not character_id:
        raise ValueError("character segment requires character_id")
    if interaction_mode == "system" and character_id:
        raise ValueError("system segment cannot have character_id")


def new_segment_id() -> str:
    return f"segment:{uuid.uuid4().hex}"


_LOCK = threading.RLock()
_SEGMENTS: dict[str, ConversationSegment] = {}


class InMemoryConversationSegments:
    """The segments of the in-memory chat runtime (tests and local development without PostgreSQL)."""

    def create_segment(self, *, session_id: str, interaction_mode: Any, character_id: str | None,
                       profile_version: int | None, transcript_policy: Any, read_memory: bool, write_memory: bool,
                       shared_memory_access: Any, carryover_summary: str | None = None) -> ConversationSegment:
        check_segment_identity(interaction_mode, character_id)
        segment = ConversationSegment(
            id=new_segment_id(), session_id=session_id, interaction_mode=interaction_mode, character_id=character_id,
            profile_version=profile_version, transcript_policy=transcript_policy, read_memory=read_memory,
            write_memory=write_memory, shared_memory_access=shared_memory_access, carryover_summary=carryover_summary,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with _LOCK:
            _SEGMENTS[segment.id] = deepcopy(segment)
        return deepcopy(segment)

    def close_segment(self, segment_id: str) -> ConversationSegment | None:
        with _LOCK:
            current = _SEGMENTS.get(segment_id)
            if current is None:
                return None
            if current.ended_at is None:
                current = current.model_copy(update={"ended_at": datetime.now(timezone.utc).isoformat()})
                _SEGMENTS[segment_id] = deepcopy(current)
            return deepcopy(current)

    def segments(self, session_id: str) -> list[ConversationSegment]:
        with _LOCK:
            values = [item for item in _SEGMENTS.values() if item.session_id == session_id]
        values.sort(key=lambda item: item.started_at)  # stable: ties keep creation order
        return deepcopy(values[-MAX_SESSION_SEGMENTS:])


def conversation_segments() -> Any:
    """The segment repository of this runtime: PostgreSQL when it is the authority."""
    from app.persistence.runtime import uses_postgresql_runtime

    if uses_postgresql_runtime():
        from app.platform.chat.persistence.segment_repository import PostgresConversationSegmentRepository

        return PostgresConversationSegmentRepository()
    return InMemoryConversationSegments()


__all__ = [
    "ConversationSegment",
    "InMemoryConversationSegments",
    "MAX_SESSION_SEGMENTS",
    "conversation_segments",
]
