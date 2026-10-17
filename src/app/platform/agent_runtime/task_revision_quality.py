"""Persist and hydrate the engineering fields added to canonical TaskRevision."""
from __future__ import annotations

import json
from typing import Any

from app.persistence.tenant import TenantContext

from .contracts import TaskConstraint, TaskRequirement, TaskRevision, ValidationSpec
from .run_repository_queries import PostgresAgentRunQueries


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, list):
        value = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _retire_stale_quality_commands(
    connection: Any,
    context: TenantContext,
    revision: TaskRevision,
) -> bool:
    """Consume pending internal quality resumes from superseded task revisions.

    A quality-stage transition and its resume prompt are committed together, so
    a crash can legitimately leave the resume command pending. If the user then
    steers the run to a new TaskRevision before that command is replayed, the old
    prompt is no longer authoritative. Retire only Omnix-internal quality resumes
    and only when ``revision`` is the current latest revision; user commands and
    recovery commands without quality identity remain untouched.

    Returns whether ``revision`` is the current latest revision so the caller can
    invalidate other revision-bound authority in the same transaction without a
    second race-prone latest-revision lookup.
    """

    row = PostgresAgentRunQueries(connection, context).latest_task_revision_id(revision.run_id).fetchone()
    if row is None or str(row[0]) != revision.revision_id:
        return False
    PostgresAgentRunQueries(connection, context).retire_stale_quality_resumes(revision.run_id, revision.revision_id)
    return True


def _stale_superseded_planning_state(
    connection: Any,
    context: TenantContext,
    revision: TaskRevision,
) -> None:
    """Invalidate durable planning authority when user steering creates a revision.

    Planning state intentionally keeps the old ``task_revision_id`` and active
    plan lineage for auditability. The next plan inspection sees the mismatch and
    establishes a fresh baseline/state for the new authoritative TaskRevision.
    """

    PostgresAgentRunQueries(connection, context).mark_planning_state_stale(revision.run_id, revision.revision_id)


def persist_task_revision_contract(connection: Any, context: TenantContext, revision: TaskRevision) -> None:
    PostgresAgentRunQueries(connection, context).update_task_revision_contract(_json(revision.requirements), _json(revision.constraints), _json(revision.validation_plan), revision.run_id, revision.revision_id)
    if _retire_stale_quality_commands(connection, context, revision):
        _stale_superseded_planning_state(connection, context, revision)


def hydrate_task_revision(connection: Any, context: TenantContext, revision: TaskRevision) -> TaskRevision:
    row = PostgresAgentRunQueries(connection, context).task_revision_contract(revision.run_id, revision.revision_id).fetchone()
    if row is None:
        return revision
    payload = revision.model_dump(mode="python")
    payload.update(
        {
            "requirements": [TaskRequirement.model_validate(item) for item in list(row[0] or [])],
            "constraints": [TaskConstraint.model_validate(item) for item in list(row[1] or [])],
            "validation_plan": [ValidationSpec.model_validate(item) for item in list(row[2] or [])],
        }
    )
    return TaskRevision.model_validate(payload)


def hydrate_task_revisions(connection: Any, context: TenantContext, revisions: list[TaskRevision]) -> list[TaskRevision]:
    return [hydrate_task_revision(connection, context, revision) for revision in revisions]
