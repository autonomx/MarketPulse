"""Typed management contracts and owner-bound memory operations."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import ChatSession, TranscriptReader

from app.conversation.memory_contracts import (
    MemoryCandidate,
    MemoryCandidateStatus,
    MemoryCategory,
    MemoryRecord,
    MemorySensitivity,
    MemoryScope,
)
from app.runtime.pagination import bounded_count
from .scope import resolve_session_memory_scope, scope_id_for
from .service import MemoryPolicyError, MemoryService


class MemoryListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[MemoryRecord]
    total: int = Field(ge=0)
    session_id: str


class MemoryCandidateListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[MemoryCandidate]
    total: int = Field(ge=0)
    session_id: str


class CreateManagedMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    scope: MemoryScope
    category: MemoryCategory
    sensitivity: MemorySensitivity = "normal"
    content: str = Field(min_length=1, max_length=4096)
    pinned: bool = False


class UpdateManagedMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=4096)


class RevisionedMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class MoveManagedMemoryRequest(RevisionedMemoryRequest):
    target_scope: MemoryScope


class CandidateResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    pinned: bool = False


class CandidateCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=200)
    expected_status: MemoryCandidateStatus


class ForgetMemoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    memory_id: str


class ForgetCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal[True] = True
    candidate_id: str


def resolve_session_scope(store: TranscriptReader, session_id: str):
    session = store.get_session(session_id)
    if session is None:
        return None, None
    return session, resolve_session_memory_scope(session)


def require_memory_write(session: ChatSession) -> None:
    if session.interaction_mode == "character" and not session.write_memory:
        raise MemoryPolicyError("character_memory_write_disabled")


def records_for_session(
    store: TranscriptReader,
    service: MemoryService,
    session_id: str,
    *,
    scope: MemoryScope | None = None,
    category: MemoryCategory | None = None,
    pinned_only: bool = False,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> MemoryListResponse | None:
    _, context = resolve_session_scope(store, session_id)
    if context is None:
        return None
    normalized_query = " ".join((query or "").strip().split()).casefold()
    records = service.list_active(context)
    filtered = [
        record
        for record in records
        if (scope is None or record.scope == scope)
        and (category is None or record.category == category)
        and (not pinned_only or record.pinned)
        and (
            not normalized_query
            or normalized_query in record.normalized_content
            or normalized_query in record.content.casefold()
        )
    ]
    filtered.sort(key=lambda record: (not record.pinned, record.scope, record.category, record.id))
    total = len(filtered)
    bounded_limit = bounded_count(limit)
    bounded_offset = max(0, offset)
    return MemoryListResponse(
        records=filtered[bounded_offset : bounded_offset + bounded_limit],
        total=total,
        session_id=session_id,
    )


def session_record(
    store: TranscriptReader,
    service: MemoryService,
    session_id: str,
    memory_id: str,
) -> tuple[bool, MemoryRecord | None]:
    """``(session found, the session's active record with this id)``."""
    _, context = resolve_session_scope(store, session_id)
    if context is None:
        return False, None
    return True, next((record for record in service.list_active(context) if record.id == memory_id), None)


def is_candidate_visible(candidate: MemoryCandidate, context: Any) -> bool:
    """A pending candidate of this owner, proposed for one of the session's scopes."""
    return (
        candidate.status == "pending"
        and (candidate.owner_type, candidate.owner_id) == (context.owner_type, context.owner_id)
        and scope_id_for(candidate.proposed_scope, context) == candidate.proposed_scope_id
    )


def candidates_for_session(
    store: TranscriptReader,
    service: MemoryService,
    session_id: str,
    *,
    limit: int = 100,
) -> MemoryCandidateListResponse | None:
    _, context = resolve_session_scope(store, session_id)
    if context is None:
        return None
    bounded_limit = bounded_count(limit)
    # Owner-aware stores filter by owner; the others take no owner and raise TypeError.
    list_candidates: Any = service.repository.list_candidates
    try:
        candidates = list_candidates(
            owner_type=context.owner_type,
            owner_id=context.owner_id,
            status="pending",
            limit=bounded_limit,
        )
    except TypeError:
        candidates = list_candidates(
            status="pending",
            limit=bounded_limit,
        )
    visible = [candidate for candidate in candidates if is_candidate_visible(candidate, context)]
    return MemoryCandidateListResponse(
        candidates=visible,
        total=len(visible),
        session_id=session_id,
    )
