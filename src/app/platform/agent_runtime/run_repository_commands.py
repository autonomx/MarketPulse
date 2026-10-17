"""Agent run storage: the durable command queue (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from .contracts import (
    AgentEvent,
    AgentRunCommand,
)
from typing import Any, TYPE_CHECKING, cast
from .repository import (
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def enqueue_command(repo: PostgresAgentRunRepository, command: AgentRunCommand) -> AgentRunCommand:
    stored, _ = repo.enqueue_command_with_status(command)
    return stored


def enqueue_command_with_status(repo: PostgresAgentRunRepository, command: AgentRunCommand) -> tuple[AgentRunCommand, str]:
    inserted = repo.connection.execute(
        """
        INSERT INTO omnix_agent_run_commands (
            workspace_id, run_id, command_id, command_type, payload,
            idempotency_key, created_at
        ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (workspace_id, run_id, idempotency_key) DO NOTHING
        RETURNING command_id
        """,
        (
            repo.context.workspace_id,
            command.run_id,
            command.command_id,
            command.command_type,
            _json(command.payload),
            command.idempotency_key,
            command.created_at,
        ),
    ).fetchone()
    row = repo.connection.execute(
        """
        SELECT command_id, command_type, payload, idempotency_key, created_at, status
          FROM omnix_agent_run_commands
         WHERE workspace_id = %s AND run_id = %s AND idempotency_key = %s
        """,
        (repo.context.workspace_id, command.run_id, command.idempotency_key),
    ).fetchone()
    stored = AgentRunCommand(
        command_id=str(row[0]),
        run_id=command.run_id,
        command_type=cast(Any, str(row[1])),
        payload=dict(row[2] or {}),
        idempotency_key=str(row[3]),
        created_at=row[4],
    )
    if inserted is not None:
        repo.append_event(
            AgentEvent(
                run_id=command.run_id,
                event_type="steering.received" if stored.command_type == "steer" else "run.status",
                payload={"command_id": stored.command_id, "command_type": stored.command_type},
            )
        )
    return stored, str(row[5])


def claim_command(repo: PostgresAgentRunRepository, run_id: str, command_id: str) -> bool:
    row = repo.connection.execute(
        """
        UPDATE omnix_agent_run_commands
           SET status = 'processing'
         WHERE workspace_id = %s AND run_id = %s AND command_id = %s
           AND status = 'pending'
        RETURNING command_id
        """,
        (repo.context.workspace_id, run_id, command_id),
    ).fetchone()
    return row is not None


def complete_command(repo: PostgresAgentRunRepository, run_id: str, command_id: str) -> None:
    repo.connection.execute(
        """
        UPDATE omnix_agent_run_commands
           SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP
         WHERE workspace_id = %s AND run_id = %s AND command_id = %s
           AND status = 'processing'
        """,
        (repo.context.workspace_id, run_id, command_id),
    )


def reset_processing_commands(repo: PostgresAgentRunRepository, run_id: str) -> None:
    repo.connection.execute(
        """
        UPDATE omnix_agent_run_commands
           SET status = 'pending', consumed_at = NULL
         WHERE workspace_id = %s AND run_id = %s AND status = 'processing'
        """,
        (repo.context.workspace_id, run_id),
    )


def claim_commands(repo: PostgresAgentRunRepository, run_id: str, *, limit: int = 20) -> list[AgentRunCommand]:
    """Compatibility batch claim used by repository consumers.

    The orchestration service uses claim_command()/complete_command() so a
    command is not marked consumed until its side effect succeeds. This
    legacy batch API preserves the Phase-3 repository contract for callers
    that explicitly want dequeue-and-consume semantics.
    """
    rows = repo.connection.execute(
        """
        WITH claimed AS (
            SELECT command_id
              FROM omnix_agent_run_commands
             WHERE workspace_id = %s AND run_id = %s AND status = 'pending'
             ORDER BY created_at, command_id
             FOR UPDATE SKIP LOCKED
             LIMIT %s
        )
        UPDATE omnix_agent_run_commands AS command
           SET status = 'consumed', consumed_at = CURRENT_TIMESTAMP
          FROM claimed
         WHERE command.workspace_id = %s AND command.run_id = %s
           AND command.command_id = claimed.command_id
        RETURNING command.command_id, command.command_type, command.payload,
                  command.idempotency_key, command.created_at
        """,
        (
            repo.context.workspace_id,
            run_id,
            max(1, min(limit, 100)),
            repo.context.workspace_id,
            run_id,
        ),
    ).fetchall()
    return [
        AgentRunCommand(
            command_id=str(row[0]),
            run_id=run_id,
            command_type=cast(Any, str(row[1])),
            payload=dict(row[2] or {}),
            idempotency_key=str(row[3]),
            created_at=row[4],
        )
        for row in rows
    ]


def list_pending_commands(repo: PostgresAgentRunRepository, run_id: str, *, limit: int = 100) -> list[AgentRunCommand]:
    rows = repo.connection.execute(
        """
        SELECT command_id, command_type, payload, idempotency_key, created_at
          FROM omnix_agent_run_commands
         WHERE workspace_id = %s AND run_id = %s AND status = 'pending'
         ORDER BY created_at, command_id
         LIMIT %s
        """,
        (repo.context.workspace_id, run_id, max(1, min(limit, 1000))),
    ).fetchall()
    return [
        AgentRunCommand(
            command_id=str(row[0]),
            run_id=run_id,
            command_type=cast(Any, str(row[1])),
            payload=dict(row[2] or {}),
            idempotency_key=str(row[3]),
            created_at=row[4],
        )
        for row in rows
    ]
