"""Agent run storage: usage accounting and run leases (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any
from .contracts import (
    WorkerLease,
)
from typing import TYPE_CHECKING
from .repository import (
    AgentLeaseConflict,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def get_usage(repo: PostgresAgentRunRepository, run_id: str) -> dict[str, Any]:
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_run_usage (workspace_id, run_id)
        VALUES (%s, %s)
        ON CONFLICT (workspace_id, run_id) DO NOTHING
        """,
        (repo.context.workspace_id, run_id),
    )
    row = repo.connection.execute(
        """
        SELECT steps, tool_calls, model_calls, input_tokens, output_tokens, cost,
               input_tokens_reported, output_tokens_reported
          FROM omnix_agent_run_usage
         WHERE workspace_id = %s AND run_id = %s
        """,
        (repo.context.workspace_id, run_id),
    ).fetchone()
    if row is None:
        raise KeyError(run_id)
    return {
        "steps": int(row[0]),
        "tool_calls": int(row[1]),
        "model_calls": int(row[2]),
        "input_tokens": int(row[3]),
        "output_tokens": int(row[4]),
        "cost": float(row[5]),
        "input_tokens_reported": bool(row[6]),
        "output_tokens_reported": bool(row[7]),
    }


def consume_usage(
    repo: PostgresAgentRunRepository,
    run_id: str,
    *,
    steps: int = 0,
    tool_calls: int = 0,
    model_calls: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    input_tokens_reported: bool = False,
    output_tokens_reported: bool = False,
    cost: float = 0.0,
    max_steps: int | None = None,
    max_tool_calls: int | None = None,
    max_output_tokens: int | None = None,
    max_cost: float | None = None,
) -> dict[str, Any] | None:
    if min(steps, tool_calls, model_calls, input_tokens, output_tokens) < 0 or cost < 0:
        raise ValueError("usage deltas must be non-negative")
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_run_usage (workspace_id, run_id)
        VALUES (%s, %s)
        ON CONFLICT (workspace_id, run_id) DO NOTHING
        """,
        (repo.context.workspace_id, run_id),
    )
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_run_usage
           SET steps = steps + %s,
               tool_calls = tool_calls + %s,
               model_calls = model_calls + %s,
               input_tokens = input_tokens + %s,
               output_tokens = output_tokens + %s,
               input_tokens_reported = input_tokens_reported OR %s,
               output_tokens_reported = output_tokens_reported OR %s,
               cost = cost + %s,
               updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s
           AND (%s::BIGINT IS NULL OR steps + %s <= %s::BIGINT)
           AND (%s::BIGINT IS NULL OR tool_calls + %s <= %s::BIGINT)
           AND (%s::BIGINT IS NULL OR output_tokens + %s <= %s::BIGINT)
           AND (%s::NUMERIC IS NULL OR cost + %s <= %s::NUMERIC)
        RETURNING steps, tool_calls, model_calls, input_tokens, output_tokens, cost,
                  input_tokens_reported, output_tokens_reported
        """,
        (
            steps,
            tool_calls,
            model_calls,
            input_tokens,
            output_tokens,
            input_tokens_reported,
            output_tokens_reported,
            cost,
            repo.context.workspace_id,
            run_id,
            max_steps,
            steps,
            max_steps,
            max_tool_calls,
            tool_calls,
            max_tool_calls,
            max_output_tokens,
            output_tokens,
            max_output_tokens,
            max_cost,
            cost,
            max_cost,
        ),
    ).fetchone()
    if row is None:
        return None
    return {
        "steps": int(row[0]),
        "tool_calls": int(row[1]),
        "model_calls": int(row[2]),
        "input_tokens": int(row[3]),
        "output_tokens": int(row[4]),
        "cost": float(row[5]),
        "input_tokens_reported": bool(row[6]),
        "output_tokens_reported": bool(row[7]),
    }


