"""Run steering: commands, authority validation and superseding task revisions (WP-8.2).

Functions over the run service; ``AgentRunService`` keeps one delegator per
function, so callers and tests that patch the service methods are unchanged.
"""
from __future__ import annotations

from .profiles import profile_produces_diff
from .exception_logging import log_recovered_exception
import hashlib
import os
from typing import Any
from app.capabilities import default_capability_registry
from .active_objective import RoutingEnvironment, make_active_objective
from .evidence import (
    EvidenceCompilationError,
    compile_task_authority,
    task_requires_workspace_mutation,
    validate_required_evidence_capabilities,
)
from .profiles import get_agent_profile, resolve_profile_capabilities
from .budget import apply_default_run_limits
from .contracts import (
    AgentEvent,
    ArtifactKind,
    AgentRunCommand,
    AgentRunSnapshot,
    AgentRunSpec,
    EvidenceDecision,
    SuccessCriterion,
    TaskRevision,
)
from app.observability.agent_logging import log_agent_activity
from .semantic_task_parser import (
    classify_semantic_task_safely,
)
from .turn_plan import TurnPlan, compile_turn_plan, derive_effective_objective
from typing import TYPE_CHECKING
from .run_repository_queries import PostgresAgentRunQueries

if TYPE_CHECKING:
    from app.platform.agent_runtime.service_core import AgentRunService


def command_with_context(
    service: AgentRunService,
    command: AgentRunCommand,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
    turn_plan: TurnPlan | None = None,
) -> AgentRunSnapshot:
    """Apply a command while keeping conversational context ephemeral."""

    log_agent_activity(
        "service.command.requested",
        category="service",
        run_id=command.run_id,
        fields={
            "command_id": command.command_id,
            "command_type": command.command_type,
            "payload": command.payload,
        },
    )
    if command.command_type == "steer":
        try:
            current = service.get(command.run_id)
            if current is None:
                raise KeyError(command.run_id)
            steering = service._compile_steering(
                current,
                command,
                reference_context=reference_context,
                turn_plan=turn_plan,
            )
        except Exception as exc:
            log_agent_activity(
                "service.command.steering_failed",
                category="service",
                level="error",
                run_id=command.run_id,
                fields={"command_id": command.command_id},
                error=exc,
                include_traceback=True,
            )
            raise
        if steering["superseding_spec"] is not None:
            return service._start_superseding_revision(
                current,
                command,
                steering["revision"],
                steering["superseding_spec"],
                reference_context=reference_context,
                **({"reference_images": reference_images} if reference_images else {}),
            )
        revision = steering["revision"]
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            repository.add_task_revision(revision)
            work.commit()
        command = command.model_copy(update={
            "payload": {
                **command.payload,
                "task_revision_id": revision.revision_id,
                "effective_objective": revision.effective_objective,
                "evidence_policy": revision.evidence_decision.policy.model_dump(mode="json"),
            }
        })
    locally_hosted = service._runtime_owns_run(command.run_id)
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        stored, status = repository.enqueue_command_with_status(command)
        current = repository.get_run(command.run_id)
        if current is None:
            raise KeyError(command.run_id)
        if current.status in {"completed", "failed", "cancelled"}:
            if status != "consumed" and repository.claim_command(
                command.run_id,
                stored.command_id,
            ):
                repository.complete_command(
                    command.run_id,
                    stored.command_id,
                )
            work.commit()
            log_agent_activity(
                "service.command.terminal_short_circuit",
                category="service",
                run_id=command.run_id,
                fields={
                    "command_id": stored.command_id,
                    "command_type": stored.command_type,
                    "status": current.status,
                },
            )
            return current
        if status == "consumed":
            work.commit()
            log_agent_activity(
                "service.command.already_consumed",
                category="service",
                run_id=command.run_id,
                fields={"command_id": stored.command_id, "status": status},
            )
            return current
        if not locally_hosted:
            work.commit()
            return current
        if not repository.claim_command(command.run_id, stored.command_id):
            work.commit()
            return current
        work.commit()

    return _run_claimed_command(service, stored, reference_context, reference_images)


