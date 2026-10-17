from __future__ import annotations

import json
import math
import random
import uuid
from collections.abc import Iterable, Iterator
from typing import Any

from app.caching.bounded_cache import bounded_lru_cache
from app.jobs.errors import JobClaimConflict
from app.runtime.pagination import MAX_PAGE_SIZE, page_limit

from .errors import EntityNotFound
from .tenant import TenantContext


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _job(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "owner_user_id": str(row[2]) if row[2] is not None else None,
        "module": str(row[3]),
        "job_type": str(row[4]),
        "status": str(row[5]),
        "resource_class": str(row[6]),
        "priority": int(row[7]),
        "input_payload": dict(row[8]),
        "output_refs": list(row[9]),
        "progress": dict(row[10]),
        "error": dict(row[11]) if row[11] is not None else None,
        "attempt_count": int(row[12]),
        "max_attempts": int(row[13]),
        "available_at": row[14].isoformat(),
        "lease_owner": str(row[15]) if row[15] is not None else None,
        "lease_token": str(row[16]) if row[16] is not None else None,
        "lease_expires_at": row[17].isoformat() if row[17] is not None else None,
        "cancel_requested_at": row[18].isoformat() if row[18] is not None else None,
        "started_at": row[19].isoformat() if row[19] is not None else None,
        "completed_at": row[20].isoformat() if row[20] is not None else None,
        "created_at": row[21].isoformat(),
        "updated_at": row[22].isoformat(),
        "metadata": dict(row[23]),
        "correlation_id": str(row[24]) if row[24] is not None else None,
    }


def _expired_retry_delay(job: dict[str, Any]) -> int:
    """Compute the handler backoff saved when a durable job was admitted."""
    try:
        metadata = job.get("metadata") or {}
        saved = metadata.get("retry_backoff") if isinstance(metadata, dict) else None
        contract = saved if isinstance(saved, dict) else {}
        base = max(0.001, min(float(contract.get("base_seconds", 2.0)), 86_400.0))
        factor = max(1.0, min(float(contract.get("factor", 2.0)), 100.0))
        maximum = max(0.001, min(float(contract.get("max_seconds", 300.0)), 86_400.0))
        jitter = max(0.0, min(float(contract.get("jitter", 0.2)), 1.0))
        attempt = max(0, int(job.get("attempt_count", 1)) - 1)
        raw = min(maximum, base * (factor ** min(attempt, 64)))
        delay = raw * (1.0 + ((random.random() * 2.0) - 1.0) * jitter)
        return max(1, math.ceil(delay))
    except (OverflowError, TypeError, ValueError):
        return 1


# Leases released per recovery pass (WP-5.5).
EXPIRED_LEASE_BATCH = 200

_JOB_COLUMNS = """
id, workspace_id, owner_user_id, module, job_type, status, resource_class,
priority, input_payload, output_refs, progress, error, attempt_count,
max_attempts, available_at, lease_owner, lease_token, lease_expires_at,
cancel_requested_at, started_at, completed_at, created_at, updated_at, metadata,
correlation_id
"""
# Record-only jobs run without a worker lease; only their foreground owner may move them.
# Chat's inline generation is fenced by the gateway node that owns it; a module's own
# record-only job types are fenced by the predicate it declares (RECORD_ONLY_JOB_GUARDS in
# its declarations.py, PA-2.2), each with one placeholder bound to the submission claim token.
_GUARD_HEAD = """
AND lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL
AND (
    (job_type = 'chat.generate'
     AND metadata #> '{compat_contract,compat,inline_execution}' = 'true'::jsonb
     AND EXISTS (
        SELECT 1 FROM omnix_runtime_nodes AS execution_owner
         WHERE execution_owner.id = %s
           AND execution_owner.id = omnix_jobs.metadata #>> '{compat_contract,compat,execution_owner}'
           AND execution_owner.node_type = 'gateway'
           AND execution_owner.status IN ('active', 'draining')
           AND execution_owner.lease_expires_at > clock_timestamp()
           AND execution_owner.metadata ->> 'workspace_id' = omnix_jobs.workspace_id
    ))"""


