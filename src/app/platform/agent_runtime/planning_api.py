"""Server-authoritative API for evidence-backed coding planning."""
from __future__ import annotations

import fnmatch
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.persistence.unit_of_work import unit_of_work

from .profiles import profile_produces_diff
from .budget import AgentBudgetError
from .coding_quality_repository import PostgresCodingQualityRepository
from .planning import (
    build_inspection_bundle,
    build_plan_authority,
    capture_planning_baseline,
    classify_operation_effect,
    command_target_paths,
    engineering_contract_digest,
    inspection_evidence_digest,
    operation_plan_failures,
    plan_conformance_failures,
    plan_gate_failures,
    plan_requires_hard_planning,
    planned_paths,
    planning_mode,
    planning_requirement_for_operation,
)
from .planning_contracts import (
    ImplementationPlanRevision,
    ImplementationPlanSubmission,
    PlanningDecision,
)
from .planning_repository import PostgresPlanningRepository
from .planning_review import (
    default_plan_semantic_reviewer,
    plan_semantic_review_freshness_failures,
    plan_semantic_review_gate_failures,
    plan_semantic_review_max_rounds,
    plan_semantic_review_risk_reasons,
    plan_semantic_review_required,
    review_plan_semantics_safely,
)
from .repository import PostgresAgentRunRepository
from .repository_guidance import compile_repository_guidance
from .service import default_agent_run_service

router = APIRouter(prefix="/api/agent-runs", tags=["agent-planning"])


class PlanningInspectRequest(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=16)
    paths: list[str] = Field(default_factory=list, max_length=16)


class PlanningSubmitRequest(BaseModel):
    plan: ImplementationPlanSubmission


class PlanningAuthorizeRequest(BaseModel):
    tool_name: str
    input: dict[str, Any] = Field(default_factory=dict)
    path: str | None = None
    command: str | None = None


def _load(service, run_id: str):
    snapshot = service.get(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="agent_run_not_found")
    if not profile_produces_diff(snapshot.spec.profile) or "diff" not in snapshot.spec.expected_artifacts:
        raise HTTPException(status_code=409, detail="agent_planning_not_applicable")
    return snapshot


def _current_revision(service, repository: PostgresAgentRunRepository, run_id: str):
    revision = service._current_revision(repository, run_id)  # internal runtime module
    if revision is None:
        raise HTTPException(status_code=409, detail="agent_planning_task_revision_unavailable")
    return revision


def _repository_guidance_digest(snapshot, revision, plan) -> str | None:
    if plan is None:
        return None
    _, digest = compile_repository_guidance(
        snapshot.spec.workspace,
        objective=revision.effective_objective,
        relevant_paths=planned_paths(plan),
    )
    return digest


def _semantic_review_required(mode: str, spec, plan=None) -> bool:
    return mode != "off" and plan_semantic_review_required(spec, plan)


def _planning_budget_http_exception(error: AgentBudgetError) -> HTTPException:
    """Expose terminal review-budget exhaustion using the normal run-budget shape."""

    code = str(error or "agent_budget_exhausted")[:500]
    return HTTPException(
        status_code=409,
        detail={
            "type": "agent_budget_error",
            "code": code,
            "message": code,
            "retryable": False,
            "scope": "run",
        },
        headers={
            "X-Omnix-Error-Type": "agent_budget_error",
            "X-Omnix-Error-Code": code,
            "X-Omnix-Retryable": "false",
        },
    )


def _plan_freshness_failures(
    plan,
    revision,
    evidence_digest: str,
    repository_guidance_digest: str | None = None,
    *,
    semantic_review_required: bool = False,
) -> list[str]:
    """Return authority-identity failures independently of operation effect."""

    if plan is None:
        return ["approved_plan_missing"]
    failures: list[str] = []
    if plan.status != "approved":
        failures.append(f"plan_not_approved:{plan.status}")
    if plan.task_revision_id != revision.revision_id:
        failures.append("plan_task_revision_stale")
    if plan.authority.engineering_contract_digest != engineering_contract_digest(revision):
        failures.append("plan_engineering_contract_stale")
    if plan.authority.inspection_evidence_digest != evidence_digest:
        failures.append("plan_inspection_evidence_stale")
    if (
        plan.authority.repository_guidance_digest is not None
        and repository_guidance_digest is not None
        and plan.authority.repository_guidance_digest != repository_guidance_digest
    ):
        failures.append("plan_repository_guidance_stale")
    failures.extend(
        plan_semantic_review_freshness_failures(
            plan,
            required=semantic_review_required,
        )
    )
    return list(dict.fromkeys(failures))