def _run_claimed_command(service, stored, reference_context, reference_images):
    """Apply a claimed command under the run lock, then cascade cancellation and parent completion."""
    try:
        # Runtime callbacks persist status changes from the Pi reader
        # thread. Keep command-side desired-state changes and the
        # corresponding runtime transition in the same critical section
        # so a callback cannot advance the durable revision between our
        # read and optimistic update.
        with service._run_lock(stored.run_id):
            current = service._apply_claimed_command(
                stored,
                reference_context=reference_context,
                **({"reference_images": reference_images} if reference_images else {}),
            )
    except Exception as exc:
        log_agent_activity(
            "service.command.failed",
            category="service",
            level="error",
            run_id=stored.run_id,
            fields={
                "command_id": stored.command_id,
                "command_type": stored.command_type,
            },
            error=exc,
            include_traceback=True,
        )
        service._mark_command_failed(stored, exc)
        raise
    else:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            repository.complete_command(stored.run_id, stored.command_id)
            work.commit()

    if stored.command_type == "cancel":
        service._cancel_descendants(stored.run_id)
    if current.status in {"completed", "failed", "cancelled"}:
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            service._maybe_finalize_parent_in_repository(repository, stored.run_id)
            work.commit()
    result = service.get(stored.run_id) or current
    log_agent_activity(
        "service.command.completed",
        category="service",
        run_id=stored.run_id,
        fields={
            "command_id": stored.command_id,
            "command_type": stored.command_type,
            "status": result.status,
            "desired_state": result.desired_state,
            "revision": result.revision,
        },
    )
    return result


def _validate_run_spec_authority(spec: AgentRunSpec) -> None:
    """Treat the durable service boundary as the final authority compiler."""
    profile = get_agent_profile(spec.profile)
    try:
        resolve_profile_capabilities(
            profile,
            requested=list(spec.capabilities),
            requested_external=list(spec.external_capabilities),
        )
    except ValueError as exc:
        raise EvidenceCompilationError(
            "run_spec_exceeds_profile_ceiling",
            str(exc),
        ) from exc
    if profile.requires_workspace and spec.workspace is None:
        raise EvidenceCompilationError(
            "required_workspace_unavailable",
            f"profile {profile.id} requires an explicitly issued workspace",
        )
    if not profile.requires_workspace and spec.workspace is not None:
        raise EvidenceCompilationError(
            "workspace_outside_profile_ceiling",
            f"profile {profile.id} does not permit local workspace authority",
        )

    registry = default_capability_registry()
    issued = set(spec.capabilities) | set(spec.external_capabilities)
    for scope in spec.resource_scopes:
        canonical = registry.canonical_id(scope.capability)
        if canonical is None:
            raise EvidenceCompilationError(
                "unknown_resource_scope_capability",
                f"resource scope references unknown capability {scope.capability}",
            )
        if canonical not in issued:
            raise EvidenceCompilationError(
                "resource_scope_outside_run_authority",
                f"resource scope {scope.capability} is not issued to this run",
            )


def _validate_evidence_authority(service: AgentRunService, spec: AgentRunSpec) -> None:
    if spec.evidence_policy.requirement != "required":
        return
    profile = get_agent_profile(spec.profile)
    decision = EvidenceDecision(
        policy=spec.evidence_policy,
        confidence=1.0,
        reason="run_spec_validation",
        classifier="deterministic",
    )
    compiled = compile_task_authority(profile, spec.objective or spec.task, decision)
    issued = set(spec.external_capabilities)
    missing_groups = [
        group
        for group in compiled.external_groups
        if not issued.intersection(group)
    ]
    if missing_groups:
        raise EvidenceCompilationError(
            "evidence_required_but_unavailable",
            "RunSpec does not issue any permitted capability for required evidence: "
            + "; ".join(",".join(group) for group in missing_groups),
        )
    issued_groups = tuple(
        tuple(cap for cap in group if cap in issued)
        for group in compiled.external_groups
        if issued.intersection(group)
    )
    grouped_evidence_caps = {
        cap
        for group in compiled.external_groups
        for cap in group
    }
    evidence_caps = tuple(
        cap
        for cap in compiled.required_external
        if cap in issued and cap in grouped_evidence_caps
    )
    validate_required_evidence_capabilities(
        evidence_caps,
        alternative_groups=issued_groups,
    )


