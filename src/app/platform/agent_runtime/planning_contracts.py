"""Durable contracts for evidence-backed coding plans.

Planning is server-authoritative. Model submissions are proposals; Omnix binds
accepted plan revisions to the current TaskRevision, repository baseline and
inspection evidence before any enforcement decision can rely on them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlanningMode = Literal["off", "shadow", "enforce"]
PlanningRequirement = Literal["free", "advisory", "hard"]
PlanningStatus = Literal["required", "submitted", "approved", "rejected", "stale", "invalid"]
PlanningConfidence = Literal["low", "medium", "high"]
InspectionCompleteness = Literal["complete", "truncated", "paginated", "partial", "unknown"]
ImpactDisposition = Literal["modify", "verify", "not_impacted"]
OperationEffect = Literal["read", "validate", "mutate", "external_mutate", "unknown"]
CausalStatus = Literal["confirmed", "supported", "tentative"]
PlanReviewSeverity = Literal["blocking", "major", "minor", "suggestion"]
PlanReviewVerdict = Literal["approve", "revise"]
PlanReviewStatus = Literal["completed", "unavailable"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")


class InspectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    run_id: str
    task_revision_id: str
    kind: str
    path: str | None = None
    query: str | None = None
    locations: list[int] = Field(default_factory=list)
    bounded_excerpt: str = Field(default="", max_length=4000)
    evidence_confidence: PlanningConfidence = "high"
    relation_strength: PlanningConfidence = "medium"
    completeness: InspectionCompleteness = "complete"
    observed_result_count: int = Field(default=0, ge=0)
    reported_total_count: int | None = Field(default=None, ge=0)
    search_scope: str = "."
    result_digest: str
    created_at: datetime = Field(default_factory=utc_now)


class ImpactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    run_id: str
    task_revision_id: str
    path: str
    relation: str
    query: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_confidence: PlanningConfidence = "high"
    impact_likelihood: PlanningConfidence = "medium"
    semantic_uncertainty: PlanningConfidence = "medium"
    relation_strength: PlanningConfidence = "medium"
    created_at: datetime = Field(default_factory=utc_now)


class PlanAuthority(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engineering_contract_digest: str
    planning_baseline_id: str
    inspection_evidence_digest: str
    repository_guidance_digest: str | None = None


class RequirementPlanCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    plan_item_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)


class PlanImpactDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    disposition: ImpactDisposition
    reason: str = Field(default="", max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)
    waiver_proof_ids: list[str] = Field(default_factory=list)
    invariant: str | None = Field(default=None, max_length=2000)


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    intent: str = Field(min_length=1, max_length=4000)
    paths: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    validation_ids: list[str] = Field(default_factory=list)
    allowed_effects: list[OperationEffect] = Field(default_factory=list)
    command_hints: list[str] = Field(default_factory=list)


class CausalHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: PlanningConfidence = "medium"
    competing_hypotheses: list[str] = Field(default_factory=list)
    verification_method: str = Field(default="", max_length=4000)
    status: CausalStatus = "tentative"


class PlanReviewFinding(BaseModel):
    """One independent semantic-review objection or recommendation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=120)
    severity: PlanReviewSeverity
    problem: str = Field(min_length=1, max_length=3000)
    user_requirement: str | None = Field(default=None, max_length=3000)
    plan_statement: str | None = Field(default=None, max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=24)
    recommendation: str | None = Field(default=None, max_length=3000)


class PlanSemanticReview(BaseModel):
    """Server-bound result from an isolated review of one exact plan proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    review_round: int = Field(ge=1)
    reviewer_session_id: str = Field(min_length=1, max_length=240)
    protocol_version: str = Field(min_length=1, max_length=120)
    task_revision_id: str
    plan_digest: str
    engineering_contract_digest: str
    inspection_evidence_digest: str
    repository_guidance_digest: str | None = None
    model_provider_id: str
    model_id: str
    reasoning_effort: str | None = None
    status: PlanReviewStatus = "completed"
    verdict: PlanReviewVerdict
    objective_fidelity: bool
    requirement_coverage: bool
    assumption_quality: bool
    architecture_fit: bool
    validation_quality: bool
    findings: list[PlanReviewFinding] = Field(default_factory=list, max_length=32)
    failure_reason: str | None = Field(default=None, max_length=2000)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def enforce_blocking_consensus_semantics(self) -> "PlanSemanticReview":
        blocking = [item for item in self.findings if item.severity == "blocking"]
        if self.status == "unavailable":
            if self.verdict != "revise" or not blocking:
                raise ValueError("unavailable semantic review must fail closed with a blocking finding")
            return self
        if self.verdict == "approve" and blocking:
            raise ValueError("approved semantic review cannot contain blocking findings")
        if self.verdict == "revise" and not blocking:
            raise ValueError("revise semantic review requires at least one blocking finding")
        if (not self.objective_fidelity or not self.requirement_coverage) and not blocking:
            raise ValueError("objective or requirement review failure requires a blocking finding")
        return self


class PlanValidationIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=80)
    requirement_ids: list[str] = Field(default_factory=list)
    invariant: str | None = Field(default=None, max_length=2000)
    command_hint: str | None = Field(default=None, max_length=1000)


class ImplementationPlanSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_plan_revision_id: str | None = None
    planning_lenses: list[str] = Field(default_factory=list)
    requirement_coverage: list[RequirementPlanCoverage] = Field(default_factory=list)
    impacts: list[PlanImpactDisposition] = Field(default_factory=list)
    changes: list[PlanItem] = Field(default_factory=list)
    validations: list[PlanValidationIntent] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    causal_hypotheses: list[CausalHypothesis] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_ambiguous_duplicate_identities(self) -> "ImplementationPlanSubmission":
        _require_unique([item.id for item in self.changes], "plan item id")
        _require_unique([item.id for item in self.validations], "validation id")
        _require_unique(
            [item.requirement_id for item in self.requirement_coverage],
            "requirement coverage id",
        )
        _require_unique([item.candidate_id for item in self.impacts], "impact candidate id")
        return self


class ImplementationPlanRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_revision_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    task_revision_id: str
    sequence: int = Field(ge=1)
    previous_plan_revision_id: str | None = None
    source: Literal["initial", "delta", "repair"] = "initial"
    status: PlanningStatus = "submitted"
    mode: PlanningMode = "shadow"
    authority: PlanAuthority
    baseline_provenance: dict[str, object] = Field(default_factory=dict)
    planning_lenses: list[str] = Field(default_factory=list)
    requirement_coverage: list[RequirementPlanCoverage] = Field(default_factory=list)
    impacts: list[PlanImpactDisposition] = Field(default_factory=list)
    changes: list[PlanItem] = Field(default_factory=list)
    validations: list[PlanValidationIntent] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    causal_hypotheses: list[CausalHypothesis] = Field(default_factory=list)
    semantic_review: PlanSemanticReview | None = None
    gate_failures: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PlanningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    task_revision_id: str | None = None
    plan_revision_id: str | None = None
    mode: PlanningMode = "shadow"
    planning_requirement: PlanningRequirement = "advisory"
    tool_name: str
    effect: OperationEffect
    target: str | None = None
    allowed: bool
    would_block: bool = False
    reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)