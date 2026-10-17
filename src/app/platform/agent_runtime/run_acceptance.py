"""Run acceptance: runtime events, revision evidence and completion of runs and parents (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .event_queries import all_events
import hashlib
from .acceptance import evaluate_acceptance
from .evidence import (
    evaluate_evidence_set,
)
from .contracts import (
    AgentArtifact,
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    TaskRevision,
)
from app.observability.agent_logging import log_agent_activity
from .repository import PostgresAgentRunRepository
from .workspace_promotion import WorkspacePromotionError
from typing import Any, TYPE_CHECKING
from .service_core import (
    _acceptance_failures_retryable,
    _acceptance_retry_count,
    _acceptance_retry_limit,
    _acceptance_retry_prompt,
    _is_clarification_request,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.service_core import AgentRunService


def _maybe_finalize_parent_in_repository(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    child_run_id: str,
) -> None:
    child = repository.get_run(child_run_id)
    if child is None or not child.spec.parent_run_id:
        return
    if child.status not in {"completed", "failed", "cancelled"}:
        return
    parent = repository.get_run(child.spec.parent_run_id)
    if parent is None or parent.status != "waiting_for_children":
        return
    terminal, failed = service._children_terminal_state(repository, parent.run_id)
    if not terminal:
        return
    if failed:
        repository.update_state(
            parent.run_id,
            expected_revision=parent.revision,
            status="failed",
            desired_state="cancelled",
            last_error="acceptance_failed:child_run_failed",
        )
    else:
        queued = service._enqueue_promote_job(
            parent.run_id,
            trigger_id=f"children-terminal:{child_run_id}",
        )
        if not queued:
            service._finalize_acceptance(repository, parent)


def _children_terminal_state(repository: PostgresAgentRunRepository, run_id: str) -> tuple[bool, bool]:
    children = repository.list_children(run_id)
    if not children:
        return True, False
    terminal = all(child.status in {"completed", "failed", "cancelled"} for child in children)
    failed = any(child.status in {"failed", "cancelled"} for child in children)
    return terminal, failed


def _finalize_acceptance(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
) -> None:
    task_revision = repository.latest_task_revision(current.run_id)
    revision_id = task_revision.revision_id if task_revision is not None else None
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="acceptance.started",
            payload={"source": "omnix", "task_revision_id": revision_id},
        )
    )
    change_set = service._capture_diff(
        repository,
        current.spec,
        task_revision_id=revision_id,
    )
    run_events = all_events(repository, current.run_id)
    all_artifacts = repository.list_artifacts(current.run_id)
    all_receipts = repository.list_evidence_receipts(current.run_id)
    events = service._events_for_revision(run_events, task_revision)
    artifacts = service._artifacts_for_revision(all_artifacts, task_revision)
    receipts = service._receipts_for_revision(all_receipts, task_revision)
    effective_policy = (
        task_revision.evidence_decision.policy
        if task_revision is not None
        else current.spec.evidence_policy
    )
    evidence_set = evaluate_evidence_set(current.run_id, effective_policy, receipts)
    result = evaluate_acceptance(
        current.spec,
        events=events,
        artifacts=artifacts,
        task_revision=task_revision,
        evidence_set=evidence_set,
    )
    children_terminal, child_failed = service._children_terminal_state(repository, current.run_id)
    failures = list(result.failures)
    if not children_terminal:
        failures.append("children_not_terminal")
    if child_failed:
        failures.append("child_run_failed")
    passed = result.passed and not failures
    promotion: dict[str, Any] | None = None
    if passed:
        try:
            promotion = service._promote_accepted_workspace(
                repository,
                current,
                task_revision_id=revision_id,
                workspace_state_id=(
                    change_set.candidate_workspace_state_id
                    if change_set is not None
                    else None
                ),
            )
        except WorkspacePromotionError as exc:
            failures.append(f"workspace_promotion_failed:{exc}")
            passed = False

    retry_count = _acceptance_retry_count(run_events, revision_id)
    runtime_available = (
        service._runtime_owns_run(current.run_id)
        or repository.get_active_lease(current.run_id) is not None
    )
    retrying = (
        not passed
        and runtime_available
        and retry_count < _acceptance_retry_limit()
        and _acceptance_failures_retryable(failures)
    )
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="acceptance.completed",
            payload={
                **result.model_dump(mode="json"),
                "passed": passed,
                "failures": failures,
                "retrying": retrying,
                "retry_attempt": retry_count + 1 if retrying else None,
                "task_revision_id": task_revision.revision_id if task_revision else None,
                "evidence_set": evidence_set.model_dump(mode="json"),
                "workspace_promotion": promotion,
            },
        )
    )
    latest = repository.get_run(current.run_id) or current
    if retrying:
        attempt = retry_count + 1
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="acceptance.retry_requested",
                payload={
                    "source": "omnix",
                    "attempt": attempt,
                    "failures": failures,
                    "task_revision_id": revision_id,
                },
            )
        )
        retry_snapshot = repository.update_state(
            current.run_id,
            expected_revision=latest.revision,
            status="running",
            desired_state="running",
            worker_id=service.worker_id,
            last_error=None,
        )
        retry_prompt = _acceptance_retry_prompt(failures, attempt=attempt)
        repository.enqueue_command_with_status(
            AgentRunCommand(
                run_id=current.run_id,
                command_type="resume",
                payload={"message": retry_prompt},
                idempotency_key=(
                    f"acceptance-retry:{current.run_id}:{attempt}:"
                    f"{hashlib.sha256(retry_prompt.encode('utf-8')).hexdigest()[:16]}"
                ),
            )
        )
        return

    if passed and promotion is not None:
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="run.completed",
                payload={"source": "omnix", "workspace_promotion": promotion},
            )
        )
    repository.update_state(
        current.run_id,
        expected_revision=latest.revision,
        status="completed" if passed else "failed",
        desired_state=None if passed else "cancelled",
        worker_id=service.worker_id,
        last_error=None if passed else "acceptance_failed:" + ",".join(failures),
    )


def _persist_runtime_event(service: AgentRunService, event: AgentEvent) -> None:
    log_agent_activity(
        "service.runtime_event.persisting",
        category="service",
        run_id=event.run_id,
        fields={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "sequence": event.sequence,
            "payload": event.payload,
        },
    )
    promote_trigger: str | None = None
    with service._run_lock(event.run_id):
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(event.run_id)
            if current is None:
                log_agent_activity(
                    "service.runtime_event.ignored_unknown_run",
                    category="service",
                    level="warning",
                    run_id=event.run_id,
                    fields={"event_type": event.event_type},
                )
                work.rollback()
                return
            if _is_clarification_request(event):
                event = event.model_copy(update={
                    "payload": {
                        **event.payload,
                        "requires_user_input": True,
                    }
                })
            repository.append_event(event)
            if current.status in {"completed", "failed", "cancelled"}:
                log_agent_activity(
                    "service.runtime_event.terminal_run_no_transition",
                    category="service",
                    run_id=event.run_id,
                    fields={"event_type": event.event_type, "status": current.status},
                )
                work.commit()
                service._close_terminal_runtime(event.run_id)
                return
            terminal_runtime = False
            if _is_clarification_request(event):
                repository.update_state(
                    event.run_id,
                    expected_revision=current.revision,
                    status="waiting_for_input",
                    desired_state="paused",
                    worker_id=service.worker_id,
                    last_error=None,
                )
            elif event.event_type == "run.started" and current.status != "running":
                current = repository.update_state(
                    event.run_id,
                    expected_revision=current.revision,
                    status="running",
                    worker_id=service.worker_id,
                )
            elif event.event_type in {"run.settled", "run.completed"}:
                if current.status not in {
                    "waiting_for_approval",
                    "waiting_for_input",
                    "pause_requested",
                    "paused",
                    "cancel_requested",
                    "cancelled",
                }:
                    children_terminal, _ = service._children_terminal_state(repository, event.run_id)
                    if children_terminal:
                        repository.update_state(
                            event.run_id,
                            expected_revision=current.revision,
                            status="waiting_for_children",
                            worker_id=service.worker_id,
                        )
                        promote_trigger = event.event_id
                    else:
                        repository.update_state(
                            event.run_id,
                            expected_revision=current.revision,
                            status="waiting_for_children",
                            worker_id=service.worker_id,
                        )
            elif event.event_type == "run.failed":
                repository.update_state(
                    event.run_id,
                    expected_revision=current.revision,
                    status="failed",
                    desired_state="cancelled",
                    worker_id=service.worker_id,
                    last_error=str(event.payload.get("error") or "Pi runtime failed")[:2000],
                )
            service._maybe_finalize_parent_in_repository(repository, event.run_id)
            latest = repository.get_run(event.run_id)
            terminal_runtime = latest is not None and latest.status in {
                "completed",
                "failed",
                "cancelled",
            }
            work.commit()
            if terminal_runtime:
                service._close_terminal_runtime(event.run_id)
    if promote_trigger is not None:
        try:
            queued = service._enqueue_promote_job(
                event.run_id,
                trigger_id=promote_trigger,
            )
            if not queued:
                terminal_after_fallback = False
                with service.unit_of_work(service.database) as work:
                    repository = service.repository_factory(work.connection, service.context)
                    current = repository.get_run(event.run_id)
                    if current is not None and current.status == "waiting_for_children":
                        service._finalize_acceptance(repository, current)
                        latest = repository.get_run(event.run_id)
                        terminal_after_fallback = latest is not None and latest.status in {
                            "completed",
                            "failed",
                            "cancelled",
                        }
                    work.commit()
                if terminal_after_fallback:
                    service._close_terminal_runtime(event.run_id)
        except Exception as exc:
            log_agent_activity(
                "service.acceptance.enqueue_failed",
                category="quality",
                level="error",
                run_id=event.run_id,
                fields={"trigger_id": promote_trigger},
                error=exc,
                include_traceback=True,
            )


def _events_for_revision(
    events: list[AgentEvent],
    task_revision: TaskRevision | None,
) -> list[AgentEvent]:
    if task_revision is None:
        return [
            event
            for event in events
            if event.event_type not in {"tool.started", "tool.completed", "tool.output"}
            or event.payload.get("task_revision_id") is None
        ]
    if task_revision.sequence <= 1:
        return [
            event
            for event in events
            if event.event_type not in {"tool.started", "tool.completed", "tool.output"}
            or event.payload.get("task_revision_id") in {None, task_revision.revision_id}
        ]
    return [
        event
        for event in events
        if event.event_type not in {"tool.started", "tool.completed", "tool.output"}
        or event.payload.get("task_revision_id") == task_revision.revision_id
    ]


def _artifacts_for_revision(
    artifacts: list[AgentArtifact],
    task_revision: TaskRevision | None,
) -> list[AgentArtifact]:
    if task_revision is None or task_revision.sequence <= 1:
        return [
            artifact
            for artifact in artifacts
            if artifact.metadata.get("task_revision_id") in {None, task_revision.revision_id if task_revision else None}
        ]
    return [
        artifact
        for artifact in artifacts
        if artifact.metadata.get("task_revision_id") == task_revision.revision_id
    ]


def _receipts_for_revision(receipts, task_revision: TaskRevision | None):
    if task_revision is None:
        return [receipt for receipt in receipts if receipt.task_revision_id is None]
    return [
        receipt
        for receipt in receipts
        if receipt.task_revision_id == task_revision.revision_id
    ]