def _planning_state_should_stale(reasons: list[str]) -> bool:
    """Only authority drift may stale an approved plan.

    An attempted off-plan mutation is itself useful shadow/enforce telemetry, but
    it does not change the TaskRevision, inspection evidence, or planning
    baseline. Turning such an attempted operation into a durable stale state
    would let a model mistake poison an otherwise valid plan.
    """

    authority_drift = {
        "plan_task_revision_stale",
        "plan_engineering_contract_stale",
        "plan_inspection_evidence_stale",
        "plan_repository_guidance_stale",
        "planning_state_task_revision_stale",
        "planning_active_plan_identity_mismatch",
        "planning_base_commit_changed",
    }
    return any(
        reason in authority_drift
        or reason.startswith("preexisting_dirty_path_modified:")
        or reason.startswith("plan_semantic_review_")
        for reason in reasons
    )


def _planning_state_status_after_submission(
    plan_status: str,
    active_plan_revision_id: str | None,
) -> str:
    """A rejected proposal must not revoke a still-valid approved plan."""

    if plan_status == "approved" or active_plan_revision_id:
        return "approved"
    return plan_status


def _ordered_union(left, right):
    output = list(left)
    for item in right:
        if item not in output:
            output.append(item)
    return output


def _normalize_workspace_path(value: object) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/")


def _baseline_dirty_paths(baseline_provenance: dict[str, Any] | None) -> set[str]:
    if not baseline_provenance:
        return set()
    return {
        normalized
        for value in baseline_provenance.get("dirty_paths", []) or []
        if (normalized := _normalize_workspace_path(value))
    }


def _planned_path_covers(pattern: str, path: str) -> bool:
    normalized_pattern = _normalize_workspace_path(pattern)
    normalized_path = _normalize_workspace_path(path)
    if not normalized_pattern or not normalized_path:
        return False
    if normalized_pattern == normalized_path:
        return True
    if any(token in normalized_pattern for token in "*?["):
        return fnmatch.fnmatchcase(normalized_path, normalized_pattern)
    return normalized_path.startswith(normalized_pattern + "/")


def _preexisting_dirty_plan_failures(
    submission: ImplementationPlanSubmission,
    baseline_provenance: dict[str, Any] | None,
) -> list[str]:
    """Reject plans that propose mutating workspace content owned by the user baseline."""

    dirty_paths = _baseline_dirty_paths(baseline_provenance)
    if not dirty_paths:
        return []
    failures: list[str] = []
    mutating_effects = {"mutate", "external_mutate", "unknown"}
    for item in submission.changes:
        if not mutating_effects.intersection(set(item.allowed_effects)):
            continue
        for dirty_path in sorted(dirty_paths):
            if any(_planned_path_covers(pattern, dirty_path) for pattern in item.paths):
                failures.append(f"plan_mutates_preexisting_dirty_path:{item.id}:{dirty_path}")
    return list(dict.fromkeys(failures))


def _preexisting_dirty_operation_failures(
    *,
    effect: str,
    target_path: str | None,
    command: str,
    baseline_provenance: dict[str, Any] | None,
) -> list[str]:
    """Block a mutation before it can overwrite a path dirty when the run began."""

    if effect in {"read", "validate"}:
        return []
    dirty_paths = _baseline_dirty_paths(baseline_provenance)
    if not dirty_paths:
        return []
    requested: list[str] = []
    if target_path:
        requested.append(_normalize_workspace_path(target_path))
    if command:
        requested.extend(_normalize_workspace_path(path) for path in command_target_paths(command))
    requested = [path for path in dict.fromkeys(requested) if path]
    protected = sorted(path for path in requested if path in dirty_paths)
    return [f"preexisting_dirty_path_mutation_forbidden:{path}" for path in protected]


