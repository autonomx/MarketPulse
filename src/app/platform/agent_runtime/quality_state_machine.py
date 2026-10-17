"""The coding-quality state machine: stages, validation, self-review, repair and review children (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .profiles import profile_produces_diff
from .event_queries import events_of_types
from .exception_logging import log_recovered_exception
from app.config.env import env_str as _env_str
import hashlib
import json
from .coding_quality import (
    CODING_INDEPENDENT_REVIEW_PHASE_ENABLED,
    CODING_VALIDATION_PHASE_ENABLED,
    candidate_validation_gate,
    capture_workspace_state,
    materialize_review_workspace,
    missing_final_validations,
    parse_review_result,
    parse_self_review_result,
    quality_attempt_limit,
    relevant_file_candidates,
    repair_prompt,
    required_review_count,
    self_review_is_acceptable,
    self_review_prompt,
    validation_prompt,
)
from .coding_quality_repository import PostgresCodingQualityRepository
from .contracts import (
    AgentEvent,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    ReviewResult,
    ReviewSnapshot,
    SelfReviewResult,
    TaskRevision,
)
from app.observability.agent_logging import log_agent_activity
from .repository import PostgresAgentRunRepository
from .repository_guidance import compile_repository_guidance
from .review_orchestration import (
    launch_reviewer_children,
    review_snapshot_id_from_child,
)
from .review_runtime import latest_reviewer_text, review_payload_is_protocol_valid
from .run_change_set import run_change_set_from_artifact
from .service_core import (
    _acceptance_retry_count as _acceptance_retry_count,
)
from typing import TYPE_CHECKING
from .service import (
    REQUEST_IMPLEMENTATION_CONTINUATION_TEMPLATE,
    REQUEST_VALIDATION_REPAIR_TEMPLATE,
    _default_review_root,
    _implementation_candidate_retry_count,
    _implementation_candidate_retry_limit,
    _pre_review_gate,
    _quality_events,
    _self_review_payload_is_protocol_valid,
    _self_review_protocol_retry_count,
    _self_review_protocol_retry_limit,
    _self_review_response_from_repository,
    _validation_event_count,
    _validation_failure_fingerprint,
    _validation_retry_limit,
)

if TYPE_CHECKING:
    from app.platform.agent_runtime.service import AgentRunService


# Returned by an extracted step that did not settle its caller.
_CONTINUE = object()


def _quality_enabled(spec: AgentRunSpec) -> bool:
    return (
        profile_produces_diff(spec.profile)
        and "diff" in spec.expected_artifacts
        and spec.quality_policy != "off"
    )


def _set_quality_stage(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    stage: str,
    attempt: int,
    task_revision_id: str | None,
    workspace_state_id: str | None = None,
    reason: str | None = None,
) -> None:
    log_agent_activity(
        "quality.stage.transition_requested",
        category="quality",
        run_id=run_id,
        fields={
            "stage": stage,
            "attempt": attempt,
            "task_revision_id": task_revision_id,
            "workspace_state_id": workspace_state_id,
            "reason": reason,
        },
    )
    quality = service.quality_repository_factory(repository.connection, service.context)
    quality.set_stage(
        run_id,
        stage=stage,
        attempt=attempt,
        task_revision_id=task_revision_id,
        workspace_state_id=workspace_state_id,
    )
    repository.append_event(
        AgentEvent(
            run_id=run_id,
            event_type="quality.stage",
            payload={
                "stage": stage,
                "attempt": attempt,
                "task_revision_id": task_revision_id,
                "workspace_state_id": workspace_state_id,
                **({"reason": reason} if reason else {}),
            },
        )
    )
    log_agent_activity(
        "quality.stage.transition_recorded",
        category="quality",
        run_id=run_id,
        fields={
            "stage": stage,
            "attempt": attempt,
            "task_revision_id": task_revision_id,
            "workspace_state_id": workspace_state_id,
        },
    )


def _queue_quality_resume(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    *,
    run_id: str,
    prompt: str,
    idempotency_key: str,
    quality_stage: str,
    quality_attempt: int,
    task_revision_id: str | None,
    workspace_state_id: str | None,
) -> tuple | None:
    """Persist an internal quality command before dispatching it to Pi."""

    command = AgentRunCommand(
        run_id=run_id,
        command_type="resume",
        payload={
            "message": prompt,
            "quality_stage": quality_stage,
            "quality_attempt": quality_attempt,
            "task_revision_id": task_revision_id,
            "workspace_state_id": workspace_state_id,
        },
        idempotency_key=idempotency_key,
    )
    stored, status = repository.enqueue_command_with_status(command)
    if status != "pending":
        return None
    return ("dispatch_command", stored)


def _request_implementation_continuation(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    *,
    attempt: int,
    workspace_state_id: str,
    failures: list[str],
    prior_stage: str,
) -> tuple | None:
    """Keep implementing when Pi settles before a reviewable candidate exists."""
    retries = _implementation_candidate_retry_count(
        repository,
        run_id=current.run_id,
        attempt=attempt,
        task_revision_id=revision.revision_id,
    )
    retry_limit = _implementation_candidate_retry_limit()
    if retries >= retry_limit:
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="quality.implementation_candidate_exhausted",
                payload={
                    "quality_attempt": attempt,
                    "continuations": retries,
                    "continuation_limit": retry_limit,
                    "failures": list(failures),
                    "task_revision_id": revision.revision_id,
                    "workspace_state_id": workspace_state_id,
                },
            )
        )
        service._quality_fail(repository, current, "quality_failed:implementation_candidate_not_ready")
        return None

    continuation = retries + 1
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="quality.implementation_continuation_requested",
            payload={
                "quality_attempt": attempt,
                "continuation": continuation,
                "continuation_limit": retry_limit,
                "failures": list(failures),
                "task_revision_id": revision.revision_id,
                "workspace_state_id": workspace_state_id,
            },
        )
    )
    next_stage = "repairing" if prior_stage == "repairing" else "implementing"
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage=next_stage,
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        reason=f"implementation_candidate_not_ready_{continuation}",
    )
    prompt = (
        REQUEST_IMPLEMENTATION_CONTINUATION_TEMPLATE.format(
            attempt=attempt,
            effective_objective=revision.effective_objective,
            failures=', '.join(failures),
        )
    )
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=(
            f"quality-implementation-continuation:{current.run_id}:{revision.revision_id}:"
            f"{attempt}:{continuation}"
        ),
        quality_stage=next_stage,
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )


def _request_self_review_protocol_retry(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    quality: PostgresCodingQualityRepository,
    *,
    attempt: int,
    workspace_state_id: str,
    response_text: str,
) -> tuple | None:
    """Retry a missing/malformed verdict without consuming a repair attempt."""

    retries = _self_review_protocol_retry_count(
        repository,
        run_id=current.run_id,
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )
    retry_limit = _self_review_protocol_retry_limit()
    if retries >= retry_limit:
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="quality.self_review_protocol_exhausted",
                payload={
                    "quality_attempt": attempt,
                    "protocol_retries": retries,
                    "protocol_retry_limit": retry_limit,
                    "response_present": bool(str(response_text or "").strip()),
                    "task_revision_id": revision.revision_id,
                    "workspace_state_id": workspace_state_id,
                },
            )
        )
        service._quality_fail(
            repository,
            current,
            "quality_failed:quality_self_review_protocol_exhausted",
        )
        return None

    protocol_retry = retries + 1
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    current_validations = [
        item for item in validations if item.workspace_state_id == workspace_state_id
    ]
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="quality.self_review_protocol_retry_requested",
            payload={
                "quality_attempt": attempt,
                "protocol_retry": protocol_retry,
                "protocol_retry_limit": retry_limit,
                "response_present": bool(str(response_text or "").strip()),
                "task_revision_id": revision.revision_id,
                "workspace_state_id": workspace_state_id,
            },
        )
    )
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="self_review",
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        reason=f"self_review_protocol_retry_{protocol_retry}",
    )
    failure_kind = (
        "no assistant verdict text"
        if not str(response_text or "").strip()
        else "a response that did not satisfy the structured self-review schema"
    )
    prompt = self_review_prompt(
        revision,
        attempt=attempt,
        validations=current_validations,
    )
    prompt += (
        f"\n\nInternal protocol retry {protocol_retry}/{retry_limit}: the previous "
        f"self-review turn produced {failure_kind}. This is transport/protocol "
        "recovery, not a new implementation quality attempt. Do not edit files, "
        "rerun tools, or send progress prose. Return exactly one complete JSON "
        "object matching the required schema now."
    )
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=(
            f"quality-self-review-protocol:{current.run_id}:{revision.revision_id}:"
            f"{workspace_state_id}:{attempt}:{protocol_retry}"
        ),
        quality_stage="self_review",
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )


def _request_validation_execution(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    *,
    attempt: int,
    workspace_state_id: str,
    missing,
) -> tuple | None:
    ids = sorted(item.id for item in missing)
    fingerprint = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:24]
    prior = _validation_event_count(
        repository,
        run_id=current.run_id,
        event_type="quality.validation_requested",
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        fingerprint=fingerprint,
    )
    retry_limit = _validation_retry_limit()
    if prior > retry_limit:
        service._quality_fail(repository, current, "quality_failed:validation_not_executed")
        return None
    repository.append_event(AgentEvent(
        run_id=current.run_id,
        event_type="quality.validation_requested",
        payload={
            "task_revision_id": revision.revision_id,
            "workspace_state_id": workspace_state_id,
            "validation_ids": ids,
            "fingerprint": fingerprint,
            "attempt": prior + 1,
            "retry_limit": retry_limit,
        },
    ))
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="validating",
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        reason="candidate_validation_required",
    )
    prompt = validation_prompt(revision, missing)
    if prior:
        missing_kinds = sorted({str(item.kind or "validation") for item in missing})
        missing_label = ", ".join(missing_kinds)
        browser_only = missing_kinds == ["browser"]
        prompt += (
            "\n\nThe previous validation turn ended without recording the required "
            f"{missing_label} evidence. "
            f"This is bounded validation attempt {prior + 1} of {retry_limit + 1}. "
            "Do not end this turn with a summary until the required validation has executed "
            "successfully, or report the concrete validation failure so Omnix can classify it."
        )
        if browser_only:
            prompt += " Finish browser validation with the governed browser assertion requested above."
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=(
            f"quality-validation:{current.run_id}:{revision.revision_id}:"
            f"{workspace_state_id}:{fingerprint}:{prior + 1}"
        ),
        quality_stage="validating",
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )


def _request_validation_retry(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    *,
    attempt: int,
    workspace_state_id: str,
    failures,
) -> tuple | None:
    validation_ids = sorted({item.validation_id for item in failures})
    failure_fingerprint = _validation_failure_fingerprint(failures)
    prior_events = [
        event for event in _quality_events(repository, current.run_id)
        if event.event_type == "quality.validation_retry_requested"
        and str(event.payload.get("task_revision_id") or "") == revision.revision_id
        and str(event.payload.get("workspace_state_id") or "") == workspace_state_id
    ]
    retry_counts = {
        validation_id: sum(
            1 for event in prior_events
            if validation_id in [str(value) for value in event.payload.get("validation_ids") or []]
        )
        for validation_id in validation_ids
    }
    limit = _validation_retry_limit()
    exhausted = sorted(
        validation_id for validation_id, count in retry_counts.items()
        if count >= limit
    )
    if exhausted:
        repository.append_event(AgentEvent(
            run_id=current.run_id,
            event_type="quality.validation_retry_exhausted",
            payload={
                "task_revision_id": revision.revision_id,
                "workspace_state_id": workspace_state_id,
                "failure_fingerprint": failure_fingerprint,
                "validation_ids": validation_ids,
                "exhausted_validation_ids": exhausted,
                "retry_limit": limit,
            },
        ))
        service._quality_fail(repository, current, "quality_failed:validation_retry_exhausted")
        return None
    retry = max(retry_counts.values(), default=0) + 1
    retry_identity = hashlib.sha256(
        "|".join(validation_ids).encode("utf-8")
    ).hexdigest()[:24]
    repository.append_event(AgentEvent(
        run_id=current.run_id,
        event_type="quality.validation_retry_requested",
        payload={
            "task_revision_id": revision.revision_id,
            "workspace_state_id": workspace_state_id,
            "fingerprint": retry_identity,
            "failure_fingerprint": failure_fingerprint,
            "retry": retry,
            "retry_limit": limit,
            "validation_ids": validation_ids,
        },
    ))
    missing = [
        spec for spec in revision.validation_plan
        if spec.required and spec.id in set(validation_ids)
    ]
    prompt = validation_prompt(revision, missing)
    prompt += "\n\nThis is a bounded same-candidate infrastructure/protocol validation retry. Do not claim completion until it executes normally."
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=f"quality-validation-retry:{current.run_id}:{revision.revision_id}:{workspace_state_id}:{retry_identity}:{retry}",
        quality_stage="validating",
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )


def _request_validation_repair(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    *,
    attempt: int,
    workspace_state_id: str,
    failures,
) -> tuple | None:
    fingerprint = _validation_failure_fingerprint(failures)
    prior = _validation_event_count(
        repository,
        run_id=current.run_id,
        event_type="quality.validation_repair_requested",
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        fingerprint=fingerprint,
    )
    if prior:
        service._quality_fail(repository, current, "quality_failed:no_progress_after_validation_failure")
        return None
    repository.append_event(AgentEvent(
        run_id=current.run_id,
        event_type="quality.validation_repair_requested",
        payload={
            "task_revision_id": revision.revision_id,
            "workspace_state_id": workspace_state_id,
            "fingerprint": fingerprint,
            "validation_ids": sorted({item.validation_id for item in failures}),
            "outcomes": sorted({item.outcome for item in failures}),
        },
    ))
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="repairing",
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
        reason="substantive_validation_failure",
    )
    failure_rows = [
        {
            "validation_id": item.validation_id,
            "outcome": item.outcome,
            "command": item.command,
            "exit_code": item.exit_code,
            "output_digest": item.output_digest,
        }
        for item in failures
    ]
    prompt = (
        REQUEST_VALIDATION_REPAIR_TEMPLATE.format(
            failure_rows=json.dumps(failure_rows, ensure_ascii=False),
        )
    )
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=f"quality-validation-repair:{current.run_id}:{revision.revision_id}:{workspace_state_id}:{fingerprint}",
        quality_stage="repairing",
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=workspace_state_id,
    )


def _advance_quality_on_settle(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
) -> tuple | None:
    revision = service._current_revision(repository, current.run_id)
    if revision is None:
        repository.update_state(
            current.run_id,
            expected_revision=current.revision,
            status="failed",
            desired_state="cancelled",
            last_error="quality_task_revision_unavailable",
        )
        return None
    quality = service.quality_repository_factory(repository.connection, service.context)
    stage_state = quality.get_stage(current.run_id) or {
        "stage": "inspect",
        "attempt": 1,
        "task_revision_id": revision.revision_id,
        "workspace_state_id": None,
    }
    stage = str(stage_state.get("stage") or "implementing")
    attempt = max(1, int(stage_state.get("attempt") or 1))

    if stage in {"inspect", "planning", "implementing", "repairing", "validating"}:
        service._quarantine_isolated_workspace_contamination(repository, current.spec)
        state = capture_workspace_state(current.spec, task_revision_id=revision.revision_id)
        if state is None:
            service._quality_fail(repository, current, "quality_workspace_state_unavailable")
            return None
        quality.add_workspace_state(state)

        outcome = _accept_without_quality_phases(service, repository, current, attempt, revision, state)
        if outcome is not _CONTINUE:
            return outcome

        outcome = _pre_review_gates(current, repository, revision, service, state, quality, attempt, stage)
        if outcome is not _CONTINUE:
            return outcome

        service._set_quality_stage(
            repository,
            run_id=current.run_id,
            stage="validating",
            attempt=attempt,
            task_revision_id=revision.revision_id,
            workspace_state_id=state.state_id,
            reason="pi_candidate_ready_for_verification",
        )
        # Pi already performed ordinary self-review inside the same coding
        # turn. Continue directly to exact-state verification/reviewer
        # orchestration without a second implementer RPC turn.
        stage = "validating"

    service._quarantine_isolated_workspace_contamination(repository, current.spec)
    state = capture_workspace_state(current.spec, task_revision_id=revision.revision_id)
    if state is None:
        service._quality_fail(repository, current, "quality_workspace_state_unavailable")
        return None
    quality.add_workspace_state(state)

    outcome = _settle_self_review(stage, repository, current, attempt, revision, state, service, quality)
    if outcome is not _CONTINUE:
        return outcome

    if (
        stage == "self_review"
        and not CODING_VALIDATION_PHASE_ENABLED
        and not CODING_INDEPENDENT_REVIEW_PHASE_ENABLED
    ):
        service._set_quality_stage(
            repository,
            run_id=current.run_id,
            stage="acceptance",
            attempt=attempt,
            task_revision_id=revision.revision_id,
            workspace_state_id=state.state_id,
            reason="coding_quality_phases_disabled",
        )
        service._finalize_acceptance(repository, current)
        return None

    validations = _reconciled_validations(quality, current, revision, service, repository, state)
    outcome = _dispatch_validation_gate(revision, validations, state, service, attempt, current, repository)
    if outcome is not _CONTINUE:
        return outcome

    review_count = required_review_count(current.spec, state)
    if review_count <= 0:
        service._set_quality_stage(
            repository,
            run_id=current.run_id,
            stage="acceptance",
            attempt=attempt,
            task_revision_id=revision.revision_id,
            workspace_state_id=state.state_id,
        )
        service._finalize_acceptance(repository, current)
        return None

    return _start_quality_reviews(current, repository, revision, service, state, validations, quality, attempt, review_count)


def _accept_without_quality_phases(service, repository, current, attempt, revision, state):
    """With the validation and review phases off, go straight to final acceptance."""
    if not CODING_VALIDATION_PHASE_ENABLED and not CODING_INDEPENDENT_REVIEW_PHASE_ENABLED:
        # Pi already performed the engineering loop and self-review.
        # Final acceptance still performs deterministic scope, diff,
        # evidence, and required-check enforcement.
        service._set_quality_stage(
            repository,
            run_id=current.run_id,
            stage="acceptance",
            attempt=attempt,
            task_revision_id=revision.revision_id,
            workspace_state_id=state.state_id,
            reason="coding_quality_phases_disabled",
        )
        service._finalize_acceptance(repository, current)
        return None
    return _CONTINUE


def _pre_review_gates(current, repository, revision, service, state, quality, attempt, stage):
    """Validation and implementation gates the candidate must pass before verification."""
    service._capture_diff(repository, current.spec, task_revision_id=revision.revision_id, workspace_state_id=state.state_id)
    artifacts = repository.list_artifacts(current.run_id)
    diff_artifact = next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.kind == "diff"
            and artifact.metadata.get("task_revision_id") == revision.revision_id
        ),
        None,
    )
    service._reconcile_change_set_validation(
        repository,
        current,
        revision,
        quality,
        workspace_state_id=state.state_id,
    )
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    validation_gate, validation_details = candidate_validation_gate(
        revision,
        validations,
        workspace_state_id=state.state_id,
    )
    if validation_gate == "validation_repair":
        return service._request_validation_repair(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, failures=validation_details,
        )
    if validation_gate == "validation_retry":
        return service._request_validation_retry(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, failures=validation_details,
        )
    if validation_gate == "validation_missing":
        return service._request_validation_execution(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, missing=validation_details,
        )
    gate, gate_details = _pre_review_gate(
        revision,
        validations,
        workspace_state_id=state.state_id,
        diff_artifact=diff_artifact,
    )
    if gate == "implementing":
        return service._request_implementation_continuation(
            repository,
            current,
            revision,
            attempt=attempt,
            workspace_state_id=state.state_id,
            failures=[str(item) for item in gate_details],
            prior_stage=stage,
        )
    return _CONTINUE


def _settle_self_review(stage, repository, current, attempt, revision, state, service, quality):
    """Record the self-review of the exact state; repair when it is not approved."""
    if stage == "self_review":
        text = _self_review_response_from_repository(
            repository,
            run_id=current.run_id,
            attempt=attempt,
            task_revision_id=revision.revision_id,
            workspace_state_id=state.state_id,
        )
        if not _self_review_payload_is_protocol_valid(text, revision):
            return service._request_self_review_protocol_retry(
                repository,
                current,
                revision,
                quality,
                attempt=attempt,
                workspace_state_id=state.state_id,
                response_text=text,
            )
        self_review = parse_self_review_result(
            text,
            run_id=current.run_id,
            revision=revision,
            workspace_state_id=state.state_id,
        )
        quality.add_self_review_result(self_review)
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="quality.self_review_completed",
                payload={
                    "attempt": attempt,
                    "self_review_result_id": self_review.self_review_result_id,
                    "verdict": self_review.verdict,
                    "requirements": [item.model_dump(mode="json") for item in self_review.requirements],
                    "findings": [item.model_dump(mode="json") for item in self_review.findings],
                    "missing_tests": list(self_review.missing_tests),
                    "residual_risks": list(self_review.residual_risks),
                    "task_revision_id": revision.revision_id,
                    "workspace_state_id": state.state_id,
                },
            )
        )
        if not self_review_is_acceptable(self_review, revision):
            return service._request_quality_repair(
                repository,
                current,
                revision,
                self_review,
                failures=["quality_self_review_not_approved"],
            )
        if CODING_VALIDATION_PHASE_ENABLED or CODING_INDEPENDENT_REVIEW_PHASE_ENABLED:
            service._set_quality_stage(
                repository,
                run_id=current.run_id,
                stage="validating",
                attempt=attempt,
                task_revision_id=revision.revision_id,
                workspace_state_id=state.state_id,
            )
    return _CONTINUE


def _reconciled_validations(quality, current, revision, service, repository, state):
    """Validation results for the revision after reconciling the run change set."""
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    service._reconcile_change_set_validation(
        repository,
        current,
        revision,
        quality,
        workspace_state_id=state.state_id,
    )
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    return validations


def _dispatch_validation_gate(revision, validations, state, service, attempt, current, repository):
    """Repair, retry or run missing validation before reviews."""
    validation_gate, validation_details = candidate_validation_gate(
        revision,
        validations,
        workspace_state_id=state.state_id,
    )
    if validation_gate == "validation_repair":
        return service._request_validation_repair(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, failures=validation_details,
        )
    if validation_gate == "validation_retry":
        return service._request_validation_retry(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, failures=validation_details,
        )
    if validation_gate == "validation_missing":
        return service._request_validation_execution(
            repository, current, revision, attempt=attempt,
            workspace_state_id=state.state_id, missing=validation_details,
        )
    return _CONTINUE


def _start_quality_reviews(current, repository, revision, service, state, validations, quality, attempt, review_count):
    """Snapshot the exact candidate for reviewers and wait for the review children."""
    service._capture_diff(repository, current.spec, task_revision_id=revision.revision_id, workspace_state_id=state.state_id)
    artifacts = repository.list_artifacts(current.run_id)
    diff_artifact = next(
        (
            artifact
            for artifact in reversed(artifacts)
            if artifact.kind == "diff"
            and artifact.metadata.get("task_revision_id") == revision.revision_id
            and artifact.metadata.get("workspace_state_id") == state.state_id
        ),
        None,
    )
    change_set = run_change_set_from_artifact(diff_artifact)
    if change_set is None:
        service._quality_fail(repository, current, "quality_run_change_set_unavailable")
        return None
    review_root = _env_str(
        "OMNIX_AGENT_REVIEW_ROOT",
        _default_review_root(current.spec),
    )
    review_workspace = materialize_review_workspace(
        current.spec,
        state,
        review_root=review_root,
    )
    _, guidance_digest = compile_repository_guidance(
        current.spec.workspace,
        objective=revision.effective_objective,
        relevant_paths=state.modified_paths,
    )
    current_validation_ids = [
        item.result_id
        for item in validations
        if item.workspace_state_id == state.state_id and item.success
    ]
    review_snapshot = ReviewSnapshot(
        run_id=current.run_id,
        task_revision_id=revision.revision_id,
        workspace_state_id=state.state_id,
        base_commit_sha=state.base_commit_sha,
        patch_checksum=change_set.patch_checksum,
        patch_storage_ref=change_set.patch_storage_ref,
        run_change_set_id=change_set.change_set_id,
        workspace_root=review_workspace.root,
        subject_paths=list(change_set.run_owned_paths),
        context_paths=list(change_set.baseline_context_paths),
        relevant_files=relevant_file_candidates(revision, state),
        validation_result_ids=current_validation_ids,
        repository_guidance_digest=guidance_digest,
    )
    quality.add_review_snapshot(review_snapshot)
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="reviewing",
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=state.state_id,
    )
    latest = repository.get_run(current.run_id) or current
    repository.update_state(
        current.run_id,
        expected_revision=latest.revision,
        status="waiting_for_children",
        worker_id=service.worker_id,
    )
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="quality.review_started",
            payload={
                "attempt": attempt,
                "review_snapshot_id": review_snapshot.snapshot_id,
                "task_revision_id": revision.revision_id,
                "workspace_state_id": state.state_id,
                "reviewer_count": review_count,
            },
        )
    )
    return ("launch_reviews", current.run_id, review_snapshot.snapshot_id, review_count)


def _execute_quality_action(service: AgentRunService, action: tuple | None) -> None:
    if not action:
        return
    if action[0] == "dispatch_command":
        _, command = action
        try:
            service.command(command)
        except Exception as exc:
            log_recovered_exception("quality command dispatch", exc)
            pass
        return
    if action[0] == "launch_reviews":
        _, parent_run_id, snapshot_id, count = action
        service._launch_reviewer_children(parent_run_id, snapshot_id, int(count))


def _dispatch_pending_quality_commands(service: AgentRunService, run_id: str, *, include_parent: bool = False) -> None:
    targets: list[str] = []
    pending: list[AgentRunCommand] = []
    try:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            snapshot = repository.get_run(run_id)
            if snapshot is not None:
                targets.append(snapshot.run_id)
                if include_parent and snapshot.spec.parent_run_id:
                    targets.append(snapshot.spec.parent_run_id)
            for target in dict.fromkeys(targets):
                for command in repository.list_pending_commands(target):
                    if (
                        command.command_type == "resume"
                        and str(command.payload.get("quality_stage") or "")
                        in {"implementing", "repairing", "validating", "self_review"}
                    ):
                        pending.append(command)
            work.rollback()
    except Exception as exc:
        log_recovered_exception("pending quality command lookup", exc)
        return

    seen: set[str] = set()
    for command in pending:
        if command.command_id in seen:
            continue
        seen.add(command.command_id)
        service._execute_quality_action(("dispatch_command", command))


def _launch_reviewer_children(service: AgentRunService, parent_run_id: str, snapshot_id: str, count: int) -> None:
    launch_reviewer_children(service, parent_run_id, snapshot_id, count)


def _review_snapshot_id_from_child(child: AgentRunSnapshot) -> str | None:
    return review_snapshot_id_from_child(child)


def _review_result_from_child(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    child: AgentRunSnapshot,
    snapshot: ReviewSnapshot,
) -> ReviewResult | None:
    """Compatibility reader that returns substantive review evidence only.

    Runtime/protocol failure is represented by ReviewAttempt and therefore
    returns ``None`` here instead of fabricating a blocked ReviewResult.
    """

    if child.status != "completed":
        return None
    revision = service._current_revision(
        repository,
        child.spec.parent_run_id or snapshot.run_id,
    )
    if revision is None or revision.revision_id != snapshot.task_revision_id:
        return None
    text = latest_reviewer_text(
        events_of_types(repository, child.run_id, {"model.message"})
    )
    if not review_payload_is_protocol_valid(text, revision):
        return None
    result = parse_review_result(
        text,
        parent_run_id=child.spec.parent_run_id or snapshot.run_id,
        reviewer_run_id=child.run_id,
        snapshot=snapshot,
    )
    deterministic_result_id = hashlib.sha256(
        f"review-result:{child.run_id}:{snapshot.snapshot_id}".encode("utf-8")
    ).hexdigest()
    return result.model_copy(update={"review_result_id": deterministic_result_id})


def _request_quality_workspace_refresh(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    *,
    current_workspace_state_id: str,
    prior_workspace_state_id: str,
) -> tuple | None:
    """Refresh exact-state quality evidence without consuming a repair attempt.

    A workspace can change after independent review because the checkout was
    updated or another authorized actor changed it. That invalidates the old
    validation/self-review/review evidence, but it is not itself substantive
    evidence that the implementation is wrong. Revalidate the new exact state
    on the same quality attempt; any later real finding may still request the
    bounded repair loop.
    """

    quality = service.quality_repository_factory(repository.connection, service.context)
    stage = quality.get_stage(current.run_id) or {"attempt": 1}
    attempt = max(1, int(stage.get("attempt") or 1))
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    missing = missing_final_validations(
        revision,
        validations,
        workspace_state_id=current_workspace_state_id,
    )
    if not missing:
        missing = [item for item in revision.validation_plan if item.required]
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="validating",
        attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=current_workspace_state_id,
        reason="workspace_changed_after_independent_review",
    )
    latest = repository.get_run(current.run_id) or current
    if latest.status != "running":
        repository.update_state(
            current.run_id,
            expected_revision=latest.revision,
            status="running",
            desired_state="running",
            worker_id=service.worker_id,
            last_error=None,
        )
    prompt = validation_prompt(revision, missing)
    prompt += (
        "\n\nOmnix detected that the workspace changed after the prior independent review. "
        f"Reviewed workspace state: {prior_workspace_state_id}. Current workspace state: "
        f"{current_workspace_state_id}. The prior validation, self-review, and independent review are "
        "stale by exact-state identity, but this is not a substantive implementation defect and does "
        "not consume a quality repair attempt. Do not mutate merely to make the state IDs match. "
        "Validate the current state against the authoritative task. If validation proves a real "
        "implementation change is needed, continue the normal Pi repair loop; ordinary in-scope edits "
        "do not require a PlanDelta. Otherwise finish the requested validation so Omnix can independently "
        "review this exact current state."
    )
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=(
            f"quality-workspace-refresh:{current.run_id}:{revision.revision_id}:"
            f"{current_workspace_state_id}:{attempt}"
        ),
        quality_stage="validating",
        quality_attempt=attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=current_workspace_state_id,
    )


def _request_quality_repair(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    revision: TaskRevision,
    review: ReviewResult | SelfReviewResult | None,
    *,
    failures: list[str],
) -> tuple | None:
    quality = service.quality_repository_factory(repository.connection, service.context)
    stage = quality.get_stage(current.run_id) or {"attempt": 1, "workspace_state_id": None}
    attempt = max(1, int(stage.get("attempt") or 1))
    if attempt >= quality_attempt_limit():
        latest = repository.get_run(current.run_id) or current
        repository.update_state(
            current.run_id,
            expected_revision=latest.revision,
            status="failed",
            desired_state="cancelled",
            last_error=("quality_failed:" + ",".join(failures))[:2000],
        )
        return None
    next_attempt = attempt + 1
    state_id = stage.get("workspace_state_id")
    validations = quality.list_validation_results(
        current.run_id,
        task_revision_id=revision.revision_id,
    )
    missing = (
        missing_final_validations(
            revision,
            validations,
            workspace_state_id=str(state_id or ""),
        )
        if state_id
        else list(revision.validation_plan)
    )
    prompt = repair_prompt(
        revision,
        review,
        missing,
        attempt=next_attempt,
    )
    if failures:
        prompt += "\nOmnix acceptance/quality failures: " + ", ".join(failures)
    service._set_quality_stage(
        repository,
        run_id=current.run_id,
        stage="repairing",
        attempt=next_attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=str(state_id) if state_id else None,
        reason="quality_gate_requires_repair",
    )
    repository.append_event(
        AgentEvent(
            run_id=current.run_id,
            event_type="quality.repair_requested",
            payload={
                "attempt": next_attempt,
                "failures": failures,
                "task_revision_id": revision.revision_id,
                "workspace_state_id": state_id,
            },
        )
    )
    latest = repository.get_run(current.run_id) or current
    if latest.status != "running":
        repository.update_state(
            current.run_id,
            expected_revision=latest.revision,
            status="running",
            desired_state="running",
            worker_id=service.worker_id,
            last_error=None,
        )
    return service._queue_quality_resume(
        repository,
        run_id=current.run_id,
        prompt=prompt,
        idempotency_key=(
            f"quality-repair:{current.run_id}:{revision.revision_id}:{next_attempt}:"
            f"{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:16]}"
        ),
        quality_stage="repairing",
        quality_attempt=next_attempt,
        task_revision_id=revision.revision_id,
        workspace_state_id=str(state_id) if state_id else None,
    )


def _quality_fail(
    service: AgentRunService,
    repository: PostgresAgentRunRepository,
    current: AgentRunSnapshot,
    reason: str,
) -> None:
    log_agent_activity(
        "quality.failed",
        category="quality",
        level="error",
        run_id=current.run_id,
        fields={"reason": reason, "status_before": current.status, "revision": current.revision},
    )
    latest = repository.get_run(current.run_id) or current
    repository.update_state(
        current.run_id,
        expected_revision=latest.revision,
        status="failed",
        desired_state="cancelled",
        last_error=reason[:2000],
    )
    return None
