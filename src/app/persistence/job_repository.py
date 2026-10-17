from __future__ import annotations

import uuid
from typing import Any, Literal

from app.runtime.pagination import MAX_PAGE_SIZE, page_limit

from .execution_repositories import JobClaimConflict
from .execution_repositories import PostgresJobRepository as _BaseJobRepository
from .execution_repositories import (
    foreground_guard_credentials,
    foreground_owner_guard,
    _job,
    _json,
)
from .tenant import TenantContext




def _job_log_compat_entry(level: Any, message: Any, data: Any) -> dict[str, Any]:
    entry = dict(data or {})
    if level is not None:
        entry["level"] = str(level)
    if message is not None:
        entry["message"] = str(message)
    return entry


def _bounded_job_log_limit(limit: int) -> int:
    """Bound log hydration independently of repository collection pagination."""
    return max(1, min(int(limit), 500))


_QUALIFIED_JOB_COLUMNS = """
jobs.id, jobs.workspace_id, jobs.owner_user_id, jobs.module, jobs.job_type,
jobs.status, jobs.resource_class, jobs.priority, jobs.input_payload,
jobs.output_refs, jobs.progress, jobs.error, jobs.attempt_count,
jobs.max_attempts, jobs.available_at, jobs.lease_owner, jobs.lease_token,
jobs.lease_expires_at, jobs.cancel_requested_at, jobs.started_at,
jobs.completed_at, jobs.created_at, jobs.updated_at, jobs.metadata,
jobs.correlation_id
"""
_ACTIVE_CHAT_JOBS = """
jobs.job_type = 'chat.generate'
AND jobs.status IN ('queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested')
AND jobs.metadata #>> '{compat_contract,compat,inline_execution}' = 'true'
"""
_NO_LIVE_CHAT_OWNER = """
NOT EXISTS (
    SELECT 1 FROM omnix_runtime_nodes AS owner
     WHERE owner.id = jobs.metadata #>> '{compat_contract,compat,execution_owner}'
       AND owner.node_type = 'gateway'
       AND owner.metadata ->> 'workspace_id' = jobs.workspace_id
       AND owner.status IN ('active', 'draining')
       AND owner.lease_expires_at > clock_timestamp()
)
"""
_UNSET = object()
# A per-job event read returns at most this many of the newest events.
MAX_JOB_EVENTS_PER_READ = 1000
_JOB_ORDERINGS = {
    "created_asc": "jobs.created_at ASC, jobs.id ASC",
    "created_desc": "jobs.created_at DESC, jobs.id DESC",
    "completed_desc": "jobs.completed_at DESC NULLS LAST, jobs.id DESC",
    "updated_desc": "jobs.updated_at DESC, jobs.id DESC",
}