@bounded_lru_cache(max_entries=4, ttl_seconds=3600.0)
def foreground_owner_guard(job: str = "omnix_jobs") -> str:
    """The fence for moving a record-only job, with the job row named ``job`` (composed on first use)."""
    from .declarations import record_only_job_guards

    branches = "".join(
        f"\n    OR (job_type = '{guard.job_type}'\n{guard.predicate.replace('{job}', 'omnix_jobs')})"
        for guard in record_only_job_guards()
    )
    guard = _GUARD_HEAD + branches + "\n)\n"
    return guard if job == "omnix_jobs" else guard.replace("omnix_jobs.", f"{job}.")


def foreground_guard_credentials(execution_owner: Any, submission_claim_token: Any) -> tuple[Any, ...]:
    """The guard's parameters: the execution owner, then the claim token for each declared module guard."""
    slots = foreground_owner_guard().count("%s") - 1
    return (execution_owner, *([submission_claim_token] * slots))



class PostgresJobRepository:
    def __init__(self, connection: Any, *, priority_aging_seconds: int = 60) -> None:
        self.connection = connection
        self.priority_aging_seconds = max(1, int(priority_aging_seconds))

    def create_job(self, context: TenantContext, payload: dict[str, Any]) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            INSERT INTO omnix_jobs (
                id, workspace_id, owner_user_id, module, job_type,
                resource_class, priority, input_payload, max_attempts,
                available_at, metadata, correlation_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                COALESCE(%s::timestamptz, CURRENT_TIMESTAMP), %s::jsonb, %s
            ) RETURNING {_JOB_COLUMNS}
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
        result = _job(row)
        self._event(context, result["id"], "job.created", {"status": "queued"})
        return result

    def get_job(self, context: TenantContext, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM omnix_jobs "
            "WHERE id = %s AND workspace_id = %s",
            (job_id, context.workspace_id),
        ).fetchone()
        return _job(row) if row is not None else None

    def list_jobs(
        self,
        context: TenantContext,
        *,
        limit: int = 100,
        status: str | None = None,
        statuses: tuple[str, ...] | None = None,
        job_types: tuple[str, ...] | None = None,
        modules: tuple[str, ...] | None = None,
        before_created_at: str | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first, filtered in SQL and keyset-paged by ``(created_at, id)`` (WP-5.5)."""
        clauses = ["workspace_id = %s"]
        params: list[Any] = [context.workspace_id]
        if status is not None:
            clauses.append("status = %s")
            params.append(status)
        if statuses:
            clauses.append("status = ANY(%s)")
            params.append(list(statuses))
        if job_types:
            clauses.append("job_type = ANY(%s)")
            params.append(list(job_types))
        if modules:
            clauses.append("module = ANY(%s)")
            params.append(list(modules))
        if before_created_at is not None and before_id is not None:
            clauses.append("(created_at, id) < (%s::timestamptz, %s)")
            params.extend([before_created_at, before_id])
        # One more than a page, so callers can tell whether another follows.
        params.append(page_limit(limit, maximum=MAX_PAGE_SIZE + 1))
        rows = self.connection.execute(
            f"SELECT {_JOB_COLUMNS} FROM omnix_jobs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id DESC LIMIT %s",
            tuple(params),
        ).fetchall()
        return [_job(row) for row in rows]

    def iter_jobs(self, context: TenantContext, **filters: Any) -> Iterator[dict[str, Any]]:
        """Every matching job, newest first, one page at a time."""
        before: tuple[str, str] | None = None
        while True:
            page = self.list_jobs(
                context,
                limit=MAX_PAGE_SIZE,
                before_created_at=before[0] if before else None,
                before_id=before[1] if before else None,
                **filters,
            )[:MAX_PAGE_SIZE]
            yield from page
            if len(page) < MAX_PAGE_SIZE:
                return
            before = (page[-1]["created_at"], page[-1]["id"])

    def release_expired_leases(
        self, context: TenantContext, *, job_id: str | None = None,
        job_type: str | None = None,
    ) -> list[dict[str, Any]]:
        expired = self.connection.execute(
            f"""SELECT {_JOB_COLUMNS} FROM omnix_jobs
                 WHERE workspace_id = %s
                   AND (%s::text IS NULL OR id = %s)
                   AND (%s::text IS NULL OR job_type = %s)
                   AND status IN ('leased', 'running', 'cancel_requested')
                   AND lease_expires_at <= clock_timestamp()
                 ORDER BY lease_expires_at, id
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED""",
            # The recovery task runs every few seconds; the rest follow then.
            (context.workspace_id, job_id, job_id, job_type, job_type, EXPIRED_LEASE_BATCH),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for expired_row in expired:
            current = _job(expired_row)
            canceled = current["status"] == "cancel_requested"
            retry = not canceled and current["attempt_count"] < current["max_attempts"]
            status = "canceled" if canceled else "retrying" if retry else "failed"
            error = None if canceled else {"code": "lease_expired"}
            delay = _expired_retry_delay(current) if retry else 0
            row = self.connection.execute(
                f"""UPDATE omnix_jobs
                       SET status = %s,
                           available_at = CASE WHEN %s THEN clock_timestamp() + (%s * INTERVAL '1 second')
                                               ELSE available_at END,
                           error = %s::jsonb,
                           lease_owner = NULL,
                           lease_token = NULL,
                           lease_expires_at = NULL,
                           updated_at = clock_timestamp(),
                           completed_at = CASE WHEN %s THEN clock_timestamp() ELSE completed_at END
                     WHERE id = %s AND workspace_id = %s
                       AND lease_token = %s AND lease_owner = %s
                       AND status = %s AND lease_expires_at <= clock_timestamp()
                    RETURNING {_JOB_COLUMNS}""",
                (
                    status, retry, delay, _json(error) if error is not None else None,
                    canceled or not retry, current["id"], context.workspace_id,
                    current["lease_token"], current["lease_owner"], current["status"],
                ),
            ).fetchone()
            if row is None:
                continue
            result = _job(row)
            results.append(result)
            self.connection.execute(
                """
                UPDATE omnix_job_attempts
                   SET status = %s, completed_at = CURRENT_TIMESTAMP,
                       error = %s::jsonb
                 WHERE job_id = %s AND attempt = %s
                   AND status IN ('leased', 'running')
                """,
                (
                    result["status"],
                    _json(result["error"]) if result["error"] is not None else None,
                    result["id"],
                    result["attempt_count"],
                ),
            )
            self._event(
                context,
                result["id"],
                "job.lease_expired",
                {"status": result["status"], "attempt": result["attempt_count"]},
            )
            if result["status"] == "canceled":
                self._event(
                    context,
                    result["id"],
                    "job.canceled",
                    {"attempt": result["attempt_count"], "reason": "lease_expired_after_cancel"},
                )
            if result["status"] == "failed":
                self.connection.execute(
                    """
                    INSERT INTO omnix_dead_letters (workspace_id, job_id, reason, payload)
                    VALUES (%s, %s, 'lease_expired', %s::jsonb)
                    """,
                    (context.workspace_id, result["id"], _json(result["error"] or {})),
                )
        return results

    def fail_retired_jobs(self, context: TenantContext, job_types: Iterable[str]) -> list[dict[str, Any]]:
        """Move this workspace's unfinished jobs of retired types to ``failed`` (reason ``module_retired``), once (PA-4.3).

        A retired type's handler is gone, so nothing would ever finish the job;
        a job of a merely disabled or unknown type is left alone.
        """
        types = sorted(set(job_types))
        if not types:
            return []
        error = {"code": "module_retired", "retryable": False}
        rows = self.connection.execute(
            f"""UPDATE omnix_jobs
                   SET status = 'failed', error = %s::jsonb,
                       lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                       completed_at = clock_timestamp(), updated_at = clock_timestamp()
                 WHERE id IN (
                       SELECT id FROM omnix_jobs
                        WHERE workspace_id = %s AND job_type = ANY(%s)
                          AND status IN ('queued', 'retrying', 'waiting', 'leased', 'running', 'cancel_requested')
                        ORDER BY created_at, id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED)
                RETURNING {_JOB_COLUMNS}""",
            # The recovery task runs every minute; the rest follow then.
            (_json(error), context.workspace_id, types, EXPIRED_LEASE_BATCH),
        ).fetchall()
        results = [_job(row) for row in rows]
        for result in results:
            self.connection.execute(
                """UPDATE omnix_job_attempts SET status = 'failed', completed_at = CURRENT_TIMESTAMP, error = %s::jsonb
                    WHERE job_id = %s AND attempt = %s AND status IN ('leased', 'running')""",
                (_json(error), result["id"], result["attempt_count"]),
            )
            self._event(context, result["id"], "job.failed", {"attempt": result["attempt_count"], "error": error})
            self.connection.execute(
                "INSERT INTO omnix_dead_letters (workspace_id, job_id, reason, payload) VALUES (%s, %s, 'module_retired', %s::jsonb)",
                (context.workspace_id, result["id"], _json(error)),
            )
        return results

    def cancel_jobs_of_types(self, context: TenantContext, job_types: Iterable[str], *, reason: str) -> list[dict[str, Any]]:
        """Cancel this workspace's unfinished jobs of these types, a batch at a time (PA-4.3: retiring a module).

        Waiting jobs become ``canceled`` with ``reason`` as their error code;
        leased and running ones become ``cancel_requested``, for their
        handler to stop. Returns the jobs changed; call again until empty.
        """
        types = sorted(set(job_types))
        if not types:
            return []
        rows = self.connection.execute(
            f"""UPDATE omnix_jobs
                   SET status = CASE WHEN status IN ('leased', 'running') THEN 'cancel_requested' ELSE 'canceled' END,
                       error = CASE WHEN status IN ('leased', 'running') THEN error ELSE %s::jsonb END,
                       completed_at = CASE WHEN status IN ('leased', 'running') THEN completed_at ELSE clock_timestamp() END,
                       cancel_requested_at = COALESCE(cancel_requested_at, clock_timestamp()),
                       updated_at = clock_timestamp()
                 WHERE id IN (
                       SELECT id FROM omnix_jobs
                        WHERE workspace_id = %s AND job_type = ANY(%s)
                          AND status IN ('queued', 'retrying', 'waiting', 'paused', 'leased', 'running')
                        ORDER BY created_at, id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED)
                RETURNING {_JOB_COLUMNS}""",
            (_json({"code": reason, "retryable": False}), context.workspace_id, types, EXPIRED_LEASE_BATCH),
        ).fetchall()
        results = [_job(row) for row in rows]
        for result in results:
            self._event(context, result["id"], "job.cancel_requested", {"status": result["status"], "reason": reason})
        return results

    def unfinished_counts(self, job_types: Iterable[str]) -> tuple[int, int]:
        """``(leased or running, waiting)`` jobs of these types in every workspace the session sees (PA-4.3)."""
        active = ["leased", "running", "cancel_requested"]
        row = self.connection.execute(
            """SELECT count(*) FILTER (WHERE status = ANY(%s)), count(*) FILTER (WHERE NOT status = ANY(%s))
                 FROM omnix_jobs
                WHERE job_type = ANY(%s)
                  AND status IN ('queued', 'retrying', 'waiting', 'paused', 'leased', 'running', 'cancel_requested')""",
            (active, active, sorted(set(job_types))),
        ).fetchone()
        return int(row[0]), int(row[1])

    def unclaimed_job_types(
        self, context: TenantContext, *, older_than_seconds: float, known_types: Iterable[str],
    ) -> dict[str, float]:
        """Job types without a handler here whose oldest waiting job is older than the threshold: type -> age in seconds.

        Workers claim only types they handle, so such a job waits (a disabled
        feature, or a newer gateway during a rolling upgrade); past the
        threshold it is worth an alert, never a failure.
        """
        rows = self.connection.execute(
            """SELECT job_type, EXTRACT(EPOCH FROM clock_timestamp() - MIN(available_at))
                 FROM omnix_jobs
                WHERE workspace_id = %s AND status IN ('queued', 'retrying')
                  AND available_at < clock_timestamp() - (%s * INTERVAL '1 second')
                  AND NOT (job_type = ANY(%s))
                GROUP BY job_type ORDER BY job_type LIMIT 100""",
            (context.workspace_id, max(0.0, float(older_than_seconds)), sorted(set(known_types))),
        ).fetchall()
        return {str(row[0]): float(row[1]) for row in rows}

    def claim_next(
        self,
        context: TenantContext,
        *,
        worker_id: str,
        resource_classes: list[str],
        lease_seconds: int = 30,
    ) -> dict[str, Any] | None:
        if not resource_classes:
            return None
        lease_seconds = max(1, min(int(lease_seconds), 3600))
        token = uuid.uuid4().hex
        row = self.connection.execute(
            f"""
            WITH candidate AS (
                SELECT id
                  FROM omnix_jobs
                 WHERE workspace_id = %s
                   AND status IN ('queued', 'retrying', 'waiting')
                   AND available_at <= CURRENT_TIMESTAMP
                   AND resource_class = ANY(%s)
                   AND attempt_count < max_attempts
                   AND job_type <> 'rpg.turn.foreground_record'
                   AND NOT (
                       job_type = 'chat.generate'
                       AND COALESCE(metadata #>> '{{compat_contract,compat,inline_execution}}', 'false') = 'true'
                   )
                   AND NOT (
                       job_type = 'assistant.deep_research'
                       AND COALESCE(input_payload ->> 'awaiting_plan_approval', 'false') = 'true'
                   )
                 ORDER BY (
                     priority::bigint + LEAST(
                         100::numeric,
                         GREATEST(0::numeric, FLOOR(EXTRACT(EPOCH FROM
                             (clock_timestamp() - created_at)) / %s))
                     )
                 ) DESC, created_at ASC, id ASC
                 FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE omnix_jobs AS jobs
               SET status = 'leased',
                   lease_owner = %s,
                   lease_token = %s,
                   lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                   attempt_count = attempt_count + 1,
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                   updated_at = CURRENT_TIMESTAMP,
                   error = NULL
              FROM candidate
             WHERE jobs.id = candidate.id
            RETURNING {_JOB_COLUMNS}
            """,
            (
                context.workspace_id,
                resource_classes,
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

    def renew_lease(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int = 30,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET lease_expires_at = CURRENT_TIMESTAMP + (%s * INTERVAL '1 second'),
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND lease_owner = %s AND lease_token = %s
               AND status IN ('leased', 'running', 'cancel_requested')
               AND lease_expires_at > CURRENT_TIMESTAMP
            RETURNING {_JOB_COLUMNS}
            """,
            (
                max(1, min(int(lease_seconds), 3600)),
                job_id,
                context.workspace_id,
                worker_id,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job lease cannot be renewed: {job_id}")
        return _job(row)

    def mark_running(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            WITH candidate AS (
                SELECT id AS candidate_id, status AS old_status
                  FROM omnix_jobs
                 WHERE id = %s AND workspace_id = %s
                   AND lease_owner = %s AND lease_token = %s
                   AND status IN ('leased', 'running')
                   AND lease_expires_at > clock_timestamp()
                 FOR UPDATE
            )
            UPDATE omnix_jobs
               SET status = 'running', updated_at = CURRENT_TIMESTAMP
              FROM candidate
             WHERE omnix_jobs.id = candidate.candidate_id
            RETURNING {_JOB_COLUMNS}, candidate.old_status
            """,
            (job_id, context.workspace_id, worker_id, lease_token),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job cannot enter running state: {job_id}")
        result = _job(row)
        self.connection.execute(
            "UPDATE omnix_job_attempts SET status = 'running' "
            "WHERE job_id = %s AND attempt = %s AND lease_token = %s",
            (job_id, result["attempt_count"], lease_token),
        )
        if str(row[-1]) == "leased":
            self._event(context, job_id, "job.running", {"worker_id": worker_id})
        return result

    def update_progress(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        progress: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a worker checkpoint without changing job state or ownership."""

        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET progress = %s::jsonb,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND lease_owner = %s AND lease_token = %s
               AND status IN ('running', 'cancel_requested')
               AND lease_expires_at > CURRENT_TIMESTAMP
            RETURNING {_JOB_COLUMNS}
            """,
            (
                _json(progress),
                job_id,
                context.workspace_id,
                worker_id,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job progress update rejected: {job_id}")
        return _job(row)

    def mark_record_only_running(
        self,
        context: TenantContext,
        *,
        job_id: str,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Start a synchronously executed audit record without a worker lease."""
        row = self.connection.execute(
            f"""
            WITH candidate AS (
                SELECT id AS candidate_id, status AS old_status
                  FROM omnix_jobs
                 WHERE id = %s AND workspace_id = %s
                   AND status IN ('queued', 'running')
                   {foreground_owner_guard()}
                 FOR UPDATE
            )
            UPDATE omnix_jobs
               SET status = 'running',
                   started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                   updated_at = CURRENT_TIMESTAMP
              FROM candidate
             WHERE omnix_jobs.id = candidate.candidate_id
            RETURNING {_JOB_COLUMNS}, candidate.old_status
            """,
            (job_id, context.workspace_id, *foreground_guard_credentials(execution_owner, submission_claim_token)),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"record-only job cannot enter running state: {job_id}")
        result = _job(row)
        if str(row[-1]) == "queued":
            self._event(context, job_id, "job.running", {"execution": "foreground_record"})
        return result

    def complete_record_only(
        self,
        context: TenantContext,
        *,
        job_id: str,
        output_refs: list[dict[str, Any]] | list[str],
        progress: dict[str, Any] | None = None,
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Complete a foreground audit record that is never worker-claimed."""
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = 'completed', output_refs = %s::jsonb,
                   progress = %s::jsonb, error = NULL,
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND status IN ('queued', 'running')
            {foreground_owner_guard()}
            RETURNING {_JOB_COLUMNS}
            """,
            (
                _json(output_refs),
                _json(progress or {"current": 1, "total": 1, "message": "completed"}),
                job_id,
                context.workspace_id,
                *foreground_guard_credentials(execution_owner, submission_claim_token),
            ),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"record-only job completion rejected: {job_id}")
        result = _job(row)
        self._event(context, job_id, "job.completed", {"execution": "foreground_record"})
        return result

    def fail_record_only(
        self,
        context: TenantContext,
        *,
        job_id: str,
        error: dict[str, Any],
        execution_owner: str | None = None,
        submission_claim_token: str | None = None,
    ) -> dict[str, Any]:
        """Fail a foreground audit record without scheduling worker retries."""
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = 'failed', error = %s::jsonb,
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND status IN ('queued', 'running')
            {foreground_owner_guard()}
            RETURNING {_JOB_COLUMNS}
            """,
            (_json(error), job_id, context.workspace_id,
             *foreground_guard_credentials(execution_owner, submission_claim_token)),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"record-only job failure rejected: {job_id}")
        result = _job(row)
        self._event(
            context,
            job_id,
            "job.failed",
            {"execution": "foreground_record", "error": error},
        )
        return result

    def complete(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        output_refs: list[dict[str, Any]] | list[str],
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = 'completed', output_refs = %s::jsonb,
                   progress = %s::jsonb, error = NULL,
                   lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                   completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND lease_owner = %s AND lease_token = %s
               AND status IN ('leased', 'running', 'cancel_requested')
               AND lease_expires_at > clock_timestamp()
            RETURNING {_JOB_COLUMNS}
            """,
            (
                _json(output_refs),
                _json(progress or {"current": 1, "total": 1, "message": "completed"}),
                job_id,
                context.workspace_id,
                worker_id,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job completion rejected: {job_id}")
        result = _job(row)
        self.connection.execute(
            """
            UPDATE omnix_job_attempts
               SET status = 'completed', completed_at = CURRENT_TIMESTAMP
             WHERE job_id = %s AND attempt = %s AND lease_token = %s
            """,
            (job_id, result["attempt_count"], lease_token),
        )
        self._event(context, job_id, "job.completed", {"attempt": result["attempt_count"]})
        return result

    def fail(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        error: dict[str, Any],
        retry_delay_seconds: int = 0,
    ) -> dict[str, Any]:
        retryable = bool(error.get("retryable", True))
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = CASE WHEN %s AND attempt_count < max_attempts
                                 THEN 'retrying' ELSE 'failed' END,
                   error = %s::jsonb,
                   available_at = CASE WHEN %s AND attempt_count < max_attempts
                                       THEN CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                                       ELSE available_at END,
                   lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                   completed_at = CASE WHEN %s AND attempt_count < max_attempts
                                       THEN NULL ELSE CURRENT_TIMESTAMP END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
               AND lease_owner = %s AND lease_token = %s
               AND status IN ('leased', 'running', 'cancel_requested')
               AND lease_expires_at > clock_timestamp()
            RETURNING {_JOB_COLUMNS}
            """,
            (
                retryable,
                _json(error),
                retryable,
                max(1, int(retry_delay_seconds)),
                retryable,
                job_id,
                context.workspace_id,
                worker_id,
                lease_token,
            ),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job failure rejected: {job_id}")
        result = _job(row)
        retry = result["status"] == "retrying"
        status = result["status"]
        self.connection.execute(
            """
            UPDATE omnix_job_attempts
               SET status = %s, completed_at = CURRENT_TIMESTAMP, error = %s::jsonb
             WHERE job_id = %s AND attempt = %s AND lease_token = %s
            """,
            (status, _json(error), job_id, result["attempt_count"], lease_token),
        )
        self._event(
            context,
            job_id,
            "job.retry_scheduled" if retry else "job.failed",
            {"attempt": result["attempt_count"], "error": error},
        )
        if not retry:
            self.connection.execute(
                """
                INSERT INTO omnix_dead_letters (workspace_id, job_id, reason, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (context.workspace_id, job_id, str(error.get("code") or "failed"), _json(error)),
            )
        return result

    def release(
        self,
        context: TenantContext,
        *,
        job_id: str,
        worker_id: str,
        lease_token: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Return an active lease to the queue with an auditable attempt result."""
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = 'queued', error = NULL, available_at = clock_timestamp(),
                   lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                   completed_at = NULL, updated_at = clock_timestamp()
             WHERE id = %s AND workspace_id = %s
               AND lease_owner = %s AND lease_token = %s
               AND status IN ('leased', 'running')
               AND lease_expires_at > clock_timestamp()
            RETURNING {_JOB_COLUMNS}
            """,
            (job_id, context.workspace_id, worker_id, lease_token),
        ).fetchone()
        if row is None:
            raise JobClaimConflict(f"job release rejected: {job_id}")
        result = _job(row)
        attempt_result = {
            "code": "job_released",
            "message": str(reason or "Job lease released"),
            "retryable": True,
        }
        self.connection.execute(
            """
            UPDATE omnix_job_attempts
               SET status = 'released', completed_at = clock_timestamp(), error = %s::jsonb
             WHERE job_id = %s AND attempt = %s AND lease_token = %s
            """,
            (_json(attempt_result), job_id, result["attempt_count"], lease_token),
        )
        self._event(
            context,
            job_id,
            "job.released",
            {"attempt": result["attempt_count"], "worker_id": worker_id, "reason": reason},
        )
        return result

    def request_cancel(self, context: TenantContext, job_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            f"""
            UPDATE omnix_jobs
               SET status = CASE
                       WHEN status IN ('queued', 'retrying', 'waiting', 'paused') THEN 'canceled'
                       WHEN status IN ('leased', 'running') THEN 'cancel_requested'
                       ELSE status
                   END,
                   cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
                   completed_at = CASE
                       WHEN status IN ('queued', 'retrying', 'waiting', 'paused') THEN CURRENT_TIMESTAMP
                       ELSE completed_at
                   END,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = %s AND workspace_id = %s
            RETURNING {_JOB_COLUMNS}
            """,
            (job_id, context.workspace_id),
        ).fetchone()
        if row is None:
            raise EntityNotFound(job_id)
        result = _job(row)
        self._event(context, job_id, "job.cancel_requested", {"status": result["status"]})
        return result

    def _event(
        self,
        context: TenantContext,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        # The notification is delivered when this transaction commits, which
        # wakes the process event readers (WP-5.4).
        row = self.connection.execute(
            """
            WITH inserted AS (
                INSERT INTO omnix_job_events (workspace_id, job_id, event_type, payload)
                VALUES (%s, %s, %s, %s::jsonb) RETURNING id
            )
            SELECT id, pg_notify('omnix_events', %s) FROM inserted
            """,
            (context.workspace_id, job_id, event_type, _json(payload), context.workspace_id),
        ).fetchone()
        return int(row[0])
