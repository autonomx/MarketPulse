"""Row conversion helpers for PostgreSQL owner-aware memory."""
from __future__ import annotations

import json
from typing import Any, cast

from app.conversation.memory_contracts import (
    MAX_MEMORY_SNAPSHOT_ITEMS,
    MemoryCandidate,
    MemorySnapshot,
    MemorySnapshotItem,
)


class OwnerMemoryRowSupport:
    database: Any
    context: Any

    @property
    def workspace_id(self) -> str:
        return self.context.workspace_id

    @staticmethod
    def candidate_select() -> str:
        return (
            "SELECT id, source_session_id, source_message_id, "
            "candidate_fingerprint, proposed_owner_type, proposed_owner_id, "
            "proposed_scope, proposed_scope_id, proposed_category, proposed_kind, "
            "proposed_payload, proposed_supersedes_memory_id, proposed_content, "
            "confidence, source, trust_level, sensitivity, extraction_metadata, "
            "status, created_at, resolved_at FROM omnix_memory_candidates"
        )

    @staticmethod
    def snapshot_select() -> str:
        return (
            "SELECT id, owner_type, owner_id, revision, created_at, session_id, "
            "token_estimate, refreshed_at FROM omnix_memory_snapshots"
        )

    @staticmethod
    def snapshot_items(connection: Any, snapshot_id: str) -> list[Any]:
        return connection.execute(
            "SELECT memory_record_id, record_revision, frozen_content, revoked_at "
            "FROM omnix_memory_snapshot_items "
            "WHERE snapshot_id = %s ORDER BY position ASC LIMIT %s",
            (snapshot_id, MAX_MEMORY_SNAPSHOT_ITEMS),
        ).fetchall()

    @staticmethod
    def candidate_from_row(row: Any) -> MemoryCandidate:
        typed = len(row) >= 21
        if typed:
            kind = str(row[9])
            payload = dict(row[10] or {})
            supersedes = str(row[11]) if row[11] is not None else None
            offset = 3
        else:
            kind = "semantic_fact"
            payload = {}
            supersedes = None
            offset = 0
        return MemoryCandidate(
            id=str(row[0]),
            owner_type=cast(Any, str(row[4])),
            owner_id=str(row[5]),
            source_session_id=str(row[1]) if row[1] is not None else "",
            source_message_id=str(row[2]),
            candidate_fingerprint=str(row[3]),
            proposed_scope=cast(Any, str(row[6])),
            proposed_scope_id=str(row[7]),
            proposed_category=cast(Any, str(row[8])),
            proposed_kind=cast(Any, kind),
            proposed_payload=payload,
            proposed_supersedes_memory_id=supersedes,
            proposed_content=str(row[9 + offset]),
            confidence=float(row[10 + offset]),
            source=cast(Any, str(row[11 + offset])),
            trust_level=cast(Any, str(row[12 + offset])),
            sensitivity=cast(Any, str(row[13 + offset])),
            extraction_metadata=dict(row[14 + offset] or {}),
            status=cast(Any, str(row[15 + offset])),
            created_at=row[16 + offset].isoformat(),
            resolved_at=(
                row[17 + offset].isoformat()
                if row[17 + offset] is not None
                else None
            ),
        )

    @staticmethod
    def snapshot_from_row(row: Any, item_rows: list[Any]) -> MemorySnapshot:
        return MemorySnapshot(
            id=str(row[0]),
            owner_type=cast(Any, str(row[1])),
            owner_id=str(row[2]),
            revision=int(row[3]),
            created_at=row[4].isoformat(),
            session_id=str(row[5]),
            token_estimate=int(row[6]),
            refreshed_at=row[7].isoformat() if row[7] is not None else None,
            items=[
                MemorySnapshotItem(
                    memory_record_id=str(item[0]),
                    record_revision=int(item[1]),
                    frozen_content=str(item[2] or ""),
                    revoked_at=item[3].isoformat() if item[3] is not None else None,
                )
                for item in item_rows
            ],
        )

    def append_event(
        self,
        connection: Any,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO omnix_memory_events
                (workspace_id, entity_type, entity_id, event_type, payload)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            """,
            (
                self.workspace_id,
                entity_type,
                entity_id,
                event_type,
                json.dumps(payload, sort_keys=True),
            ),
        )