class PostgresJobRepository(_BaseJobRepository):
    """Job repository with explicitly qualified durable queue operations."""

    def set_created_at_now(self, context: TenantContext, *, job_id: str) -> str | None:
        row = self.connection.execute(
            """UPDATE omnix_jobs SET created_at = clock_timestamp()
                WHERE id = %s AND workspace_id = %s RETURNING created_at""",
            (job_id, context.workspace_id),
        ).fetchone()
        return row[0].isoformat() if row is not None else None

    def delete_job(self, context: TenantContext, *, job_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM omnix_jobs WHERE id = %s AND workspace_id = %s",
            (job_id, context.workspace_id),
        )
        return cursor.rowcount > 0

    def delete_claimed_job(
        self,
        context: TenantContext,
        *,
        job_id: str,
        lease_owner: str,
        lease_token: str,
    ) -> bool:
        cursor = self.connection.execute(
            """DELETE FROM omnix_jobs
                WHERE id = %s AND workspace_id = %s
                  AND lease_owner = %s AND lease_token = %s""",
            (job_id, context.workspace_id, lease_owner, lease_token),
        )
        return cursor.rowcount > 0

    def delete_job_ids(
        self, context: TenantContext, *, job_ids: tuple[str, ...]
    ) -> int:
        if not job_ids:
            return 0
        cursor = self.connection.execute(
            "DELETE FROM omnix_jobs WHERE workspace_id = %s AND id = ANY(%s)",
            (context.workspace_id, list(job_ids)),
        )
        return int(cursor.rowcount)

    def release_interrupted_job(
        self,
        context: TenantContext,
        *,
        job_id: str,
        status: str,
        max_attempts: int,
        error_code: str,
        resume_policy: str,
    ) -> bool:
        retryable = status == "retrying"
        row = self.connection.execute(
            """UPDATE omnix_jobs
                  SET status = %s,
                      max_attempts = %s,
                      available_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE available_at END,
                      error = jsonb_build_object('code', %s::text, 'resume_policy', %s::text),
                      lease_owner = NULL,
                      lease_token = NULL,
                      lease_expires_at = NULL,
                      completed_at = CASE WHEN %s THEN NULL ELSE CURRENT_TIMESTAMP END,
                      updated_at = CURRENT_TIMESTAMP
                WHERE workspace_id = %s AND id = %s RETURNING id""",
            (status, max_attempts, retryable, error_code, resume_policy,
             retryable, context.workspace_id, job_id),
        ).fetchone()
        return row is not None

    def set_max_attempts_to_current(self, context: TenantContext, *, job_id: str) -> bool:
        row = self.connection.execute(
            """UPDATE omnix_jobs SET max_attempts = attempt_count
                WHERE workspace_id = %s AND id = %s RETURNING id""",
            (context.workspace_id, job_id),
        ).fetchone()
        return row is not None

    def import_job(
        self,
        context: TenantContext,
        *,
        job_id: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        job = self.create_job(
            context,
            {
                "id": job_id,
                "module": item.get("module", "legacy"),
                "job_type": item.get("job_type") or item.get("type") or "legacy",
                "resource_class": item.get("resource_class", "cpu"),
                "priority": item.get("priority", 0),
                "max_attempts": item.get("max_attempts", 3),
                "input_payload": item.get("input_payload") or {},
                "metadata": {**dict(item.get("metadata") or {}), "legacy_import": True},
            },
        )
        status = str(item.get("status") or "queued")
        row = self.connection.execute(
            """UPDATE omnix_jobs SET status = %s, output_refs = %s::jsonb,
                      progress = %s::jsonb, error = %s::jsonb,
                      attempt_count = %s, completed_at = %s::timestamptz,
                      updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND workspace_id = %s RETURNING id""",
            (status, _json(item.get("output_refs") or []),
             _json(item.get("progress") or {}),
             _json(item.get("error")) if item.get("error") is not None else None,
             int(item.get("attempt_count", 0)), item.get("completed_at"),
             job["id"], context.workspace_id),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"imported job was not persisted: {job_id}")
        return self.get_job(context, job_id) or job

    def import_job_history(
        self,
        context: TenantContext,
        *,
        job_id: str,
        item: dict[str, Any],
    ) -> None:
        for event in list(item.get("events") or []):
            self.connection.execute(
                """INSERT INTO omnix_job_events (
                       workspace_id, job_id, event_type, payload, created_at
                   ) VALUES (%s, %s, %s, %s::jsonb,
                             COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))""",
                (context.workspace_id, job_id, event.get("event_type", "legacy.event"),
                 _json(event.get("payload") or {}), event.get("created_at")),
            )
        attempt_count = int(item.get("attempt_count", 0))
        lease = dict((item.get("metadata") or {}).get("lease") or {})
        for attempt in range(1, attempt_count + 1):
            token = str(lease.get("token") or lease.get("lease_token") or f"legacy:{job_id}:{attempt}")
            worker = str(lease.get("worker_id") or lease.get("owner_id") or "worker:legacy")
            status = "completed" if item.get("status") == "completed" else str(item.get("status") or "legacy")
            self.connection.execute(
                """INSERT INTO omnix_job_attempts (
                       job_id, attempt, worker_id, lease_token, status,
                       started_at, completed_at, error
                   ) VALUES (%s, %s, %s, %s, %s,
                             COALESCE(%s::timestamptz, CURRENT_TIMESTAMP),
                             %s::timestamptz, %s::jsonb)
                   ON CONFLICT (job_id, attempt) DO NOTHING""",
                (job_id, attempt, worker, token, status, lease.get("claimed_at"),
                 item.get("completed_at"),
                 _json(item.get("error")) if item.get("error") is not None else None),
            )

    def job_exists(self, job_id: str) -> bool:
        return self.connection.execute(
            "SELECT EXISTS (SELECT 1 FROM omnix_jobs WHERE id = %s)", (job_id,)
        ).fetchone()[0]

    def diagnostic_snapshot(self, context: TenantContext) -> dict[str, Any]:
        groups = self.connection.execute(
            """SELECT resource_class, status, count(*),
                      COALESCE(max(EXTRACT(EPOCH FROM (clock_timestamp() - created_at)))
                          FILTER (WHERE status = 'queued'), 0),
                      count(*) FILTER (WHERE lease_expires_at < clock_timestamp())
                 FROM omnix_jobs WHERE workspace_id = %s
                GROUP BY resource_class, status
                LIMIT 100""",
            (context.workspace_id,),
        ).fetchall()
        events = self.connection.execute(
            """SELECT event_type, count(*) FROM omnix_job_events
                WHERE workspace_id = %s
                  AND created_at > clock_timestamp() - INTERVAL '60 seconds'
                GROUP BY event_type
                ORDER BY count(*) DESC, event_type
                LIMIT 100""",
            (context.workspace_id,),
        ).fetchall()
        session_owners = self.connection.execute(
            """SELECT count(DISTINCT input_payload ->> 'session_id') FROM omnix_jobs
                WHERE workspace_id = %s AND job_type = 'chat.generate' AND status = 'running'""",
            (context.workspace_id,),
        ).fetchone()[0]
        dead_letters = self.connection.execute(
            """SELECT count(*) FROM omnix_dead_letters
                WHERE workspace_id = %s AND resolved_at IS NULL""",
            (context.workspace_id,),
        ).fetchone()[0]
        return {
            "groups": groups,
            "events": events,
            "session_owners": session_owners,
            "dead_letter_count": dead_letters,
        }

    def metrics_snapshot(self, context: TenantContext) -> dict[str, Any]:
        """Active jobs by type and status, and unresolved dead letters (WP-10.3).

        One row per (type, status): registered types times six statuses is far
        below the cap, which only bounds metric label cardinality.
        """
        active = self.connection.execute(
            """SELECT job_type, status, count(*),
                      COALESCE(max(EXTRACT(EPOCH FROM (clock_timestamp() - created_at)))
                          FILTER (WHERE status IN ('queued', 'waiting', 'retrying')), 0),
                      count(*) FILTER (WHERE lease_expires_at < clock_timestamp())
                 FROM omnix_jobs
                WHERE workspace_id = %s
                  AND status IN ('queued', 'leased', 'running', 'waiting', 'retrying', 'cancel_requested')
                GROUP BY job_type, status
                ORDER BY job_type, status
                LIMIT 1000""",
            (context.workspace_id,),
        ).fetchall()
        dead_letters = self.connection.execute(
            """SELECT count(*) FROM omnix_dead_letters
                WHERE workspace_id = %s AND resolved_at IS NULL""",
            (context.workspace_id,),
        ).fetchone()[0]
        return {
            "active": [
                {"job_type": str(row[0]), "status": str(row[1]), "count": int(row[2]),
                 "oldest_waiting_age_seconds": float(row[3]), "expired_leases": int(row[4])}
                for row in active
            ],
            "dead_letter_count": int(dead_letters),
        }

    def set_cancel_compat(
        self, context: TenantContext, *, job_id: str, payload: dict[str, Any]
    ) -> None:
        self.connection.execute(
            """UPDATE omnix_jobs
                  SET metadata = jsonb_set(metadata, '{compat_contract,cancel}', %s::jsonb, TRUE)
                WHERE id = %s AND workspace_id = %s""",
            (_json(payload), job_id, context.workspace_id),
        )

    def update_progress_compat(
        self,
        context: TenantContext,
        *,
        job_id: str,
        progress: dict[str, Any],
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> bool:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("job progress update requires both lease credentials")
        if worker_id and lease_token:
            predicate = """AND jobs.lease_owner = %s AND jobs.lease_token = %s
                             AND jobs.status IN ('running', 'cancel_requested')
                             AND jobs.lease_expires_at > clock_timestamp()"""
            credentials: tuple[Any, ...] = (worker_id, lease_token)
        else:
            predicate = "AND jobs.status IN ('running', 'cancel_requested') " + foreground_owner_guard("jobs")
            credentials = foreground_guard_credentials(execution_owner, submission_claim_token)
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                  SET progress = %s::jsonb, updated_at = clock_timestamp()
                WHERE jobs.id = %s AND jobs.workspace_id = %s {predicate}
                RETURNING jobs.id""",
            (_json(progress), job_id, context.workspace_id, *credentials),
        ).fetchone()
        return row is not None

    def update_input_compat(
        self,
        context: TenantContext,
        *,
        job_id: str,
        input_payload: dict[str, Any],
        compat: dict[str, Any] | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> bool:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("job input update requires both lease credentials")
        if worker_id and lease_token:
            predicate = """AND jobs.lease_owner = %s AND jobs.lease_token = %s
                             AND jobs.status IN ('running', 'cancel_requested')
                             AND jobs.lease_expires_at > clock_timestamp()"""
            credentials: tuple[Any, ...] = (worker_id, lease_token)
        else:
            predicate = "AND jobs.status IN ('running', 'cancel_requested') " + foreground_owner_guard("jobs")
            credentials = foreground_guard_credentials(execution_owner, submission_claim_token)
        compat_patch = _json(compat or {})
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                  SET input_payload = %s::jsonb,
                      metadata = CASE WHEN %s::boolean THEN
                          jsonb_set(
                              jobs.metadata,
                              '{{compat_contract,compat}}',
                              ((COALESCE(jobs.metadata #> '{{compat_contract,compat}}', '{{}}'::jsonb)
                                  - 'execution_owner') || %s::jsonb ||
                               CASE WHEN COALESCE(jobs.metadata #> '{{compat_contract,compat}}', '{{}}'::jsonb)
                                             ? 'execution_owner'
                                    THEN jsonb_build_object(
                                        'execution_owner',
                                        jobs.metadata #> '{{compat_contract,compat}}' -> 'execution_owner'
                                    )
                                    ELSE '{{}}'::jsonb END),
                              TRUE
                          )
                      ELSE jobs.metadata END,
                      updated_at = clock_timestamp()
                WHERE jobs.id = %s AND jobs.workspace_id = %s {predicate}
                RETURNING jobs.id""",
            (_json(input_payload), compat is not None, compat_patch,
             job_id, context.workspace_id, *credentials),
        ).fetchone()
        return row is not None

    def update_awaiting_plan_input(
        self,
        context: TenantContext,
        *,
        job_id: str,
        input_payload: dict[str, Any],
        compat: dict[str, Any] | None = None,
    ) -> bool:
        """Update only a caller-owned research plan that is awaiting approval."""
        row = self.connection.execute(
            """UPDATE omnix_jobs
                  SET input_payload = %s::jsonb,
                      metadata = CASE WHEN %s::boolean THEN
                          jsonb_set(metadata, '{compat_contract,compat}',
                                    (COALESCE(metadata #> '{compat_contract,compat}', '{}'::jsonb)
                                     - 'execution_owner') || %s::jsonb, TRUE)
                      ELSE metadata END,
                      updated_at = clock_timestamp()
                WHERE id = %s AND workspace_id = %s AND owner_user_id = %s
                  AND job_type = 'assistant.deep_research'
                  AND status IN ('queued', 'waiting')
                  AND COALESCE(input_payload ->> 'awaiting_plan_approval', 'false') = 'true'
                  AND lease_owner IS NULL AND lease_token IS NULL
                RETURNING id""",
            (_json(input_payload), compat is not None, _json(compat or {}),
             job_id, context.workspace_id, context.user_id),
        ).fetchone()
        return row is not None

    def update_stages_compat(
        self,
        context: TenantContext,
        *,
        job_id: str,
        stages: list[dict[str, Any]],
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> bool:
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("job stage update requires both lease credentials")
        if worker_id and lease_token:
            predicate = """AND jobs.lease_owner = %s AND jobs.lease_token = %s
                             AND jobs.status IN ('running', 'cancel_requested')
                             AND jobs.lease_expires_at > clock_timestamp()"""
            credentials: tuple[Any, ...] = (worker_id, lease_token)
        else:
            predicate = "AND jobs.status IN ('running', 'cancel_requested') " + foreground_owner_guard("jobs")
            credentials = foreground_guard_credentials(execution_owner, submission_claim_token)
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                  SET metadata = jsonb_set(
                          jobs.metadata, '{{compat_contract,stages}}', %s::jsonb, TRUE
                      ),
                      updated_at = clock_timestamp()
                WHERE jobs.id = %s AND jobs.workspace_id = %s {predicate}
                RETURNING jobs.id""",
            (_json(stages), job_id, context.workspace_id, *credentials),
        ).fetchone()
        return row is not None

    def finalize_cancel_compat(
        self,
        context: TenantContext,
        *,
        job_id: str,
        reason: str,
        now: str,
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> bool:
        del now
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("cancel finalization requires both lease credentials")
        if worker_id and lease_token:
            predicate = """AND jobs.lease_owner = %s AND jobs.lease_token = %s
                             AND jobs.status = 'cancel_requested'
                             AND jobs.lease_expires_at > clock_timestamp()"""
            credentials: tuple[Any, ...] = (worker_id, lease_token)
        else:
            predicate = "AND jobs.status IN ('running', 'cancel_requested') " + foreground_owner_guard("jobs")
            credentials = foreground_guard_credentials(execution_owner, submission_claim_token)
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                  SET status = 'canceled',
                      lease_owner = NULL,
                      lease_token = NULL,
                      lease_expires_at = NULL,
                      completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP),
                      updated_at = CURRENT_TIMESTAMP,
                      metadata = jsonb_set(
                          metadata,
                          '{{compat_contract,cancel}}',
                          jsonb_build_object(
                              'requested', TRUE,
                              'requested_at', COALESCE(
                                  jobs.metadata #>> '{{compat_contract,cancel,requested_at}}', clock_timestamp()::text
                              ),
                              'acknowledged_at', clock_timestamp()::text,
                              'reason', COALESCE(
                                  jobs.metadata #>> '{{compat_contract,cancel,reason}}', %s::text
                              )
                          ),
                          TRUE
                      )
                WHERE jobs.id = %s AND jobs.workspace_id = %s {predicate}
                RETURNING jobs.id""",
            (reason, job_id, context.workspace_id, *credentials),
        ).fetchone()
        return row is not None

    def append_compat_logs(
        self,
        context: TenantContext,
        *,
        job_id: str,
        logs: list[dict[str, Any]],
        worker_id: str | None = None,
        lease_token: str | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> bool:
        if not logs:
            return True
        if bool(worker_id) != bool(lease_token):
            raise JobClaimConflict("job log append requires both lease credentials")
        if worker_id and lease_token:
            predicate = """AND jobs.lease_owner = %s AND jobs.lease_token = %s
                             AND jobs.status IN ('running', 'cancel_requested')
                             AND jobs.lease_expires_at > clock_timestamp()"""
            credentials: tuple[Any, ...] = (worker_id, lease_token)
        else:
            predicate = "AND jobs.status IN ('running', 'cancel_requested') " + foreground_owner_guard("jobs")
            credentials = foreground_guard_credentials(execution_owner, submission_claim_token)
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                   SET updated_at = clock_timestamp()
                 WHERE jobs.id = %s AND jobs.workspace_id = %s {predicate}
                 RETURNING jobs.id""",
            (job_id, context.workspace_id, *credentials),
        ).fetchone()
        if row is None:
            return False
        seq_row = self.connection.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM omnix_job_logs WHERE workspace_id = %s AND job_id = %s",
            (context.workspace_id, job_id),
        ).fetchone()
        seq = int(seq_row[0])
        for item in logs:
            seq += 1
            data = {key: value for key, value in item.items() if key not in {"level", "message"}}
            self.connection.execute(
                """INSERT INTO omnix_job_logs
                       (job_id, workspace_id, seq, level, message, data)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
                (
                    job_id,
                    context.workspace_id,
                    seq,
                    str(item["level"]) if item.get("level") is not None else None,
                    str(item["message"]) if item.get("message") is not None else None,
                    _json(data),
                ),
            )
        return True

    def list_job_logs(self, context: TenantContext, *, job_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT level, message, data FROM (
                       SELECT level, message, data, seq
                         FROM omnix_job_logs
                        WHERE workspace_id = %s AND job_id = %s
                        ORDER BY seq DESC LIMIT %s
                   ) recent
                ORDER BY seq ASC""",
            (context.workspace_id, job_id, _bounded_job_log_limit(limit)),
        ).fetchall()
        return [_job_log_compat_entry(row[0], row[1], row[2]) for row in rows]

    def list_job_logs_for_jobs(
        self, context: TenantContext, *, job_ids: list[str], limit_per_job: int = 500
    ) -> dict[str, list[dict[str, Any]]]:
        if not job_ids:
            return {}
        rows = self.connection.execute(
            """SELECT requested.job_id, recent.level, recent.message, recent.data
                 FROM unnest(%s::text[]) AS requested(job_id)
                 CROSS JOIN LATERAL (
                     SELECT level, message, data, seq
                       FROM omnix_job_logs
                      WHERE workspace_id = %s AND job_id = requested.job_id
                      ORDER BY seq DESC LIMIT %s
                 ) AS recent
                ORDER BY requested.job_id, recent.seq ASC""",
            (job_ids, context.workspace_id, _bounded_job_log_limit(limit_per_job)),
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in job_ids}
        for row in rows:
            result[str(row[0])].append(
                _job_log_compat_entry(row[1], row[2], row[3])
            )
        return result

    def list_job_events(
        self, context: TenantContext, *, job_id: str, limit: int = MAX_JOB_EVENTS_PER_READ,
    ) -> list[Any]:
        """The job's newest ``limit`` events, oldest first."""
        return self.connection.execute(
            """SELECT id, job_id, event_type, payload, created_at FROM (
                   SELECT id, job_id, event_type, payload, created_at
                     FROM omnix_job_events
                    WHERE workspace_id = %s AND job_id = %s
                    ORDER BY id DESC LIMIT %s
               ) AS newest ORDER BY id ASC""",
            (context.workspace_id, job_id, max(1, min(int(limit), MAX_JOB_EVENTS_PER_READ))),
        ).fetchall()

    def count_job_events(self) -> int:
        return int(self.connection.execute(
            "SELECT count(*) FROM omnix_job_events"
        ).fetchone()[0])

    def query_jobs(
        self,
        context: TenantContext,
        *,
        job_id: str | None = None,
        module: str | None = None,
        job_type: str | None = None,
        job_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        input_fields: tuple[tuple[str, str], ...] = (),
        metadata_fields: tuple[tuple[str, str], ...] = (),
        lease_owner: str | None = None,
        lease_owner_pattern: str | None = None,
        after_created: tuple[str, str] | None = None,
        order_by: Literal["created_asc", "created_desc", "completed_desc", "updated_desc"] = "created_desc",
        limit: int = 100,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> list[dict[str, Any]]:
        """Read job records through the kernel-owned table boundary."""
        if order_by not in _JOB_ORDERINGS:
            raise ValueError(f"unsupported job ordering: {order_by}")
        clauses = ["jobs.workspace_id = %s"]
        parameters: list[Any] = [context.workspace_id]
        if job_id is not None:
            clauses.append("jobs.id = %s")
            parameters.append(job_id)
        if module is not None:
            clauses.append("jobs.module = %s")
            parameters.append(module)
        if job_type is not None:
            clauses.append("jobs.job_type = %s")
            parameters.append(job_type)
        if job_types:
            clauses.append("jobs.job_type = ANY(%s)")
            parameters.append(list(job_types))
        if statuses:
            clauses.append("jobs.status = ANY(%s)")
            parameters.append(list(statuses))
        for key, value in input_fields:
            clauses.append("jobs.input_payload ->> %s = %s")
            parameters.extend((key, value))
        for key, value in metadata_fields:
            clauses.append("jobs.metadata ->> %s = %s")
            parameters.extend((key, value))
        if lease_owner is not None:
            clauses.append("jobs.lease_owner = %s")
            parameters.append(lease_owner)
        if lease_owner_pattern is not None:
            clauses.append("jobs.lease_owner LIKE %s")
            parameters.append(lease_owner_pattern)
        if after_created is not None:
            if order_by != "created_asc":
                raise ValueError("created cursor requires created_asc ordering")
            clauses.append("(jobs.created_at, jobs.id) > (%s::timestamptz, %s)")
            parameters.extend(after_created)
        parameters.append(max(1, min(int(limit), 500)))
        lock = " FOR UPDATE" if for_update else ""
        if skip_locked:
            if not for_update:
                raise ValueError("skip_locked requires for_update")
            lock += " SKIP LOCKED"
        rows = self.connection.execute(
            f"""SELECT {_QUALIFIED_JOB_COLUMNS} FROM omnix_jobs AS jobs
                 WHERE {' AND '.join(clauses)}
                 ORDER BY {_JOB_ORDERINGS[order_by]} LIMIT %s{lock}""",
            tuple(parameters),
        ).fetchall()
        return [_job(row) for row in rows]

    def patch_job(
        self,
        context: TenantContext,
        *,
        job_id: str,
        expected_statuses: tuple[str, ...] = (),
        lease_owner: str | None = None,
        lease_token: str | None = None,
        status: str | None = None,
        metadata_set: dict[str, Any] | None = None,
        metadata_remove: tuple[str, ...] = (),
        input_payload: dict[str, Any] | None = None,
        output_refs: list[dict[str, Any]] | None = None,
        progress: dict[str, Any] | None = None,
        error: Any = _UNSET,
        max_attempts: int | None = None,
        priority: int | None = None,
        available_at_now: bool = False,
        clear_lease: bool = False,
        completed_at_now: bool = False,
    ) -> dict[str, Any] | None:
        """Apply a constrained job mutation without exposing SQL to features."""
        assignments: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        parameters: list[Any] = []
        if status is not None:
            assignments.append("status = %s")
            parameters.append(status)
        if metadata_set is not None or metadata_remove:
            assignments.append("metadata = (metadata - %s::text[]) || %s::jsonb")
            parameters.extend((list(metadata_remove), _json(metadata_set or {})))
        if input_payload is not None:
            assignments.append("input_payload = %s::jsonb")
            parameters.append(_json(input_payload))
        if output_refs is not None:
            assignments.append("output_refs = %s::jsonb")
            parameters.append(_json(output_refs))
        if progress is not None:
            assignments.append("progress = %s::jsonb")
            parameters.append(_json(progress))
        if error is not _UNSET:
            assignments.append("error = %s::jsonb")
            parameters.append(None if error is None else _json(error))
        if max_attempts is not None:
            assignments.append("max_attempts = %s")
            parameters.append(max(1, int(max_attempts)))
        if priority is not None:
            assignments.append("priority = %s")
            parameters.append(int(priority))
        if available_at_now:
            assignments.append("available_at = CURRENT_TIMESTAMP")
        if clear_lease:
            assignments.extend((
                "lease_owner = NULL",
                "lease_token = NULL",
                "lease_expires_at = NULL",
            ))
        if completed_at_now:
            assignments.append("completed_at = CURRENT_TIMESTAMP")
        if len(assignments) == 1:
            raise ValueError("job patch must change at least one field")
        clauses = ["jobs.id = %s", "jobs.workspace_id = %s"]
        parameters.extend((job_id, context.workspace_id))
        if expected_statuses:
            clauses.append("jobs.status = ANY(%s)")
            parameters.append(list(expected_statuses))
        if lease_owner is not None:
            clauses.append("jobs.lease_owner = %s")
            parameters.append(lease_owner)
        if lease_token is not None:
            clauses.append("jobs.lease_token = %s")
            parameters.append(lease_token)
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                   SET {', '.join(assignments)}
                 WHERE {' AND '.join(clauses)}
                 RETURNING {_QUALIFIED_JOB_COLUMNS}""",
            tuple(parameters),
        ).fetchone()
        return _job(row) if row is not None else None

    def has_pending_higher_priority_tts(
        self,
        context: TenantContext,
        *,
        resource_classes: tuple[str, ...],
    ) -> bool:
        row = self.connection.execute(
            """SELECT EXISTS (
                   SELECT 1 FROM omnix_jobs
                    WHERE workspace_id = %s AND resource_class = ANY(%s)
                      AND (
                          (status IN ('queued', 'waiting', 'retrying')
                           AND available_at <= CURRENT_TIMESTAMP
                           AND attempt_count < max_attempts)
                          OR (status IN ('leased', 'running', 'cancel_requested')
                              AND lease_expires_at > CURRENT_TIMESTAMP)
                      )
               )""",
            (context.workspace_id, list(resource_classes)),
        ).fetchone()
        return bool(row[0])

    def delete_jobs(
        self,
        context: TenantContext,
        *,
        module: str | None = None,
        job_type: str | None = None,
        job_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        input_fields: tuple[tuple[str, str], ...] = (),
        metadata_fields: tuple[tuple[str, str], ...] = (),
        job_ids: tuple[str, ...] = (),
    ) -> int:
        clauses = ["workspace_id = %s"]
        parameters: list[Any] = [context.workspace_id]
        if job_ids:
            clauses.append("id = ANY(%s)")
            parameters.append(list(job_ids))
        if module is not None:
            clauses.append("module = %s")
            parameters.append(module)
        if job_type is not None:
            clauses.append("job_type = %s")
            parameters.append(job_type)
        if job_types:
            clauses.append("job_type = ANY(%s)")
            parameters.append(list(job_types))
        if statuses:
            clauses.append("status = ANY(%s)")
            parameters.append(list(statuses))
        for key, value in input_fields:
            clauses.append("input_payload ->> %s = %s")
            parameters.extend((key, value))
        for key, value in metadata_fields:
            clauses.append("metadata ->> %s = %s")
            parameters.extend((key, value))
        cursor = self.connection.execute(
            "DELETE FROM omnix_jobs WHERE " + " AND ".join(clauses),
            tuple(parameters),
        )
        return int(cursor.rowcount)

    def update_attempt_status(
        self,
        context: TenantContext,
        *,
        job_id: str,
        status: str,
        expected_statuses: tuple[str, ...] = (),
        attempt: int | None = None,
        lease_token: str | None = None,
        error: Any = _UNSET,
    ) -> int:
        assignments = ["status = %s"]
        parameters: list[Any] = [status]
        if status in {"completed", "failed", "canceled"}:
            assignments.append("completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)")
        if error is not _UNSET:
            assignments.append("error = %s::jsonb")
            parameters.append(None if error is None else _json(error))
        clauses = [
            "attempts.job_id = %s",
            "EXISTS (SELECT 1 FROM omnix_jobs AS jobs "
            "WHERE jobs.id = attempts.job_id AND jobs.workspace_id = %s)",
        ]
        parameters.extend((job_id, context.workspace_id))
        if expected_statuses:
            clauses.append("attempts.status = ANY(%s)")
            parameters.append(list(expected_statuses))
        if attempt is not None:
            clauses.append("attempts.attempt = %s")
            parameters.append(int(attempt))
        if lease_token is not None:
            clauses.append("attempts.lease_token = %s")
            parameters.append(lease_token)
        cursor = self.connection.execute(
            f"UPDATE omnix_job_attempts AS attempts SET {', '.join(assignments)} "
            f"WHERE {' AND '.join(clauses)}",
            tuple(parameters),
        )
        return int(cursor.rowcount)

    def find_by_input(
        self,
        context: TenantContext,
        *,
        job_type: str,
        input_fields: tuple[tuple[str, str], ...],
        statuses: tuple[str, ...] | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        clauses = ["jobs.workspace_id = %s", "jobs.job_type = %s"]
        parameters: list[Any] = [context.workspace_id, job_type]
        for key, value in input_fields:
            clauses.append("jobs.input_payload ->> %s = %s")
            parameters.extend((key, value))
        if statuses is not None:
            clauses.append("jobs.status = ANY(%s)")
            parameters.append(list(statuses))
        parameters.append(max(1, min(int(limit), 500)))
        rows = self.connection.execute(
            f"""SELECT {_QUALIFIED_JOB_COLUMNS} FROM omnix_jobs AS jobs
                 WHERE {' AND '.join(clauses)}
                 ORDER BY jobs.created_at, jobs.id LIMIT %s""",
            tuple(parameters),
        ).fetchall()
        return [_job(row) for row in rows]

    def has_earlier_by_input(
        self,
        context: TenantContext,
        *,
        job_type: str,
        input_fields: tuple[tuple[str, str], ...],
        statuses: tuple[str, ...],
        before_created_at: str,
        before_id: str,
    ) -> bool:
        clauses = [
            "jobs.workspace_id = %s",
            "jobs.job_type = %s",
            "jobs.status = ANY(%s)",
        ]
        parameters: list[Any] = [context.workspace_id, job_type, list(statuses)]
        for key, value in input_fields:
            clauses.append("jobs.input_payload ->> %s = %s")
            parameters.extend((key, value))
        clauses.append("(jobs.created_at, jobs.id) < (%s::timestamptz, %s)")
        parameters.extend((before_created_at, before_id))
        row = self.connection.execute(
            f"SELECT 1 FROM omnix_jobs AS jobs WHERE {' AND '.join(clauses)} LIMIT 1",
            tuple(parameters),
        ).fetchone()
        return row is not None

    def require_running_lease(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
    ) -> None:
        row = self.connection.execute(
            """SELECT id FROM omnix_jobs
                 WHERE id = %s AND workspace_id = %s
                   AND lease_owner = %s AND lease_token = %s
                   AND status = 'running' AND cancel_requested_at IS NULL
                   AND lease_expires_at > clock_timestamp()
                 FOR UPDATE""",
            (job_id, context.workspace_id, worker_id, lease_token),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job lease lost or canceled: {job_id}")

    def require_execution_lease(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
    ) -> None:
        row = self.connection.execute(
            """SELECT id FROM omnix_jobs
                 WHERE id = %s AND workspace_id = %s
                   AND lease_owner = %s AND lease_token = %s
                   AND status IN ('leased', 'running')
                   AND lease_expires_at > clock_timestamp()
                 FOR UPDATE""",
            (job_id, context.workspace_id, worker_id, lease_token),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"durable feature execution lease lost: {job_id}")

    def lock_job(self, context: TenantContext, *, job_id: str) -> bool:
        row = self.connection.execute(
            "SELECT id FROM omnix_jobs WHERE id = %s AND workspace_id = %s FOR UPDATE",
            (job_id, context.workspace_id),
        ).fetchone()
        return row is not None

    def latest_committed_event_cursor(self, context: TenantContext) -> tuple[int, int]:
        """``(tx_id, id)`` of the newest event every reader can already see."""
        row = self.connection.execute(
            """SELECT tx_id::text::bigint, id FROM omnix_job_events
                WHERE workspace_id = %s AND tx_id < pg_snapshot_xmin(pg_current_snapshot())
                ORDER BY tx_id DESC, id DESC LIMIT 1""",
            (context.workspace_id,),
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def list_committed_events(
        self, context: TenantContext, *, after: tuple[int, int], limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Events after ``after`` in commit order (WP-5.4).

        Only rows whose transaction is older than every transaction in
        progress are returned, ordered by ``(tx_id, id)``: an event that
        commits late with a lower id is delivered after, never skipped.
        """
        rows = self.connection.execute(
            """SELECT tx_id::text::bigint, id, job_id, event_type, payload, created_at
                 FROM omnix_job_events
                WHERE workspace_id = %s
                  AND (tx_id, id) > (%s::text::xid8, %s)
                  AND tx_id < pg_snapshot_xmin(pg_current_snapshot())
                ORDER BY tx_id, id LIMIT %s""",
            (context.workspace_id, str(int(after[0])), int(after[1]), max(1, min(int(limit), 5000))),
        ).fetchall()
        return [
            {
                "tx_id": int(row[0]),
                "id": int(row[1]),
                "job_id": str(row[2]),
                "event_type": str(row[3]),
                "payload": dict(row[4]),
                "created_at": row[5].isoformat(),
            }
            for row in rows
        ]

    def latest_event_id(self, context: TenantContext) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM omnix_job_events WHERE workspace_id = %s",
            (context.workspace_id,),
        ).fetchone()
        return int(row[0])

    def list_events(
        self,
        context: TenantContext,
        *,
        after_id: int = 0,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id = %s"]
        parameters: list[Any] = [context.workspace_id]
        if job_id is not None:
            clauses.append("job_id = %s")
            parameters.append(job_id)
        else:
            clauses.append("id > %s")
            parameters.append(max(0, int(after_id)))
        parameters.append(max(1, min(int(limit), 1000)))
        rows = self.connection.execute(
            "SELECT id, job_id, event_type, payload, created_at "
            "FROM omnix_job_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY id ASC LIMIT %s",
            tuple(parameters),
        ).fetchall()
        return [
            {
                "id": int(row[0]),
                "job_id": str(row[1]),
                "event_type": str(row[2]),
                "payload": dict(row[3]),
                "created_at": row[4].isoformat(),
            }
            for row in rows
        ]

    def list_recoverable_chat_jobs(self, context: TenantContext, *, limit: int = 100,
                                  after_created_at: str | None = None,
                                  after_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"""SELECT {_QUALIFIED_JOB_COLUMNS} FROM omnix_jobs AS jobs
                 WHERE jobs.workspace_id = %s AND {_ACTIVE_CHAT_JOBS}
                   AND {_NO_LIVE_CHAT_OWNER}
                   AND (%s::timestamptz IS NULL OR (jobs.created_at, jobs.id) > (%s::timestamptz, %s))
                 ORDER BY jobs.created_at, jobs.id LIMIT %s""",
            (context.workspace_id, after_created_at, after_created_at, after_id,
             page_limit(limit, maximum=MAX_PAGE_SIZE)),
        ).fetchall()
        return [_job(row) for row in rows]

    def recover_chat_job(self, context: TenantContext, *, job_id: str) -> dict[str, Any] | None:
        """Recheck owner/status when transitioning, so only one recovery can win."""
        error = {"code": "chat_generation_failed", "retryable": True,
                 "message": "Gateway execution owner stopped or expired before Chat generation completed."}
        row = self.connection.execute(
            f"""UPDATE omnix_jobs AS jobs
                   SET status = CASE WHEN jobs.status = 'cancel_requested' THEN 'canceled' ELSE 'failed' END,
                       error = CASE WHEN jobs.status = 'cancel_requested' THEN NULL ELSE %s::jsonb END,
                       lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                       completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                       metadata = CASE WHEN jobs.status = 'cancel_requested' THEN
                           jsonb_set(jobs.metadata, '{{compat_contract,cancel}}',
                               COALESCE(jobs.metadata #> '{{compat_contract,cancel}}', '{{}}'::jsonb) ||
                               jsonb_build_object('requested', TRUE, 'acknowledged_at', CURRENT_TIMESTAMP), TRUE)
                           ELSE jobs.metadata END
                 WHERE jobs.id = %s AND jobs.workspace_id = %s
                   AND {_ACTIVE_CHAT_JOBS} AND {_NO_LIVE_CHAT_OWNER}
                 RETURNING {_QUALIFIED_JOB_COLUMNS}""",
            (_json(error), job_id, context.workspace_id),
        ).fetchone()
        if row is None:
            return None
        result = _job(row)
        self.connection.execute(
            "UPDATE omnix_job_attempts SET status = %s, completed_at = CURRENT_TIMESTAMP "
            "WHERE job_id = %s AND status IN ('leased', 'running')",
            (result["status"], job_id),
        )
        self._event(context, job_id, f"job.{result['status']}", {
            "recovery": True,
            "execution_owner": (result.get("metadata") or {}).get("compat_contract", {}).get("compat", {}).get("execution_owner"),
        })
        return result

    def cancel_active_job(
        self, context: TenantContext, *, job_id: str,
    ) -> dict[str, Any]:
        """Finalize cancellation for a leased job whose work must stop now.

        The worker may still be unwinding an external provider call. Clearing
        its lease makes every subsequent durable write fail the ownership
        check, while the worker's transaction boundaries prevent partial
        chapter commits.
        """
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs AS jobs
               SET status = 'canceled', lease_owner = NULL, lease_token = NULL,
                   lease_expires_at = NULL, cancel_requested_at = COALESCE(
                       cancel_requested_at, CURRENT_TIMESTAMP),
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE jobs.id = %s AND jobs.workspace_id = %s
               AND jobs.status IN ('leased', 'running', 'cancel_requested')
            RETURNING {_QUALIFIED_JOB_COLUMNS}
            """, (job_id, context.workspace_id),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"active job cancellation rejected: {job_id}")
        result = _job(row)
        self.connection.execute(
            """UPDATE omnix_job_attempts
                  SET status = 'canceled', completed_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND status IN ('leased', 'running')""",
            (job_id,),
        )
        self._event(context, job_id, "job.canceled", {
            "attempt": result["attempt_count"], "immediate": True,
        })
        return result

    def acknowledge_cancel(
        self, context: TenantContext, *, job_id: str, worker_id: str, lease_token: str,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs AS jobs
               SET status = 'canceled', lease_owner = NULL, lease_token = NULL,
                   lease_expires_at = NULL, completed_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
             WHERE jobs.id = %s AND jobs.workspace_id = %s
               AND jobs.lease_owner = %s AND jobs.lease_token = %s
               AND jobs.status = 'cancel_requested'
            RETURNING {_QUALIFIED_JOB_COLUMNS}
            """, (job_id, context.workspace_id, worker_id, lease_token),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job cancellation acknowledgement rejected: {job_id}")
        result = _job(row)
        self.connection.execute(
            "UPDATE omnix_job_attempts SET status = 'canceled', completed_at = CURRENT_TIMESTAMP "
            "WHERE job_id = %s AND attempt = %s AND lease_token = %s",
            (job_id, result["attempt_count"], lease_token),
        )
        self._event(context, job_id, "job.canceled", {"attempt": result["attempt_count"]})
        return result

    def create_job_once(
        self,
        context: TenantContext,
        payload: dict[str, Any],
        *,
        reconcile_queued: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """Create one deterministic job identity or reconcile a stronger queued signal."""

        row = self.connection.execute(
            f"""
            INSERT INTO omnix_jobs AS jobs (
                id, workspace_id, owner_user_id, module, job_type,
                resource_class, priority, input_payload, max_attempts,
                available_at, metadata, correlation_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::jsonb, %s
            )
            ON CONFLICT (id) DO NOTHING
            RETURNING {_QUALIFIED_JOB_COLUMNS}
            """,
            (
                payload["id"],
                context.workspace_id,
                payload.get("owner_user_id") or context.user_id,
                payload["module"],
                payload["job_type"],
                payload["resource_class"],
                int(payload.get("priority", 0)),
                _json(payload.get("input_payload") or {}),
                max(1, int(payload.get("max_attempts", 3))),
                payload.get("available_at"),
                _json(payload.get("metadata") or {}),
                payload.get("correlation_id"),
            ),
        ).fetchone()
        if row is not None:
            result = _job(row)
            self._event(context, result["id"], "job.created", {"status": "queued"})
            return result, True
        if not reconcile_queued:
            existing = self.get_job(context, str(payload["id"]))
            if existing is None:
                raise RuntimeError(f"deterministic_job_identity_conflict:{payload['id']}")
            return existing, False
        updated = self.connection.execute(
            f"""
            UPDATE omnix_jobs AS jobs
               SET priority = GREATEST(jobs.priority, %s),
                   input_payload = %s::jsonb,
                   metadata = %s::jsonb,
                   max_attempts = GREATEST(jobs.max_attempts, %s),
                   updated_at = CURRENT_TIMESTAMP
             WHERE jobs.id = %s AND jobs.workspace_id = %s
               AND jobs.status IN ('queued', 'waiting', 'retrying')
            RETURNING {_QUALIFIED_JOB_COLUMNS}
            """,
            (
                int(payload.get("priority", 0)),
                _json(payload.get("input_payload") or {}),
                _json(payload.get("metadata") or {}),
                max(1, int(payload.get("max_attempts", 3))),
                payload["id"],
                context.workspace_id,
            ),
        ).fetchone()
        if updated is not None:
            result = _job(updated)
            self._event(
                context,
                result["id"],
                "job.signal_reconciled",
                {"priority": result["priority"], "status": result["status"]},
            )
            return result, False
        existing = self.get_job(context, str(payload["id"]))
        if existing is None:
            raise RuntimeError(f"deterministic_job_identity_conflict:{payload['id']}")
        return existing, False

    def claim_next(
        self,
        context: TenantContext,
        *,
        worker_id: str,
        resource_classes: list[str],
        job_types: list[str] | None = None,
        lease_seconds: int = 30,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        if not resource_classes:
            return None
        lease_seconds = max(1, min(int(lease_seconds), 3600))
        token = uuid.uuid4().hex
        row = self.connection.execute(
            f"""
            WITH candidate AS (
                SELECT queued.id
                  FROM omnix_jobs AS queued
                 WHERE queued.workspace_id = %s
                   AND (%s::text IS NULL OR queued.id = %s)
                   AND queued.cancel_requested_at IS NULL
                   AND queued.status IN ('queued', 'retrying', 'waiting')
                   AND queued.available_at <= CURRENT_TIMESTAMP
                   AND queued.resource_class = ANY(%s)
                   AND (%s::text[] IS NULL OR queued.job_type = ANY(%s))
                   AND queued.attempt_count < queued.max_attempts
                   AND queued.job_type <> 'rpg.turn.foreground_record'
                   AND NOT (
                       queued.job_type = 'chat.generate'
                       AND COALESCE(queued.metadata #>> '{{compat_contract,compat,inline_execution}}', 'false') = 'true'
                   )
                   AND NOT (
                       queued.job_type = 'assistant.deep_research'
                       AND COALESCE(queued.input_payload ->> 'awaiting_plan_approval', 'false') = 'true'
                   )
                 ORDER BY (
                     queued.priority::bigint + LEAST(
                         100::numeric,
                         GREATEST(0::numeric, FLOOR(EXTRACT(EPOCH FROM
                             (clock_timestamp() - queued.created_at)) / %s))
                     )
                 ) DESC, queued.created_at ASC, queued.id ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE omnix_jobs AS jobs
               SET status = 'leased',
                   lease_owner = %s,
                   lease_token = %s,
                   lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                   attempt_count = jobs.attempt_count + 1,
                   started_at = COALESCE(jobs.started_at, CURRENT_TIMESTAMP),
                   updated_at = CURRENT_TIMESTAMP,
                   error = NULL
              FROM candidate
             WHERE jobs.id = candidate.id
            RETURNING {_QUALIFIED_JOB_COLUMNS}
            """,
            (
                context.workspace_id,
                job_id, job_id,
                resource_classes,
                job_types,
                job_types,
                self.priority_aging_seconds,
                worker_id,
                token,
                lease_seconds,
            ),
        ).fetchone()
        if row is None:
            return None
        result = _job(row)
        self.connection.execute(
            """
            INSERT INTO omnix_job_attempts
                (job_id, attempt, worker_id, lease_token, status)
            VALUES (%s, %s, %s, %s, 'leased')
            ON CONFLICT (job_id, attempt) DO UPDATE
               SET worker_id = EXCLUDED.worker_id,
                   lease_token = EXCLUDED.lease_token,
                   status = 'leased',
                   started_at = CURRENT_TIMESTAMP,
                   completed_at = NULL,
                   error = NULL
            """,
            (result["id"], result["attempt_count"], worker_id, token),
        )
        self._event(
            context,
            result["id"],
            "job.claimed",
            {"worker_id": worker_id, "attempt": result["attempt_count"]},
        )
        return result
