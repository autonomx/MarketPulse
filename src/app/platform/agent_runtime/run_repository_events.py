"""Agent run storage: the per-run event log (WP-8.2).

Functions over the run repository; ``PostgresAgentRunRepository`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, cast
from .contracts import (
    AgentEvent,
)
from app.observability.agent_logging import log_agent_activity
from typing import TYPE_CHECKING
from .repository import (
    EVENT_PAGE_SIZE,
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.repository import PostgresAgentRunRepository


def append_event(repo: PostgresAgentRunRepository, event: AgentEvent) -> AgentEvent:
    """Persist an event and mirror the durable result to the agent trace."""

    log_agent_activity(
        "durable.event.append_requested",
        category="durable",
        run_id=event.run_id,
        fields={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "expected_sequence": event.sequence,
            "payload": event.payload,
        },
    )
    try:
        stored = repo._append_event(event)
    except Exception as exc:
        log_agent_activity(
            "durable.event.append_failed",
            category="durable",
            level="error",
            run_id=event.run_id,
            fields={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "expected_sequence": event.sequence,
            },
            error=exc,
            include_traceback=True,
        )
        raise
    log_agent_activity(
        "durable.event.persisted",
        category="durable",
        run_id=stored.run_id,
        fields={
            "event_id": stored.event_id,
            "sequence": stored.sequence,
            "event_type": stored.event_type,
            "correlation_id": stored.correlation_id,
            "causation_id": stored.causation_id,
            "payload": stored.payload,
        },
    )
    return stored


def _append_event(repo: PostgresAgentRunRepository, event: AgentEvent) -> AgentEvent:
    # The run's counter row orders its appends (WP-7.4) without locking
    # the run row. GREATEST with the indexed MAX lets the counter catch up
    # with events written by code that predates it.
    row = repo.connection.execute(
        """
        INSERT INTO omnix_agent_run_event_counters AS counter (workspace_id, run_id, last_sequence)
        SELECT run.workspace_id, run.run_id,
               COALESCE((SELECT MAX(sequence) FROM omnix_agent_run_events AS event
                          WHERE event.workspace_id = run.workspace_id AND event.run_id = run.run_id), 0) + 1
          FROM omnix_agent_runs AS run
         WHERE run.workspace_id = %s AND run.run_id = %s
        ON CONFLICT (workspace_id, run_id) DO UPDATE
           SET last_sequence = GREATEST(
                   counter.last_sequence,
                   COALESCE((SELECT MAX(sequence) FROM omnix_agent_run_events AS event
                              WHERE event.workspace_id = counter.workspace_id AND event.run_id = counter.run_id), 0)
               ) + 1
        RETURNING last_sequence
        """,
        (repo.context.workspace_id, event.run_id),
    ).fetchone()
    if row is None:
        raise KeyError(event.run_id)
    sequence = int(row[0])
    stored = event.model_copy(update={"sequence": sequence})
    repo.connection.execute(
        """
        INSERT INTO omnix_agent_run_events (
            workspace_id, run_id, sequence, event_id, event_type,
            payload, correlation_id, causation_id, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
        """,
        (
            repo.context.workspace_id,
            stored.run_id,
            sequence,
            stored.event_id,
            stored.event_type,
            _json(stored.payload),
            stored.correlation_id,
            stored.causation_id,
            stored.created_at,
        ),
    )
    repo.outbox.append(
        repo.context,
        aggregate_type="agent_run",
        aggregate_id=stored.run_id,
        event_type=stored.event_type,
        payload=stored.model_dump(mode="json"),
        ordering_key=f"agent:{stored.run_id}",
        correlation_id=stored.correlation_id,
        causation_id=stored.causation_id,
        event_key=f"agent:{stored.event_id}",
    )
    return stored


def list_events(repo: PostgresAgentRunRepository, run_id: str, *, after_sequence: int = 0, limit: int = 500) -> list[AgentEvent]:
    return repo._event_page(run_id, after_sequence=after_sequence, limit=max(1, min(limit, 5000)))


def iter_events(
    repo: PostgresAgentRunRepository,
    run_id: str,
    *,
    event_types: Iterable[str] | None = None,
    page_size: int = EVENT_PAGE_SIZE,
) -> Iterator[AgentEvent]:
    """Every event of a run (optionally of some types), read in pages (WP-7.4)."""
    types = sorted(set(event_types)) if event_types is not None else None
    after = 0
    while True:
        page = repo._event_page(run_id, after_sequence=after, limit=page_size, event_types=types)
        yield from page
        if len(page) < page_size:
            return
        after = int(page[-1].sequence or after)


def latest_event(
    repo: PostgresAgentRunRepository,
    run_id: str,
    event_type: str,
    *,
    payload_contains: dict[str, Any] | None = None,
) -> AgentEvent | None:
    """The run's most recent event of ``event_type`` (whose payload contains ``payload_contains``)."""
    rows = repo.connection.execute(
        """
        SELECT event_id, sequence, event_type, payload, correlation_id, causation_id, created_at
          FROM omnix_agent_run_events
         WHERE workspace_id = %s AND run_id = %s AND event_type = %s
           AND (%s::jsonb IS NULL OR payload @> %s::jsonb)
         ORDER BY sequence DESC
         LIMIT 1
        """,
        (
            repo.context.workspace_id,
            run_id,
            event_type,
            _json(payload_contains) if payload_contains is not None else None,
            _json(payload_contains) if payload_contains is not None else None,
        ),
    ).fetchall()
    return repo._events_from_rows(run_id, rows)[0] if rows else None


def _event_page(
    repo: PostgresAgentRunRepository,
    run_id: str,
    *,
    after_sequence: int,
    limit: int,
    event_types: list[str] | None = None,
) -> list[AgentEvent]:
    rows = repo.connection.execute(
        """
        SELECT event_id, sequence, event_type, payload, correlation_id, causation_id, created_at
          FROM omnix_agent_run_events
         WHERE workspace_id = %s AND run_id = %s AND sequence > %s
           AND (%s::text[] IS NULL OR event_type = ANY(%s::text[]))
         ORDER BY sequence
         LIMIT %s
        """,
        (repo.context.workspace_id, run_id, max(0, after_sequence), event_types, event_types, limit),
    ).fetchall()
    return repo._events_from_rows(run_id, rows)


def _events_from_rows(run_id: str, rows: list[Any]) -> list[AgentEvent]:
    return [
        AgentEvent(
            event_id=str(row[0]),
            run_id=run_id,
            sequence=int(row[1]),
            event_type=cast(Any, str(row[2])),
            payload=dict(row[3] or {}),
            correlation_id=str(row[4]) if row[4] else None,
            causation_id=str(row[5]) if row[5] else None,
            created_at=row[6],
        )
        for row in rows
    ]


def latest_progress_event(repo: PostgresAgentRunRepository, run_id: str) -> AgentEvent | None:
    """Return the latest durable event that represents agent progress.

    Worker heartbeats and orchestration bookkeeping keep a lease or
    durable state current but do not prove that Pi is advancing. The
    supervisor uses this event-log checkpoint to detect a live worker
    whose runtime has stopped making progress. In particular, approving
    or resuming a stuck run must not move the progress checkpoint forward.
    """
    row = repo.connection.execute(
        """
        SELECT event_id, sequence, event_type, payload,
               correlation_id, causation_id, created_at
          FROM omnix_agent_run_events
         WHERE workspace_id = %s
           AND run_id = %s
           AND event_type NOT IN (
               'worker.heartbeat',
               'run.status',
               'approval.requested',
               'approval.resolved',
               'steering.received',
               'task.revised',
               'run.recovery_requested',
               'run.recovery_failed',
               'run.stall_suspected'
           )
         ORDER BY sequence DESC
         LIMIT 1
        """,
        (repo.context.workspace_id, run_id),
    ).fetchone()
    if row is None:
        return None
    return AgentEvent(
        event_id=str(row[0]),
        run_id=run_id,
        sequence=int(row[1]),
        event_type=cast(Any, str(row[2])),
        payload=dict(row[3] or {}),
        correlation_id=str(row[4]) if row[4] else None,
        causation_id=str(row[5]) if row[5] else None,
        created_at=row[6],
    )


def count_events(repo: PostgresAgentRunRepository, run_id: str, event_type: str) -> int:
    row = repo.connection.execute(
        """
        SELECT COUNT(*)
          FROM omnix_agent_run_events
         WHERE workspace_id = %s AND run_id = %s AND event_type = %s
        """,
        (repo.context.workspace_id, run_id, event_type),
    ).fetchone()
    return int(row[0] or 0) if row else 0
