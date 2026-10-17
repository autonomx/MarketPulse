"""Workflow execution: advancing a run, executing steps and rendering their inputs (WP-8.2).

Functions over the workflow runtime; ``PostgresWorkflowRuntime`` keeps one
delegator per function, so callers and tests are unchanged.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any
from .capability_requests import AssistantToolRequest
from app.capabilities.executor import (
    LEGACY_APPROVER,
    CapabilityGrant,
)
from app.persistence.unit_of_work import unit_of_work
from app.capabilities import default_capability_registry
from .workflows import (
    WORKFLOW_END,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowStepDefinition,
)
from typing import TYPE_CHECKING
from .workflow_runtime import (
    WorkflowRuntimeError,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.workflow_runtime import PostgresWorkflowRuntime


def _resolve_approval(workflow: PostgresWorkflowRuntime, run_id: str, step_id: str, *, approved_by: str | None) -> None:
    approved = approved_by is not None
    with unit_of_work(workflow.database) as work:
        if approved:
            # The approver is kept with the step until it executes (WP-4.5).
            row = workflow.repository(work.connection).approve_step(approved_by, run_id, step_id).fetchone()
            if row is None:
                raise WorkflowRuntimeError("workflow step is not waiting for approval")
            workflow.repository(work.connection).resume_run_after_approval(run_id)
        else:
            row = workflow.repository(work.connection).reject_step(run_id, step_id).fetchone()
            if row is None:
                raise WorkflowRuntimeError("workflow step is not waiting for approval")
            workflow.repository(work.connection).cancel_run_after_rejection(run_id)
        workflow._append_event(
            work.connection,
            WorkflowEvent(
                run_id=run_id,
                event_type=(
                    "workflow.approval.approved"
                    if approved
                    else "workflow.approval.rejected"
                ),
                payload={"step_id": step_id, **({"approved_by": approved_by} if approved else {})},
            ),
        )
        if not approved:
            workflow._append_event(
                work.connection,
                WorkflowEvent(
                    run_id=run_id,
                    event_type="workflow.run.cancelled",
                    payload={"error": "approval_rejected"},
                ),
            )
        work.commit()


def _advance(workflow: PostgresWorkflowRuntime, run_id: str) -> None:
    workflow._ensure_supervisor()
    while True:
        state = workflow.get_status(run_id)
        if state is None or state["status"] in {
            "paused", "cancelled", "completed", "failed", "waiting_for_approval"
        }:
            return
        definition = workflow._definition(
            str(state["workflow_id"]),
            version=int(state["workflow_version"]),
        )
        if definition is None:
            workflow._set_status(run_id, "failed", error="workflow_definition_missing")
            return
        current_step_id = str(state.get("current_step_id") or "")
        if not current_step_id:
            workflow._finish(run_id)
            return
        step = next(
            (item for item in definition.steps if item.id == current_step_id),
            None,
        )
        if step is None:
            workflow._set_status(run_id, "failed", error="workflow_current_step_missing")
            return
        step_row = workflow._step_state(run_id, step.id)
        if step_row is None:
            workflow._set_status(run_id, "failed", error="workflow_step_state_missing")
            return

        step_status = str(step_row["status"])
        if step_status == "completed":
            result = dict(step_row.get("result") or {})
            workflow._transition(
                run_id,
                workflow._next_target(definition, step, result),
            )
            continue
        if step_status == "running":
            # The owner heartbeat determines whether this is active or an
            # abandoned unknown-outcome step. Never double-claim it.
            return
        if step_status in {"failed", "skipped"}:
            workflow._set_status(
                run_id,
                "failed",
                error=f"workflow_step_not_runnable:{step.id}:{step_status}",
            )
            return

        approval_required = workflow._step_requires_approval(step)
        if approval_required and step_status != "approved":
            if step_status == "pending":
                workflow._set_waiting_for_approval(run_id, step.id)
            elif step_status == "waiting_for_approval":
                workflow._set_status(run_id, "waiting_for_approval")
            else:
                workflow._set_status(
                    run_id,
                    "failed",
                    error=f"workflow_approval_state_invalid:{step.id}:{step_status}",
                )
            return

        approved_by = None
        if approval_required:
            approved_by = str((step_row.get("result") or {}).get("approved_by") or LEGACY_APPROVER)
        claimed = workflow._claim_step(run_id, step.id)
        if claimed is None:
            return
        attempts = claimed
        try:
            context = workflow._context(run_id, dict(state["input_payload"]))
            result = workflow._execute_with_timeout(
                run_id,
                step,
                context,
                approved_by=approved_by,
            )
        except Exception as exc:
            message = str(exc)[:1000]
            unknown_outcome = message.startswith("step_timeout_outcome_unknown")
            retry_safe = workflow._step_retry_safe(step)
            latest = workflow.get_status(run_id)
            if latest is not None and latest["status"] == "cancelled":
                return
            if (
                not unknown_outcome
                and retry_safe
                and attempts <= step.retry_limit
                and workflow._set_step_retry(run_id, step.id, message)
            ):
                continue
            workflow._set_step_failed(run_id, step.id, message)
            workflow._set_status(run_id, "failed", error=message)
            return

        if not workflow._set_step_completed(run_id, step.id, result):
            return
        workflow._transition(
            run_id,
            workflow._next_target(definition, step, result),
        )


def _step_requires_approval(step: WorkflowStepDefinition) -> bool:
    if step.kind == "approval" or step.requires_approval:
        return True
    if step.kind != "capability":
        return False
    capability = default_capability_registry().get(str(step.capability_id or ""))
    return bool(
        capability is not None
        and (
            capability.approval_policy != "allow_automatic"
            or capability.requires_confirmation
        )
    )


def _step_retry_safe(step: WorkflowStepDefinition) -> bool:
    if step.kind != "capability":
        return True
    capability = default_capability_registry().get(str(step.capability_id or ""))
    return bool(capability is not None and capability.effect == "read")


def _execute_with_timeout(
    workflow: PostgresWorkflowRuntime,
    run_id: str,
    step: WorkflowStepDefinition,
    context: dict[str, Any],
    *,
    approved_by: str | None,
) -> dict[str, Any]:
    if step.timeout_seconds is None:
        return workflow._execute_step(run_id, step, context, approved_by=approved_by)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"workflow-{step.id[:16]}")
    future = executor.submit(workflow._execute_step, run_id, step, context, approved_by=approved_by)
    try:
        return future.result(timeout=step.timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise WorkflowRuntimeError(
            f"step_timeout_outcome_unknown:{step.id}:{step.timeout_seconds}s"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _execute_step(
    workflow: PostgresWorkflowRuntime,
    run_id: str,
    step: WorkflowStepDefinition,
    context: dict[str, Any],
    *,
    approved_by: str | None,
) -> dict[str, Any]:
    if step.kind == "condition":
        return {
            "condition": step.condition,
            "matched": workflow._condition(step.condition, context),
        }
    if step.kind == "approval":
        return {"approved": approved_by is not None, "approved_by": approved_by}
    namespace = str(step.capability_id).split(".", 1)[0]
    request = AssistantToolRequest(
        tool_id=namespace,
        action_id=str(step.capability_id),
        session_id=f"workflow:{run_id}",
        proposal_id=f"workflow:{run_id}:{step.id}",
        input=workflow._render_input(step.input_template, context),
    )
    payload = workflow.capability_executor(
        CapabilityGrant("workflow", f"workflow:{run_id}", approved_by=approved_by),
        request,
        user_request=f"workflow:{run_id}",
    )
    execution = payload.execution_result
    if execution.error:
        raise WorkflowRuntimeError(execution.error)
    return execution.model_dump(mode="json")


def _context(workflow: PostgresWorkflowRuntime, run_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
    with unit_of_work(workflow.database) as work:
        rows = workflow.repository(work.connection).step_results(run_id).fetchall()
        work.rollback()
    return {
        "input": input_payload,
        "steps": {str(row[0]): dict(row[1] or {}) for row in rows},
    }


def _render_input(cls, template: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    return {key: cls._render_value(value, context) for key, value in template.items()}


def _render_value(cls, value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return cls._lookup(context, value[1:])
    if isinstance(value, list):
        return [cls._render_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: cls._render_value(item, context) for key, item in value.items()}
    return value


def _lookup(context: dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not part:
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _condition(cls, expression: str | None, context: dict[str, Any]) -> bool:
    if not expression:
        return True
    if "!=" in expression:
        left, right = [part.strip() for part in expression.split("!=", 1)]
        return str(cls._lookup(context, left)).casefold() != right.strip("'\"").casefold()
    if "==" in expression:
        left, right = [part.strip() for part in expression.split("==", 1)]
        return str(cls._lookup(context, left)).casefold() == right.strip("'\"").casefold()
    return bool(cls._lookup(context, expression.strip()))


def _next_target(
    definition: WorkflowDefinition,
    step: WorkflowStepDefinition,
    result: dict[str, Any],
) -> str | None:
    if step.kind == "condition":
        explicit = step.on_true_step_id if bool(result.get("matched")) else step.on_false_step_id
        if explicit:
            return None if explicit == WORKFLOW_END else explicit
    if step.next_step_id:
        return None if step.next_step_id == WORKFLOW_END else step.next_step_id
    index = next(index for index, item in enumerate(definition.steps) if item.id == step.id)
    return definition.steps[index + 1].id if index + 1 < len(definition.steps) else None
