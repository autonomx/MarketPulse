"""Durable independent-review orchestration.

This module is intentionally service-structural: callers provide the quality-aware
AgentRunService instance rather than importing it here. That keeps reviewer
execution/recovery semantics shared without creating a service import cycle.
"""
from __future__ import annotations

from .event_queries import events_of_types
from .exception_logging import log_recovered_exception

import hashlib
import json
import re
from typing import Any

from .coding_quality import (
    legacy_required_review_count,
    parse_review_result,
    review_is_acceptable,
    review_prompt,
    review_workspace_matches_snapshot,
)
from .coding_quality_repository import PostgresCodingQualityRepository
from .contracts import AgentEvent, AgentRunSnapshot, AgentRunSpec, ReviewResult, ReviewSnapshot, WorkspaceSpec
from .planning_repository import PostgresPlanningRepository
from .repository import PostgresAgentRunRepository
from .resource_grants import PostgresResourceGrantRepository, ResourceGrantError
from .review_runtime import (
    REVIEW_PROTOCOL_VERSION,
    finish_completed_attempt,
    finish_protocol_failed_attempt,
    finish_runtime_failed_attempt,
    latest_attempt_by_slot,
    latest_reviewer_text,
    new_review_attempt,
    retry_slots,
    review_complexity_score,
    review_payload_is_protocol_valid,
    reviewer_child_run_id,
    results_by_slot,
)
from .subagents import ChildRunRequest, default_reviewer_limits, derive_child_spec
from app.prompts import prompt_template
from .run_repository_queries import PostgresAgentRunQueries


REVIEW_PROMPT_WITH_CONTEXT_TEMPLATE = prompt_template(
    'agent_runtime.review_orchestration_core.review_prompt_with_context', "1",
    (
        '\n'
        '\n'
        'REVIEW PROTOCOL V2:\n'
        'Pass A — blind correctness: call the Omnix Run Change Set tool, inspect that '
        'authoritative run-owned subject plus changed source, callers/contracts, and raw '
        'validation evidence first. Form your own correctness judgment before using '
        'implementation planning claims. Do not infer correctness from an approved plan or from '
        'prior agent conclusions.\n'
        'Pass B — coverage reconciliation: after the blind pass, compare your independent '
        'understanding against the following durable planning/evidence artifacts. Treat them as '
        'implementation-produced claims that may be incomplete or wrong; use them to find missed '
        'impact, not to anchor approval.\n'
        'UNTRUSTED_PLANNING_CONTEXT_JSON={planning_context}\n'
        'Return the same single structured JSON verdict required above.'
    ),
)


_REVIEW_MARKER = re.compile(r"REVIEW_SNAPSHOT_ID=([a-f0-9]+)")
_REVIEW_SLOT_MARKER = re.compile(r"REVIEW_SLOT=(\d+)")
_REVIEW_ATTEMPT_MARKER = re.compile(r"REVIEW_RUNTIME_ATTEMPT=(\d+)")
_TERMINAL = {"completed", "failed", "cancelled"}
_READ_REVIEW_CAPABILITIES = [
    "workspace.read",
    "workspace.list",
    "workspace.search",
    "workspace.git_status",
    "workspace.run_change_set",
]


def review_snapshot_id_from_child(child: AgentRunSnapshot) -> str | None:
    match = _REVIEW_MARKER.search(child.spec.task)
    return match.group(1) if match else None


def _slot_from_child(child: AgentRunSnapshot) -> int | None:
    match = _REVIEW_SLOT_MARKER.search(child.spec.task)
    return int(match.group(1)) if match else None


def _runtime_attempt_from_child(child: AgentRunSnapshot) -> int | None:
    match = _REVIEW_ATTEMPT_MARKER.search(child.spec.task)
    return int(match.group(1)) if match else None


def _reviewer_marker_prefix(snapshot: ReviewSnapshot, slot: int, runtime_attempt: int) -> str:
    return (
        f"REVIEW_SNAPSHOT_ID={snapshot.snapshot_id}\n"
        f"REVIEW_SLOT={slot}\n"
        f"REVIEW_RUNTIME_ATTEMPT={runtime_attempt}\n"
        f"REVIEW_PROTOCOL_VERSION={REVIEW_PROTOCOL_VERSION}\n"
    )