def _merge_plan_delta(
    previous: ImplementationPlanRevision,
    delta: ImplementationPlanSubmission,
) -> ImplementationPlanSubmission:
    """Resolve an immutable PlanDelta into cumulative effective authority.

    A PlanDelta is intentionally incremental: omitted entries continue to be
    authorized by the previously approved revision. Entries with the same
    identity refine that prior entry without discarding previously authorized
    paths/coverage. Candidate dispositions are the exception because a newer
    semantic classification supersedes the older disposition for that exact
    candidate.
    """

    coverage = {item.requirement_id: item for item in previous.requirement_coverage}
    coverage_order = [item.requirement_id for item in previous.requirement_coverage]
    for item in delta.requirement_coverage:
        prior = coverage.get(item.requirement_id)
        if prior is None:
            coverage_order.append(item.requirement_id)
            coverage[item.requirement_id] = item
        else:
            coverage[item.requirement_id] = prior.model_copy(update={
                "plan_item_ids": _ordered_union(prior.plan_item_ids, item.plan_item_ids),
                "validation_ids": _ordered_union(prior.validation_ids, item.validation_ids),
            })

    impacts = {item.candidate_id: item for item in previous.impacts}
    impact_order = [item.candidate_id for item in previous.impacts]
    for plan_impact_disposition in delta.impacts:
        if plan_impact_disposition.candidate_id not in impacts:
            impact_order.append(plan_impact_disposition.candidate_id)
        impacts[plan_impact_disposition.candidate_id] = plan_impact_disposition

    changes = {item.id: item for item in previous.changes}
    change_order = [item.id for item in previous.changes]
    for plan_item in delta.changes:
        prior_change = changes.get(plan_item.id)
        if prior_change is None:
            change_order.append(plan_item.id)
            changes[plan_item.id] = plan_item
        else:
            changes[plan_item.id] = prior_change.model_copy(update={
                "intent": plan_item.intent,
                "paths": _ordered_union(prior_change.paths, plan_item.paths),
                "requirement_ids": _ordered_union(prior_change.requirement_ids, plan_item.requirement_ids),
                "candidate_ids": _ordered_union(prior_change.candidate_ids, plan_item.candidate_ids),
                "validation_ids": _ordered_union(prior_change.validation_ids, plan_item.validation_ids),
                "allowed_effects": _ordered_union(prior_change.allowed_effects, plan_item.allowed_effects),
                "command_hints": _ordered_union(prior_change.command_hints, plan_item.command_hints),
            })

    validations = {item.id: item for item in previous.validations}
    validation_order = [item.id for item in previous.validations]
    for plan_validation_intent in delta.validations:
        prior_validation = validations.get(plan_validation_intent.id)
        if prior_validation is None:
            validation_order.append(plan_validation_intent.id)
            validations[plan_validation_intent.id] = plan_validation_intent
        else:
            validations[plan_validation_intent.id] = prior_validation.model_copy(update={
                "kind": plan_validation_intent.kind,
                "requirement_ids": _ordered_union(prior_validation.requirement_ids, plan_validation_intent.requirement_ids),
                "invariant": plan_validation_intent.invariant if plan_validation_intent.invariant is not None else prior_validation.invariant,
                "command_hint": plan_validation_intent.command_hint if plan_validation_intent.command_hint is not None else prior_validation.command_hint,
            })

    hypotheses = {item.hypothesis: item for item in previous.causal_hypotheses}
    hypothesis_order = [item.hypothesis for item in previous.causal_hypotheses]
    for causal_hypothesis in delta.causal_hypotheses:
        if causal_hypothesis.hypothesis not in hypotheses:
            hypothesis_order.append(causal_hypothesis.hypothesis)
        hypotheses[causal_hypothesis.hypothesis] = causal_hypothesis

    return ImplementationPlanSubmission(
        previous_plan_revision_id=previous.plan_revision_id,
        planning_lenses=_ordered_union(previous.planning_lenses, delta.planning_lenses),
        requirement_coverage=[coverage[key] for key in coverage_order],
        impacts=[impacts[key] for key in impact_order],
        changes=[changes[key] for key in change_order],
        validations=[validations[key] for key in validation_order],
        assumptions=_ordered_union(previous.assumptions, delta.assumptions),
        blockers=_ordered_union(previous.blockers, delta.blockers),
        causal_hypotheses=[hypotheses[key] for key in hypothesis_order],
    )


def _inspection_response(mode, revision, evidence, candidates, lenses, state):
    return {
        "mode": mode,
        "task_revision_id": revision.revision_id,
        "planning_lenses": list(lenses),
        "requirements": [item.model_dump(mode="json") for item in revision.requirements],
        "validation_plan": [item.model_dump(mode="json") for item in revision.validation_plan],
        "inspection_evidence": [item.model_dump(mode="json") for item in evidence],
        "impact_candidates": [item.model_dump(mode="json") for item in candidates],
        "inspection_evidence_digest": inspection_evidence_digest(evidence),
        "planning_state": state,
    }


def _unknown_command_is_explicitly_planned(plan, command: str) -> bool:
    if plan is None or not command.strip():
        return False
    normalized = command.strip().casefold()
    return any(
        "unknown" in item.allowed_effects
        and any(normalized.startswith(hint.strip().casefold()) for hint in item.command_hints if hint.strip())
        for item in plan.changes
    )


