from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_GENERIC_FALLBACK_TEXT = {
    "the action resolves according to the current state.",
    "the action resolves according to the current turn contract.",
    "nothing else happens.",
    "you cannot do that.",
}


@dataclass(frozen=True)
class BaselineScenario:
    scenario_id: str
    category: str
    player_input: str
    expected_affordances: tuple[str, ...]
    allowed_truth_classes: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    allowed_forward_outcomes: tuple[str, ...]
    clarification_required: bool = False
    notes: str = ""

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "BaselineScenario":
        return cls(
            scenario_id=str(row.get("scenario_id") or "").strip(),
            category=str(row.get("category") or "").strip(),
            player_input=str(row.get("player_input") or "").strip(),
            expected_affordances=_string_tuple(row.get("expected_affordances")),
            allowed_truth_classes=_string_tuple(row.get("allowed_truth_classes")),
            forbidden_claims=_string_tuple(row.get("forbidden_claims")),
            allowed_forward_outcomes=_string_tuple(row.get("allowed_forward_outcomes")),
            clarification_required=bool(row.get("clarification_required", False)),
            notes=str(row.get("notes") or "").strip(),
        )


@dataclass(frozen=True)
class BaselineObservation:
    scenario_id: str
    final_visible_text: str
    selected_affordance: str
    truth_classes: tuple[str, ...]
    forward_outcome: str
    candidate_source: str = ""
    grounding_decision: str = ""
    fallback_reason: str = ""
    latency_ms: float = 0.0
    hard_state_claims: tuple[str, ...] = ()
    hidden_fact_leaks: tuple[str, ...] = ()
    agency_violation: bool = False
    repeated_content: bool = False
    stale_response_selected: bool = False
    local_retrieval_hit: bool = False
    hermes_status: str = "not_invoked"

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "BaselineObservation":
        return cls(
            scenario_id=str(row.get("scenario_id") or "").strip(),
            final_visible_text=str(row.get("final_visible_text") or "").strip(),
            selected_affordance=str(row.get("selected_affordance") or "").strip(),
            truth_classes=_string_tuple(row.get("truth_classes")),
            forward_outcome=str(row.get("forward_outcome") or "").strip(),
            candidate_source=str(row.get("candidate_source") or "").strip(),
            grounding_decision=str(row.get("grounding_decision") or "").strip(),
            fallback_reason=str(row.get("fallback_reason") or "").strip(),
            latency_ms=float(row.get("latency_ms") or 0.0),
            hard_state_claims=_string_tuple(row.get("hard_state_claims")),
            hidden_fact_leaks=_string_tuple(row.get("hidden_fact_leaks")),
            agency_violation=bool(row.get("agency_violation", False)),
            repeated_content=bool(row.get("repeated_content", False)),
            stale_response_selected=bool(row.get("stale_response_selected", False)),
            local_retrieval_hit=bool(row.get("local_retrieval_hit", False)),
            hermes_status=str(row.get("hermes_status") or "not_invoked").strip(),
        )


