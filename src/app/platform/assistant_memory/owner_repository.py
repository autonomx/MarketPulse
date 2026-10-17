"""Owner-isolated provider-free memory repository.

Production installs the tenant-scoped PostgreSQL adapter. Tests use the
in-memory canonical repository with owner-aware filtering; no SQLite behavior
or schema remains.
"""
from __future__ import annotations
from app.config.env import env_str as _env_str

from copy import deepcopy
from pathlib import Path

from app.conversation.memory_contracts import MemoryCandidate, MemoryRecord, MemorySnapshot
from app.runtime.pagination import bounded_count
from .repository import InMemoryMemoryRepository


class OwnerAwareInMemoryMemoryRepository(InMemoryMemoryRepository):
    def __init__(self, db_path: str | Path | None = None) -> None:
        override = (_env_str("OMNIX_ASSISTANT_MEMORY_DB_PATH") or "").strip()
        resolved = db_path if db_path is not None else (Path(override) if override else None)
        super().__init__(resolved)

    def list_records(
        self,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        scope: str | None = None,
        scope_id: str | None = None,
        status: str | None = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        values = super().list_records(
            scope=scope,
            scope_id=scope_id,
            status=status,
            limit=10_000,
            offset=0,
        )
        values = [
            record
            for record in values
            if (owner_type is None or record.owner_type == owner_type)
            and (owner_id is None or record.owner_id == owner_id)
        ]
        start = max(0, int(offset))
        return deepcopy(values[start : start + bounded_count(limit)])

    def create_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        return super().create_candidate(candidate)

    def list_candidates(
        self,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryCandidate]:
        values = super().list_candidates(status=status, limit=10_000)
        values = [
            candidate
            for candidate in values
            if (owner_type is None or candidate.owner_type == owner_type)
            and (owner_id is None or candidate.owner_id == owner_id)
        ]
        start = max(0, int(offset))
        return deepcopy(values[start : start + bounded_count(limit)])

    def latest_snapshot(
        self,
        session_id: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> MemorySnapshot | None:
        with self._state.lock:
            values = [
                snapshot
                for snapshot in self._state.snapshots.values()
                if snapshot.session_id == session_id
                and (owner_type is None or snapshot.owner_type == owner_type)
                and (owner_id is None or snapshot.owner_id == owner_id)
            ]
            values.sort(
                key=lambda item: (item.revision, item.created_at, item.id),
                reverse=True,
            )
            return deepcopy(values[0]) if values else None

    def delete_owner(self, *, owner_type: str, owner_id: str) -> tuple[int, int, int]:
        """Delete all memory state for one owner without exposing storage internals."""

        with self._state.lock:
            record_ids = [
                record_id
                for record_id, record in self._state.records.items()
                if record.owner_type == owner_type and record.owner_id == owner_id
            ]
            candidate_ids = [
                candidate_id
                for candidate_id, candidate in self._state.candidates.items()
                if candidate.owner_type == owner_type and candidate.owner_id == owner_id
            ]
            snapshot_ids = [
                snapshot_id
                for snapshot_id, snapshot in self._state.snapshots.items()
                if snapshot.owner_type == owner_type and snapshot.owner_id == owner_id
            ]

            for record_id in record_ids:
                self._state.records.pop(record_id, None)
            for candidate_id in candidate_ids:
                self._state.candidates.pop(candidate_id, None)
            for snapshot_id in snapshot_ids:
                self._state.snapshots.pop(snapshot_id, None)

            self._append_event(
                "owner",
                f"{owner_type}:{owner_id}",
                "memory.owner_reset",
                {
                    "record_count": len(record_ids),
                    "candidate_count": len(candidate_ids),
                    "snapshot_count": len(snapshot_ids),
                },
            )
            return len(record_ids), len(candidate_ids), len(snapshot_ids)


__all__ = ["OwnerAwareInMemoryMemoryRepository"]
