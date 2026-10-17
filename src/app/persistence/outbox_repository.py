from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .tenant import TenantContext


class OutboxDeliveryConflict(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _request_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class PostgresOutboxRepository:
    """Transactional outbox with revisioned event envelopes and ordered claims."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def retention_counts(self) -> dict[str, int]:
        row = self.connection.execute(
            """SELECT
                   (SELECT count(*) FROM omnix_outbox_events),
                   (SELECT count(*) FROM omnix_outbox_consumer_inbox),
                   (SELECT count(*) FROM omnix_outbox_dead_letters),
                   (SELECT COALESCE(max(pg_column_size(payload)), 0) FROM omnix_outbox_events)"""
        ).fetchone()
        return {
            "outbox_events": int(row[0]),
            "outbox_consumer_inbox": int(row[1]),
            "outbox_dead_letters": int(row[2]),
            "max_outbox_payload_bytes": int(row[3]),
        }

    def delete_retained(
        self, *, record_type: str, retention_days: int, batch_size: int
    ) -> int:
        batch = max(1, min(int(batch_size), 100_000))
        if record_type == "outbox_consumer_inbox":
            statement = """DELETE FROM omnix_outbox_consumer_inbox
                WHERE (consumer_id, event_key) IN (
                    SELECT consumer_id, event_key FROM omnix_outbox_consumer_inbox
                     WHERE status IN ('completed', 'dead_letter')
                       AND updated_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                     ORDER BY updated_at, consumer_id, event_key LIMIT %s
                )"""
        elif record_type == "outbox_events":
            statement = """DELETE FROM omnix_outbox_events
                WHERE id IN (
                    SELECT id FROM omnix_outbox_events
                     WHERE status = 'published'
                       AND published_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                       AND NOT EXISTS (
                           SELECT 1 FROM omnix_outbox_consumer_inbox AS inbox
                            WHERE inbox.event_key = omnix_outbox_events.event_key
                       )
                     ORDER BY published_at, id LIMIT %s
                )"""
        elif record_type == "outbox_dead_letters":
            statement = """DELETE FROM omnix_outbox_dead_letters
                WHERE id IN (
                    SELECT id FROM omnix_outbox_dead_letters
                     WHERE resolved_at IS NOT NULL
                       AND resolved_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                     ORDER BY resolved_at, id LIMIT %s
                )"""
        else:
            raise ValueError(f"unsupported outbox retention type: {record_type}")
        cursor = self.connection.execute(statement, (int(retention_days), batch))
        return int(cursor.rowcount)

    def lag(self, context: TenantContext) -> dict[str, Any]:
        """Relay lag of one workspace: undelivered events and the oldest age."""
        row = self.connection.execute(
            """SELECT count(*) FILTER (WHERE status <> 'dead_letter'),
                      COALESCE(EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - min(created_at)
                               FILTER (WHERE status <> 'dead_letter')), 0),
                      count(*) FILTER (WHERE status = 'dead_letter')
                 FROM omnix_outbox_events
                WHERE workspace_id = %s AND status <> 'published'""",
            (context.workspace_id,),
        ).fetchone()
        return {
            "unpublished": int(row[0]),
            "oldest_unpublished_age_seconds": round(float(row[1]), 3),
            "dead_letters": int(row[2]),
        }

    def _next_sequence(self, workspace_id: str, ordering_key: str) -> int:
        row = self.connection.execute(
            """
            INSERT INTO omnix_outbox_sequences (workspace_id, ordering_key, next_sequence)
            VALUES (%s, %s, 2)
            ON CONFLICT (workspace_id, ordering_key) DO UPDATE
               SET next_sequence = omnix_outbox_sequences.next_sequence + 1
            RETURNING next_sequence - 1
            """,
            (workspace_id, ordering_key),
        ).fetchone()
        return int(row[0])

    def append(
        self,
        context: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        ordering_key: str | None = None,
        schema_version: int = 1,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        event_key: str | None = None,
        skip_existing: bool = False,
    ) -> int:
        """Append one event; returns its id.

        With ``skip_existing`` an event whose ``event_key`` is already in the
        outbox is not appended again, and the existing event's id is returned:
        a producer that may publish twice (a retried turn) publishes once.
        """
        key = event_key or uuid.uuid4().hex
        sequence = (
            self._next_sequence(context.workspace_id, ordering_key)
            if ordering_key is not None
            else None
        )
        row = self.connection.execute(
            """
            INSERT INTO omnix_outbox_events (
                event_key, workspace_id, aggregate_type, aggregate_id,
                event_type, ordering_key, aggregate_sequence, schema_version,
                correlation_id, causation_id, payload, occurred_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                CURRENT_TIMESTAMP
            )
            """
            + (" ON CONFLICT (event_key) DO NOTHING" if skip_existing else "")
            + " RETURNING id",
            (
                key,
                context.workspace_id,
                aggregate_type,
                aggregate_id,
                event_type,
                ordering_key,
                sequence,
                max(1, int(schema_version)),
                correlation_id,
                causation_id,
                _json(payload),
            ),
        ).fetchone()
        if row is None:
            row = self.connection.execute(
                "SELECT id FROM omnix_outbox_events WHERE event_key = %s", (key,),
            ).fetchone()
        return int(row[0])

    def claim_batch(
        self,
        *,
        consumer_id: str,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        token = uuid.uuid4().hex
        rows = self.connection.execute(
            """
            WITH candidates AS (
                SELECT candidate.id
                  FROM omnix_outbox_events AS candidate
                 WHERE (
                           (
                               candidate.status IN ('pending', 'retrying')
                               AND candidate.available_at <= CURRENT_TIMESTAMP
                           )
                           OR (
                               candidate.status = 'claimed'
                               AND candidate.claim_expires_at <= CURRENT_TIMESTAMP
                           )
                       )
                   AND (
                       candidate.ordering_key IS NULL
                       OR NOT EXISTS (
                           SELECT 1
                             FROM omnix_outbox_events AS earlier
                            WHERE earlier.workspace_id = candidate.workspace_id
                              AND earlier.ordering_key = candidate.ordering_key
                              AND earlier.id < candidate.id
                              AND earlier.status <> 'published'
                       )
                   )
                 ORDER BY candidate.id ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
            )
            UPDATE omnix_outbox_events AS events
               SET status = 'claimed', claimed_by = %s, claim_token = %s,
                   claim_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                   publication_attempted_at = CURRENT_TIMESTAMP,
                   attempt_count = attempt_count + 1
              FROM candidates
             WHERE events.id = candidates.id
            RETURNING events.id, events.event_key, events.workspace_id,
                      events.aggregate_type, events.aggregate_id, events.event_type,
                      events.ordering_key, events.aggregate_sequence,
                      events.schema_version, events.correlation_id,
                      events.causation_id, events.payload, events.attempt_count,
                      events.claim_token, events.claim_expires_at,
                      events.occurred_at, events.created_at
            """,
            (max(1, min(int(limit), 500)), consumer_id, token, max(1, lease_seconds)),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "event_key": str(row[1]),
                "workspace_id": str(row[2]),
                "aggregate_type": str(row[3]),
                "aggregate_id": str(row[4]),
                "event_type": str(row[5]),
                "ordering_key": str(row[6]) if row[6] is not None else None,
                "aggregate_sequence": int(row[7]) if row[7] is not None else None,
                "schema_version": int(row[8]),
                "correlation_id": str(row[9]) if row[9] is not None else None,
                "causation_id": str(row[10]) if row[10] is not None else None,
                "payload": dict(row[11]),
                "attempt_count": int(row[12]),
                "claim_token": str(row[13]),
                "claim_expires_at": row[14].isoformat(),
                "occurred_at": row[15].isoformat(),
                "created_at": row[16].isoformat(),
            }
            for row in rows
        ]

    def mark_published(self, *, event_id: int, claim_token: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_outbox_events
               SET status = 'published', published_at = CURRENT_TIMESTAMP,
                   claimed_by = NULL, claim_token = NULL, claim_expires_at = NULL,
                   last_error = NULL
             WHERE id = %s AND status = 'claimed' AND claim_token = %s
            """,
            (event_id, claim_token),
        )
        return cursor.rowcount == 1

    def mark_retry(
        self,
        *,
        event_id: int,
        claim_token: str,
        error: str,
        retry_delay_seconds: int = 0,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_outbox_events
               SET status = 'retrying',
                   available_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                   claimed_by = NULL, claim_token = NULL, claim_expires_at = NULL,
                   last_error = %s
             WHERE id = %s AND status = 'claimed' AND claim_token = %s
            """,
            (max(0, int(retry_delay_seconds)), error[:2000], event_id, claim_token),
        )
        return cursor.rowcount == 1

    def mark_dead_letter(
        self,
        *,
        event_id: int,
        claim_token: str,
        consumer_id: str,
        reason: str,
    ) -> bool:
        row = self.connection.execute(
            """
            UPDATE omnix_outbox_events
               SET status = 'dead_letter', claimed_by = NULL, claim_token = NULL,
                   claim_expires_at = NULL, last_error = %s
             WHERE id = %s AND status = 'claimed' AND claim_token = %s
            RETURNING workspace_id, event_key, payload, attempt_count
            """,
            (reason[:2000], event_id, claim_token),
        ).fetchone()
        if row is None:
            return False
        self.connection.execute(
            """
            INSERT INTO omnix_outbox_dead_letters (
                workspace_id, consumer_id, event_key, reason, payload, attempt_count
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (consumer_id, event_key) DO UPDATE
               SET reason = EXCLUDED.reason,
                   payload = EXCLUDED.payload,
                   attempt_count = EXCLUDED.attempt_count,
                   resolved_at = NULL
            """,
            (
                str(row[0]),
                consumer_id,
                str(row[1]),
                reason[:2000],
                _json(dict(row[2])),
                int(row[3]),
            ),
        )
        return True


class PostgresOutboxConsumerRepository:
    """Durable per-consumer inbox for at-least-once delivery deduplication."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def begin(
        self,
        *,
        consumer_id: str,
        event_key: str,
        lease_seconds: int = 30,
    ) -> dict[str, Any]:
        token = uuid.uuid4().hex
        row = self.connection.execute(
            """
            INSERT INTO omnix_outbox_consumer_inbox (
                consumer_id, event_key, status, claim_token,
                claim_expires_at, attempt_count
            ) VALUES (
                %s, %s, 'processing', %s,
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'), 1
            )
            ON CONFLICT (consumer_id, event_key) DO UPDATE
               SET status = 'processing', claim_token = EXCLUDED.claim_token,
                   claim_expires_at = EXCLUDED.claim_expires_at,
                   attempt_count = omnix_outbox_consumer_inbox.attempt_count + 1,
                   last_error = NULL, updated_at = CURRENT_TIMESTAMP
             WHERE omnix_outbox_consumer_inbox.status = 'failed'
                OR (
                    omnix_outbox_consumer_inbox.status = 'processing'
                    AND omnix_outbox_consumer_inbox.claim_expires_at <= CURRENT_TIMESTAMP
                )
            RETURNING status, claim_token, claim_expires_at, attempt_count,
                      completed_at, result
            """,
            (consumer_id, event_key, token, max(1, lease_seconds)),
        ).fetchone()
        if row is not None:
            return {
                "state": "claimed",
                "claim_token": str(row[1]),
                "claim_expires_at": row[2].isoformat(),
                "attempt_count": int(row[3]),
                "completed_at": row[4].isoformat() if row[4] is not None else None,
                "result": dict(row[5]) if row[5] is not None else None,
            }
        existing = self.connection.execute(
            """
            SELECT status, claim_token, claim_expires_at, attempt_count,
                   completed_at, result
              FROM omnix_outbox_consumer_inbox
             WHERE consumer_id = %s AND event_key = %s
            """,
            (consumer_id, event_key),
        ).fetchone()
        if existing is None:
            raise OutboxDeliveryConflict("consumer inbox reservation disappeared")
        states = {"completed": "duplicate_completed", "dead_letter": "dead_lettered"}
        return {
            "state": states.get(str(existing[0]), "busy"),
            "claim_token": None,
            "claim_expires_at": existing[2].isoformat() if existing[2] is not None else None,
            "attempt_count": int(existing[3]),
            "completed_at": existing[4].isoformat() if existing[4] is not None else None,
            "result": dict(existing[5]) if existing[5] is not None else None,
        }

    def complete(
        self,
        *,
        consumer_id: str,
        event_key: str,
        claim_token: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_outbox_consumer_inbox
               SET status = 'completed', result = %s::jsonb,
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                   claim_token = NULL, claim_expires_at = NULL, last_error = NULL
             WHERE consumer_id = %s AND event_key = %s
               AND status = 'processing' AND claim_token = %s
               AND claim_expires_at > CURRENT_TIMESTAMP
            """,
            (_json(result or {}), consumer_id, event_key, claim_token),
        )
        return cursor.rowcount == 1

    def fail(
        self,
        *,
        consumer_id: str,
        event_key: str,
        claim_token: str,
        error: str,
        max_attempts: int = 5,
    ) -> str:
        row = self.connection.execute(
            """
            UPDATE omnix_outbox_consumer_inbox
               SET status = CASE WHEN attempt_count >= %s THEN 'dead_letter' ELSE 'failed' END,
                   last_error = %s, updated_at = CURRENT_TIMESTAMP,
                   claim_token = NULL, claim_expires_at = NULL
             WHERE consumer_id = %s AND event_key = %s
               AND status = 'processing' AND claim_token = %s
            RETURNING status, attempt_count
            """,
            (max(1, int(max_attempts)), error[:2000], consumer_id, event_key, claim_token),
        ).fetchone()
        if row is None:
            raise OutboxDeliveryConflict("consumer failure rejected by claim guard")
        status = str(row[0])
        if status == "dead_letter":
            event = self.connection.execute(
                "SELECT workspace_id, payload FROM omnix_outbox_events WHERE event_key = %s",
                (event_key,),
            ).fetchone()
            if event is not None:
                self.connection.execute(
                    """
                    INSERT INTO omnix_outbox_dead_letters (
                        workspace_id, consumer_id, event_key, reason, payload, attempt_count
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (consumer_id, event_key) DO UPDATE
                       SET reason = EXCLUDED.reason,
                           payload = EXCLUDED.payload,
                           attempt_count = EXCLUDED.attempt_count,
                           resolved_at = NULL
                    """,
                    (
                        str(event[0]),
                        consumer_id,
                        event_key,
                        error[:2000],
                        _json(dict(event[1])),
                        int(row[1]),
                    ),
                )
        return status

    def reset_for_replay(self, *, consumer_id: str, event_key: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM omnix_outbox_consumer_inbox "
            "WHERE consumer_id = %s AND event_key = %s "
            "AND status IN ('completed', 'failed', 'dead_letter')",
            (consumer_id, event_key),
        )
        return cursor.rowcount == 1

    def undelivered(self, *, consumer_id: str, aggregate_types: list[str], after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        """Unpublished events of these aggregate types this consumer has not completed or dead-lettered, by id."""
        rows = self.connection.execute(
            """SELECT events.id, events.event_key, events.event_type
                 FROM omnix_outbox_events AS events
                WHERE events.aggregate_type = ANY(%s) AND events.status IN ('pending', 'retrying', 'claimed')
                  AND events.id > %s
                  AND NOT EXISTS (
                      SELECT 1 FROM omnix_outbox_consumer_inbox AS inbox
                       WHERE inbox.consumer_id = %s AND inbox.event_key = events.event_key
                         AND inbox.status IN ('completed', 'dead_letter'))
                ORDER BY events.id
                LIMIT %s""",
            (sorted(set(aggregate_types)), int(after_id), consumer_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [{"id": int(row[0]), "event_key": str(row[1]), "event_type": str(row[2])} for row in rows]

    def dead_letter(self, *, consumer_id: str, event_key: str, reason: str) -> bool:
        """Close one consumer's delivery of an event as dead-lettered, unless it is being processed (PA-4.3).

        Only this consumer's delivery ends: the event still reaches its other
        consumers, and the relay skips a dead-lettered delivery.
        """
        row = self.connection.execute(
            """INSERT INTO omnix_outbox_consumer_inbox AS inbox (consumer_id, event_key, status, last_error)
               VALUES (%s, %s, 'dead_letter', %s)
               ON CONFLICT (consumer_id, event_key) DO UPDATE
                  SET status = 'dead_letter', last_error = EXCLUDED.last_error,
                      claim_token = NULL, claim_expires_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE inbox.status = 'failed'
                   OR (inbox.status = 'processing' AND inbox.claim_expires_at <= CURRENT_TIMESTAMP)
               RETURNING attempt_count""",
            (consumer_id, event_key, reason[:2000]),
        ).fetchone()
        if row is None:
            return False
        self.connection.execute(
            """INSERT INTO omnix_outbox_dead_letters (workspace_id, consumer_id, event_key, reason, payload, attempt_count)
               SELECT workspace_id, %s, event_key, %s, payload, GREATEST(%s, 1) FROM omnix_outbox_events WHERE event_key = %s
               ON CONFLICT (consumer_id, event_key) DO UPDATE
                  SET reason = EXCLUDED.reason, attempt_count = EXCLUDED.attempt_count, resolved_at = NULL""",
            (consumer_id, reason[:2000], int(row[0]), event_key),
        )
        return True


class PostgresSideEffectRepository:
    """Durable idempotency receipts for externally visible side effects."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def reserve(
        self,
        context: TenantContext,
        *,
        effect_scope: str,
        idempotency_key: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        digest = _request_hash(request)
        row = self.connection.execute(
            """
            INSERT INTO omnix_side_effect_receipts (
                workspace_id, effect_scope, idempotency_key, request_hash
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING status, request_hash, result, error
            """,
            (context.workspace_id, effect_scope, idempotency_key, digest),
        ).fetchone()
        owner = row is not None
        if row is None:
            row = self.connection.execute(
                """
                SELECT status, request_hash, result, error
                  FROM omnix_side_effect_receipts
                 WHERE workspace_id = %s AND effect_scope = %s AND idempotency_key = %s
                """,
                (context.workspace_id, effect_scope, idempotency_key),
            ).fetchone()
        if row is None:
            raise OutboxDeliveryConflict("side-effect reservation disappeared")
        if str(row[1]) != digest:
            raise OutboxDeliveryConflict(
                "idempotency key was reused with a different side-effect request"
            )
        return {
            "owner": owner,
            "status": str(row[0]),
            "result": dict(row[2]) if row[2] is not None else None,
            "error": str(row[3]) if row[3] is not None else None,
        }

    def complete(
        self,
        context: TenantContext,
        *,
        effect_scope: str,
        idempotency_key: str,
        result: dict[str, Any],
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_side_effect_receipts
               SET status = 'completed', result = %s::jsonb, error = NULL,
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND effect_scope = %s AND idempotency_key = %s
               AND status = 'reserved'
            """,
            (_json(result), context.workspace_id, effect_scope, idempotency_key),
        )
        return cursor.rowcount == 1