def acquire_lease(repo: PostgresAgentRunRepository, run_id: str, *, worker_id: str, ttl_seconds: int = 30) -> WorkerLease:
    token = uuid.uuid4().hex
    expires = datetime.now(timezone.utc) + timedelta(seconds=max(5, ttl_seconds))
    row = repo.connection.execute(
        """
        INSERT INTO omnix_agent_worker_leases (
            workspace_id, run_id, worker_id, lease_token, lease_expires_at, heartbeat_at
        ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (workspace_id, run_id) DO UPDATE
           SET worker_id = EXCLUDED.worker_id,
               lease_token = EXCLUDED.lease_token,
               lease_expires_at = EXCLUDED.lease_expires_at,
               heartbeat_at = CURRENT_TIMESTAMP,
               revision = omnix_agent_worker_leases.revision + 1
         WHERE omnix_agent_worker_leases.lease_expires_at <= CURRENT_TIMESTAMP
            OR omnix_agent_worker_leases.worker_id = EXCLUDED.worker_id
        RETURNING worker_id, lease_token, lease_expires_at, heartbeat_at, revision
        """,
        (repo.context.workspace_id, run_id, worker_id, token, expires),
    ).fetchone()
    if row is None:
        raise AgentLeaseConflict(f"run {run_id} is leased by another worker")
    return WorkerLease(
        run_id=run_id,
        worker_id=str(row[0]),
        lease_token=str(row[1]),
        lease_expires_at=row[2],
        heartbeat_at=row[3],
        revision=int(row[4]),
    )


def get_active_lease(repo: PostgresAgentRunRepository, run_id: str) -> WorkerLease | None:
    row = repo.connection.execute(
        """
        SELECT worker_id, lease_token, lease_expires_at, heartbeat_at, revision
          FROM omnix_agent_worker_leases
         WHERE workspace_id = %s AND run_id = %s
           AND lease_expires_at > CURRENT_TIMESTAMP
        """,
        (repo.context.workspace_id, run_id),
    ).fetchone()
    if row is None:
        return None
    return WorkerLease(
        run_id=run_id,
        worker_id=str(row[0]),
        lease_token=str(row[1]),
        lease_expires_at=row[2],
        heartbeat_at=row[3],
        revision=int(row[4]),
    )


def renew_lease(repo: PostgresAgentRunRepository, run_id: str, *, worker_id: str, ttl_seconds: int = 30) -> WorkerLease:
    """Renew an active lease without touching run state or changing ownership identity.

    Heartbeats are liveness bookkeeping, not ownership acquisition. Keeping
    renewal on ``omnix_agent_worker_leases`` means review/acceptance can hold
    the authoritative run row without blocking worker liveness. The stable
    lease token identifies one ownership generation until the lease expires
    or another worker acquires it.
    """

    expires = datetime.now(timezone.utc) + timedelta(seconds=max(5, ttl_seconds))
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_worker_leases
           SET lease_expires_at = %s,
               heartbeat_at = CURRENT_TIMESTAMP,
               revision = revision + 1
         WHERE workspace_id = %s AND run_id = %s AND worker_id = %s
           AND lease_expires_at > CURRENT_TIMESTAMP
        RETURNING worker_id, lease_token, lease_expires_at, heartbeat_at, revision
        """,
        (expires, repo.context.workspace_id, run_id, worker_id),
    ).fetchone()
    if row is None:
        owner = repo.connection.execute(
            """
            SELECT worker_id, lease_expires_at
              FROM omnix_agent_worker_leases
             WHERE workspace_id = %s AND run_id = %s
            """,
            (repo.context.workspace_id, run_id),
        ).fetchone()
        if owner is None:
            raise AgentLeaseConflict(f"run {run_id} has no active lease to renew")
        raise AgentLeaseConflict(
            f"run {run_id} lease cannot be renewed by {worker_id}; "
            f"owner={owner[0]} expires_at={owner[1]}"
        )
    return WorkerLease(
        run_id=run_id,
        worker_id=str(row[0]),
        lease_token=str(row[1]),
        lease_expires_at=row[2],
        heartbeat_at=row[3],
        revision=int(row[4]),
    )
