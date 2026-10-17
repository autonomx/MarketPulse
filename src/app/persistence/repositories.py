from __future__ import annotations

import json
from typing import Any

from .errors import EntityNotFound, IdempotencyConflict
from .tenant import TenantContext


class PostgresIdempotencyRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def reserve(
        self,
        context: TenantContext,
        *,
        scope: str,
        key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        normalized_scope = str(scope).strip()
        normalized_key = str(key).strip()
        normalized_hash = str(request_hash).strip()
        if not normalized_scope or not normalized_key or not normalized_hash:
            raise ValueError("scope, key, and request_hash are required")
        inserted = self.connection.execute(
            """
            INSERT INTO omnix_idempotency_keys
                (workspace_id, operation_scope, operation_key, request_hash)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING status, request_hash, response, created_at, completed_at
            """,
            (context.workspace_id, normalized_scope, normalized_key, normalized_hash),
        ).fetchone()
        owner = inserted is not None
        row = inserted or self.connection.execute(
            """
            SELECT status, request_hash, response, created_at, completed_at
              FROM omnix_idempotency_keys
             WHERE workspace_id = %s
               AND operation_scope = %s
               AND operation_key = %s
            """,
            (context.workspace_id, normalized_scope, normalized_key),
        ).fetchone()
        if row is None:
            raise RuntimeError("idempotency reservation was not persisted")
        if str(row[1]) != normalized_hash:
            raise IdempotencyConflict(
                f"operation key {normalized_scope}/{normalized_key} was reused with different input"
            )
        return {
            "owner": owner,
            "status": str(row[0]),
            "request_hash": str(row[1]),
            "response": dict(row[2]) if row[2] is not None else None,
            "created_at": row[3].isoformat(),
            "completed_at": row[4].isoformat() if row[4] is not None else None,
        }

    def complete(
        self,
        context: TenantContext,
        *,
        scope: str,
        key: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            UPDATE omnix_idempotency_keys
               SET status = 'completed',
                   response = %s::jsonb,
                   completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
             WHERE workspace_id = %s
               AND operation_scope = %s
               AND operation_key = %s
            RETURNING status, request_hash, response, created_at, completed_at
            """,
            (
                json.dumps(response, sort_keys=True, separators=(",", ":")),
                context.workspace_id,
                scope,
                key,
            ),
        ).fetchone()
        if row is None:
            raise EntityNotFound(f"{scope}/{key}")
        return {
            "owner": False,
            "status": str(row[0]),
            "request_hash": str(row[1]),
            "response": dict(row[2]),
            "created_at": row[3].isoformat(),
            "completed_at": row[4].isoformat(),
        }
