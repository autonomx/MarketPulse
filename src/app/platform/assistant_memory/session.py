"""Atomic session metadata updates for owner-bound Chat memory snapshots."""
from __future__ import annotations

from threading import RLock
from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import ChatSession, ChatSessionMutationPort
from app.conversation.concurrency import CHAT_MUTATION_LOCK
from .lifecycle import MemorySnapshotView, resolve_snapshot_view
from .scope import resolve_session_memory_scope
from .service import MemoryService

_SESSION_MEMORY_LOCK = RLock()


class RefreshSessionMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_snapshot_revision: int | None = Field(default=None, ge=1)
    token_budget: int = Field(default=4_000, ge=0, le=64_000)


class SessionMemoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    memory_enabled: bool
    read_memory: bool = False
    write_memory: bool = False
    owner_type: str = "system"
    owner_id: str = "system-assistant"
    snapshot_id: str | None = None
    snapshot_revision: int | None = Field(default=None, ge=1)
    memory_record_count: int = Field(default=0, ge=0)
    last_refreshed_at: str | None = None
    snapshot: MemorySnapshotView | None = None


class SessionMemoryConflictError(RuntimeError):
    pass


def _read_enabled(session: ChatSession) -> bool:
    return session.read_memory if session.interaction_mode == "character" else session.memory_enabled


def get_session_memory_state(
    store: ChatSessionMutationPort,
    memory_service: MemoryService,
    session_id: str,
) -> SessionMemoryState | None:
    session = store.get_session(session_id)
    if session is None:
        return None
    context = resolve_session_memory_scope(session)
    view = None
    if _read_enabled(session) and session.memory_snapshot_id:
        view = resolve_snapshot_view(memory_service, context, session.memory_snapshot_id)
    active_count = view.active_count if view is not None else 0
    return SessionMemoryState(
        session_id=session.id,
        memory_enabled=_read_enabled(session),
        read_memory=session.read_memory,
        write_memory=session.write_memory,
        owner_type=context.owner_type,
        owner_id=context.owner_id,
        snapshot_id=session.memory_snapshot_id,
        snapshot_revision=session.memory_snapshot_revision,
        memory_record_count=active_count,
        last_refreshed_at=session.memory_last_refreshed_at,
        snapshot=view,
    )


def refresh_session_memory(
    store: ChatSessionMutationPort,
    memory_service: MemoryService,
    session_id: str,
    request: RefreshSessionMemoryRequest,
) -> SessionMemoryState | None:
    with _SESSION_MEMORY_LOCK, CHAT_MUTATION_LOCK:
        session = store.get_session(session_id)
        if session is None:
            return None
        if session.interaction_mode == "character" and not session.read_memory:
            raise SessionMemoryConflictError("character memory read is disabled")
        current_revision = session.memory_snapshot_revision
        if request.expected_snapshot_revision is not None and request.expected_snapshot_revision != current_revision:
            raise SessionMemoryConflictError(
                "memory snapshot revision conflict: "
                f"expected {request.expected_snapshot_revision}, actual {current_revision}"
            )
        context = resolve_session_memory_scope(session)
        snapshot = memory_service.create_session_snapshot(
            context,
            token_budget=request.token_budget,
            refresh=current_revision is not None,
        )
        refreshed_at = snapshot.refreshed_at or snapshot.created_at
        if session.interaction_mode == "system":
            session.memory_enabled = True
        session.memory_snapshot_id = snapshot.id
        session.memory_snapshot_revision = snapshot.revision
        session.memory_record_count = len(snapshot.items)
        session.memory_last_refreshed_at = refreshed_at
        session.updated_at = refreshed_at
        store._save_session(session)
        view = resolve_snapshot_view(memory_service, context, snapshot.id)
        return SessionMemoryState(
            session_id=session.id,
            memory_enabled=True,
            read_memory=session.read_memory,
            write_memory=session.write_memory,
            owner_type=context.owner_type,
            owner_id=context.owner_id,
            snapshot_id=snapshot.id,
            snapshot_revision=snapshot.revision,
            memory_record_count=view.active_count if view else 0,
            last_refreshed_at=refreshed_at,
            snapshot=view,
        )
    return None