def _candidate_review_signature(candidates) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            item.candidate_id,
            item.path,
            item.relation,
            item.impact_likelihood,
            item.semantic_uncertainty,
            tuple(item.evidence_ids),
        )
        for item in candidates
    )


def _plan_next_action(
    status: str,
    failures: list[str],
    *,
    review_required: bool = False,
) -> str:
    if status == "approved":
        if review_required:
            return "high-risk working plan independently reviewed and persisted; implementation may proceed"
        return "ordinary working plan persisted; Pi may continue its implementation loop"
    if any(item.startswith("plan_mutates_preexisting_dirty_path:") for item in failures):
        return (
            "do not modify paths that were already dirty when the run began; preserve those user-owned changes "
            "and revise the plan to use an unmodified source/test path or report the baseline conflict"
        )
    if "plan_semantic_review_consensus_exhausted" in failures:
        return (
            "independent plan-review consensus was not reached within the bounded review cycle; "
            "report the unresolved blocking disagreement or request clarification rather than continuing broad inspection"
        )
    if any(item == "plan_semantic_review_unavailable" for item in failures):
        return "independent semantic plan review is unavailable; retry review without broadening repository inspection"
    if any(item.startswith("plan_semantic_review_blocking:") for item in failures):
        return "revise the plan to resolve the independent review's blocking findings, then resubmit"
    return "fix structural plan errors; only consequential operations require hard plan approval"


@router.post("/{run_id}/planning/inspect")
def inspect_agent_plan(run_id: str, request: PlanningInspectRequest) -> dict[str, Any]:
    service = default_agent_run_service()
    snapshot = _load(service, run_id)
    mode = planning_mode()
    with unit_of_work(service.database) as work:
        runs = PostgresAgentRunRepository(work.connection, service.context)
        planning = PostgresPlanningRepository(work.connection, service.context)
        revision = _current_revision(service, runs, run_id)
        fresh_evidence, fresh_candidates, lenses = build_inspection_bundle(
            snapshot.spec,
            revision,
            queries=request.queries,
            paths=request.paths,
        )
        for item in fresh_evidence:
            planning.add_inspection_evidence(item)
        for impact_candidate in fresh_candidates:
            planning.add_impact_candidate(impact_candidate)
        evidence = planning.list_inspection_evidence(run_id, task_revision_id=revision.revision_id)
        candidates = planning.list_impact_candidates(run_id, task_revision_id=revision.revision_id)
        state = planning.get_state(run_id)
        if (
            state is None
            or state.get("task_revision_id") != revision.revision_id
            or not state.get("planning_baseline_id")
        ):
            baseline_id, baseline = capture_planning_baseline(snapshot.spec)
            state = planning.set_state(
                run_id,
                mode=mode,
                task_revision_id=revision.revision_id,
                status="required",
                latest_plan_revision_id=None,
                active_plan_revision_id=None,
                planning_baseline_id=baseline_id,
                baseline_provenance=baseline,
            )
        elif state.get("mode") != mode:
            state = planning.set_state(
                run_id,
                mode=mode,
                task_revision_id=revision.revision_id,
                status=str(state.get("status") or "required"),
                latest_plan_revision_id=(
                    str(state.get("latest_plan_revision_id"))
                    if state.get("latest_plan_revision_id") else None
                ),
                active_plan_revision_id=(
                    str(state.get("active_plan_revision_id"))
                    if state.get("active_plan_revision_id") else None
                ),
                planning_baseline_id=(
                    str(state.get("planning_baseline_id"))
                    if state.get("planning_baseline_id") else None
                ),
                baseline_provenance=dict(state.get("baseline_provenance") or {}),
            )
        active_id = str(state.get("active_plan_revision_id") or "") if state else ""
        active_plan = planning.get_plan(run_id, active_id) if active_id else None
        review_required = _semantic_review_required(mode, snapshot.spec, active_plan)
        current_digest = inspection_evidence_digest(evidence)
        guidance_digest = _repository_guidance_digest(snapshot, revision, active_plan)
        freshness = _plan_freshness_failures(
            active_plan,
            revision,
            current_digest,
            guidance_digest,
            semantic_review_required=review_required,
        ) if active_plan is not None else []
        if active_plan is not None and freshness:
            state = planning.set_state(
                run_id,
                mode=mode,
                task_revision_id=revision.revision_id,
                status="stale",
                latest_plan_revision_id=(
                    str(state.get("latest_plan_revision_id"))
                    if state and state.get("latest_plan_revision_id") else None
                ),
                active_plan_revision_id=active_plan.plan_revision_id,
                planning_baseline_id=active_plan.authority.planning_baseline_id,
                baseline_provenance=dict(active_plan.baseline_provenance),
            )
        response = _inspection_response(mode, revision, evidence, candidates, lenses, state)
        work.commit()
    return response


