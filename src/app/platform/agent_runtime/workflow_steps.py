"""Workflow step state: claims, retries, approvals waits, completion and run status (WP-8.2).

Functions over the workflow runtime; ``PostgresWorkflowRuntime`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from typing import Any
from app.persistence.unit_of_work import unit_of_work
from .workflows import (
    WorkflowEvent,
)
from typing import TYPE_CHECKING
from .workflow_runtime import (
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.workflow_runtime import PostgresWorkflowRuntime


def _step_state(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str) -> dict[str, Any] | None:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).step_state_row(run_id, step_id).fetchone()
        work.rollback()
    return (
        {
            "status": str(row[0]),
            "attempts": int(row[1]),
            "result": dict(row[2] or {}),
            "worker_id": str(row[3]) if row[3] else None,
            "lease_expires_at": row[4],
        }
        if row
        else None
    )


def _claim_step(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str) -> int | None:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).claim_step(workflow.worker_id, run_id, step_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.step.started",
                    payload={"step_id": step_id, "attempt": int(row[0])},
                ),
            )
        work.commit()
    return int(row[0]) if row else None


def _set_waiting_for_approval(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str) -> None:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).mark_step_waiting_for_approval(run_id, step_id).fetchone()
        if row is not None:
            workflow.repository(work.connection).mark_run_waiting_for_approval(run_id, step_id)
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.approval.requested",
                    payload={"step_id": step_id},
                ),
            )
        work.commit()


def _set_step_retry(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str, error: str) -> bool:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).reset_step_for_retry(error, run_id, step_id, workflow.worker_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.step.retry",
                    payload={"step_id": step_id, "error": error},
                ),
            )
        work.commit()
    return row is not None


def _set_step_failed(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str, error: str) -> bool:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).fail_step(error, run_id, step_id, workflow.worker_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.step.failed",
                    payload={"step_id": step_id, "error": error},
                ),
            )
        work.commit()
    return row is not None


def _set_step_completed(
    workflow: PostgresWorkflowRuntime,
    run_id: str,
    step_id: str,
    result: dict[str, Any],
) -> bool:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).complete_step(_json(result), run_id, step_id, workflow.worker_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.step.completed",
                    payload={"step_id": step_id, "result": result},
                ),
            )
        work.commit()
    return row is not None


def _transition(workflow: PostgresWorkflowRuntime, run_id: str, target: str | None) -> None:
    if target is None:
        workflow._finish(run_id)
        return
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).move_run_to_step(target, run_id).fetchone()
        work.commit()
    if row is None:
        return


def _finish(workflow: PostgresWorkflowRuntime, run_id: str) -> None:
    with unit_of_work(workflow.database) as work:
        workflow.repository(work.connection).skip_unfinished_steps(run_id)
        row = workflow.repository(work.connection).complete_run(run_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.run.completed",
                ),
            )
        work.commit()


def _set_status(workflow: PostgresWorkflowRuntime, run_id: str, status: str, *, error: str | None = None) -> None:
    terminal = status in {"completed", "failed", "cancelled"}
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).set_run_status(status, error, terminal, run_id).fetchone()
        if row is not None:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type=f"workflow.run.{status}",
                    payload={"error": error} if error else {},
                ),
            )
        work.commit()
