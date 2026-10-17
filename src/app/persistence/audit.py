"""Kernel-owned durable audit event repository."""
from __future__ import annotations

import json
from typing import Any

from .tenant import TenantContext


class PostgresAuditRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def append(
        self,
        context: TenantContext,
        *,
        aggregate_type: str,
        aggregate_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> int:
        row = self.connection.execute(
            """INSERT INTO omnix_audit_events
                   (workspace_id, actor_user_id, aggregate_type, aggregate_id,
                    action, trace_id, payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
               RETURNING id""",
            (context.workspace_id, context.user_id, aggregate_type, aggregate_id,
             action, trace_id,
             json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))),
        ).fetchone()
        return int(row[0])

    def count_events(self) -> int:
        return int(self.connection.execute(
            "SELECT count(*) FROM omnix_audit_events"
        ).fetchone()[0])


class PostgresAuditSink:
    """Writes ``app.security.audit`` events to ``omnix_audit_events`` (WP-4.8).

    Actors that are not users (agent runs, services) and workspaces that do not
    exist are stored as NULL with the raw values kept in the payload.
    """

    def __init__(self, database: Any) -> None:
        self.database = database

    def write(self, event: Any) -> None:
        from .tenant_scope import system_scope

        payload = {**event.details, "outcome": event.outcome}
        if event.actor_user_id:
            payload["actor"] = event.actor_user_id
        with system_scope("audit.write"), self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO omnix_audit_events
                       (workspace_id, actor_user_id, aggregate_type, aggregate_id, action, payload)
                   VALUES ((SELECT id FROM omnix_workspaces WHERE id = %s),
                           (SELECT id FROM omnix_users WHERE id = %s),
                           %s, %s, %s, %s::jsonb)""",
                (event.workspace_id, event.actor_user_id, event.target_type, event.target_id,
                 event.action, json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)),
            )
