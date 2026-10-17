"""Agent run storage: capability executions and evidence-query reservations (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from app.capabilities.registry import capability_definition_hash

from datetime import datetime
from typing import Any
from .contracts import (
    AgentApproval,
)
from typing import TYPE_CHECKING
from .repository import (
    AgentRunConcurrencyError,
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def ensure_capability_execution(
    repo: PostgresAgentRunRepository,
    run_id: str,
    execution_key: str,
    capability_id: str,
    request_payload: dict[str, Any],
) -> dict[str, Any]:
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_capability_executions (
            workspace_id, run_id, execution_key, capability_id, state, request_payload
        ) VALUES (%s, %s, %s, %s, 'created', %s::jsonb)
        ON CONFLICT (workspace_id, run_id, execution_key) DO NOTHING
        """,
        (
            repo.context.workspace_id,
            run_id,
            execution_key,
            capability_id,
            _json(request_payload),
        ),
    )
    row = repo.connection.execute(
        """
        SELECT capability_id, state, request_payload, result_payload, error,
               state_changed, created_at, updated_at
          FROM omnix_agent_capability_executions
         WHERE workspace_id = %s AND run_id = %s AND execution_key = %s
        """,
        (repo.context.workspace_id, run_id, execution_key),
    ).fetchone()
    if row is None:
        raise AgentRunConcurrencyError("capability execution disappeared")
    return {
        "capability_id": str(row[0]),
        "state": str(row[1]),
        "request_payload": dict(row[2] or {}),
        "result_payload": dict(row[3] or {}),
        "error": str(row[4]) if row[4] else None,
        "state_changed": bool(row[5]),
        "created_at": row[6],
        "updated_at": row[7],
    }


def find_capability_approval(
    repo: PostgresAgentRunRepository,
    run_id: str,
    capability_id: str,
    execution_key: str,
) -> AgentApproval | None:
    row = repo.connection.execute(
        """
        SELECT approval_id
          FROM omnix_agent_approvals
         WHERE workspace_id = %s AND run_id = %s AND capability_id = %s
           AND request_payload ->> 'execution_key' = %s
           AND capability_definition_hash IS NOT DISTINCT FROM %s
         ORDER BY created_at
         LIMIT 1
        """,
        (repo.context.workspace_id, run_id, capability_id, execution_key,
         capability_definition_hash(capability_id)),
    ).fetchone()
    if row is None:
        return None
    return repo.get_approval(run_id, str(row[0]))


def mark_capability_waiting_for_approval(
    repo: PostgresAgentRunRepository,
    run_id: str,
    execution_key: str,
) -> None:
    repo.connection.execute(
        """
        UPDATE omnix_agent_capability_executions
           SET state = 'waiting_for_approval', updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND execution_key = %s
           AND state = 'created'
        """,
        (repo.context.workspace_id, run_id, execution_key),
    )