def _submit_plan(run_id: str, request: PlanningSubmitRequest, *, amend: bool) -> dict[str, Any]:
    service = default_agent_run_service()
    snapshot = _load(service, run_id)
    mode = planning_mode()
    review_required = False
    semantic_review = None
    review_round: int | None = None
    max_review_rounds = plan_semantic_review_max_rounds()

    with unit_of_work(service.database) as work:
        runs = PostgresAgentRunRepository(work.connection, service.context)
        quality = PostgresCodingQualityRepository(work.connection, service.context)
        planning = PostgresPlanningRepository(work.connection, service.context)
        revision = _current_revision(service, runs, run_id)
        state = planning.get_state(run_id)
        if state is None or state.get("task_revision_id") != revision.revision_id:
            baseline_id, baseline = capture_planning_baseline(snapshot.spec)
            state = planning.set_state(
                run_id,
                mode=mode,
                task_revision_id=revision.revision_id,
                status="required",
                latest_plan_revision_id=None,
                active_plan_revision_id=None,
                planning_baseline_id=baseline_id,
                baseline_provenance=baseline,
            )

        # Serialize lineage while producing the immutable review snapshot. The
        # lock is deliberately released before the model review call below.
        planning.lock_state(run_id)
        state = planning.get_state(run_id) or state
        active_id = str(state.get("active_plan_revision_id") or "") or None

        previous, previous_id = _previous_plan_revision(request, amend, active_id, planning, run_id, revision)

        evidence = planning.list_inspection_evidence(run_id, task_revision_id=revision.revision_id)
        candidates = planning.list_impact_candidates(run_id, task_revision_id=revision.revision_id)
        if previous is not None:
            baseline_id = previous.authority.planning_baseline_id
            baseline = dict(previous.baseline_provenance)
        else:
            baseline_id = str(state.get("planning_baseline_id") or "")
            baseline = dict(state.get("baseline_provenance") or {})
            if not baseline_id or not baseline:
                baseline_id, baseline = capture_planning_baseline(snapshot.spec)

        proposed = request.plan.model_copy(update={
            "previous_plan_revision_id": previous_id if amend else None,
        })
        submission = _merge_plan_delta(previous, proposed) if previous is not None else proposed
        review_risk_reasons = plan_semantic_review_risk_reasons(snapshot.spec, submission)
        review_required = _semantic_review_required(mode, snapshot.spec, submission)
        paths = [path for item in submission.changes for path in item.paths]
        _, guidance_digest = compile_repository_guidance(
            snapshot.spec.workspace,
            objective=revision.effective_objective,
            relevant_paths=paths,
        )
        authority = build_plan_authority(
            revision,
            baseline_id=baseline_id,
            evidence=evidence,
            repository_guidance_digest=guidance_digest,
        )
        failures = plan_gate_failures(snapshot.spec, revision, submission, candidates, evidence)
        failures.extend(_preexisting_dirty_plan_failures(submission, baseline))

        active_id, failures, review_round, revision, semantic_review = _semantic_plan_review(failures, review_required, work, service, run_id, revision, max_review_rounds, active_id, baseline_id, authority, candidates, snapshot, submission, evidence, runs, planning, paths, baseline, review_round, semantic_review)

        failures, new_state, plan, status = _record_plan_revision(failures, quality, run_id, amend, revision, planning, previous_id, mode, authority, baseline, submission, semantic_review, active_id, baseline_id)
        work.commit()
    return {
        "mode": mode,
        "approved": status == "approved",
        "plan_revision": plan.model_dump(mode="json"),
        "semantic_review": semantic_review.model_dump(mode="json") if semantic_review is not None else None,
        "semantic_review_required": review_required,
        "semantic_review_risk_reasons": review_risk_reasons,
        "semantic_review_round": review_round,
        "semantic_review_max_rounds": max_review_rounds if review_required else None,
        "gate_failures": failures,
        "planning_state": new_state,
        "next_action": _plan_next_action(status, failures, review_required=review_required),
    }


