"""Baseline/candidate evaluation and rollout policy for coding-agent quality.

Phase 0 and Phase 31 deliberately share one metric contract.  The same scenario
corpus is run with the legacy/baseline quality configuration and with a candidate
quality policy; this module aggregates the resulting observations, computes the
quality/cost deltas, and makes a deterministic rollout decision.

The evaluator is runtime-neutral.  Live suites, replay fixtures and production
instrumentation can all emit :class:`CodingQualitySample` without granting the
evaluator any workspace or external-system authority.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import QualityPolicy


EvaluationVariant = Literal["baseline", "candidate"]
RolloutPolicy = Literal["standard", "strict", "critical"]


class CodingQualitySample(BaseModel):
    """One scenario execution reduced to quality and efficiency signals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    variant: EvaluationVariant
    quality_policy: QualityPolicy = "off"
    completed: bool
    requirements_total: int = Field(default=0, ge=0)
    requirements_satisfied: int = Field(default=0, ge=0)
    final_state_validated: bool = False
    stale_validation_accepted: bool = False
    stale_review_accepted: bool = False
    reviewer_required: bool = False
    reviewer_approved: bool | None = None
    injected_defect: bool = False
    reviewer_caught_defect: bool | None = None
    repair_attempts: int = Field(default=0, ge=0)
    repair_succeeded: bool | None = None
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)
    cost: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, object] = Field(default_factory=dict)

    @property
    def requirement_coverage(self) -> float:
        if self.requirements_total <= 0:
            return 1.0 if self.completed else 0.0
        return min(1.0, self.requirements_satisfied / self.requirements_total)


