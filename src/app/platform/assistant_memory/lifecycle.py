"""Safe projection of frozen memory snapshots for active Chat sessions."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.memory_contracts import MemoryScopeContext
from .policy import prompt_eligibility
from .service import MemoryService


class MemorySnapshotViewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_record_id: str
    record_revision: int = Field(ge=1)
    content: str
    active: bool
    invalidation_reason: str | None = None


class MemorySnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    session_id: str
    revision: int = Field(ge=1)
    token_estimate: int = Field(ge=0)
    created_at: str
    refreshed_at: str | None = None
    items: list[MemorySnapshotViewItem] = Field(default_factory=list)
    active_count: int = Field(ge=0)
    invalidated_count: int = Field(ge=0)


def resolve_snapshot_view(
    service: MemoryService,
    context: MemoryScopeContext,
    snapshot_id: str | None,
) -> MemorySnapshotView | None:
    if not snapshot_id:
        return None
    snapshot = service.repository.get_snapshot(snapshot_id)
    if snapshot is None or snapshot.session_id != context.session_id:
        return None
    if (snapshot.owner_type, snapshot.owner_id) != (context.owner_type, context.owner_id):
        return None

    items: list[MemorySnapshotViewItem] = []
    for item in snapshot.items:
        record = service.repository.get_record(item.memory_record_id)
        reason: str | None
        if item.revoked_at:
            reason = "snapshot_item_revoked"
        elif record is None:
            reason = "record_forgotten"
        elif (record.owner_type, record.owner_id) != (context.owner_type, context.owner_id):
            reason = "owner_mismatch"
        else:
            decision = prompt_eligibility(record, context)
            reason = None if decision.allowed else decision.reason
        items.append(
            MemorySnapshotViewItem(
                memory_record_id=item.memory_record_id,
                record_revision=item.record_revision,
                content=item.frozen_content if reason is None else "",
                active=reason is None,
                invalidation_reason=reason,
            )
        )

    active_count = sum(1 for item in items if item.active)
    return MemorySnapshotView(
        snapshot_id=snapshot.id,
        session_id=snapshot.session_id,
        revision=snapshot.revision,
        token_estimate=snapshot.token_estimate,
        created_at=snapshot.created_at,
        refreshed_at=snapshot.refreshed_at,
        items=items,
        active_count=active_count,
        invalidated_count=len(items) - active_count,
    )
