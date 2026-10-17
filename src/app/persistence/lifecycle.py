from __future__ import annotations

import json
from typing import Any

from .audit import PostgresAuditRepository
from .job_repository import PostgresJobRepository
from .outbox_repository import PostgresOutboxRepository


class PostgresLifecycleRepository:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.jobs = PostgresJobRepository(connection)
        self.audit = PostgresAuditRepository(connection)
        self.outbox = PostgresOutboxRepository(connection)

    def capacity_report(self) -> dict[str, Any]:
        outbox_counts = self.outbox.retention_counts()
        from .declarations import module_capacity_counts

        row = self.connection.execute("SELECT pg_database_size(current_database())").fetchone()
        policy = self.connection.execute(
            """
            SELECT max_outbox_payload_bytes, max_jsonb_record_bytes,
                   disk_warning_percent, disk_hard_stop_percent, cleanup_batch_size
              FROM omnix_capacity_policy WHERE singleton = TRUE
            """
        ).fetchone()
        return {
            "database_bytes": int(row[0]),
            "counts": {
                "outbox_events": outbox_counts["outbox_events"],
                "outbox_consumer_inbox": outbox_counts["outbox_consumer_inbox"],
                "outbox_dead_letters": outbox_counts["outbox_dead_letters"],
                "job_events": self.jobs.count_job_events(),
                "audit_events": self.audit.count_events(),
                # Row counts modules declare (declarations.py, PA-2.2).
                **{name: counter.count(self.connection) for name, counter in module_capacity_counts().items()},
            },
            "max_outbox_payload_bytes_observed": outbox_counts["max_outbox_payload_bytes"],
            "policy": {
                "max_outbox_payload_bytes": int(policy[0]),
                "max_jsonb_record_bytes": int(policy[1]),
                "disk_warning_percent": int(policy[2]),
                "disk_hard_stop_percent": int(policy[3]),
                "cleanup_batch_size": int(policy[4]),
            },
        }

    def cleanup(self, *, batch_size: int | None = None) -> dict[str, Any]:
        before = self.capacity_report()
        resolved_batch = max(
            1,
            min(
                int(batch_size or before["policy"]["cleanup_batch_size"]),
                100_000,
            ),
        )
        run_id = int(
            self.connection.execute(
                """
                INSERT INTO omnix_lifecycle_cleanup_runs (
                    status, capacity_before
                ) VALUES ('running', %s::jsonb)
                RETURNING id
                """,
                (json.dumps(before, sort_keys=True, separators=(",", ":")),),
            ).fetchone()[0]
        )
        deleted: dict[str, int] = {}
        try:
            deleted["consumer_inbox"] = self._delete_with_policy(
                record_type="outbox_consumer_inbox",
                batch_size=resolved_batch,
            )
            deleted["outbox_events"] = self._delete_with_policy(
                record_type="outbox_events",
                batch_size=resolved_batch,
            )
            deleted["dead_letters"] = self._delete_with_policy(
                record_type="outbox_dead_letters",
                batch_size=resolved_batch,
            )
            deleted["runtime_failure_evidence"] = self._delete_with_policy(
                record_type="runtime_failure_evidence",
                batch_size=resolved_batch,
            )
            from .declarations import module_retention

            for record_type, declaration in module_retention().items():
                if declaration.capacity_cleanup:
                    deleted[record_type] = self._delete_with_policy(record_type=record_type, batch_size=resolved_batch)
            after = self.capacity_report()
            self.connection.execute(
                """
                UPDATE omnix_lifecycle_cleanup_runs
                   SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                       deleted_counts = %s::jsonb, capacity_after = %s::jsonb
                 WHERE id = %s
                """,
                (
                    json.dumps(deleted, sort_keys=True, separators=(",", ":")),
                    json.dumps(after, sort_keys=True, separators=(",", ":")),
                    run_id,
                ),
            )
            return {"ok": True, "run_id": run_id, "deleted": deleted, "before": before, "after": after}
        except Exception as exc:
            self.connection.execute(
                """
                UPDATE omnix_lifecycle_cleanup_runs
                   SET status = 'failed', completed_at = CURRENT_TIMESTAMP,
                       error = %s
                 WHERE id = %s
                """,
                (f"{exc.__class__.__name__}: {exc}"[:2000], run_id),
            )
            raise

    def _delete_with_policy(self, *, record_type: str, batch_size: int) -> int:
        policy = self.connection.execute(
            "SELECT retention_days, enabled FROM omnix_retention_policies WHERE record_type = %s",
            (record_type,),
        ).fetchone()
        if policy is None or not bool(policy[1]):
            return 0
        if record_type.startswith("outbox_"):
            return self.outbox.delete_retained(
                record_type=record_type,
                retention_days=int(policy[0]),
                batch_size=batch_size,
            )
        if record_type == "runtime_failure_evidence":
            cursor = self.connection.execute(
                """DELETE FROM omnix_runtime_failure_evidence
                    WHERE id IN (
                        SELECT id FROM omnix_runtime_failure_evidence
                         WHERE created_at < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                         ORDER BY created_at, id LIMIT %s
                    )""",
                (int(policy[0]), batch_size),
            )
            return int(cursor.rowcount)
        from .declarations import module_retention

        declaration = module_retention().get(record_type)
        if declaration is not None and declaration.capacity_cleanup:
            return declaration.delete(self.connection, int(policy[0]), batch_size)
        raise ValueError(f"unsupported lifecycle retention type: {record_type}")
