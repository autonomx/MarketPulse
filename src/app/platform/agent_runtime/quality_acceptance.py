"""Quality-aware acceptance: change sets, workspace tool results, steering and run completion (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .event_queries import all_events, events_of_types, json_object, latest_event
from .exception_logging import log_recovered_exception
from .acceptance import evaluate_acceptance
from .coding_quality import (
    CODING_INDEPENDENT_REVIEW_PHASE_ENABLED,
    CODING_VALIDATION_PHASE_ENABLED,
    capture_workspace_state,
    compile_task_engineering_contract,
    quality_failure_reasons,
    validation_kind_for_capability,
    validation_kind_for_command,
    validation_result_from_tool_event,
)
from .coding_quality_repository import PostgresCodingQualityRepository
from .contracts import (
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    RunChangeSet,
    TaskRevision,
)
from app.observability.agent_logging import log_agent_activity
from .evidence import evaluate_evidence_set
from .planning_acceptance import evaluate_planning_acceptance
from .repository import PostgresAgentRunRepository
from .review_orchestration import (
    finalize_reviewer_child_in_repository,
    review_snapshot_id_from_child,
)
from .run_change_set import run_change_set_from_artifact
from .workspace_promotion import WorkspacePromotionError
from .service_core import (
    AgentRunService as _CoreAgentRunService,
    _acceptance_failures_retryable,
    _acceptance_retry_count as _acceptance_retry_count,
)
from .task_revision_quality import (
    hydrate_task_revision,
    persist_task_revision_contract,
)
from typing import Any, TYPE_CHECKING
from .service import (
    _BLOCKED_SETTLE,
    _TERMINAL,
    _is_terminal_self_review_message,
    _terminal_message_settles_quality_stage,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.service import AgentRunService


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def run_change_set(service: AgentRunService, run_id: str) -> tuple[RunChangeSet, str]:
    """Return the one authoritative run-owned subject for parent or reviewer."""
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(run_id)
        if current is None:
            work.rollback()
            raise KeyError(run_id)
        change_set: RunChangeSet | None = None
        if current.spec.profile == "coding-reviewer" and current.spec.parent_run_id:
            snapshot_id = review_snapshot_id_from_child(current)
            if not snapshot_id:
                work.rollback()
                raise RuntimeError("review_run_change_set_snapshot_unavailable")
            quality = service.quality_repository_factory(work.connection, service.context)
            review_snapshot = quality.get_review_snapshot(current.spec.parent_run_id, snapshot_id)
            if review_snapshot is None or not review_snapshot.run_change_set_id:
                work.rollback()
                raise RuntimeError("review_run_change_set_snapshot_unavailable")
            for artifact in reversed(repository.list_artifacts(current.spec.parent_run_id)):
                candidate = run_change_set_from_artifact(artifact)
                if candidate is not None and candidate.change_set_id == review_snapshot.run_change_set_id:
                    change_set = candidate
                    break
        else:
            if not service._quality_enabled(current.spec):
                work.rollback()
                raise RuntimeError("agent_run_change_set_not_applicable")
            revision = service._current_revision(repository, run_id)
            if revision is None:
                work.rollback()
                raise RuntimeError("agent_run_change_set_revision_unavailable")
            service._quarantine_isolated_workspace_contamination(repository, current.spec)
            state = capture_workspace_state(current.spec, task_revision_id=revision.revision_id)
            if state is None:
                work.rollback()
                raise RuntimeError("agent_run_change_set_workspace_unavailable")
            service.quality_repository_factory(work.connection, service.context).add_workspace_state(state)
            change_set = service._capture_diff(
                repository,
                current.spec,
                task_revision_id=revision.revision_id,
                workspace_state_id=state.state_id,
            )
        if change_set is None:
            work.rollback()
            raise RuntimeError("agent_run_change_set_unavailable")
        work.commit()
    patch = service.blob_store.read_bytes(
        change_set.patch_storage_ref,
        expected_checksum=change_set.patch_checksum,
    ).decode("utf-8", errors="replace")
    return change_set, patch


def command_with_context(
    service: AgentRunService,
    command: AgentRunCommand,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
    turn_plan=None,
) -> AgentRunSnapshot:
    result = _CoreAgentRunService.command_with_context(service,
        command,
        reference_context=reference_context,
        reference_images=reference_images,
        turn_plan=turn_plan,
    )
    if command.command_type != "steer" or result.run_id != command.run_id:
        service._dispatch_pending_quality_commands(command.run_id, include_parent=True)
        return result

    stale_reviewers: list[str] = []
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(command.run_id)
        revision = repository.latest_task_revision(command.run_id)
        if current is not None and revision is not None:
            requirements, constraints, validation_plan = compile_task_engineering_contract(
                revision.effective_objective,
                revision.effective_success_criteria,
                profile=current.spec.profile,
                mutating="diff" in revision.expected_artifacts,
            )
            revision = revision.model_copy(
                update={
                    "requirements": requirements,
                    "constraints": constraints,
                    "validation_plan": validation_plan,
                }
            )
            persist_task_revision_contract(work.connection, service.context, revision)
            if service._quality_enabled(current.spec) and current.status not in _TERMINAL:
                quality = service.quality_repository_factory(work.connection, service.context)
                quality.set_stage(
                    current.run_id,
                    stage="implementing",
                    attempt=1,
                    task_revision_id=revision.revision_id,
                )
                repository.append_event(
                    AgentEvent(
                        run_id=current.run_id,
                        event_type="quality.stage",
                        payload={
                            "stage": "implementing",
                            "attempt": 1,
                            "task_revision_id": revision.revision_id,
                            "reason": "task_revision_changed",
                        },
                    )
                )
                stale_reviewers = [
                    child.run_id
                    for child in repository.list_children(current.run_id)
                    if child.spec.profile == "coding-reviewer" and child.status not in _TERMINAL
                ]
        work.commit()

    for child_id in stale_reviewers:
        try:
            service.command(
                AgentRunCommand(
                    run_id=child_id,
                    command_type="cancel",
                    payload={"reason": "parent_task_revision_changed"},
                    idempotency_key=f"quality-stale-reviewer:{command.run_id}:{child_id}",
                )
            )
        except Exception as exc:
            # Stale review evidence is revision-bound and cannot pass even
            # if best-effort cancellation loses a race with completion.
            log_recovered_exception("stale reviewer cancellation", exc)
            pass
    current_result = service.get(result.run_id) or result
    service._dispatch_pending_quality_commands(command.run_id, include_parent=True)
    return current_result


def _current_revision(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    run_id: str,
) -> TaskRevision | None:
    revision = repository.latest_task_revision(run_id)
    if revision is None:
        return None
    revision = hydrate_task_revision(repository.connection, service.context, revision)
    if revision.requirements or revision.validation_plan:
        return revision
    current = repository.get_run(run_id)
    if current is None:
        return revision
    requirements, constraints, validation_plan = compile_task_engineering_contract(
        revision.effective_objective,
        revision.effective_success_criteria,
        profile=current.spec.profile,
        mutating="diff" in revision.expected_artifacts,
    )
    revision = revision.model_copy(
        update={
            "requirements": requirements,
            "constraints": constraints,
            "validation_plan": validation_plan,
        }
    )
    persist_task_revision_contract(repository.connection, service.context, revision)
    return revision


def _record_workspace_tool_result(service: AgentRunService, event: AgentEvent) -> None:
    log_agent_activity(
        "quality.tool_result.received",
        category="quality",
        run_id=event.run_id,
        fields={
            "tool_call_id": event.payload.get("tool_call_id"),
            "tool": event.payload.get("tool"),
            "is_error": event.payload.get("is_error"),
            "task_revision_id": event.payload.get("task_revision_id"),
        },
    )
    call_id = str(event.payload.get("tool_call_id") or "")
    if not call_id:
        log_agent_activity(
            "quality.tool_result.unbound",
            category="quality",
            level="warning",
            run_id=event.run_id,
            fields={"reason": "missing_tool_call_id"},
        )
        return
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(event.run_id)
        if current is None or current.status in _TERMINAL or not service._quality_enabled(current.spec):
            log_agent_activity(
                "quality.tool_result.ignored",
                category="quality",
                level="debug",
                run_id=event.run_id,
                fields={
                    "found": current is not None,
                    "status": current.status if current is not None else None,
                    "quality_enabled": service._quality_enabled(current.spec) if current is not None else None,
                },
            )
            work.rollback()
            return
        started = latest_event(
            repository, event.run_id, "tool.started", payload_contains={"tool_call_id": call_id}
        )
        tool = str(event.payload.get("tool") or (started.payload.get("tool") if started else "") or "")
        args = json_object(started.payload.get("args")) if started else {}
        command = str(args.get("command") or "")
        capability_id = str(args.get("capability_id") or event.payload.get("capability_id") or "").strip()
        quality = service.quality_repository_factory(work.connection, service.context)
        mutating_or_validation = (
            tool in {"edit", "write", "bash", "powershell"}
            or validation_kind_for_command(command) is not None
            or validation_kind_for_capability(capability_id) is not None
        )
        if not mutating_or_validation:
            work.commit()
            return
        revision = service._current_revision(repository, event.run_id)
        active_revision_id = revision.revision_id if revision is not None else None
        event_revision_id = event.payload.get("task_revision_id")
        bound_revision_id = str(event_revision_id) if event_revision_id else active_revision_id
        service._quarantine_isolated_workspace_contamination(repository, current.spec)
        state = capture_workspace_state(current.spec, task_revision_id=bound_revision_id)
        if state is None:
            log_agent_activity(
                "quality.workspace_state.unavailable",
                category="quality",
                level="warning",
                run_id=event.run_id,
                fields={"task_revision_id": bound_revision_id, "tool_call_id": call_id},
            )
            work.rollback()
            return
        quality.add_workspace_state(state)
        augmented = event.model_copy(
            update={
                "payload": {
                    **event.payload,
                    "args": args,
                    "command": command,
                }
            }
        )
        validation = validation_result_from_tool_event(
            augmented,
            run_id=event.run_id,
            task_revision_id=bound_revision_id,
            workspace_state_id=state.state_id,
            revision=revision if bound_revision_id == active_revision_id else None,
        )
        if validation is not None:
            quality.add_validation_result(validation)
            log_agent_activity(
                "quality.validation.recorded",
                category="quality",
                run_id=event.run_id,
                fields={
                    "result_id": validation.result_id,
                    "validation_id": validation.validation_id,
                    "kind": validation.kind,
                    "success": validation.success,
                    "command": validation.command,
                    "task_revision_id": validation.task_revision_id,
                    "workspace_state_id": validation.workspace_state_id,
                },
            )
            repository.append_event(
                AgentEvent(
                    run_id=event.run_id,
                    event_type="quality.validation_recorded",
                    payload={
                        "result_id": validation.result_id,
                        "validation_id": validation.validation_id,
                        "kind": validation.kind,
                        "success": validation.success,
                        "task_revision_id": validation.task_revision_id,
                        "workspace_state_id": validation.workspace_state_id,
                        "command": validation.command,
                        "metadata": dict(validation.metadata),
                    },
                )
            )
        else:
            log_agent_activity(
                "quality.validation.not_recognized",
                category="quality",
                level="debug",
                run_id=event.run_id,
                fields={
                    "tool": tool,
                    "command": command,
                    "capability_id": capability_id,
                    "task_revision_id": bound_revision_id,
                },
            )
        work.commit()


def _reconcile_change_set_validation(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    quality: PostgresCodingQualityRepository,
    *,
    workspace_state_id: str,
) -> None:
    """Recover an exact-state change-set result missed during event ingestion.

    Runtime events and quality advancement are persisted independently. A
    change-set tool completion can therefore be present in the durable
    event stream while its derived validation row is absent when a settle
    checkpoint is evaluated. Reconcile only the authoritative tool and
    exact candidate state; stale or malformed results remain unavailable to
    the gate.
    """

    existing = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    recorded_call_ids = {
        str(item.metadata.get("tool_call_id") or "")
        for item in existing
        if item.validation_id == "final-diff-review"
    }
    events = events_of_types(repository, current.run_id, {"tool.started", "tool.completed"})
    started_by_call_id = {
        str(item.payload.get("tool_call_id") or ""): item
        for item in events
        if item.event_type == "tool.started"
        and str(item.payload.get("tool_call_id") or "")
    }
    for event in reversed(events):
        if event.event_type != "tool.completed" or str(event.payload.get("tool") or "") != "omnix_change_set":
            continue
        call_id = str(event.payload.get("tool_call_id") or "")
        if not call_id or call_id in recorded_call_ids:
            continue
        started = started_by_call_id.get(call_id)
        args = json_object(started.payload.get("args")) if started else {}
        augmented = event.model_copy(
            update={
                "payload": {
                    **event.payload,
                    "args": args,
                    "command": "omnix_change_set",
                }
            }
        )
        validation = validation_result_from_tool_event(
            augmented,
            run_id=current.run_id,
            task_revision_id=revision.revision_id,
            workspace_state_id=workspace_state_id,
            revision=revision,
        )
        if validation is None or validation.workspace_state_id != workspace_state_id:
            continue
        quality.add_validation_result(validation)
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="quality.validation_recorded",
                payload={
                    "result_id": validation.result_id,
                    "validation_id": validation.validation_id,
                    "kind": validation.kind,
                    "success": validation.success,
                    "task_revision_id": validation.task_revision_id,
                    "workspace_state_id": validation.workspace_state_id,
                    "command": validation.command,
                    "metadata": dict(validation.metadata),
                    "reconciled": True,
                },
            )
        )
        log_agent_activity(
            "quality.validation.reconciled",
            category="quality",
            run_id=current.run_id,
            fields={
                "validation_id": validation.validation_id,
                "success": validation.success,
                "workspace_state_id": workspace_state_id,
                "tool_call_id": call_id,
            },
        )
        return


def _persist_runtime_event(service: AgentRunService, event: AgentEvent) -> None:
    log_agent_activity(
        "service.runtime_event.received",
        category="service",
        run_id=event.run_id,
        fields={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "sequence": event.sequence,
            "payload": event.payload,
        },
    )
    quality_message_settle = False
    if _is_terminal_self_review_message(event):
        with service.unit_of_work(service.database) as probe:
            current = service.repository_factory(probe.connection, service.context).get_run(event.run_id)
            if current is not None and service._quality_enabled(current.spec):
                stage = service.quality_repository_factory(probe.connection, service.context).get_stage(event.run_id)
                quality_message_settle = _terminal_message_settles_quality_stage(
                    event,
                    str((stage or {}).get("stage") or ""),
                )
            probe.rollback()

    if event.event_type not in {"run.settled", "run.completed"} and not quality_message_settle:
        _CoreAgentRunService._persist_runtime_event(service, event)
        if event.event_type == "tool.completed":
            service._record_workspace_tool_result(event)
        service._dispatch_pending_quality_commands(event.run_id, include_parent=True)
        return

    with service.unit_of_work(service.database) as probe:
        current = service.repository_factory(probe.connection, service.context).get_run(event.run_id)
        probe.rollback()
    if current is None or not service._quality_enabled(current.spec):
        _CoreAgentRunService._persist_runtime_event(service, event)
        service._dispatch_pending_quality_commands(event.run_id, include_parent=True)
        return

    promote = False
    with service._run_lock(event.run_id):
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            current = repository.get_run(event.run_id)
            if current is None:
                work.rollback()
                return
            repository.append_event(event)
            log_agent_activity(
                "service.quality_event.persisted",
                category="quality",
                run_id=event.run_id,
                fields={"event_type": event.event_type, "status": current.status},
            )
            if current.status in _TERMINAL or current.status in _BLOCKED_SETTLE:
                work.commit()
                if current.status in _TERMINAL:
                    service._close_terminal_runtime(event.run_id)
                return
            repository.update_state(
                event.run_id,
                expected_revision=current.revision,
                status="waiting_for_children",
                worker_id=service.worker_id,
            )
            promote = True
            work.commit()
    if promote:
        try:
            queued = service._enqueue_promote_job(
                event.run_id,
                trigger_id=event.event_id,
            )
            if not queued:
                with service.unit_of_work(service.database) as work:
                    repository = service.repository_factory(work.connection, service.context)
                    current = repository.get_run(event.run_id)
                    post_action = (
                        service._advance_quality_on_settle(repository, current)
                        if current is not None and current.status not in _TERMINAL
                        else None
                    )
                    work.commit()
                service._execute_quality_action(post_action)
                service._dispatch_pending_quality_commands(event.run_id, include_parent=True)
        except Exception as exc:
            log_agent_activity(
                "service.quality_settle.enqueue_failed",
                category="quality",
                level="error",
                run_id=event.run_id,
                fields={"trigger_id": event.event_id},
                error=exc,
                include_traceback=True,
            )


def _maybe_finalize_parent_in_repository(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    child_run_id: str,
) -> None:
    if finalize_reviewer_child_in_repository(service, repository, child_run_id):
        return
    _CoreAgentRunService._maybe_finalize_parent_in_repository(service, repository, child_run_id)


def _finalize_acceptance(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
) -> None:
    if not service._quality_enabled(current.spec):
        _CoreAgentRunService._finalize_acceptance(service, repository, current)
        return

    revision = service._current_revision(repository, current.run_id)
    revision_id = revision.revision_id if revision is not None else None
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="acceptance.started",
            payload={"source": "omnix", "task_revision_id": revision_id},
        )
    )
    quality = service.quality_repository_factory(repository.connection, service.context)
    service._quarantine_isolated_workspace_contamination(repository, current.spec)
    state = capture_workspace_state(current.spec, task_revision_id=revision_id)
    if state is not None:
        quality.add_workspace_state(state)
    change_set = service._capture_diff(
        repository,
        current.spec,
        task_revision_id=revision_id,
        workspace_state_id=state.state_id if state else None,
    )
    run_events = all_events(repository, current.run_id)
    all_artifacts = repository.list_artifacts(current.run_id)
    all_receipts = repository.list_evidence_receipts(current.run_id)
    events = service._events_for_revision(run_events, revision)
    artifacts = service._artifacts_for_revision(all_artifacts, revision)
    receipts = service._receipts_for_revision(all_receipts, revision)
    effective_policy = revision.evidence_decision.policy if revision is not None else current.spec.evidence_policy
    evidence_set = evaluate_evidence_set(current.run_id, effective_policy, receipts)
    result = evaluate_acceptance(
        current.spec,
        events=events,
        artifacts=artifacts,
        task_revision=revision,
        evidence_set=evidence_set,
    )

    quality = service.quality_repository_factory(repository.connection, service.context)
    acceptance_stage = quality.get_stage(current.run_id) or {}
    reviewed_workspace_state_id = (
        str(acceptance_stage.get("workspace_state_id") or "").strip() or None
    )
    service._quarantine_isolated_workspace_contamination(repository, current.spec)
    state = capture_workspace_state(current.spec, task_revision_id=revision_id)
    if state is not None:
        quality.add_workspace_state(state)
    workspace_changed_after_review = bool(
        str(acceptance_stage.get("stage") or "") == "acceptance"
        and reviewed_workspace_state_id
        and state is not None
        and state.state_id != reviewed_workspace_state_id
    )
    validations = quality.list_validation_results(current.run_id, task_revision_id=revision_id)
    reviews = quality.list_review_results(current.run_id, task_revision_id=revision_id)
    self_reviews = quality.list_self_review_results(current.run_id, task_revision_id=revision_id)
    quality_failures = quality_failure_reasons(current, revision, state, validations, reviews, self_reviews)
    planning_assessment = evaluate_planning_acceptance(
        repository.connection,
        service.context,
        current,
        revision,
        modified_paths=list(result.modified_paths),
    )
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="planning.conformance_evaluated",
            payload={
                "mode": planning_assessment.mode,
                "plan_revision_id": planning_assessment.plan_revision_id,
                "would_block": planning_assessment.would_block,
                "blocks_acceptance": planning_assessment.blocks_acceptance,
                "fail_closed": planning_assessment.fail_closed,
                "hard_gate_required": planning_assessment.hard_gate_required,
                "failures": list(planning_assessment.failures),
                "task_revision_id": revision_id,
                "workspace_state_id": state.state_id if state else None,
            },
        )
    )

    failures, passed, promotion = _acceptance_failures(current, repository, quality_failures, result, workspace_changed_after_review, planning_assessment, service, revision_id, state, change_set)

    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="acceptance.completed",
            payload={
                **result.model_dump(mode="json"),
                "passed": passed,
                "failures": failures,
                "retrying": False,
                "task_revision_id": revision_id,
                "workspace_state_id": state.state_id if state else None,
                "evidence_set": evidence_set.model_dump(mode="json"),
                "quality_policy": current.spec.quality_policy,
                "workspace_promotion": promotion,
                "planning": {
                    "mode": planning_assessment.mode,
                    "plan_revision_id": planning_assessment.plan_revision_id,
                    "would_block": planning_assessment.would_block,
                    "blocks_acceptance": planning_assessment.blocks_acceptance,
                    "fail_closed": planning_assessment.fail_closed,
                    "failures": list(planning_assessment.failures),
                },
            },
        )
    )
    latest = repository.get_run(current.run_id) or current
    outcome = _refresh_after_review_drift(workspace_changed_after_review, planning_assessment, service, repository, latest, reviewed_workspace_state_id, revision, state)
    if outcome is not _CONTINUE:
        return outcome
    if passed:
        latest = _complete_accepted_run(promotion, repository, current, service, quality, revision_id, state, latest)
        return

    outcome = _settle_failed_acceptance(current, latest, repository, service, failures, result, planning_assessment, revision, reviews, state)
    if outcome is not _CONTINUE:
        return outcome


def _acceptance_failures(current, repository, quality_failures, result, workspace_changed_after_review, planning_assessment, service, revision_id, state, change_set):
    """All acceptance failures, then promotion of a passing candidate."""
    non_reviewer_children = [
        child for child in repository.list_children(current.run_id)
        if child.spec.profile != "coding-reviewer"
    ]
    children_terminal = all(child.status in _TERMINAL for child in non_reviewer_children)
    child_failed = any(child.status in {"failed", "cancelled"} for child in non_reviewer_children)
    failures = list(result.failures) + list(quality_failures)
    if workspace_changed_after_review:
        failures.append("quality_workspace_changed_after_review")
    if planning_assessment.blocks_acceptance:
        failures.extend(planning_assessment.failures)
    if not children_terminal:
        failures.append("children_not_terminal")
    if child_failed:
        failures.append("child_run_failed")
    failures = list(dict.fromkeys(failures))
    passed = result.passed and not failures
    promotion: dict[str, Any] | None = None
    if passed:
        try:
            promotion = service._promote_accepted_workspace(
                repository,
                current,
                task_revision_id=revision_id,
                workspace_state_id=(
                    state.state_id
                    if state is not None
                    else (
                        change_set.candidate_workspace_state_id
                        if change_set is not None
                        else None
                    )
                ),
            )
        except WorkspacePromotionError as exc:
            failures.append(f"workspace_promotion_failed:{exc}")
            passed = False
    return failures, passed, promotion


def _refresh_after_review_drift(workspace_changed_after_review, planning_assessment, service, repository, latest, reviewed_workspace_state_id, revision, state):
    """The workspace changed after review: re-review the new state, or fail when that is impossible."""
    if workspace_changed_after_review:
        if planning_assessment.blocks_acceptance:
            service._quality_fail(
                repository,
                latest,
                "review_integrity_failed:workspace_changed_after_review:"
                + ",".join(planning_assessment.failures),
            )
            return
        if revision is None or state is None or reviewed_workspace_state_id is None:
            service._quality_fail(
                repository,
                latest,
                "review_integrity_failed:workspace_refresh_context_unavailable",
            )
            return
        service._request_quality_workspace_refresh(
            repository,
            latest,
            revision,
            current_workspace_state_id=state.state_id,
            prior_workspace_state_id=reviewed_workspace_state_id,
        )
        return
    return _CONTINUE


def _complete_accepted_run(promotion, repository, current, service, quality, revision_id, state, latest):
    """Record the promotion, the acceptance stage and completion of an accepted run."""
    if promotion is not None:
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="run.completed",
                payload={"source": "omnix", "workspace_promotion": promotion},
            )
        )
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="acceptance",
        attempt=max(1, int((quality.get_stage(current.run_id) or {}).get("attempt") or 1)),
        task_revision_id=revision_id,
        workspace_state_id=state.state_id if state else None,
    )
    latest = repository.get_run(current.run_id) or latest
    repository.update_state(
        current.run_id,
        expected_revision=latest.revision,
        status="completed",
        worker_id=service.worker_id,
        last_error=None,
    )
    return latest


def _settle_failed_acceptance(current, latest, repository, service, failures, result, planning_assessment, revision, reviews, state):
    """A failed acceptance ends Pi-native runs, requests a bounded repair when the failure is repairable, and fails closed otherwise."""
    if not CODING_VALIDATION_PHASE_ENABLED and not CODING_INDEPENDENT_REVIEW_PHASE_ENABLED:
        # In Pi-native mode, settling ends Pi's single autonomous coding
        # turn. Deterministic acceptance may reject the candidate, but it
        # must not inject an automatic repair prompt and take control of
        # Pi's implementation loop again.
        latest = repository.get_run(current.run_id) or latest
        repository.update_state(
            current.run_id,
            expected_revision=latest.revision,
            status="failed",
            desired_state="cancelled",
            worker_id=service.worker_id,
            last_error=("acceptance_failed:" + ",".join(failures))[:2000],
        )
        return

    repairable_acceptance = _acceptance_failures_retryable(list(result.failures)) if result.failures else True
    fail_closed = planning_assessment.fail_closed or any(
        failure in {
            "modified_paths_outside_scope",
            "preexisting_dirty_paths_modified",
            "evidence_requirements_unsatisfied",
            "user_visible_attribution_unavailable",
            "child_run_failed",
        }
        for failure in failures
    ) or any(str(failure).startswith("workspace_promotion_failed:") for failure in failures)
    if revision is not None and repairable_acceptance and not fail_closed:
        latest_review = next(
            (
                review
                for review in reversed(reviews)
                if state is not None and review.workspace_state_id == state.state_id
            ),
            None,
        )
        service._request_quality_repair(
            repository,
            latest,
            revision,
            latest_review,
            failures=failures,
        )
        return

    latest = repository.get_run(current.run_id) or latest
    repository.update_state(
        current.run_id,
        expected_revision=latest.revision,
        status="failed",
        desired_state="cancelled",
        worker_id=service.worker_id,
        last_error=("acceptance_failed:" + ",".join(failures))[:2000],
    )
    return _CONTINUE
