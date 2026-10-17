from __future__ import annotations

import json
from typing import Any


# Far above any cohort size; keeps node reads bounded (WP-5.5).
MAX_NODES_PER_READ = 1000


class RuntimeNodeConflict(RuntimeError):
    pass


class PostgresRuntimeCoordinationRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def register(
        self,
        *,
        node_id: str,
        node_type: str,
        software_version: str,
        lease_seconds: int = 30,
        capabilities: list[str] | None = None,
        resource_classes: list[str] | None = None,
        process_id: str | None = None,
        host_fingerprint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            INSERT INTO omnix_runtime_nodes (
                id, node_type, status, capabilities, resource_classes,
                software_version, process_id, host_fingerprint,
                heartbeat_at, lease_expires_at, metadata
            ) VALUES (
                %s, %s, 'active', %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                %s::jsonb
            )
            ON CONFLICT (id) DO UPDATE
               SET node_type = EXCLUDED.node_type,
                   status = 'active',
                   capabilities = EXCLUDED.capabilities,
                   resource_classes = EXCLUDED.resource_classes,
                   software_version = EXCLUDED.software_version,
                   process_id = EXCLUDED.process_id,
                   host_fingerprint = EXCLUDED.host_fingerprint,
                   heartbeat_at = CURRENT_TIMESTAMP,
                   lease_expires_at = EXCLUDED.lease_expires_at,
                   stopped_at = NULL,
                   metadata = EXCLUDED.metadata
             WHERE omnix_runtime_nodes.status IN ('stale', 'stopped')
                OR omnix_runtime_nodes.lease_expires_at <= CURRENT_TIMESTAMP
            RETURNING id, node_type, status, capabilities, resource_classes,
                      software_version, heartbeat_at, lease_expires_at
            """,
            (
                node_id,
                node_type,
                capabilities or [],
                resource_classes or [],
                software_version,
                process_id,
                host_fingerprint,
                max(1, min(int(lease_seconds), 3600)),
                json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeNodeConflict(f"runtime node is already live: {node_id}")
        return self._record(row)

    def heartbeat(self, *, node_id: str, lease_seconds: int = 30) -> dict[str, Any]:
        row = self.connection.execute(
            """
            UPDATE omnix_runtime_nodes
               SET heartbeat_at = clock_timestamp(),
                   lease_expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
             WHERE id = %s AND status IN ('active', 'draining')
               AND lease_expires_at > clock_timestamp()
            RETURNING id, node_type, status, capabilities, resource_classes,
                      software_version, heartbeat_at, lease_expires_at
            """,
            (max(1, min(int(lease_seconds), 3600)), node_id),
        ).fetchone()
        if row is None:
            raise RuntimeNodeConflict(f"runtime node heartbeat rejected: {node_id}")
        return self._record(row)

    def begin_draining(self, node_id: str) -> bool:
        cursor = self.connection.execute(
            "UPDATE omnix_runtime_nodes SET status = 'draining' "
            "WHERE id = %s AND status = 'active' AND lease_expires_at > CURRENT_TIMESTAMP",
            (node_id,),
        )
        return cursor.rowcount == 1

    def stop(self, node_id: str) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE omnix_runtime_nodes
               SET status = 'stopped', stopped_at = CURRENT_TIMESTAMP,
                   lease_expires_at = CURRENT_TIMESTAMP
             WHERE id = %s AND status IN ('active', 'draining', 'stale')
            """,
            (node_id,),
        )
        return cursor.rowcount == 1

    def mark_stale_nodes(self) -> list[str]:
        rows = self.connection.execute(
            """
            UPDATE omnix_runtime_nodes
               SET status = 'stale'
             WHERE id IN (
                   SELECT id FROM omnix_runtime_nodes
                    WHERE status IN ('active', 'draining')
                      AND lease_expires_at <= CURRENT_TIMESTAMP
                    ORDER BY lease_expires_at, id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED)
            RETURNING id
            """,
            (MAX_NODES_PER_READ,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def live_nodes(self, *, node_type: str | None = None) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, node_type, status, capabilities, resource_classes,
                   software_version, heartbeat_at, lease_expires_at
              FROM omnix_runtime_nodes
             WHERE status IN ('active', 'draining')
               AND lease_expires_at > CURRENT_TIMESTAMP
               AND (%s::text IS NULL OR node_type = %s::text)
             ORDER BY node_type, id
             LIMIT %s
            """,
            (node_type, node_type, MAX_NODES_PER_READ),
        ).fetchall()
        return [self._record(row) for row in rows]

    def record_failure_evidence(
        self,
        *,
        scenario: str,
        outcome: str,
        node_id: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> int:
        row = self.connection.execute(
            """
            INSERT INTO omnix_runtime_failure_evidence (
                scenario, node_id, aggregate_type, aggregate_id, outcome, evidence
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                scenario,
                node_id,
                aggregate_type,
                aggregate_id,
                outcome,
                json.dumps(evidence or {}, sort_keys=True, separators=(",", ":")),
            ),
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "node_type": str(row[1]),
            "status": str(row[2]),
            "capabilities": list(row[3]),
            "resource_classes": list(row[4]),
            "software_version": str(row[5]),
            "heartbeat_at": row[6].isoformat(),
            "lease_expires_at": row[7].isoformat(),
        }
