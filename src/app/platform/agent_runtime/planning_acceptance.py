"""Server-side planning conformance evaluated at final coding acceptance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.persistence.tenant import TenantContext

from .profiles import profile_produces_diff
from .contracts import AgentRunSnapshot, TaskRevision
from .planning import (
    engineering_contract_digest,
    inspection_evidence_digest,
    plan_conformance_failures,
    planned_paths,
    planning_mode,
    planning_requirement_for_operation,
)
from .planning_contracts import PlanningMode
from .planning_repository import PostgresPlanningRepository
from .repository_guidance import compile_repository_guidance

_FATAL_PLANNING_FAILURES = {
    "planning_task_revision_unavailable",
    "approved_plan_missing",
    "plan_task_revision_stale",
    "plan_engineering_contract_stale",
    "plan_inspection_evidence_stale",
    "plan_repository_guidance_stale",
    "planning_state_missing",
    "planning_state_task_revision_stale",
    "planning_active_plan_identity_mismatch",
    "planning_base_commit_changed",
    "planning_workspace_unavailable",
}


@dataclass(frozen=True)
class PlanningAcceptanceAssessment:
    mode: PlanningMode
    plan_revision_id: str | None
    failures: tuple[str, ...] = ()
    hard_gate_required: bool = False

    @property
    def would_block(self) -> bool:
        return bool(self.failures)

    @property
    def blocks_acceptance(self) -> bool:
        return self.mode == "enforce" and self.hard_gate_required and self.would_block

    @property
    def fail_closed(self) -> bool:
        """Authority-integrity failures cannot be retroactively repaired."""

        return self.mode == "enforce" and self.hard_gate_required and any(
            failure in _FATAL_PLANNING_FAILURES
            or failure.startswith("latest_plan_state_not_approved:")
            or failure.startswith("unplanned_consequential_path:")
            or failure.startswith("preexisting_dirty_path_modified:")
            for failure in self.failures
        )


def evaluate_planning_acceptance(
    connection: Any,
    context: TenantContext,
    snapshot: AgentRunSnapshot,
    revision: TaskRevision | None,
    *,
    modified_paths: list[str] | tuple[str, ...] = (),
) -> PlanningAcceptanceAssessment:
    """Recompute plan authority and conformance from durable/server truth.

    This is intentionally independent of the model-facing ``omnix_plan check``
    action. A model may request a check for feedback, but only Omnix acceptance
    decides whether missing/stale/nonconformant planning evidence is fatal.
    Shadow mode records the same failures without changing completion authority.
    """

    mode = planning_mode()
    if (
        mode == "off"
        or not profile_produces_diff(snapshot.spec.profile)
        or "diff" not in snapshot.spec.expected_artifacts
    ):
        return PlanningAcceptanceAssessment(mode=mode, plan_revision_id=None)
    hard_gate_required = any(
        planning_requirement_for_operation("mutate", target_path=path) == "hard"
        for path in modified_paths
    )
    if not hard_gate_required:
        return PlanningAcceptanceAssessment(
            mode=mode,
            plan_revision_id=None,
            hard_gate_required=False,
        )
    if revision is None:
        return PlanningAcceptanceAssessment(
            mode=mode,
            plan_revision_id=None,
            failures=("planning_task_revision_unavailable",),
            hard_gate_required=True,
        )

    planning = PostgresPlanningRepository(connection, context)
    state = planning.get_state(snapshot.run_id)
    evidence = planning.list_inspection_evidence(
        snapshot.run_id,
        task_revision_id=revision.revision_id,
    )
    candidates = planning.list_impact_candidates(
        snapshot.run_id,
        task_revision_id=revision.revision_id,
    )
    plan = planning.latest_approved_plan(
        snapshot.run_id,
        task_revision_id=revision.revision_id,
    )
    failures: list[str] = []
    if plan is None:
        failures.append("approved_plan_missing")
    else:
        if plan.task_revision_id != revision.revision_id:
            failures.append("plan_task_revision_stale")
        if plan.authority.engineering_contract_digest != engineering_contract_digest(revision):
            failures.append("plan_engineering_contract_stale")
        if plan.authority.inspection_evidence_digest != inspection_evidence_digest(evidence):
            failures.append("plan_inspection_evidence_stale")
        if plan.authority.repository_guidance_digest is not None:
            _, guidance_digest = compile_repository_guidance(
                snapshot.spec.workspace,
                objective=revision.effective_objective,
                relevant_paths=planned_paths(plan),
            )
            if plan.authority.repository_guidance_digest != guidance_digest:
                failures.append("plan_repository_guidance_stale")
        failures.extend(plan_conformance_failures(snapshot.spec, plan, candidates))

    if state is None:
        failures.append("planning_state_missing")
    else:
        if state.get("task_revision_id") != revision.revision_id:
            failures.append("planning_state_task_revision_stale")
        status = str(state.get("status") or "required")
        if status != "approved":
            failures.append(f"latest_plan_state_not_approved:{status}")
        active_id = str(state.get("active_plan_revision_id") or "") or None
        if plan is not None and active_id != plan.plan_revision_id:
            failures.append("planning_active_plan_identity_mismatch")

    return PlanningAcceptanceAssessment(
        mode=mode,
        plan_revision_id=plan.plan_revision_id if plan is not None else None,
        failures=tuple(dict.fromkeys(failures)),
        hard_gate_required=True,
    )