def _previous_plan_revision(request, amend, active_id, planning, run_id, revision):
    """The active revision an amendment extends; a first submission must not name one."""
    previous = None
    previous_id = request.plan.previous_plan_revision_id
    if amend:
        if not active_id:
            raise HTTPException(status_code=409, detail="agent_plan_amend_requires_active_revision")
        if previous_id and previous_id != active_id:
            raise HTTPException(status_code=409, detail="agent_plan_amend_must_extend_active_revision")
        previous_id = active_id
        previous = planning.get_plan(run_id, previous_id)
        if previous is None or previous.task_revision_id != revision.revision_id:
            raise HTTPException(status_code=409, detail="agent_plan_previous_revision_stale")
    else:
        if previous_id:
            raise HTTPException(status_code=422, detail="agent_plan_submit_must_not_set_previous_revision")
        if active_id:
            raise HTTPException(status_code=409, detail="agent_plan_submit_requires_amend")
    return previous, previous_id


def _semantic_plan_review(failures, review_required, work, service, run_id, revision, max_review_rounds, active_id, baseline_id, authority, candidates, snapshot, submission, evidence, runs, planning, paths, baseline, review_round, semantic_review):
    """Model review of the plan outside the lineage lock; every authority-bearing input is revalidated after it."""
    if not failures and review_required:
        completed_rejections = planning.completed_semantic_review_rejections(run_id, revision.revision_id)
        if completed_rejections >= max_review_rounds:
            failures.append("plan_semantic_review_consensus_exhausted")
        else:
            review_round = completed_rejections + 1
            expected_revision_id = revision.revision_id
            expected_active_id = active_id
            expected_baseline_id = baseline_id
            expected_evidence_digest = authority.inspection_evidence_digest
            expected_contract_digest = authority.engineering_contract_digest
            expected_guidance_digest = authority.repository_guidance_digest
            expected_candidates = _candidate_review_signature(candidates)

            # Release the database/lineage lock before calling the model.
            # The second phase revalidates every authority-bearing identity.
            work.commit()
            reviewer = default_plan_semantic_reviewer(snapshot.spec, submission)
            try:
                semantic_review = review_plan_semantics_safely(
                    reviewer,
                    spec=snapshot.spec,
                    revision=revision,
                    submission=submission,
                    authority=authority,
                    evidence=evidence,
                    candidates=candidates,
                    review_round=review_round,
                    final_round=review_round == max_review_rounds,
                )
            except AgentBudgetError as exc:
                raise _planning_budget_http_exception(exc) from exc

            planning.lock_state(run_id)
            refreshed_revision = _current_revision(service, runs, run_id)
            refreshed_state = planning.get_state(run_id)
            refreshed_active_id = (
                str(refreshed_state.get("active_plan_revision_id") or "") or None
                if refreshed_state is not None
                else None
            )
            refreshed_evidence = planning.list_inspection_evidence(
                run_id,
                task_revision_id=refreshed_revision.revision_id,
            )
            refreshed_candidates = planning.list_impact_candidates(
                run_id,
                task_revision_id=refreshed_revision.revision_id,
            )
            _, refreshed_guidance_digest = compile_repository_guidance(
                snapshot.spec.workspace,
                objective=refreshed_revision.effective_objective,
                relevant_paths=paths,
            )
            current_baseline_id, _ = capture_planning_baseline(snapshot.spec)
            review_context_stale = (
                refreshed_revision.revision_id != expected_revision_id
                or refreshed_state is None
                or refreshed_active_id != expected_active_id
                or current_baseline_id != expected_baseline_id
                or engineering_contract_digest(refreshed_revision) != expected_contract_digest
                or inspection_evidence_digest(refreshed_evidence) != expected_evidence_digest
                or refreshed_guidance_digest != expected_guidance_digest
                or _candidate_review_signature(refreshed_candidates) != expected_candidates
            )
            if review_context_stale:
                raise HTTPException(status_code=409, detail="agent_plan_review_context_stale")

            revision = refreshed_revision
            evidence = refreshed_evidence
            candidates = refreshed_candidates
            active_id = refreshed_active_id
            failures = plan_gate_failures(snapshot.spec, revision, submission, candidates, evidence)
            failures.extend(_preexisting_dirty_plan_failures(submission, baseline))
            failures.extend(
                plan_semantic_review_gate_failures(
                    semantic_review,
                    required=True,
                )
            )
    return active_id, failures, review_round, revision, semantic_review