def _planning_review_context(
    connection: Any,
    context: Any,
    *,
    run_id: str,
    task_revision_id: str,
) -> str:
    """Return bounded, explicitly untrusted planning context for pass-two review."""

    planning = PostgresPlanningRepository(connection, context)
    state = planning.get_state(run_id)
    plan = planning.latest_approved_plan(run_id, task_revision_id=task_revision_id)
    candidates = planning.list_impact_candidates(run_id, task_revision_id=task_revision_id)
    evidence = planning.list_inspection_evidence(run_id, task_revision_id=task_revision_id)
    payload = {
        "planning_state": {
            "status": (state or {}).get("status"),
            "active_plan_revision_id": (state or {}).get("active_plan_revision_id"),
            "planning_baseline_id": (state or {}).get("planning_baseline_id"),
        },
        "approved_plan": plan.model_dump(mode="json") if plan is not None else None,
        "impact_candidates": [
            item.model_dump(mode="json") for item in candidates[:120]
        ],
        "inspection_evidence": [
            {
                "evidence_id": item.evidence_id,
                "kind": item.kind,
                "path": item.path,
                "completeness": item.completeness,
                "result_digest": item.result_digest,
                "locations": list(item.locations[:30]),
                "excerpt": item.bounded_excerpt[:800] if item.bounded_excerpt else None,
            }
            for item in evidence[:160]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _review_prompt_with_context(
    connection: Any,
    context: Any,
    *,
    revision: Any,
    snapshot: ReviewSnapshot,
    validations: list[Any],
) -> str:
    """Use a blind-first then reconciliation review protocol.

    The implementer's self-review verdict is intentionally not supplied. Planning
    artifacts are supplied only after the blind review instruction and are
    explicitly claims to verify, not trusted correctness evidence.
    """

    base = review_prompt(revision, snapshot, validations)
    planning_context = _planning_review_context(
        connection,
        context,
        run_id=snapshot.run_id,
        task_revision_id=revision.revision_id,
    )
    return (
        base
        + REVIEW_PROMPT_WITH_CONTEXT_TEMPLATE.format(planning_context=planning_context)
    )


def _per_slot_available(
    available: dict[str, int | float | None],
    divisor: int,
) -> dict[str, int | float | None]:
    divisor = max(1, divisor)
    return {
        "max_steps": int(available.get("max_steps") or 0) // divisor,
        "max_tool_calls": int(available.get("max_tool_calls") or 0) // divisor,
        "max_tokens": (
            int(available["max_tokens"] or 0) // divisor
            if available.get("max_tokens") is not None
            else None
        ),
        "max_cost": (
            float(available["max_cost"] or 0.0) / divisor
            if available.get("max_cost") is not None
            else None
        ),
        # Concurrent reviewer wall times share the parent deadline; do not divide.
        "max_wall_time_seconds": int(available.get("max_wall_time_seconds") or 0),
    }


def launch_reviewer_children(
    service: Any,
    parent_run_id: str,
    snapshot_id: str,
    count: int,
) -> None:
    """Launch missing/retry reviewer slots with durable attempt + grant identity."""

    required = max(1, int(count))
    while True:
        launch: tuple[AgentRunSpec, AgentRunSnapshot] | None = None
        with service._run_lock(parent_run_id):
            with service.unit_of_work(service.database) as work:
                repository = service.repository_factory(work.connection, service.context)
                locked = PostgresAgentRunQueries(work.connection, service.context).lock_run(parent_run_id).fetchone()
                if locked is None:
                    work.rollback()
                    return
                parent = repository.get_run(parent_run_id)
                if (
                    parent is None
                    or parent.status in _TERMINAL
                    or parent.status != "waiting_for_children"
                ):
                    work.rollback()
                    return
                quality = service.quality_repository_factory(work.connection, service.context)
                snapshot = quality.get_review_snapshot(parent_run_id, snapshot_id)
                revision = service._current_revision(repository, parent_run_id)
                stage = quality.get_stage(parent_run_id) or {}
                if snapshot is None or revision is None:
                    work.rollback()
                    return
                if (
                    str(stage.get("stage") or "") != "reviewing"
                    or snapshot.task_revision_id != revision.revision_id
                    or snapshot.workspace_state_id != stage.get("workspace_state_id")
                ):
                    work.rollback()
                    return
                if not review_workspace_matches_snapshot(parent.spec, snapshot):
                    latest = repository.get_run(parent_run_id) or parent
                    repository.update_state(
                        parent_run_id,
                        expected_revision=latest.revision,
                        status="failed",
                        desired_state="cancelled",
                        last_error="quality_review_snapshot_integrity_mismatch",
                    )
                    work.commit()
                    return

                attempts, exhausted, slots = _reviewer_slots(quality, parent_run_id, snapshot, revision, required)
                if exhausted or not slots:
                    work.rollback()
                    return
                slot = slots[0]
                latest_by_slot = latest_attempt_by_slot(attempts)
                runtime_attempt = (
                    latest_by_slot[slot].runtime_attempt + 1
                    if slot in latest_by_slot
                    else 1
                )
                validations = quality.list_validation_results(
                    parent_run_id,
                    task_revision_id=revision.revision_id,
                )
                prompt = _reviewer_marker_prefix(snapshot, slot, runtime_attempt) + _review_prompt_with_context(
                    work.connection,
                    service.context,
                    revision=revision,
                    snapshot=snapshot,
                    validations=validations,
                )
                reviewer_objective = (
                    f"Independently review immutable snapshot {snapshot.snapshot_id} "
                    f"for correctness and completeness (reviewer slot {slot})."
                )
                child_id, child_spec, grants, parent_usage = _reviewer_child_spec(snapshot, parent, service, work, parent_run_id, repository, slots, revision, validations, prompt, reviewer_objective, slot, runtime_attempt)
                existing = repository.get_run(child_id)
                if existing is not None:
                    # Deterministic retry identity makes concurrent supervisors
                    # converge on the same attempt without duplicate execution.
                    work.rollback()
                    if existing.status not in _TERMINAL and service.runtime.get_status(existing.run_id) is None:
                        try:
                            service.runtime.start(existing.spec)
                        except Exception as exc:
                            log_recovered_exception("reviewer runtime rehydration", exc)
                            pass
                    continue

                service._validate_run_spec_authority(child_spec)
                service._validate_evidence_authority(child_spec)
                try:
                    grants.assert_can_grant(
                        parent,
                        child_spec.limits,
                        parent_usage=parent_usage,
                        protected_fraction=0.10,
                    )
                except ResourceGrantError as exc:
                    latest = repository.get_run(parent_run_id) or parent
                    repository.append_event(
                        AgentEvent(
                            run_id=parent_run_id,
                            event_type="quality.review_runtime_exhausted",
                            payload={
                                "review_snapshot_id": snapshot.snapshot_id,
                                "reviewer_slot": slot,
                                "runtime_attempt": runtime_attempt,
                                "failure_class": "parent_global_budget_exhausted",
                                "reason": str(exc),
                                "task_revision_id": revision.revision_id,
                                "workspace_state_id": snapshot.workspace_state_id,
                            },
                        )
                    )
                    repository.update_state(
                        parent_run_id,
                        expected_revision=latest.revision,
                        status="failed",
                        desired_state="cancelled",
                        last_error=("review_runtime_exhausted:parent_global_budget_exhausted:" + str(exc))[:2000],
                    )
                    work.commit()
                    return

                launch = _start_reviewer_attempt(child_spec, service, repository, grants, parent_run_id, snapshot, slot, runtime_attempt, parent, quality, revision)
                work.commit()
        if launch is not None:
            service._launch_runtime(launch[0], launch[1])


def _reviewer_slots(quality, parent_run_id, snapshot, revision, required):
    """Reviewer slots still to run for this snapshot, from prior attempts and results."""
    attempts = quality.list_review_attempts(
        parent_run_id,
        review_snapshot_id=snapshot.snapshot_id,
        task_revision_id=revision.revision_id,
    )
    results = [
        item
        for item in quality.list_review_results(
            parent_run_id,
            task_revision_id=revision.revision_id,
        )
        if item.review_snapshot_id == snapshot.snapshot_id
        and item.workspace_state_id == snapshot.workspace_state_id
    ]
    slots, pending, exhausted = retry_slots(
        required_slots=required,
        attempts=attempts,
        results=results,
    )
    return attempts, exhausted, slots


def _reviewer_child_spec(snapshot, parent, service, work, parent_run_id, repository, slots, revision, validations, prompt, reviewer_objective, slot, runtime_attempt):
    """The reviewer's run spec: the immutable snapshot workspace, read-only capabilities and a share of the parent budget."""
    workspace = WorkspaceSpec(
        root=snapshot.workspace_root,
        repository=(parent.spec.workspace.repository if parent.spec.workspace else None)
        or (parent.spec.workspace.root if parent.spec.workspace else snapshot.workspace_root),
        base_ref=snapshot.base_commit_sha,
        worktree=snapshot.workspace_root,
        isolation_policy="immutable_review_snapshot",
        allowed_paths=list(parent.spec.workspace.allowed_paths if parent.spec.workspace else ["**"]),
        forbidden_paths=list(parent.spec.workspace.forbidden_paths if parent.spec.workspace else []),
    )

    grants = PostgresResourceGrantRepository(work.connection, service.context)
    parent_usage = repository.get_usage(parent_run_id)
    available = grants.available_capacity(
        parent,
        parent_usage=parent_usage,
        protected_fraction=0.10,
    )
    # Divide only among reviewer slots that need capacity *now*.
    # Never divide by hypothetical future quality attempts.
    slot_available = _per_slot_available(available, len(slots))
    complexity = review_complexity_score(
        snapshot,
        revision,
        validation_count=len(validations),
    )
    limits = default_reviewer_limits(
        parent.spec.limits,
        complexity_score=complexity,
        available=slot_available,
    )
    request = ChildRunRequest(
        task=prompt,
        objective=reviewer_objective,
        profile_id="coding-reviewer",
        provider_id=parent.spec.model.provider_id,
        model_id=parent.spec.model.model_id,
        reasoning_effort=parent.spec.model.reasoning_effort,
        capabilities=list(_READ_REVIEW_CAPABILITIES),
        external_capabilities=[],
        success_criteria=[
            "Return a structured independent review verdict for the immutable snapshot."
        ],
        limits=limits,
    )
    child_spec = derive_child_spec(parent, request, workspace_override=workspace)
    child_id = reviewer_child_run_id(
        parent_run_id=parent_run_id,
        snapshot=snapshot,
        reviewer_slot=slot,
        runtime_attempt=runtime_attempt,
        model=parent.spec.model,
    )
    child_spec = child_spec.model_copy(update={"run_id": child_id})
    return child_id, child_spec, grants, parent_usage


def _start_reviewer_attempt(child_spec, service, repository, grants, parent_run_id, snapshot, slot, runtime_attempt, parent, quality, revision):
    """Issue the reviewer run, grant its budget and record the review attempt."""
    issued = service._prepare_workspace(service._bind_github_repository_authority(child_spec))
    child_snapshot = service._persist_starting_run(repository, issued)
    grants.add_grant(
        parent_run_id=parent_run_id,
        child_run_id=issued.run_id,
        limits=issued.limits,
    )
    attempt = new_review_attempt(
        parent_run_id=parent_run_id,
        reviewer_run_id=issued.run_id,
        snapshot=snapshot,
        reviewer_slot=slot,
        runtime_attempt=runtime_attempt,
        model=parent.spec.model,
    )
    quality.add_review_attempt(attempt)
    repository.append_event(
        AgentEvent(
            run_id=parent_run_id,
            event_type="quality.review_attempt_started",
            payload={
                "review_attempt_id": attempt.review_attempt_id,
                "reviewer_run_id": issued.run_id,
                "review_snapshot_id": snapshot.snapshot_id,
                "reviewer_slot": slot,
                "runtime_attempt": runtime_attempt,
                "protocol_version": attempt.protocol_version,
                "limits": issued.limits.model_dump(mode="json"),
                "task_revision_id": revision.revision_id,
                "workspace_state_id": snapshot.workspace_state_id,
            },
        )
    )
    launch = (issued, child_snapshot)
    return launch


def _legacy_slot(
    repository: PostgresAgentRunRepository,
    parent_run_id: str,
    snapshot_id: str,
    child: AgentRunSnapshot,
) -> int:
    marker = _slot_from_child(child)
    if marker is not None:
        return marker
    matching = sorted(
        [
            item.run_id
            for item in repository.list_children(parent_run_id)
            if item.spec.profile == "coding-reviewer"
            and review_snapshot_id_from_child(item) == snapshot_id
        ]
    )
    return matching.index(child.run_id) if child.run_id in matching else 0


def consume_terminal_reviewer_in_repository(
    service: Any,
    repository: PostgresAgentRunRepository,
    child: AgentRunSnapshot,
    snapshot: ReviewSnapshot,
    revision: Any,
    quality: PostgresCodingQualityRepository,
) -> ReviewResult | None:
    """Persist terminal ReviewAttempt and optional substantive ReviewResult once."""

    if child.status not in _TERMINAL:
        return None
    existing_results = [
        item
        for item in quality.list_review_results(
            snapshot.run_id,
            task_revision_id=revision.revision_id,
        )
        if item.review_snapshot_id == snapshot.snapshot_id
        and item.reviewer_run_id == child.run_id
    ]
    if existing_results:
        return existing_results[-1]

    attempt = quality.get_review_attempt_by_reviewer(child.run_id)
    if attempt is None:
        # Rolling-deploy/backward compatibility: an old reviewer child can be
        # consumed without ever fabricating a substantive runtime verdict.
        slot = _legacy_slot(repository, snapshot.run_id, snapshot.snapshot_id, child)
        runtime_attempt = _runtime_attempt_from_child(child) or 1
        attempt = new_review_attempt(
            parent_run_id=snapshot.run_id,
            reviewer_run_id=child.run_id,
            snapshot=snapshot,
            reviewer_slot=slot,
            runtime_attempt=runtime_attempt,
            model=child.spec.model,
            protocol_version="review-v1-legacy" if _slot_from_child(child) is None else "review-v2-legacy",
        )
        quality.add_review_attempt(attempt)

    if attempt.status != "running":
        return None

    text = latest_reviewer_text(events_of_types(repository, child.run_id, {"model.message"}))
    result: ReviewResult | None = None
    if child.status != "completed":
        finished = finish_runtime_failed_attempt(attempt, child)
    elif not review_payload_is_protocol_valid(
        text,
        revision,
        require_path_claims=attempt.protocol_version == REVIEW_PROTOCOL_VERSION,
    ):
        finished = finish_protocol_failed_attempt(attempt)
    else:
        finished = finish_completed_attempt(attempt)
        parsed = parse_review_result(
            text,
            parent_run_id=child.spec.parent_run_id or snapshot.run_id,
            reviewer_run_id=child.run_id,
            snapshot=snapshot,
        )
        deterministic_result_id = hashlib.sha256(
            f"review-result:{child.run_id}:{snapshot.snapshot_id}".encode("utf-8")
        ).hexdigest()
        result = parsed.model_copy(update={"review_result_id": deterministic_result_id})
        quality.add_review_result(result)

    quality.add_review_attempt(finished)
    repository.append_event(
        AgentEvent(
            run_id=snapshot.run_id,
            event_type="quality.review_attempt_completed",
            payload={
                "review_attempt_id": finished.review_attempt_id,
                "reviewer_run_id": child.run_id,
                "review_snapshot_id": snapshot.snapshot_id,
                "reviewer_slot": finished.reviewer_slot,
                "runtime_attempt": finished.runtime_attempt,
                "status": finished.status,
                "failure_class": finished.failure_class,
                "failure_reason": finished.failure_reason,
                "retryable": finished.retryable,
                "task_revision_id": finished.task_revision_id,
                "workspace_state_id": finished.workspace_state_id,
            },
        )
    )
    if result is not None:
        repository.append_event(
            AgentEvent(
                run_id=snapshot.run_id,
                event_type="quality.review_completed",
                payload={
                    "reviewer_run_id": child.run_id,
                    "review_snapshot_id": snapshot.snapshot_id,
                    "reviewer_slot": finished.reviewer_slot,
                    "runtime_attempt": finished.runtime_attempt,
                    "verdict": result.verdict,
                    "findings": [item.model_dump(mode="json") for item in result.findings],
                    "missing_tests": list(result.missing_tests),
                    "task_revision_id": result.task_revision_id,
                    "workspace_state_id": result.workspace_state_id,
                },
            )
        )
    return result


def _fail_review_runtime_exhausted(
    service: Any,
    repository: PostgresAgentRunRepository,
    parent: AgentRunSnapshot,
    snapshot: ReviewSnapshot,
    attempts: list[Any],
    exhausted_slots: list[int],
) -> None:
    latest_by_slot = latest_attempt_by_slot(attempts)
    detail = []
    for slot in exhausted_slots:
        item = latest_by_slot.get(slot)
        detail.append(
            {
                "reviewer_slot": slot,
                "runtime_attempt": item.runtime_attempt if item else None,
                "failure_class": item.failure_class if item else "runtime_failure",
                "failure_reason": item.failure_reason if item else "review attempt unavailable",
            }
        )
    repository.append_event(
        AgentEvent(
            run_id=parent.run_id,
            event_type="quality.review_runtime_exhausted",
            payload={
                "review_snapshot_id": snapshot.snapshot_id,
                "exhausted_slots": list(exhausted_slots),
                "attempts": detail,
                "task_revision_id": snapshot.task_revision_id,
                "workspace_state_id": snapshot.workspace_state_id,
            },
        )
    )
    latest = repository.get_run(parent.run_id) or parent
    first_class = str(detail[0].get("failure_class") or "runtime_failure") if detail else "runtime_failure"
    repository.update_state(
        parent.run_id,
        expected_revision=latest.revision,
        status="failed",
        desired_state="cancelled",
        last_error=f"review_runtime_exhausted:{first_class}"[:2000],
    )


def reconcile_review_progress_in_repository(
    service: Any,
    repository: PostgresAgentRunRepository,
    parent_run_id: str,
) -> tuple[str, str, int] | tuple[str, int] | None:
    """Consume terminal attempts and advance one review-stage parent.

    Returns an action when a reviewer retry or acceptance job needs execution.
    Runtime/protocol failure never invokes implementation repair.
    """

    parent = repository.get_run(parent_run_id)
    if (
        parent is None
        or parent.status != "waiting_for_children"
        or parent.desired_state != "running"
        or not service._quality_enabled(parent.spec)
    ):
        return None
    quality = service.quality_repository_factory(repository.connection, service.context)
    stage = quality.get_stage(parent_run_id)
    if stage is None or str(stage.get("stage") or "") != "reviewing":
        return None
    revision = service._current_revision(repository, parent_run_id)
    state_id = str(stage.get("workspace_state_id") or "")
    if revision is None or not state_id:
        service._quality_fail(repository, parent, "quality_review_recovery_missing_revision_or_workspace_state")
        return None
    state = quality.get_workspace_state(parent_run_id, state_id)
    snapshot = quality.latest_review_snapshot(
        parent_run_id,
        task_revision_id=revision.revision_id,
        workspace_state_id=state_id,
    )
    if state is None or snapshot is None:
        service._quality_fail(repository, parent, "quality_review_snapshot_missing_after_recovery")
        return None
    if (
        snapshot.task_revision_id != revision.revision_id
        or snapshot.workspace_state_id != state_id
    ):
        service._quality_fail(repository, parent, "quality_review_snapshot_stale_after_recovery")
        return None

    for child in repository.list_children(parent_run_id):
        if (
            child.spec.profile == "coding-reviewer"
            and review_snapshot_id_from_child(child) == snapshot.snapshot_id
            and child.status in _TERMINAL
        ):
            service.terminal_reviewer_consumer(
                service,
                repository,
                child,
                snapshot,
                revision,
                quality,
            )

    required = legacy_required_review_count(parent.spec, state)
    attempts = quality.list_review_attempts(
        parent_run_id,
        review_snapshot_id=snapshot.snapshot_id,
        task_revision_id=revision.revision_id,
    )
    results = [
        item
        for item in quality.list_review_results(
            parent_run_id,
            task_revision_id=revision.revision_id,
        )
        if item.workspace_state_id == state_id
        and item.review_snapshot_id == snapshot.snapshot_id
    ]
    launch_slots, pending_slots, exhausted_slots = retry_slots(
        required_slots=required,
        attempts=attempts,
        results=results,
    )
    if exhausted_slots:
        _fail_review_runtime_exhausted(
            service,
            repository,
            parent,
            snapshot,
            attempts,
            exhausted_slots,
        )
        return None
    if launch_slots:
        for slot in launch_slots:
            prior = latest_attempt_by_slot(attempts).get(slot)
            repository.append_event(
                AgentEvent(
                    run_id=parent_run_id,
                    event_type="quality.review_retry_requested",
                    payload={
                        "review_snapshot_id": snapshot.snapshot_id,
                        "reviewer_slot": slot,
                        "next_runtime_attempt": (prior.runtime_attempt + 1 if prior else 1),
                        "prior_failure_class": prior.failure_class if prior else None,
                        "prior_failure_reason": prior.failure_reason if prior else None,
                        "quality_attempt": int(stage.get("attempt") or 1),
                        "task_revision_id": revision.revision_id,
                        "workspace_state_id": state_id,
                    },
                )
            )
        return ("launch_reviews", snapshot.snapshot_id, required)
    if pending_slots:
        return None

    return _settle_review_verdicts(attempts, results, required, service, repository, parent, snapshot, revision, parent_run_id, stage, state_id)


def _settle_review_verdicts(attempts, results, required, service, repository, parent, snapshot, revision, parent_run_id, stage, state_id):
    """Accept when enough reviewers approve; repair on a substantive rejection; fail closed on missing slots."""
    by_slot = results_by_slot(attempts, results)
    if len(by_slot) < required:
        # Every unresolved slot should have appeared in launch/pending/exhausted.
        # If durable state violates that invariant, fail closed as review runtime.
        _fail_review_runtime_exhausted(
            service,
            repository,
            parent,
            snapshot,
            attempts,
            [slot for slot in range(required) if slot not in by_slot],
        )
        return None

    approvals = [
        result for result in by_slot.values() if review_is_acceptable(result, revision)
    ]
    if len(approvals) >= required:
        service._set_quality_stage(
            repository,
            run_id=parent_run_id,
            stage="acceptance",
            attempt=max(1, int(stage.get("attempt") or 1)),
            task_revision_id=revision.revision_id,
            workspace_state_id=state_id,
        )
        latest = repository.get_run(parent_run_id) or parent
        if latest.status == "waiting_for_children":
            latest = repository.update_state(
                parent_run_id,
                expected_revision=latest.revision,
                status="running",
                worker_id=service.worker_id,
            )
        return ("promote_acceptance", latest.revision)

    # Only a *valid structured substantive verdict* reaches this repair path.
    # Reviewer runtime/protocol failures have already been handled above.
    latest_review = next(
        (
            item
            for item in reversed(results)
            if not review_is_acceptable(item, revision)
        ),
        results[-1] if results else None,
    )
    service._request_quality_repair(
        repository,
        parent,
        revision,
        latest_review,
        failures=["quality_independent_review_not_approved"],
    )
    return None


def finalize_reviewer_child_in_repository(
    service: Any,
    repository: PostgresAgentRunRepository,
    child_run_id: str,
) -> bool:
    """Consume one terminal reviewer callback and reconcile its parent.

    Returns ``True`` when the child belonged to the coding-quality review
    protocol, allowing the service facade to avoid generic child-failure logic.
    The parent row is locked here as well as in supervisor reconciliation so a
    reviewer callback and a concurrent worker cannot both advance the same
    review-stage authority transition.
    """

    child = repository.get_run(child_run_id)
    if (
        child is None
        or child.spec.profile != "coding-reviewer"
        or not child.spec.parent_run_id
        or child.status not in _TERMINAL
    ):
        return False
    locked = PostgresAgentRunQueries(repository.connection, repository.context).lock_run(child.spec.parent_run_id).fetchone()
    if locked is None:
        return False
    parent = repository.get_run(child.spec.parent_run_id)
    if parent is None or not service._quality_enabled(parent.spec):
        return False
    reconcile_review_progress_in_repository(service, repository, parent.run_id)
    return True
