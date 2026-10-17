"""Validated typed-memory lifecycle layered on the canonical memory service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.conversation.memory_contracts import (
    MemoryCategory,
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryScopeContext,
)
from .owner_service import OwnerAwareMemoryService


class RoutinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity: str = Field(min_length=1, max_length=160)
    days: list[str] = Field(default_factory=list, max_length=7)
    start_time: str | None = None
    end_time: str | None = None
    timezone: str | None = Field(default=None, max_length=100)
    evidence_count: int = Field(default=1, ge=1)
    exceptions: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: list[str]) -> list[str]:
        allowed = {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}
        normalized = list(dict.fromkeys(item.upper() for item in value))
        if any(item not in allowed for item in normalized):
            raise ValueError("routine days must use RFC5545 weekday abbreviations")
        return normalized

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        datetime.strptime(value, "%H:%M")
        return value


class EpisodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurred_at: str
    participants: list[str] = Field(default_factory=list, max_length=32)
    importance: int = Field(default=50, ge=0, le=100)
    emotional_relevance: int = Field(default=0, ge=0, le=100)


class GoalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(default="active", pattern="^(active|completed|abandoned)$")
    target_at: str | None = None
    priority: int = Field(default=50, ge=0, le=100)


class OpenLoopPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(default="open", pattern="^(open|completed|cancelled)$")
    due_at: str | None = None
    follow_up_after: str | None = None


class TemporalFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid_from: str | None = None
    valid_until: str | None = None
    timezone: str | None = Field(default=None, max_length=100)


_KIND_MODELS: dict[MemoryKind, type[BaseModel] | None] = {
    "routine": RoutinePayload,
    "episode": EpisodePayload,
    "goal": GoalPayload,
    "open_loop": OpenLoopPayload,
    "temporal_fact": TemporalFactPayload,
    "semantic_fact": None,
    "preference": None,
    "instruction": None,
    "relationship_state": None,
    "pronunciation": None,
}

_DEFAULT_CATEGORY: dict[MemoryKind, MemoryCategory] = {
    "semantic_fact": "fact",
    "preference": "preference",
    "instruction": "instruction",
    "relationship_state": "relationship",
    "episode": "fact",
    "routine": "fact",
    "goal": "project",
    "open_loop": "project",
    "temporal_fact": "fact",
    "pronunciation": "preference",
}


def validate_typed_payload(kind: MemoryKind, payload: dict[str, Any] | None) -> dict[str, Any]:
    model = _KIND_MODELS[kind]
    raw = dict(payload or {})
    if model is None:
        return raw
    return model.model_validate(raw).model_dump(mode="json", exclude_none=True)


def _apply_record_metadata(
    service: OwnerAwareMemoryService,
    record: MemoryRecord,
    *,
    kind: MemoryKind,
    payload: dict[str, Any],
    supersedes_memory_id: str | None,
    contradiction_group: str | None,
) -> MemoryRecord:
    changed = record.model_copy(
        update={
            "kind": kind,
            "structured_payload": payload,
            "supersedes_memory_id": supersedes_memory_id,
            "contradiction_group": contradiction_group,
        }
    )
    repository = service.repository
    return repository.update_record(changed, expected_revision=record.revision)


def create_typed_memory(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    *,
    kind: MemoryKind,
    content: str,
    payload: dict[str, Any] | None = None,
    scope: MemoryScope = "global",
    category: MemoryCategory | None = None,
    provenance_id: str | None = None,
    pinned: bool = False,
    supersedes_memory_id: str | None = None,
    contradiction_group: str | None = None,
) -> MemoryRecord:
    validated = validate_typed_payload(kind, payload)
    record = service.create_explicit_memory(
        context,
        scope=scope,
        category=category or _DEFAULT_CATEGORY[kind],
        content=content,
        provenance_id=provenance_id,
        pinned=pinned,
    )
    return _apply_record_metadata(
        service,
        record,
        kind=kind,
        payload=validated,
        supersedes_memory_id=supersedes_memory_id,
        contradiction_group=contradiction_group,
    )


def supersede_typed_memory(
    service: OwnerAwareMemoryService,
    context: MemoryScopeContext,
    record_id: str,
    *,
    kind: MemoryKind,
    content: str,
    payload: dict[str, Any] | None = None,
    provenance_id: str | None = None,
) -> MemoryRecord:
    previous = service.repository.get_record(record_id)
    if previous is None:
        raise KeyError(record_id)
    if (previous.owner_type, previous.owner_id) != (context.owner_type, context.owner_id):
        raise ValueError("owner_mismatch")
    archived = previous.model_copy(update={"status": "superseded"})
    service.repository.update_record(archived, expected_revision=previous.revision)
    contradiction_group = previous.contradiction_group or f"memory-claim:{previous.id}"
    return create_typed_memory(
        service,
        context,
        kind=kind,
        content=content,
        payload=payload,
        scope=previous.scope,
        category=previous.category,
        provenance_id=provenance_id,
        supersedes_memory_id=previous.id,
        contradiction_group=contradiction_group,
    )


__all__ = [
    "EpisodePayload",
    "GoalPayload",
    "OpenLoopPayload",
    "RoutinePayload",
    "TemporalFactPayload",
    "create_typed_memory",
    "supersede_typed_memory",
    "validate_typed_payload",
]