def _record_plan_revision(failures, quality, run_id, amend, revision, planning, previous_id, mode, authority, baseline, submission, semantic_review, active_id, baseline_id):
    """Store the gated plan revision and advance the planning state."""
    failures = list(dict.fromkeys(failures))
    status = "approved" if not failures else "rejected"
    stage = quality.get_stage(run_id) or {}
    source = (
        "repair"
        if str(stage.get("stage") or "") == "repairing"
        else "delta" if amend else "initial"
    )
    plan = ImplementationPlanRevision(
        run_id=run_id,
        task_revision_id=revision.revision_id,
        sequence=planning.next_plan_sequence(run_id, revision.revision_id),
        previous_plan_revision_id=previous_id if amend else None,
        source=cast(Any, source),
        status=cast(Any, status),
        mode=mode,
        authority=authority,
        baseline_provenance=baseline,
        planning_lenses=submission.planning_lenses,
        requirement_coverage=submission.requirement_coverage,
        impacts=submission.impacts,
        changes=submission.changes,
        validations=submission.validations,
        assumptions=submission.assumptions,
        blockers=submission.blockers,
        causal_hypotheses=submission.causal_hypotheses,
        semantic_review=semantic_review,
        gate_failures=failures,
    )
    planning.add_plan(plan)
    active_id = plan.plan_revision_id if status == "approved" else active_id
    state_status = _planning_state_status_after_submission(status, active_id)
    new_state = planning.set_state(
        run_id,
        mode=mode,
        task_revision_id=revision.revision_id,
        status=state_status,
        latest_plan_revision_id=plan.plan_revision_id,
        active_plan_revision_id=active_id,
        planning_baseline_id=baseline_id,
        baseline_provenance=baseline,
    )
    return failures, new_state, plan, status


@router.post("/{run_id}/planning/submit")
def submit_agent_plan(run_id: str, request: PlanningSubmitRequest) -> dict[str, Any]:
    return _submit_plan(run_id, request, amend=False)


@router.post("/{run_id}/planning/amend")
def amend_agent_plan(run_id: str, request: PlanningSubmitRequest) -> dict[str, Any]:
    return _submit_plan(run_id, request, amend=True)


@router.post("/{run_id}/planning/check")
def check_agent_plan(run_id: str) -> dict[str, Any]:
    service = default_agent_run_service()
    snapshot = _load(service, run_id)
    mode = planning_mode()
    with unit_of_work(service.database) as work:
        runs = PostgresAgentRunRepository(work.connection, service.context)
        planning = PostgresPlanningRepository(work.connection, service.context)
        revision = _current_revision(service, runs, run_id)
        state = planning.get_state(run_id)
        evidence = planning.list_inspection_evidence(run_id, task_revision_id=revision.revision_id)
        candidates = planning.list_impact_candidates(run_id, task_revision_id=revision.revision_id)
        plan = planning.latest_approved_plan(run_id, task_revision_id=revision.revision_id)
        review_required = _semantic_review_required(mode, snapshot.spec, plan)
        digest = inspection_evidence_digest(evidence)
        guidance_digest = _repository_guidance_digest(snapshot, revision, plan)
        failures = _plan_freshness_failures(
            plan,
            revision,
            digest,
            guidance_digest,
            semantic_review_required=review_required,
        )
        if plan is not None:
            failures.extend(plan_conformance_failures(snapshot.spec, plan, candidates))
        if state is None:
            failures.append("planning_state_missing")
        else:
            if state.get("task_revision_id") != revision.revision_id:
                failures.append("planning_state_task_revision_stale")
            if str(state.get("status") or "") != "approved":
                failures.append(f"latest_plan_state_not_approved:{state.get('status')}")
            active_id = str(state.get("active_plan_revision_id") or "") or None
            if plan is not None and active_id != plan.plan_revision_id:
                failures.append("planning_active_plan_identity_mismatch")
        work.rollback()
    failures = list(dict.fromkeys(failures))
    hard_gate_required = plan_requires_hard_planning(plan)
    return {
        "mode": mode,
        "passed": not failures,
        "would_block": hard_gate_required and bool(failures),
        "planning_requirement": "hard" if hard_gate_required else "advisory",
        "plan_revision_id": plan.plan_revision_id if plan else None,
        "failures": failures,
    }