def claim_capability_execution(repo: PostgresAgentRunRepository, run_id: str, execution_key: str) -> bool:
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_capability_executions
           SET state = 'running', updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND execution_key = %s
           AND state IN ('created','waiting_for_approval')
        RETURNING execution_key
        """,
        (repo.context.workspace_id, run_id, execution_key),
    ).fetchone()
    return row is not None


def finish_capability_execution(
    repo: PostgresAgentRunRepository,
    run_id: str,
    execution_key: str,
    *,
    result_payload: dict[str, Any],
    error: str | None,
    state_changed: bool,
) -> None:
    repo.connection.execute(
        """
        UPDATE omnix_agent_capability_executions
           SET state = %s, result_payload = %s::jsonb, error = %s,
               state_changed = %s, updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND execution_key = %s
           AND state IN ('created','waiting_for_approval','running')
        """,
        (
            "failed" if error else "completed",
            _json(result_payload),
            error,
            state_changed,
            repo.context.workspace_id,
            run_id,
            execution_key,
        ),
    )


def reserve_evidence_query(
    repo: PostgresAgentRunRepository,
    run_id: str,
    task_revision_id: str,
    execution_key: str,
    *,
    max_queries: int,
    max_sources: int,
    max_extracts: int,
    requested_sources: int,
    requested_extracts: int,
) -> dict[str, Any]:
    """Atomically reserve one evidence attempt and aggregate source/extract capacity."""
    locked = repo.connection.execute(
        """
        SELECT revision_id
          FROM omnix_agent_task_revisions
         WHERE workspace_id = %s AND run_id = %s AND revision_id = %s
         FOR UPDATE
        """,
        (repo.context.workspace_id, run_id, task_revision_id),
    ).fetchone()
    if locked is None:
        raise KeyError(task_revision_id)

    existing = repo.connection.execute(
        """
        SELECT reserved_sources, reserved_extracts, actual_sources,
               actual_extracts, state
          FROM omnix_agent_evidence_query_reservations
         WHERE workspace_id = %s AND run_id = %s
           AND task_revision_id = %s AND execution_key = %s
        """,
        (
            repo.context.workspace_id,
            run_id,
            task_revision_id,
            execution_key,
        ),
    ).fetchone()
    if existing is not None:
        return {
            "allowed": True,
            "reused": True,
            "reserved_sources": int(existing[0] or 0),
            "reserved_extracts": int(existing[1] or 0),
            "actual_sources": int(existing[2] or 0),
            "actual_extracts": int(existing[3] or 0),
            "state": str(existing[4]),
        }

    aggregate = repo.connection.execute(
        """
        SELECT COUNT(*),
               COALESCE(SUM(reserved_sources), 0),
               COALESCE(SUM(reserved_extracts), 0)
          FROM omnix_agent_evidence_query_reservations
         WHERE workspace_id = %s AND run_id = %s
           AND task_revision_id = %s
        """,
        (repo.context.workspace_id, run_id, task_revision_id),
    ).fetchone()
    queries = int(aggregate[0] or 0)
    used_sources = int(aggregate[1] or 0)
    used_extracts = int(aggregate[2] or 0)
    if queries >= max_queries:
        return {"allowed": False, "reason": "query_budget_exceeded"}

    remaining_sources = max(0, max_sources - used_sources)
    remaining_extracts = max(0, max_extracts - used_extracts)
    sources = min(max(0, requested_sources), remaining_sources)
    extracts = min(max(0, requested_extracts), remaining_extracts)
    if requested_sources > 0 and sources <= 0:
        return {"allowed": False, "reason": "source_budget_exceeded"}
    if requested_extracts > 0 and extracts <= 0:
        extracts = 0

    repo.connection.execute(
        """
        INSERT INTO omnix_agent_evidence_query_reservations (
            workspace_id, run_id, task_revision_id, execution_key,
            reserved_sources, reserved_extracts
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            repo.context.workspace_id,
            run_id,
            task_revision_id,
            execution_key,
            sources,
            extracts,
        ),
    )
    return {
        "allowed": True,
        "reused": False,
        "reserved_sources": sources,
        "reserved_extracts": extracts,
        "actual_sources": 0,
        "actual_extracts": 0,
        "state": "reserved",
    }


def finish_evidence_query(
    repo: PostgresAgentRunRepository,
    run_id: str,
    task_revision_id: str,
    execution_key: str,
    *,
    actual_sources: int,
    actual_extracts: int,
    failed: bool,
) -> None:
    repo.connection.execute(
        """
        UPDATE omnix_agent_evidence_query_reservations
           SET reserved_sources = LEAST(reserved_sources, %s),
               reserved_extracts = LEAST(reserved_extracts, %s),
               actual_sources = %s,
               actual_extracts = %s,
               state = %s,
               updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s
           AND task_revision_id = %s AND execution_key = %s
        """,
        (
            max(0, actual_sources),
            max(0, actual_extracts),
            max(0, actual_sources),
            max(0, actual_extracts),
            "failed" if failed else "completed",
            repo.context.workspace_id,
            run_id,
            task_revision_id,
            execution_key,
        ),
    )


def reclaim_stale_read_capability_execution(
    repo: PostgresAgentRunRepository,
    run_id: str,
    execution_key: str,
    *,
    stale_before: datetime,
) -> bool:
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_capability_executions
           SET state = 'created', updated_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND execution_key = %s
           AND state = 'running' AND updated_at <= %s
        RETURNING execution_key
        """,
        (
            repo.context.workspace_id,
            run_id,
            execution_key,
            stale_before,
        ),
    ).fetchone()
    return row is not None