class SeededQualityProbe(BaseModel):
    """Ground-truth defect probe defined independently of model output."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    probe_id: str = Field(min_length=1)
    defect_id: str = Field(min_length=1)
    reviewer_caught_defect: bool
    repair_succeeded: bool
    metadata: dict[str, object] = Field(default_factory=dict)


class CodingQualityAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    variant: EvaluationVariant
    scenario_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0.0, le=1.0)
    requirement_coverage_rate: float = Field(ge=0.0, le=1.0)
    final_state_validation_rate: float = Field(ge=0.0, le=1.0)
    stale_acceptance_count: int = Field(ge=0)
    reviewer_approval_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    reviewer_catch_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    repair_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_output_tokens: float = Field(ge=0.0)
    average_tool_calls: float = Field(ge=0.0)
    average_wall_time_seconds: float = Field(ge=0.0)
    average_cost: float = Field(ge=0.0)


class CodingQualityComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline: CodingQualityAggregate
    candidate: CodingQualityAggregate
    completion_rate_delta: float
    requirement_coverage_delta: float
    final_state_validation_delta: float
    output_token_delta: float
    tool_call_delta: float
    wall_time_delta_seconds: float
    cost_delta: float
    seeded_reviewer_catch_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    seeded_repair_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    matched_scenarios: list[str] = Field(default_factory=list)
    baseline_only_scenarios: list[str] = Field(default_factory=list)
    candidate_only_scenarios: list[str] = Field(default_factory=list)


class RolloutThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_completion_rate: float
    max_completion_regression: float
    min_requirement_coverage: float
    max_requirement_coverage_regression: float
    min_final_state_validation_rate: float
    max_stale_acceptances: int
    min_reviewer_catch_rate: float | None = None
    min_repair_success_rate: float | None = None
    max_token_overhead_ratio: float | None = None
    max_wall_time_overhead_ratio: float | None = None


class CodingQualityRolloutDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: RolloutPolicy
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    thresholds: RolloutThresholds


_ROLLOUT_THRESHOLDS: dict[RolloutPolicy, RolloutThresholds] = {
    "standard": RolloutThresholds(
        min_completion_rate=0.90,
        max_completion_regression=0.02,
        min_requirement_coverage=0.95,
        max_requirement_coverage_regression=0.02,
        min_final_state_validation_rate=0.95,
        max_stale_acceptances=0,
        min_reviewer_catch_rate=0.80,
        min_repair_success_rate=0.80,
        max_token_overhead_ratio=1.00,
        max_wall_time_overhead_ratio=1.50,
    ),
    "strict": RolloutThresholds(
        min_completion_rate=0.95,
        max_completion_regression=0.00,
        min_requirement_coverage=0.98,
        max_requirement_coverage_regression=0.00,
        min_final_state_validation_rate=1.00,
        max_stale_acceptances=0,
        min_reviewer_catch_rate=0.90,
        min_repair_success_rate=0.90,
        max_token_overhead_ratio=1.25,
        max_wall_time_overhead_ratio=2.00,
    ),
    "critical": RolloutThresholds(
        min_completion_rate=0.98,
        max_completion_regression=0.00,
        min_requirement_coverage=1.00,
        max_requirement_coverage_regression=0.00,
        min_final_state_validation_rate=1.00,
        max_stale_acceptances=0,
        min_reviewer_catch_rate=0.95,
        min_repair_success_rate=0.95,
        max_token_overhead_ratio=1.50,
        max_wall_time_overhead_ratio=2.50,
    ),
}


def rollout_thresholds(policy: RolloutPolicy) -> RolloutThresholds:
    return _ROLLOUT_THRESHOLDS[policy]


def _rate(values: list[bool]) -> float | None:
    if not values:
        return None
    return sum(bool(value) for value in values) / len(values)


def _mean(values: list[float | int]) -> float:
    return fmean(float(value) for value in values) if values else 0.0


def aggregate_quality_samples(
    samples: list[CodingQualitySample],
    *,
    variant: EvaluationVariant | None = None,
) -> CodingQualityAggregate:
    selected = [sample for sample in samples if variant is None or sample.variant == variant]
    if not selected:
        raise ValueError("quality evaluation requires at least one sample")
    observed_variant = variant or selected[0].variant
    if any(sample.variant != observed_variant for sample in selected):
        raise ValueError("aggregate must contain exactly one evaluation variant")

    reviewer_samples = [
        bool(sample.reviewer_approved)
        for sample in selected
        if sample.reviewer_required and sample.reviewer_approved is not None
    ]
    defect_samples = [
        bool(sample.reviewer_caught_defect)
        for sample in selected
        if sample.injected_defect and sample.reviewer_caught_defect is not None
    ]
    repair_samples = [
        bool(sample.repair_succeeded)
        for sample in selected
        if sample.repair_attempts > 0 and sample.repair_succeeded is not None
    ]
    return CodingQualityAggregate(
        variant=observed_variant,
        scenario_count=len(selected),
        completion_rate=_mean([int(sample.completed) for sample in selected]),
        requirement_coverage_rate=_mean([sample.requirement_coverage for sample in selected]),
        final_state_validation_rate=_mean([int(sample.final_state_validated) for sample in selected]),
        stale_acceptance_count=sum(
            sample.stale_validation_accepted or sample.stale_review_accepted
            for sample in selected
        ),
        reviewer_approval_rate=_rate(reviewer_samples),
        reviewer_catch_rate=_rate(defect_samples),
        repair_success_rate=_rate(repair_samples),
        average_output_tokens=_mean([sample.output_tokens for sample in selected]),
        average_tool_calls=_mean([sample.tool_calls for sample in selected]),
        average_wall_time_seconds=_mean([sample.wall_time_seconds for sample in selected]),
        average_cost=_mean([sample.cost for sample in selected]),
    )


def compare_quality_baseline(
    baseline_samples: list[CodingQualitySample],
    candidate_samples: list[CodingQualitySample],
    *,
    seeded_probes: list[SeededQualityProbe] | None = None,
) -> CodingQualityComparison:
    baseline = aggregate_quality_samples(baseline_samples, variant="baseline")
    candidate = aggregate_quality_samples(candidate_samples, variant="candidate")
    baseline_ids = {sample.scenario_id for sample in baseline_samples}
    candidate_ids = {sample.scenario_id for sample in candidate_samples}
    probes = list(seeded_probes or [])
    seeded_reviewer_catch_rate = _rate([probe.reviewer_caught_defect for probe in probes])
    seeded_repair_success_rate = _rate([probe.repair_succeeded for probe in probes])
    return CodingQualityComparison(
        baseline=baseline,
        candidate=candidate,
        completion_rate_delta=candidate.completion_rate - baseline.completion_rate,
        requirement_coverage_delta=(
            candidate.requirement_coverage_rate - baseline.requirement_coverage_rate
        ),
        final_state_validation_delta=(
            candidate.final_state_validation_rate - baseline.final_state_validation_rate
        ),
        output_token_delta=candidate.average_output_tokens - baseline.average_output_tokens,
        tool_call_delta=candidate.average_tool_calls - baseline.average_tool_calls,
        wall_time_delta_seconds=(
            candidate.average_wall_time_seconds - baseline.average_wall_time_seconds
        ),
        cost_delta=candidate.average_cost - baseline.average_cost,
        seeded_reviewer_catch_rate=seeded_reviewer_catch_rate,
        seeded_repair_success_rate=seeded_repair_success_rate,
        matched_scenarios=sorted(baseline_ids & candidate_ids),
        baseline_only_scenarios=sorted(baseline_ids - candidate_ids),
        candidate_only_scenarios=sorted(candidate_ids - baseline_ids),
    )


def _overhead_ratio(candidate: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0 if candidate <= 0 else float("inf")
    return max(0.0, (candidate - baseline) / baseline)


def evaluate_rollout_policy(
    comparison: CodingQualityComparison,
    *,
    policy: RolloutPolicy = "strict",
) -> CodingQualityRolloutDecision:
    thresholds = rollout_thresholds(policy)
    candidate = comparison.candidate
    reasons: list[str] = []

    if comparison.baseline_only_scenarios or comparison.candidate_only_scenarios:
        reasons.append("scenario_corpus_mismatch")
    if candidate.completion_rate < thresholds.min_completion_rate:
        reasons.append("candidate_completion_rate_below_threshold")
    if comparison.completion_rate_delta < -thresholds.max_completion_regression:
        reasons.append("completion_rate_regressed")
    if candidate.requirement_coverage_rate < thresholds.min_requirement_coverage:
        reasons.append("candidate_requirement_coverage_below_threshold")
    if (
        comparison.requirement_coverage_delta
        < -thresholds.max_requirement_coverage_regression
    ):
        reasons.append("requirement_coverage_regressed")
    if candidate.final_state_validation_rate < thresholds.min_final_state_validation_rate:
        reasons.append("final_state_validation_below_threshold")
    if candidate.stale_acceptance_count > thresholds.max_stale_acceptances:
        reasons.append("stale_quality_evidence_was_accepted")
    reviewer_catch_rate = comparison.seeded_reviewer_catch_rate if comparison.seeded_reviewer_catch_rate is not None else candidate.reviewer_catch_rate
    repair_success_rate = comparison.seeded_repair_success_rate if comparison.seeded_repair_success_rate is not None else candidate.repair_success_rate
    if thresholds.min_reviewer_catch_rate is not None and reviewer_catch_rate is not None and reviewer_catch_rate < thresholds.min_reviewer_catch_rate:
        reasons.append("reviewer_catch_rate_below_threshold")
    if thresholds.min_repair_success_rate is not None and repair_success_rate is not None and repair_success_rate < thresholds.min_repair_success_rate:
        reasons.append("repair_success_rate_below_threshold")

    token_overhead = _overhead_ratio(
        candidate.average_output_tokens,
        comparison.baseline.average_output_tokens,
    )
    if (
        thresholds.max_token_overhead_ratio is not None
        and token_overhead > thresholds.max_token_overhead_ratio
    ):
        reasons.append("token_overhead_above_threshold")
    wall_overhead = _overhead_ratio(
        candidate.average_wall_time_seconds,
        comparison.baseline.average_wall_time_seconds,
    )
    if (
        thresholds.max_wall_time_overhead_ratio is not None
        and wall_overhead > thresholds.max_wall_time_overhead_ratio
    ):
        reasons.append("wall_time_overhead_above_threshold")

    # Strict/critical rollout is fail-closed if the corpus intended to exercise
    # independent review/repair contains no measurable observations at all.
    if policy in {"strict", "critical"}:
        if reviewer_catch_rate is None:
            reasons.append("reviewer_catch_rate_unmeasured")
        if repair_success_rate is None:
            reasons.append("repair_success_rate_unmeasured")

    return CodingQualityRolloutDecision(
        policy=policy,
        allowed=not reasons,
        reasons=reasons,
        thresholds=thresholds,
    )


def evaluation_report(
    comparison: CodingQualityComparison,
    decision: CodingQualityRolloutDecision,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "comparison": comparison.model_dump(mode="json"),
        "rollout": decision.model_dump(mode="json"),
    }


def write_evaluation_report(
    path: str | Path,
    comparison: CodingQualityComparison,
    decision: CodingQualityRolloutDecision,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(evaluation_report(comparison, decision), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
