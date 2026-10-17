from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.narration.narrative_engine.authority import DeliveryMode
from app.apps.rpg.narration.narrative_engine.delivery import (
    NarrativeDeliveryAdvance,
    NarrativeDeliveryConflict,
    NarrativeDeliveryRecord,
)

from app.apps.rpg.foundation.persistence.rpg_repository import canonical_json
from app.persistence.tenant import TenantContext


_COLUMNS = """
workspace_id, response_id, semantic_hash, mode, status,
block_ids_jsonb, delivered_block_ids_jsonb, next_index, revision,
cancel_reason, metadata_jsonb, created_at, updated_at, completed_at
"""


def _record(row: Any) -> NarrativeDeliveryRecord:
    return NarrativeDeliveryRecord(
        response_id=str(row[1]),
        semantic_hash=str(row[2]),
        mode=DeliveryMode(str(row[3])),
        status=str(row[4]),
        block_ids=tuple(str(item) for item in row[5]),
        delivered_block_ids=tuple(str(item) for item in row[6]),
        next_index=int(row[7]),
        revision=int(row[8]),
        cancel_reason=str(row[9] or ""),
        metadata=dict(row[10] or {}),
    )


class PostgresRpgNarrativeDeliveryRepository:
    """Mutable cursor persistence for immutable canonical narrative responses."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def open(
        self,
        context: TenantContext,
        *,
        response_id: str,
        semantic_hash: str,
        mode: DeliveryMode,
        block_ids: tuple[str, ...],
        metadata: Mapping[str, Any] | None = None,
    ) -> NarrativeDeliveryRecord:
        response = self.connection.execute(
            """
            SELECT content_hash
              FROM omnix_rpg_narrative_responses
             WHERE workspace_id = %s AND response_id = %s
             FOR SHARE
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        if response is None:
            raise NarrativeDeliveryConflict(
                f"canonical response must be persisted before delivery: {response_id}"
            )
        if str(response[0]) != semantic_hash:
            raise NarrativeDeliveryConflict(
                f"semantic hash differs from persisted response: {response_id}"
            )

        complete = mode is DeliveryMode.BLOCKING
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_rpg_narrative_deliveries (
                workspace_id, response_id, semantic_hash, mode, status,
                block_ids_jsonb, delivered_block_ids_jsonb, next_index,
                metadata_jsonb, completed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                %s::jsonb, CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            ON CONFLICT DO NOTHING
            RETURNING {_COLUMNS}
            """,
            (
                context.workspace_id,
                response_id,
                semantic_hash,
                mode.value,
                "complete" if complete else "pending",
                canonical_json(list(block_ids)),
                canonical_json(list(block_ids) if complete else []),
                len(block_ids) if complete else 0,
                canonical_json(dict(metadata or {})),
                complete,
            ),
        ).fetchone()
        if row is not None:
            return _record(row)

        existing = self._get_for_update(context, response_id)
        if existing is None:
            raise NarrativeDeliveryConflict(
                f"delivery disappeared while opening: {response_id}"
            )
        self._validate(existing, semantic_hash, block_ids)
        if (
            mode is DeliveryMode.BLOCKING
            and not existing.complete
            and not existing.cancelled
        ):
            upgraded = self.connection.execute(
                f"""
                UPDATE omnix_rpg_narrative_deliveries
                   SET mode = 'blocking',
                       status = 'complete',
                       delivered_block_ids_jsonb = block_ids_jsonb,
                       next_index = jsonb_array_length(block_ids_jsonb),
                       revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP,
                       completed_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND response_id = %s
                RETURNING {_COLUMNS}
                """,
                (context.workspace_id, response_id),
            ).fetchone()
            if upgraded is None:
                raise NarrativeDeliveryConflict(
                    f"delivery changed while upgrading to blocking: {response_id}"
                )
            return _record(upgraded)
        return existing

    def get(
        self,
        context: TenantContext,
        response_id: str,
    ) -> NarrativeDeliveryRecord | None:
        row = self.connection.execute(
            f"""
            SELECT {_COLUMNS}
              FROM omnix_rpg_narrative_deliveries
             WHERE workspace_id = %s AND response_id = %s
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        return _record(row) if row is not None else None

    def advance(
        self,
        context: TenantContext,
        response_id: str,
        *,
        expected_semantic_hash: str,
    ) -> NarrativeDeliveryAdvance:
        record = self._get_for_update(context, response_id)
        if record is None:
            raise NarrativeDeliveryConflict(f"unknown narrative delivery: {response_id}")
        self._validate(record, expected_semantic_hash, record.block_ids)
        if record.complete or record.cancelled:
            return NarrativeDeliveryAdvance(record)
        if record.next_index >= len(record.block_ids):
            row = self.connection.execute(
                f"""
                UPDATE omnix_rpg_narrative_deliveries
                   SET status = 'complete',
                       completed_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND response_id = %s
                RETURNING {_COLUMNS}
                """,
                (context.workspace_id, response_id),
            ).fetchone()
            return NarrativeDeliveryAdvance(_record(row))

        block_id = record.block_ids[record.next_index]
        delivered = (*record.delivered_block_ids, block_id)
        next_index = record.next_index + 1
        complete = next_index == len(record.block_ids)
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_narrative_deliveries
               SET status = %s,
                   delivered_block_ids_jsonb = %s::jsonb,
                   next_index = %s,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
             WHERE workspace_id = %s AND response_id = %s
            RETURNING {_COLUMNS}
            """,
            (
                "complete" if complete else "streaming",
                canonical_json(list(delivered)),
                next_index,
                complete,
                context.workspace_id,
                response_id,
            ),
        ).fetchone()
        if row is None:
            raise NarrativeDeliveryConflict(
                f"delivery changed while publishing block: {response_id}"
            )
        return NarrativeDeliveryAdvance(_record(row), block_id)

    def cancel(
        self,
        context: TenantContext,
        response_id: str,
        *,
        expected_semantic_hash: str,
        reason: str,
    ) -> NarrativeDeliveryRecord:
        record = self._get_for_update(context, response_id)
        if record is None:
            raise NarrativeDeliveryConflict(f"unknown narrative delivery: {response_id}")
        self._validate(record, expected_semantic_hash, record.block_ids)
        if record.cancelled:
            return record
        if record.complete or record.next_index > 0:
            raise NarrativeDeliveryConflict(
                "canonical delivery can only be cancelled before first publication"
            )
        row = self.connection.execute(
            f"""
            UPDATE omnix_rpg_narrative_deliveries
               SET status = 'cancelled',
                   cancel_reason = %s,
                   revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP,
                   completed_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND response_id = %s
            RETURNING {_COLUMNS}
            """,
            (
                reason or "cancelled_before_publication",
                context.workspace_id,
                response_id,
            ),
        ).fetchone()
        if row is None:
            raise NarrativeDeliveryConflict(
                f"delivery changed while cancelling: {response_id}"
            )
        return _record(row)

    def _get_for_update(
        self,
        context: TenantContext,
        response_id: str,
    ) -> NarrativeDeliveryRecord | None:
        row = self.connection.execute(
            f"""
            SELECT {_COLUMNS}
              FROM omnix_rpg_narrative_deliveries
             WHERE workspace_id = %s AND response_id = %s
             FOR UPDATE
            """,
            (context.workspace_id, response_id),
        ).fetchone()
        return _record(row) if row is not None else None

    @staticmethod
    def _validate(
        record: NarrativeDeliveryRecord,
        semantic_hash: str,
        block_ids: tuple[str, ...],
    ) -> None:
        if record.semantic_hash != semantic_hash:
            raise NarrativeDeliveryConflict(
                f"semantic hash mismatch for narrative delivery {record.response_id}"
            )
        if record.block_ids != block_ids:
            raise NarrativeDeliveryConflict(
                f"canonical block order changed for narrative delivery {record.response_id}"
            )