@dataclass(frozen=True)
class ScenarioEvaluation:
    scenario_id: str
    compliant: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaselineMetrics:
    scenario_count: int
    observation_count: int
    labeled_outcome_compliance_rate: float
    current_turn_answer_rate: float
    forward_motion_rate: float
    generic_fallback_rate: float
    agency_violation_rate: float
    unsupported_hard_state_claim_rate: float
    hidden_information_leakage_rate: float
    repeated_content_rate: float
    stale_response_selection_rate: float
    local_retrieval_hit_rate: float
    hermes_invocation_rate: float
    hermes_success_rate: float
    hermes_timeout_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    evaluations: tuple[ScenarioEvaluation, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_count": self.scenario_count,
            "observation_count": self.observation_count,
            "labeled_outcome_compliance_rate": self.labeled_outcome_compliance_rate,
            "current_turn_answer_rate": self.current_turn_answer_rate,
            "forward_motion_rate": self.forward_motion_rate,
            "generic_fallback_rate": self.generic_fallback_rate,
            "agency_violation_rate": self.agency_violation_rate,
            "unsupported_hard_state_claim_rate": self.unsupported_hard_state_claim_rate,
            "hidden_information_leakage_rate": self.hidden_information_leakage_rate,
            "repeated_content_rate": self.repeated_content_rate,
            "stale_response_selection_rate": self.stale_response_selection_rate,
            "local_retrieval_hit_rate": self.local_retrieval_hit_rate,
            "hermes_invocation_rate": self.hermes_invocation_rate,
            "hermes_success_rate": self.hermes_success_rate,
            "hermes_timeout_rate": self.hermes_timeout_rate,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "evaluations": [
                {
                    "scenario_id": row.scenario_id,
                    "compliant": row.compliant,
                    "issues": list(row.issues),
                }
                for row in self.evaluations
            ],
        }


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_baseline_scenarios(path: str | Path) -> tuple[BaselineScenario, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("scenarios") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("baseline fixture must contain a scenarios list")
    scenarios = tuple(BaselineScenario.from_mapping(row) for row in rows if isinstance(row, dict))
    validate_scenarios(scenarios)
    return scenarios


def validate_scenarios(scenarios: Sequence[BaselineScenario]) -> None:
    if not scenarios:
        raise ValueError("baseline fixture must not be empty")
    seen: set[str] = set()
    for scenario in scenarios:
        if not scenario.scenario_id:
            raise ValueError("scenario_id is required")
        if scenario.scenario_id in seen:
            raise ValueError(f"duplicate scenario_id: {scenario.scenario_id}")
        seen.add(scenario.scenario_id)
        if not scenario.category:
            raise ValueError(f"category is required for {scenario.scenario_id}")
        if not scenario.player_input:
            raise ValueError(f"player_input is required for {scenario.scenario_id}")
        if not scenario.expected_affordances:
            raise ValueError(f"expected_affordances are required for {scenario.scenario_id}")
        if not scenario.allowed_truth_classes:
            raise ValueError(f"allowed_truth_classes are required for {scenario.scenario_id}")
        if not scenario.allowed_forward_outcomes:
            raise ValueError(f"allowed_forward_outcomes are required for {scenario.scenario_id}")


def evaluate_baseline(
    scenarios: Sequence[BaselineScenario],
    observations: Sequence[BaselineObservation],
) -> BaselineMetrics:
    validate_scenarios(scenarios)
    scenario_by_id = {row.scenario_id: row for row in scenarios}
    observation_by_id: dict[str, BaselineObservation] = {}
    for observation in observations:
        if observation.scenario_id in observation_by_id:
            raise ValueError(f"duplicate observation: {observation.scenario_id}")
        if observation.scenario_id not in scenario_by_id:
            raise ValueError(f"unknown scenario observation: {observation.scenario_id}")
        observation_by_id[observation.scenario_id] = observation

    evaluations: list[ScenarioEvaluation] = []
    answer_flags: list[bool] = []
    forward_flags: list[bool] = []
    generic_flags: list[bool] = []
    agency_flags: list[bool] = []
    hard_claim_flags: list[bool] = []
    hidden_flags: list[bool] = []
    repeated_flags: list[bool] = []
    stale_flags: list[bool] = []
    retrieval_flags: list[bool] = []
    hermes_invoked_flags: list[bool] = []
    hermes_success_flags: list[bool] = []
    hermes_timeout_flags: list[bool] = []
    latencies: list[float] = []

    for scenario in scenarios:
        scenario_observation = observation_by_id.get(scenario.scenario_id)
        issues: list[str] = []
        if scenario_observation is None:
            issues.append("missing_observation")
            evaluations.append(ScenarioEvaluation(scenario.scenario_id, False, tuple(issues)))
            continue

        if scenario_observation.selected_affordance not in scenario.expected_affordances:
            issues.append("unexpected_affordance")
        if scenario_observation.forward_outcome not in scenario.allowed_forward_outcomes:
            issues.append("unexpected_forward_outcome")
        if not set(scenario_observation.truth_classes).issubset(set(scenario.allowed_truth_classes)):
            issues.append("disallowed_truth_class")
        lowered = scenario_observation.final_visible_text.casefold()
        if any(claim.casefold() in lowered for claim in scenario.forbidden_claims):
            issues.append("forbidden_claim")
        if scenario.clarification_required and scenario_observation.forward_outcome != "clarification":
            issues.append("clarification_required")
        if scenario_observation.agency_violation:
            issues.append("agency_violation")
        if scenario_observation.hard_state_claims:
            issues.append("unsupported_hard_state_claim")
        if scenario_observation.hidden_fact_leaks:
            issues.append("hidden_information_leakage")

        normalized_text = " ".join(lowered.split())
        generic = (
            scenario_observation.fallback_reason.casefold() == "generic"
            or normalized_text in _GENERIC_FALLBACK_TEXT
        )
        has_answer = bool(scenario_observation.final_visible_text.strip()) and not generic
        forward = scenario_observation.forward_outcome in scenario.allowed_forward_outcomes and not generic

        answer_flags.append(has_answer)
        forward_flags.append(forward)
        generic_flags.append(generic)
        agency_flags.append(scenario_observation.agency_violation)
        hard_claim_flags.append(bool(scenario_observation.hard_state_claims))
        hidden_flags.append(bool(scenario_observation.hidden_fact_leaks))
        repeated_flags.append(scenario_observation.repeated_content)
        stale_flags.append(scenario_observation.stale_response_selected)
        retrieval_flags.append(scenario_observation.local_retrieval_hit)
        invoked = scenario_observation.hermes_status not in {"", "not_invoked"}
        hermes_invoked_flags.append(invoked)
        hermes_success_flags.append(scenario_observation.hermes_status == "success")
        hermes_timeout_flags.append(scenario_observation.hermes_status == "timeout")
        latencies.append(max(0.0, scenario_observation.latency_ms))
        evaluations.append(ScenarioEvaluation(scenario.scenario_id, not issues, tuple(issues)))

    completed = len(answer_flags)
    compliant = sum(1 for row in evaluations if row.compliant)
    hermes_invoked_count = sum(hermes_invoked_flags)
    return BaselineMetrics(
        scenario_count=len(scenarios),
        observation_count=len(observations),
        labeled_outcome_compliance_rate=_rate(compliant, len(scenarios)),
        current_turn_answer_rate=_rate(sum(answer_flags), completed),
        forward_motion_rate=_rate(sum(forward_flags), completed),
        generic_fallback_rate=_rate(sum(generic_flags), completed),
        agency_violation_rate=_rate(sum(agency_flags), completed),
        unsupported_hard_state_claim_rate=_rate(sum(hard_claim_flags), completed),
        hidden_information_leakage_rate=_rate(sum(hidden_flags), completed),
        repeated_content_rate=_rate(sum(repeated_flags), completed),
        stale_response_selection_rate=_rate(sum(stale_flags), completed),
        local_retrieval_hit_rate=_rate(sum(retrieval_flags), completed),
        hermes_invocation_rate=_rate(hermes_invoked_count, completed),
        hermes_success_rate=_rate(sum(hermes_success_flags), hermes_invoked_count),
        hermes_timeout_rate=_rate(sum(hermes_timeout_flags), hermes_invoked_count),
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        evaluations=tuple(evaluations),
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _percentile(values: Sequence[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (percentile / 100.0) * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = rank - lower
    return round((ordered[lower] * (1.0 - weight)) + (ordered[upper] * weight), 3)