def _compile_steering(
    service: AgentRunService,
    current: AgentRunSnapshot,
    command: AgentRunCommand,
    *,
    reference_context: str = "",
    turn_plan: TurnPlan | None = None,
) -> dict[str, Any]:
    message = str(command.payload.get("message") or "").strip()
    reference_context = str(reference_context or "").strip()
    if not message:
        raise ValueError("steering message is required")
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        revisions = repository.list_task_revisions(current.run_id)
        work.rollback()
    latest, previous_objective, prior_request, routing_environment = _steering_context(revisions, current)

    semantic_compilation, turn_plan = _steering_turn_plan(turn_plan, message, current, prior_request, service, reference_context, previous_objective, routing_environment)

    effective = derive_effective_objective(
        previous_objective,
        turn_plan,
    )
    # Compile policy from the TurnPlan's latest authoritative request only.
    # Previous objective remains reference-only and cannot widen authority.
    if semantic_compilation.requires_clarification:
        detail = "; ".join(
            anomaly.detail
            for anomaly in semantic_compilation.anomalies
        )
        raise EvidenceCompilationError(
            "semantic_clarification_required",
            detail or "steering has multiple plausible execution targets",
        )

    target_profile_id = semantic_compilation.profile_id or current.spec.profile
    target_profile = get_agent_profile(target_profile_id)
    decision = semantic_compilation.evidence_decision
    semantic_actions = list(semantic_compilation.action_intents)

    if (
        current.spec.workspace is not None
        and current.spec.workspace.repository
        and any(r.source_class in {"repo_ci_state", "repo_contents"} for r in decision.policy.requirements)
    ):
        repository_name = service._github_origin_repository(current.spec.workspace.repository)
        decision = decision.model_copy(update={
            "policy": service._bind_repository_evidence_policy(
                decision.policy,
                workspace=current.spec.workspace,
                repository_name=repository_name,
            )
        })
    compiled = compile_task_authority(
        target_profile,
        turn_plan.effective_request,
        decision,
        semantic_action_intents=semantic_actions,
        allow_text_semantic_fallback=False,
    )
    required_local = set(compiled.required_local)
    required_external = set(compiled.required_external)
    issued_local = set(current.spec.capabilities)
    issued_external = set(current.spec.external_capabilities)
    fits = (
        target_profile_id == current.spec.profile
        and required_local.issubset(issued_local)
        and required_external.issubset(issued_external)
    )
    expected_artifacts: list[ArtifactKind] = (
        ["diff"]
        if profile_produces_diff(target_profile_id)
        and task_requires_workspace_mutation(
            turn_plan.effective_request,
            semantic_action_intents=semantic_actions,
            allow_text_semantic_fallback=False,
        )
        else []
    )
    checks = ["successful_test_command"] if expected_artifacts else []
    sequence = (latest.sequence + 1) if latest is not None else 2
    digest = hashlib.sha256(
        f"{current.run_id}:{command.idempotency_key}".encode("utf-8")
    ).hexdigest()
    revision = TaskRevision(
        revision_id=digest,
        run_id=current.run_id,
        sequence=sequence,
        previous_revision_id=latest.revision_id if latest else None,
        source_command_id=command.idempotency_key,
        user_instruction=message,
        effective_objective=effective,
        effective_success_criteria=[
            SuccessCriterion(
                id="user-request",
                description="Complete the latest effective user task and report verifiable evidence.",
            )
        ],
        evidence_decision=decision,
        required_local_capabilities=list(compiled.required_local),
        required_external_capabilities=list(compiled.required_external),
        expected_artifacts=expected_artifacts,
        acceptance_checks=checks,
    )
    if fits:
        return {"revision": revision, "superseding_spec": None}

    return _superseding_steering(current, target_profile, target_profile_id, command, turn_plan, effective, compiled, decision, expected_artifacts, revision)


