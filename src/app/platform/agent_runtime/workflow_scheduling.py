"""Workflow schedules: registration of schedules, due fires and the supervisor pass (WP-8.2).

Functions over the workflow runtime; ``PostgresWorkflowRuntime`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from .exception_logging import log_recovered_exception
from datetime import datetime, timedelta, timezone
import uuid
from app.persistence.unit_of_work import unit_of_work
from .workflows import (
    WorkflowEvent,
    WorkflowScheduleSnapshot,
)
from typing import Any, TYPE_CHECKING
from .workflow_runtime import (
    WorkflowRuntimeError,
    _json,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.workflow_runtime import PostgresWorkflowRuntime


def schedule(
    workflow: PostgresWorkflowRuntime,
    workflow_id: str,
    input_payload: dict[str, Any],
    *,
    run_at: datetime,
    interval_seconds: int | None = None,
    version: int | None = None,
    schedule_id: str | None = None,
) -> str:
    workflow._ensure_supervisor()
    if run_at.tzinfo is None or run_at.utcoffset() is None:
        raise WorkflowRuntimeError("workflow_schedule_run_at_must_be_timezone_aware")
    if interval_seconds is not None and int(interval_seconds) < 60:
        raise WorkflowRuntimeError("workflow_schedule_interval_minimum_60_seconds")
    if "idempotency_key" in input_payload:
        raise WorkflowRuntimeError("workflow_schedule_input_reserves_idempotency_key")
    definition = workflow._definition(workflow_id, version=version)
    if definition is None:
        raise KeyError(workflow_id)
    issued_id = str(schedule_id or uuid.uuid4().hex).strip()
    if not issued_id:
        raise WorkflowRuntimeError("workflow_schedule_id_required")
    normalized_run_at = run_at.astimezone(timezone.utc)
    interval = int(interval_seconds) if interval_seconds is not None else None
    with unit_of_work(workflow.database) as work:
        inserted = workflow.repository(work.connection).insert_schedule(issued_id, definition.id, definition.version, _json(input_payload), interval, normalized_run_at).fetchone()
        if inserted is None:
            existing = workflow.repository(work.connection).schedule_row(issued_id).fetchone()
            if existing is None:
                raise WorkflowRuntimeError("workflow_schedule_conflict")
            same = (
                str(existing[0]) == definition.id
                and int(existing[1]) == definition.version
                and dict(existing[2] or {}) == dict(input_payload)
                and (
                    int(existing[3]) if existing[3] is not None else None
                ) == interval
                and existing[4] == normalized_run_at
                and bool(existing[5])
            )
            if not same:
                raise WorkflowRuntimeError(
                    f"workflow_schedule_id_reused:{issued_id}"
                )
            work.rollback()
            return issued_id
        work.commit()
    return issued_id


def list_schedules(workflow: PostgresWorkflowRuntime) -> list[WorkflowScheduleSnapshot]:
    with unit_of_work(workflow.database) as work:
        rows = workflow.repository(work.connection).schedules().fetchall()
        work.rollback()
    return [
        WorkflowScheduleSnapshot(
            schedule_id=str(row[0]),
            workflow_id=str(row[1]),
            workflow_version=int(row[2]),
            input_payload=dict(row[3] or {}),
            interval_seconds=int(row[4]) if row[4] is not None else None,
            next_run_at=row[5],
            enabled=bool(row[6]),
            last_enqueued_at=row[7],
        )
        for row in rows
    ]


def cancel_schedule(workflow: PostgresWorkflowRuntime, schedule_id: str) -> None:
    with unit_of_work(workflow.database) as work:
        row = workflow.repository(work.connection).disable_schedule(schedule_id).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        workflow.repository(work.connection).cancel_pending_fires(schedule_id)
        work.commit()


def _supervise_once(workflow: PostgresWorkflowRuntime) -> None:
    workflow._enqueue_due_schedule_fires()
    workflow._dispatch_pending_schedule_fires()
    error = "step_outcome_unknown_after_worker_loss"
    resumable: list[str] = []
    with unit_of_work(workflow.database) as work:
        workflow.repository(work.connection).renew_step_leases(workflow.worker_id)
        stale = workflow.repository(work.connection).expired_running_steps().fetchall()
        for run_id, step_id in stale:
            claimed = workflow.repository(work.connection).fail_expired_step(error, str(run_id), str(step_id)).fetchone()
            if claimed is None:
                continue
            failed_run = workflow.repository(work.connection).fail_run_for_expired_step(error, str(run_id)).fetchone()
            if failed_run is not None:
                workflow._append_event(
                    work.connection,
                    WorkflowEvent(
                        run_id=str(run_id),
                        event_type="workflow.step.failed",
                        payload={
                            "step_id": str(step_id),
                            "error": error,
                        },
                    ),
                )
                workflow._append_event(
                    work.connection,
                    WorkflowEvent(
                        run_id=str(run_id),
                        event_type="workflow.run.failed",
                        payload={"error": error},
                    ),
                )
        resumable = [
            str(row[0])
            for row in workflow.repository(work.connection).runs_to_advance().fetchall()
        ]
        work.commit()
    for run_id in resumable:
        try:
            workflow._advance(run_id)
        except Exception as exc:
            # The run remains durable; the next supervisor pass retries only
            # safe boundary states. In-flight side effects are never replayed.
            log_recovered_exception("workflow recovery", exc)
            continue


def _enqueue_due_schedule_fires(workflow: PostgresWorkflowRuntime) -> None:
    now = datetime.now(timezone.utc)
    with unit_of_work(workflow.database) as work:
        rows = workflow.repository(work.connection).due_schedules().fetchall()
        for schedule_id, scheduled_for, interval_seconds in rows:
            workflow.repository(work.connection).insert_schedule_fire(str(schedule_id), scheduled_for)
            if interval_seconds is None:
                next_run_at = None
                enabled = False
            else:
                interval = int(interval_seconds)
                elapsed = max(
                    0.0,
                    (now - scheduled_for).total_seconds(),
                )
                intervals_to_advance = int(elapsed // interval) + 1
                next_run_at = scheduled_for + timedelta(
                    seconds=interval * intervals_to_advance
                )
                enabled = True
            workflow.repository(work.connection).advance_schedule(scheduled_for, next_run_at, enabled, str(schedule_id))
        work.commit()


def _dispatch_pending_schedule_fires(workflow: PostgresWorkflowRuntime) -> None:
    with unit_of_work(workflow.database) as work:
        rows = workflow.repository(work.connection).pending_schedule_fires().fetchall()
        work.rollback()
    for schedule_id, scheduled_for, workflow_id, version, input_payload in rows:
        definition = workflow._definition(
            str(workflow_id),
            version=int(version),
        )
        if definition is None:
            with unit_of_work(workflow.database) as work:
                workflow.repository(work.connection).fail_schedule_fire(str(schedule_id), scheduled_for)
                work.commit()
            continue
        payload = dict(input_payload or {})
        payload["idempotency_key"] = (
            f"workflow-schedule:{schedule_id}:"
            f"{scheduled_for.astimezone(timezone.utc).isoformat()}"
        )
        try:
            run_id = workflow._start_definition(definition, payload)
        except Exception as exc:
            # Keep the fire pending. A later supervisor pass retries the
            # durable dispatch; deterministic run idempotency prevents
            # duplicate workflow execution if the first attempt committed.
            log_recovered_exception("scheduled workflow dispatch", exc)
            continue
        with unit_of_work(workflow.database) as work:
            workflow.repository(work.connection).start_schedule_fire(run_id, str(schedule_id), scheduled_for)
            work.commit()