@router.post("/{run_id}/planning/authorize")
def authorize_agent_planned_operation(
    run_id: str,
    request: PlanningAuthorizeRequest,
) -> dict[str, Any]:
    service = default_agent_run_service()
    snapshot = _load(service, run_id)
    mode = planning_mode()
    command = str(request.command or request.input.get("command") or "")
    target = str(request.path or request.input.get("path") or "").strip() or None
    effect = classify_operation_effect(request.tool_name, command=command)
    requirement = planning_requirement_for_operation(
        effect,
        target_path=target,
        command=command,
    )

    if mode == "off":
        return {
            "allowed": True,
            "would_block": False,
            "mode": mode,
            "effect": effect,
            "planning_requirement": requirement,
            "reasons": [],
        }

    with unit_of_work(service.database) as work:
        runs = PostgresAgentRunRepository(work.connection, service.context)
        quality = PostgresCodingQualityRepository(work.connection, service.context)
        planning = PostgresPlanningRepository(work.connection, service.context)
        revision = _current_revision(service, runs, run_id)
        state = planning.get_state(run_id)
        if (
            state is None
            or state.get("task_revision_id") != revision.revision_id
            or not state.get("planning_baseline_id")
        ):
            baseline_id, baseline = capture_planning_baseline(snapshot.spec)
            state = planning.set_state(
                run_id,
                mode=mode,
                task_revision_id=revision.revision_id,
                status="required",
                latest_plan_revision_id=None,
                active_plan_revision_id=None,
                planning_baseline_id=baseline_id,
                baseline_provenance=baseline,
            )
        evidence = planning.list_inspection_evidence(run_id, task_revision_id=revision.revision_id)
        candidates = planning.list_impact_candidates(run_id, task_revision_id=revision.revision_id)
        plan = planning.latest_approved_plan(run_id, task_revision_id=revision.revision_id)
        review_required = _semantic_review_required(mode, snapshot.spec, plan)

        baseline_provenance = dict(state.get("baseline_provenance") or {})
        protected_reasons = _preexisting_dirty_operation_failures(
            effect=effect,
            target_path=target,
            command=command,
            baseline_provenance=baseline_provenance,
        )
        effective_requirement = "hard" if protected_reasons else requirement
        reasons: list[str] = list(protected_reasons)
        if requirement == "hard":
            guidance_digest = _repository_guidance_digest(snapshot, revision, plan)
            reasons.extend(operation_plan_failures(
                plan,
                revision,
                effect=effect,
                target_path=target,
                command=command,
                current_evidence_digest=inspection_evidence_digest(evidence),
                quality_stage=quality.get_stage(run_id),
            ))
            reasons.extend(
                item for item in _plan_freshness_failures(
                    plan,
                    revision,
                    inspection_evidence_digest(evidence),
                    guidance_digest,
                    semantic_review_required=review_required,
                )
                if item not in reasons
            )
            if state.get("task_revision_id") != revision.revision_id:
                reasons.append("planning_state_task_revision_stale")
            if str(state.get("status") or "") in {"rejected", "stale", "invalid", "required", "submitted"}:
                reasons.append(f"latest_plan_state_not_approved:{state.get('status')}")
            active_id = str(state.get("active_plan_revision_id") or "") or None
            if plan is not None and active_id != plan.plan_revision_id:
                reasons.append("planning_active_plan_identity_mismatch")
            if effect == "unknown" and command and not _unknown_command_is_explicitly_planned(plan, command):
                reasons.append("unknown_command_requires_explicit_plan_hint")
            if plan is not None:
                drift = plan_conformance_failures(snapshot.spec, plan, candidates)
                reasons.extend(
                    item for item in drift
                    if item.startswith("unplanned_consequential_path:")
                    or item.startswith("preexisting_dirty_path_modified:")
                    or item == "planning_base_commit_changed"
                )

        reasons = list(dict.fromkeys(reasons))
        would_block = effective_requirement == "hard" and bool(reasons)
        # Shadow planning may observe ordinary hard-plan failures without blocking,
        # but it must never authorize overwriting content that was dirty before
        # the run. That is workspace provenance safety, not planning policy.
        allowed = not would_block or (mode == "shadow" and not protected_reasons)
        if would_block and plan is not None and _planning_state_should_stale(reasons):
            planning.mark_state_stale(run_id)
        decision = PlanningDecision(
            run_id=run_id,
            task_revision_id=revision.revision_id,
            plan_revision_id=plan.plan_revision_id if plan else None,
            mode=mode,
            planning_requirement=effective_requirement,
            tool_name=request.tool_name,
            effect=effect,
            target=target or (command[:500] if command else None),
            allowed=allowed,
            would_block=would_block,
            reasons=reasons,
        )
        planning.add_decision(decision)
        work.commit()

    reason: str | None
    if protected_reasons and not allowed:
        reason = (
            "Omnix protected pre-existing workspace changes from being overwritten: "
            + ", ".join(protected_reasons)
            + ". Preserve that baseline-dirty path; use another test/source path or ask the user to resolve the pre-existing change."
        )
    else:
        reason = (
            "Omnix hard planning authority blocked this consequential operation: " + ", ".join(reasons)
            if not allowed else None
        )
    return {
        "allowed": allowed,
        "would_block": would_block,
        "mode": mode,
        "effect": effect,
        "planning_requirement": effective_requirement,
        "plan_revision_id": plan.plan_revision_id if plan else None,
        "reasons": reasons,
        "reason": reason,
    }