def _steering_context(revisions, current):
    """The objective a steer revises, the latest request that changed it, and the run's routing environment."""
    latest = revisions[-1] if revisions else None
    previous_objective = (
        latest.effective_objective
        if latest is not None
        else (current.spec.objective or current.spec.task)
    )
    # Reconstruct the latest user instruction that actually changed
    # executable objective authority. Response-only and replay revisions
    # intentionally leave effective_objective unchanged and must not become
    # the replay target for a later direct/API steering command.
    prior_request = current.spec.task
    prior_effective = str(current.spec.objective or current.spec.task)
    for revision in revisions:
        if revision.effective_objective != prior_effective:
            prior_request = revision.user_instruction
        prior_effective = revision.effective_objective
    workspace_name = None
    if current.spec.workspace is not None:
        workspace_name = os.path.basename(
            str(current.spec.workspace.root or "").rstrip("\\/")
        ) or None
    routing_environment = RoutingEnvironment(
        active_workspace=workspace_name,
        workspace_source=("configured_default" if workspace_name else "none"),
        workspace_attached_this_turn=False,
    )
    return latest, previous_objective, prior_request, routing_environment


def _steering_turn_plan(turn_plan, message, current, prior_request, service, reference_context, previous_objective, routing_environment):
    """The TurnPlan for a steering message: Chat's trusted plan after identity checks, or one semantic parse here."""
    if turn_plan is not None:
        # A TurnPlan passed through this keyword-only in-process boundary is
        # compiler output from Chat, not user command payload. Validate its
        # identity before using it, then compile authority again below.
        if turn_plan.latest_request != message:
            raise EvidenceCompilationError(
                "turn_plan_message_mismatch",
                "trusted TurnPlan does not match the steering message",
            )
        if turn_plan.active_run_id not in {None, current.run_id}:
            raise EvidenceCompilationError(
                "turn_plan_run_mismatch",
                "trusted TurnPlan targets a different Agent run",
            )
        if turn_plan.run_action != "steer_agent":
            raise EvidenceCompilationError(
                "turn_plan_action_mismatch",
                f"trusted TurnPlan cannot steer this run: {turn_plan.run_action}",
            )
        semantic_task = turn_plan.semantic_task
        semantic_compilation = turn_plan.compilation
    else:
        # Direct/non-Chat command callers have no trusted plan, so the
        # durable service performs the semantic parse exactly once here.
        active_objective = make_active_objective(
            canonical_request=prior_request,
            base_request=current.spec.task,
            profile=current.spec.profile,
            status="active",
            run_id=current.run_id,
        )
        semantic_task = classify_semantic_task_safely(
            service.semantic_task_parser(
                provider_id=current.spec.model.provider_id,
                model_id=current.spec.model.model_id,
            ),
            message,
            reference_context=reference_context,
            previous_objective=previous_objective,
            current_environment=routing_environment.model_dump(mode="json"),
        )
        if semantic_task is None:
            raise EvidenceCompilationError(
                "semantic_parser_unavailable",
                "steering requires semantic parsing; Omnix will not guess a stateful domain",
            )
        turn_plan = compile_turn_plan(
            message,
            semantic_task,
            active_objective=active_objective,
            routing_environment=routing_environment,
        )
        semantic_task = turn_plan.semantic_task
        semantic_compilation = turn_plan.compilation
    return semantic_compilation, turn_plan


