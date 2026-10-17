"""Owner-scoped user controls for companion memory lifecycle and data access."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import TranscriptReader

from .companion_context import invalidate_companion_context
from app.conversation.memory_contracts import MemoryCandidate, MemoryRecord, MemoryRecordStatus, MemoryScopeContext
from .paging import iter_candidates, iter_records
from .policy import is_visible_in_scope
from .service import MemoryPolicyError, MemoryService
from .temporal_retrieval import invalidate_temporal_retrieval


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exported_at: str
    owner_type: str
    owner_id: str
    records: list[MemoryRecord] = Field(default_factory=list)
    candidates: list[MemoryCandidate] = Field(default_factory=list)


class MemoryResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    owner_type: str
    owner_id: str
    record_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    snapshot_count: int = Field(ge=0)


class RecentAutomaticMemoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    records: list[MemoryRecord] = Field(default_factory=list)


def _visible_records(
    service: MemoryService,
    context: MemoryScopeContext,
    *,
    status: MemoryRecordStatus | None = None,
) -> list[MemoryRecord]:
    values: list[MemoryRecord] = []
    seen: set[str] = set()
    for scope in ("global", "workspace", "project", "session"):
        scope_id = {
            "global": context.profile_id,
            "workspace": context.workspace_id,
            "project": context.project_id,
            "session": context.session_id,
        }[scope]
        if scope_id is None:
            continue
        records = iter_records(
            service.repository,
            owner_type=context.owner_type,
            owner_id=context.owner_id,
            scope=scope,
            scope_id=scope_id,
            status=status,
        )
        for record in records:
            if record.id not in seen and is_visible_in_scope(record, context):
                seen.add(record.id)
                values.append(record)
    values.sort(key=lambda item: (item.status, not item.pinned, item.scope, item.category, item.id))
    return values


def _visible_record(service: MemoryService, context: MemoryScopeContext, record_id: str) -> MemoryRecord:
    record = service.repository.get_record(record_id)
    if record is None:
        raise MemoryPolicyError("memory_not_found")
    if (record.owner_type, record.owner_id) != (context.owner_type, context.owner_id):
        raise MemoryPolicyError("owner_mismatch")
    if not is_visible_in_scope(record, context):
        raise MemoryPolicyError("scope_mismatch")
    return record


def _invalidate(context: MemoryScopeContext) -> None:
    invalidate_companion_context(context.session_id)
    invalidate_temporal_retrieval(owner_type=context.owner_type, owner_id=context.owner_id)


def set_memory_archived(
    service: MemoryService,
    context: MemoryScopeContext,
    record_id: str,
    *,
    archived: bool,
    expected_revision: int,
) -> MemoryRecord:
    record = _visible_record(service, context, record_id)
    expected_status = "active" if archived else "archived"
    if record.status != expected_status:
        raise MemoryPolicyError(
            "memory_not_active" if archived else "memory_not_archived"
        )
    changed = record.model_copy(
        update={
            "status": "archived" if archived else "active",
            "pinned": False if archived else record.pinned,
            "updated_at": _utcnow(),
        }
    )
    stored = service.repository.update_record(changed, expected_revision=expected_revision)
    _invalidate(context)
    return stored


def undo_automatic_memory(
    service: MemoryService,
    context: MemoryScopeContext,
    session_message_ids: set[str],
    record_id: str,
    *,
    expected_revision: int,
) -> bool:
    record = _visible_record(service, context, record_id)
    if not bool(record.structured_payload.get("automatic_direct_assertion")):
        raise MemoryPolicyError("memory_not_automatic_direct_assertion")
    if not record.provenance_id or record.provenance_id not in session_message_ids:
        raise MemoryPolicyError("automatic_memory_not_from_session")
    forgotten = service.forget_memory(
        context,
        record.id,
        expected_revision=expected_revision,
    )
    _invalidate(context)
    return forgotten


def recent_automatic_memories(
    service: MemoryService,
    context: MemoryScopeContext,
    session_message_ids: set[str],
    *,
    limit: int = 5,
) -> list[MemoryRecord]:
    records = [
        record
        for record in _visible_records(service, context, status="active")
        if bool(record.structured_payload.get("automatic_direct_assertion"))
        and record.provenance_id in session_message_ids
    ]
    records.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
    return records[: max(0, min(limit, 20))]


def export_owner_memory(service: MemoryService, context: MemoryScopeContext) -> MemoryExportResponse:
    candidates: list[MemoryCandidate] = []
    for status in ("pending", "accepted", "rejected"):
        candidates.extend(
            iter_candidates(
                service.repository,
                owner_type=context.owner_type,
                owner_id=context.owner_id,
                status=status,
            )
        )
    candidates.sort(key=lambda item: (item.status, item.created_at, item.id))
    return MemoryExportResponse(
        exported_at=_utcnow(),
        owner_type=context.owner_type,
        owner_id=context.owner_id,
        records=_visible_records(service, context, status=None),
        candidates=candidates,
    )


def reset_owner_memory(
    store: TranscriptReader,
    service: MemoryService,
    context: MemoryScopeContext,
) -> MemoryResetResponse:
    delete_owner = getattr(service.repository, "delete_owner", None)
    if not callable(delete_owner):
        raise MemoryPolicyError("owner_reset_not_supported")
    record_count, candidate_count, snapshot_count = delete_owner(
        owner_type=context.owner_type,
        owner_id=context.owner_id,
    )
    clear_snapshots = getattr(store, "clear_memory_snapshots_for_owner", None)
    if callable(clear_snapshots):
        clear_snapshots(context.owner_type, context.owner_id)
    _invalidate(context)
    return MemoryResetResponse(
        owner_type=context.owner_type,
        owner_id=context.owner_id,
        record_count=record_count,
        candidate_count=candidate_count,
        snapshot_count=snapshot_count,
    )


__all__ = [
    "MemoryExportResponse",
    "MemoryResetResponse",
    "RecentAutomaticMemoryResponse",
    "export_owner_memory",
    "recent_automatic_memories",
    "reset_owner_memory",
    "set_memory_archived",
    "undo_automatic_memory",
]
