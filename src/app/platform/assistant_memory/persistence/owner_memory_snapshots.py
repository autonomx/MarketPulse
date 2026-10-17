"""Snapshot persistence for PostgreSQL owner-aware memory."""
from __future__ import annotations

from typing import Any

from app.conversation.memory_contracts import MemorySnapshot
from app.runtime.pagination import bounded_count

from .owner_memory_rows import OwnerMemoryRowSupport


class OwnerMemorySnapshotMixin(OwnerMemoryRowSupport):
    def create_snapshot(self, snapshot: MemorySnapshot) -> MemorySnapshot:
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO omnix_memory_snapshots (
                    id, workspace_id, owner_type, owner_id, revision, status,
                    created_at, session_id, token_estimate, refreshed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'active', %s::timestamptz,
                    %s, %s, %s::timestamptz
                )
                """,
                (
                    snapshot.id,
                    self.workspace_id,
                    snapshot.owner_type,
                    snapshot.owner_id,
                    snapshot.revision,
                    snapshot.created_at,
                    snapshot.session_id,
                    snapshot.token_estimate,
                    snapshot.refreshed_at,
                ),
            )
            for position, item in enumerate(snapshot.items):
                connection.execute(
                    """
                    INSERT INTO omnix_memory_snapshot_items (
                        snapshot_id, memory_record_id, position, record_revision,
                        frozen_content, revoked_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::timestamptz)
                    """,
                    (
                        snapshot.id,
                        item.memory_record_id,
                        position,
                        item.record_revision,
                        item.frozen_content,
                        item.revoked_at,
                    ),
                )
        return snapshot

    def delete_snapshot_items_for_record(self, record_id: str) -> int:
        """Remove a forgotten record's frozen text from this workspace's snapshots."""
        with self.database.transaction() as connection:
            deleted = connection.execute(
                "DELETE FROM omnix_memory_snapshot_items "
                "WHERE memory_record_id = %s AND snapshot_id IN ("
                "SELECT id FROM omnix_memory_snapshots WHERE workspace_id = %s)",
                (record_id, self.workspace_id),
            ).rowcount
        return int(deleted or 0)

    def get_snapshot(self, snapshot_id: str) -> MemorySnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                self.snapshot_select()
                + " WHERE id = %s AND workspace_id = %s",
                (snapshot_id, self.workspace_id),
            ).fetchone()
            if row is None:
                return None
            items = self.snapshot_items(connection, str(row[0]))
        return self.snapshot_from_row(row, items)

    def latest_snapshot(
        self,
        session_id: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
    ) -> MemorySnapshot | None:
        clauses = [
            "workspace_id = %s",
            "session_id = %s",
            "status = 'active'",
        ]
        parameters: list[Any] = [self.workspace_id, session_id]
        if owner_type is not None:
            clauses.append("owner_type = %s")
            parameters.append(owner_type)
        if owner_id is not None:
            clauses.append("owner_id = %s")
            parameters.append(owner_id)
        with self.database.connection() as connection:
            row = connection.execute(
                self.snapshot_select()
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY revision DESC, created_at DESC, id DESC LIMIT 1",
                tuple(parameters),
            ).fetchone()
            if row is None:
                return None
            items = self.snapshot_items(connection, str(row[0]))
        return self.snapshot_from_row(row, items)

    def list_snapshots(
        self,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[MemorySnapshot]:
        clauses = ["workspace_id = %s"]
        parameters: list[Any] = [self.workspace_id]
        for column, value in (
            ("owner_type", owner_type),
            ("owner_id", owner_id),
            ("session_id", session_id),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        parameters.append(bounded_count(limit))
        values: list[MemorySnapshot] = []
        with self.database.connection() as connection:
            rows = connection.execute(
                self.snapshot_select()
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC LIMIT %s",
                tuple(parameters),
            ).fetchall()
            for row in rows:
                values.append(
                    self.snapshot_from_row(
                        row,
                        self.snapshot_items(connection, str(row[0])),
                    )
                )
        return values

    def set_snapshot_status(
        self,
        snapshot_id: str,
        status: str,
    ) -> MemorySnapshot:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                UPDATE omnix_memory_snapshots
                   SET status = %s
                 WHERE id = %s AND workspace_id = %s
                 RETURNING id, owner_type, owner_id, revision, created_at,
                           session_id, token_estimate, refreshed_at
                """,
                (status, snapshot_id, self.workspace_id),
            ).fetchone()
            if row is None:
                from app.platform.assistant_memory.repository import MemoryNotFoundError

                raise MemoryNotFoundError(snapshot_id)
            items = self.snapshot_items(connection, snapshot_id)
        return self.snapshot_from_row(row, items)