def _superseding_steering(current, target_profile, target_profile_id, command, turn_plan, effective, compiled, decision, expected_artifacts, revision):
    """A steer that needs more authority than the run holds starts a superseding run with exactly that authority."""
    workspace = current.spec.workspace
    if target_profile.requires_workspace and workspace is None:
        raise EvidenceCompilationError(
            "required_workspace_unavailable",
            f"steering requires profile {target_profile_id}, but this run has no issued workspace",
        )
    replacement_run_id = hashlib.sha256(
        f"supersede:{current.run_id}:{command.idempotency_key}".encode("utf-8")
    ).hexdigest()
    replacement = AgentRunSpec(
        run_id=replacement_run_id,
        session_id=current.spec.session_id,
        task=turn_plan.effective_request,
        objective=effective,
        profile=target_profile_id,
        model=current.spec.model,
        capabilities=list(compiled.required_local),
        external_capabilities=list(compiled.required_external),
        context_sources=list(target_profile.context_sources),
        workspace=workspace if target_profile.requires_workspace else None,
        execution=current.spec.execution,
        limits=current.spec.limits,
        approval_policy=current.spec.approval_policy,
        request_mode=current.spec.request_mode,
        evidence_policy=decision.policy,
        supersedes_run_id=current.run_id,
        success_criteria=[
            SuccessCriterion(
                id="user-request",
                description="Complete the latest effective user task and report verifiable evidence.",
            )
        ],
        expected_artifacts=expected_artifacts,
    )
    return {"revision": revision, "superseding_spec": replacement}


def _start_superseding_revision(
    service: AgentRunService,
    current: AgentRunSnapshot,
    command: AgentRunCommand,
    revision: TaskRevision,
    replacement_spec: AgentRunSpec,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
) -> AgentRunSnapshot:
    """Atomically reserve a superseding run and its steering audit trail."""
    if service.job_store is not None:
        return service._submit_superseding_revision(
            current,
            command,
            revision,
            replacement_spec,
        )
    service._validate_run_spec_authority(replacement_spec)
    service._validate_evidence_authority(replacement_spec)
    issued = service._prepare_workspace(
        service._bind_github_repository_authority(replacement_spec)
    )

    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        locked = PostgresAgentRunQueries(work.connection, service.context).lock_superseding_run_id(current.run_id).fetchone()
        if locked is None:
            raise KeyError(current.run_id)
        existing_replacement_id = str(locked[0]) if locked[0] else None
        if existing_replacement_id:
            replacement = repository.get_run(existing_replacement_id)
            if replacement is None:
                raise RuntimeError("superseding run link points to missing run")
            work.rollback()
            return replacement

        stored, command_status = repository.enqueue_command_with_status(command)
        repository.add_task_revision(revision)
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="steering.received",
                payload={
                    "command_id": stored.command_id,
                    "idempotency_key": stored.idempotency_key,
                    "task_revision_id": revision.revision_id,
                    "superseding_run_id": issued.run_id,
                },
            )
        )
        if command_status != "consumed" and repository.claim_command(
            current.run_id,
            stored.command_id,
        ):
            repository.complete_command(current.run_id, stored.command_id)

        snapshot = service._persist_starting_run(repository, issued)
        repository.mark_superseded(current.run_id, issued.run_id)
        work.commit()

    service.runtime.close_run(current.run_id)
    if reference_context or reference_images:
        return service._launch_runtime(
            issued,
            snapshot,
            reference_context=reference_context,
            reference_images=reference_images,
        )
    return service._launch_runtime(issued, snapshot)


