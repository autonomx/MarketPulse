"""What the kernel reads about the agent runtime without loading it (ADR-0016, PA-2.2): kernel imports only."""
from __future__ import annotations

from typing import Any

from app.persistence.declarations import RetentionDeclaration

# Agent run events kept for the life of the run: milestones and evidence.
AGENT_EVENT_KEEP_PREFIXES = ("evidence.", "approval.", "acceptance.", "artifact.")
AGENT_EVENT_KEEP_TYPES = (
    "run.created", "run.started", "run.completed", "run.failed", "run.settled", "run.superseded",
    "run.unsandboxed",
    "task.revised", "quality.review_completed", "quality.self_review_completed", "quality.validation_recorded",
)


def _delete_run_events(connection: Any, days: int, batch: int) -> int:
    """Events of finished runs, except milestones and evidence."""
    return int(connection.execute(
        """DELETE FROM omnix_agent_run_events WHERE ctid IN (
               SELECT events.ctid FROM omnix_agent_run_events AS events
                 JOIN omnix_agent_runs AS runs
                   ON runs.workspace_id = events.workspace_id AND runs.run_id = events.run_id
                WHERE runs.status IN ('completed', 'failed', 'cancelled')
                  AND COALESCE(runs.completed_at, runs.updated_at) < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                  AND NOT (events.event_type = ANY(%s))
                  AND NOT (events.event_type LIKE ANY(%s))
                LIMIT %s)""",
        (days, list(AGENT_EVENT_KEEP_TYPES), [prefix + "%" for prefix in AGENT_EVENT_KEEP_PREFIXES], batch),
    ).rowcount)


RETENTION = (RetentionDeclaration("agent_run_events", delete=_delete_run_events),)
