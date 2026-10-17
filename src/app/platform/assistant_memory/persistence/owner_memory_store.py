"""PostgreSQL candidates and session snapshots for owner-isolated assistant memory.

Curated memory records live in Memory v2; ``production_owner_memory_repository``
puts the v2 record store in front of this repository.
"""
from __future__ import annotations

from app.persistence.database import PostgresDatabase, default_database
from app.security.tenant_context import RequestTenant
from .owner_memory_candidates import OwnerMemoryCandidateMixin
from .owner_memory_snapshots import OwnerMemorySnapshotMixin


class PostgresOwnerAwareMemoryRepository(
    OwnerMemoryCandidateMixin,
    OwnerMemorySnapshotMixin,
):
    """Memory candidates and snapshots, by logical owner and memory scope."""
    context = RequestTenant()

    def __init__(self, database: PostgresDatabase | None = None) -> None:
        self.database = database or default_database()
        self.context = None  # follows the request tenant

    def delete_owner_candidates_and_snapshots(
        self,
        *,
        owner_type: str,
        owner_id: str,
    ) -> tuple[int, int]:
        """An owner reset's share here: the owner's candidates and snapshots, counted."""
        with self.database.transaction() as connection:
            snapshot_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM omnix_memory_snapshots "
                    "WHERE workspace_id = %s AND owner_type = %s AND owner_id = %s",
                    (self.workspace_id, owner_type, owner_id),
                ).fetchone()[0]
            )
            candidate_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM omnix_memory_candidates "
                    "WHERE workspace_id = %s AND proposed_owner_type = %s "
                    "AND proposed_owner_id = %s",
                    (self.workspace_id, owner_type, owner_id),
                ).fetchone()[0]
            )
            connection.execute(
                "DELETE FROM omnix_memory_candidates "
                "WHERE workspace_id = %s AND proposed_owner_type = %s "
                "AND proposed_owner_id = %s",
                (self.workspace_id, owner_type, owner_id),
            )
            # Chat sessions pinned to these snapshots are cleared by their foreign key
            # (memory_snapshot_id ... ON DELETE SET NULL), in this transaction.
            connection.execute(
                "DELETE FROM omnix_memory_snapshots "
                "WHERE workspace_id = %s AND owner_type = %s AND owner_id = %s",
                (self.workspace_id, owner_type, owner_id),
            )
            self.append_event(
                connection,
                "owner",
                f"{owner_type}:{owner_id}",
                "memory.owner_reset",
                {"candidate_count": candidate_count, "snapshot_count": snapshot_count},
            )
        return candidate_count, snapshot_count


__all__ = ["PostgresOwnerAwareMemoryRepository"]


def production_owner_memory_repository():
    """Curated memory records in Memory v2, with this repository's candidates and snapshots."""
    from app.platform.assistant_memory.v2.memory_repository import CuratedMemoryRepository

    return CuratedMemoryRepository(PostgresOwnerAwareMemoryRepository())