def _submit_superseding_revision(
    service: AgentRunService,
    current: AgentRunSnapshot,
    command: AgentRunCommand,
    revision: TaskRevision,
    replacement_spec: AgentRunSpec,
) -> AgentRunSnapshot:
    """Persist the replacement and hand workspace/runtime work to jobs."""
    from .jobs import create_agent_workspace_prepare_request, enqueue_agent_job

    issued = apply_default_run_limits(service._prepare_start_spec(replacement_spec))
    service._validate_run_spec_authority(issued)
    service._validate_evidence_authority(issued)
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        locked = PostgresAgentRunQueries(work.connection, service.context).lock_superseding_run_id(current.run_id).fetchone()
        if locked is None:
            raise KeyError(current.run_id)
        existing_id = str(locked[0]) if locked[0] else None
        if existing_id:
            existing = repository.get_run(existing_id)
            if existing is None:
                raise RuntimeError("superseding run link points to missing run")
            work.rollback()
            return existing
        stored, status = repository.enqueue_command_with_status(command)
        repository.add_task_revision(revision)
        repository.append_event(
            AgentEvent(
                run_id=current.run_id,
                event_type="steering.received",
                payload={
                    "command_id": stored.command_id,
                    "idempotency_key": stored.idempotency_key,
                    "task_revision_id": revision.revision_id,
                    "superseding_run_id": issued.run_id,
                },
            )
        )
        if status != "consumed" and repository.claim_command(
            current.run_id,
            stored.command_id,
        ):
            repository.complete_command(current.run_id, stored.command_id)
        snapshot = repository.create_run(issued)
        repository.mark_superseded(current.run_id, issued.run_id)
        work.commit()
    enqueue_agent_job(
        service.job_store,
        create_agent_workspace_prepare_request(issued.run_id),
        idempotency_key=f"run:{issued.run_id}:workspace-prepare",
    )
    return snapshot


def _mark_command_failed(service: AgentRunService, command: AgentRunCommand, error: Exception) -> None:
    """Make transport/runtime command failures visible and terminal.

    A command updates desired state before it reaches the local runtime. If
    the runtime process has already exited, leaving that intermediate state
    durable makes runs appear permanently paused or cancellation-pending.
    """
    log_agent_activity(
        "service.command.failure_state_persisting",
        category="service",
        level="error",
        run_id=command.run_id,
        fields={"command_id": command.command_id, "command_type": command.command_type},
        error=error,
    )
    service.runtime.close_run(command.run_id)
    terminal_status = "cancelled" if command.command_type == "cancel" else "failed"
    desired_state = "cancelled"
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(command.run_id)
        if current is not None and current.status not in {"completed", "failed", "cancelled"}:
            repository.update_state(
                command.run_id,
                expected_revision=current.revision,
                status=terminal_status,
                desired_state=desired_state,
                worker_id=service.worker_id,
                last_error=f"command_failed:{type(error).__name__}: {error}"[:2000],
            )
        repository.complete_command(command.run_id, command.command_id)
        work.commit()
    if command.command_type == "cancel":
        service._cancel_descendants(command.run_id)


def _apply_claimed_command(
    service: AgentRunService,
    stored: AgentRunCommand,
    *,
    reference_context: str = "",
    reference_images: list[dict[str, str]] | None = None,
) -> AgentRunSnapshot:
    runtime_command = stored
    approval_request = None
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        current = repository.get_run(stored.run_id)
        if current is None:
            raise KeyError(stored.run_id)
        desired = current.desired_state
        status = current.status
        if stored.command_type in {"approve", "reject"}:
            approval_id = str(stored.payload.get("approval_id") or "")
            if not approval_id:
                raise ValueError("approval_id is required")
            approval = repository.get_approval(stored.run_id, approval_id)
            if approval is None:
                raise KeyError(approval_id)
            resolution: dict[str, Any] = {"source": "agent_run_command"}
            if stored.payload.get("issued_by"):
                resolution["decided_by"] = str(stored.payload["issued_by"])
            repository.resolve_approval(
                stored.run_id,
                approval_id,
                approved=stored.command_type == "approve",
                resolution_payload=resolution,
            )
            approval_request = approval.request_payload
            desired, status = "running", "running"
        elif stored.command_type == "pause":
            desired, status = "paused", "pause_requested"
        elif stored.command_type == "resume":
            desired, status = "running", "resume_requested"
        elif stored.command_type == "cancel":
            desired, status = "cancelled", "cancel_requested"
        current = repository.update_state(
            stored.run_id,
            expected_revision=current.revision,
            status=status,
            desired_state=desired,
            worker_id=service.worker_id,
        )
        if approval_request is not None:
            runtime_command = stored.model_copy(update={
                "payload": {
                    **stored.payload,
                    "approval_request": approval_request,
                }
            })
        work.commit()

    active = service.runtime.get_status(stored.run_id)
    if active is None and stored.command_type == "resume":
        # A durable resume is executable work, not merely desired-state
        # bookkeeping. Rehydrate a missing local Pi session before
        # consuming the command so `resume_requested` cannot become a
        # permanent state with no runtime behind it.
        service.runtime.start(current.spec)
        active = service.runtime.get_status(stored.run_id)
        if active is None:
            raise RuntimeError("resume_runtime_rehydration_failed")
        runtime_command = stored.model_copy(update={
            "payload": {
                **stored.payload,
                "runtime_rehydrated": True,
            }
        })
    if (
        active is None
        and stored.command_type == "steer"
        and current.status == "waiting_for_input"
    ):
        # The Pi process is local and may have disappeared while the
        # durable run was waiting. Rehydrate it on the user's answer
        # instead of terminalizing a perfectly valid clarification wait.
        service.runtime.start(current.spec)
        active = service.runtime.get_status(stored.run_id)
    if active is not None:
        contextual_command = getattr(service.runtime, "command_with_context", None)
        def send_runtime_command() -> None:
            if (
                stored.command_type == "steer"
                and (reference_context or reference_images)
                and callable(contextual_command)
            ):
                contextual_command(
                    stored,
                    reference_context=reference_context,
                    **({"reference_images": reference_images} if reference_images else {}),
                )
            else:
                service.runtime.command(runtime_command)

        try:
            send_runtime_command()
        except Exception as exc:
            if stored.command_type != "steer" or current.status != "waiting_for_input":
                raise
            log_recovered_exception("clarification runtime rehydration", exc)
            # A stale in-memory session can still have a snapshot even
            # though its child process exited. Recreate it once and retry
            # the user's answer while the durable run remains waiting.
            service.runtime.close_run(stored.run_id)
            service.runtime.start(current.spec)
            send_runtime_command()
        runtime_status = service.runtime.get_status(stored.run_id)
        if runtime_status is not None:
            with service.unit_of_work(service.database) as work:
                repository = service.repository_factory(work.connection, service.context)
                persisted = repository.get_run(stored.run_id)
                if persisted is not None:
                    current = repository.update_state(
                        stored.run_id,
                        expected_revision=persisted.revision,
                        status=runtime_status.status,
                        desired_state=runtime_status.desired_state,
                    )
                work.commit()
    elif stored.command_type == "cancel":
        with service.unit_of_work(service.database) as work:
            repository = service.repository_factory(work.connection, service.context)
            persisted = repository.get_run(stored.run_id)
            if persisted is not None and persisted.status != "cancelled":
                current = repository.update_state(
                    stored.run_id,
                    expected_revision=persisted.revision,
                    status="cancelled",
                    desired_state="cancelled",
                )
            work.commit()
    return current


def _cancel_descendants(service: AgentRunService, run_id: str) -> None:
    with service.unit_of_work(service.database) as work:
        repository = service.repository_factory(work.connection, service.context)
        children = repository.list_children(run_id)
        work.rollback()
    for child in children:
        if child.status in {"completed", "failed", "cancelled"}:
            continue
        service.command(
            AgentRunCommand(
                run_id=child.run_id,
                command_type="cancel",
                payload={"reason": f"parent_cancelled:{run_id}"},
                idempotency_key=f"parent-cancel:{run_id}:{child.run_id}",
            )
        )
