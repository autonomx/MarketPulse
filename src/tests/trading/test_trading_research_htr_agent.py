from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.apps.trading.research.contracts import (
    ResearchActionProposal,
    ResearchActionRecord,
    ResearchCoverage,
    ResearchValidationReport,
    StrategyResearchFeatures,
    TradingResearchRequest,
    ValidationFeatureResult,
    fingerprint,
)
from app.apps.trading.research.coordinator import _research_status
from app.apps.trading.research.hermes_contract import TradingHermesNextActionDecision
from app.apps.trading.research.policy import evaluate_research_policy
from app.apps.trading.strategy_research_policy import apply_research_policy_to_quality


def _request(**updates):
    now = datetime.now(timezone.utc)
    values = dict(
        request_id="r",
        instrument_id="equity:NASDAQ:XYZ",
        requested_at=now,
        decision_context_at=now,
        evidence_cutoff_at=now,
        deadline_at=now + timedelta(seconds=30),
    )
    values.update(updates)
    return TradingResearchRequest(**values)


def _failed_source(step: int, operation: str) -> ResearchActionRecord:
    now = datetime.now(timezone.utc)
    return ResearchActionRecord(
        action_id=f"a-{step}", trace_id="trace", instrument_id="equity:NASDAQ:XYZ",
        step=step, operation=operation, args={}, reason="fixture", status="failed",
        result_summary={}, evidence_ids=(), requested_at=now, completed_at=now,
        error_code="ProviderError",
        immutable_fingerprint=fingerprint({"step": step, "operation": operation}),
    )


def _features(**updates) -> StrategyResearchFeatures:
    now = datetime.now(timezone.utc)
    values = dict(
        feature_id="feature-1",
        strategy_id="strategy-1",
        instrument_id="equity:NASDAQ:XYZ",
        fact_set_id="facts-1",
        decision_at=now,
        omnix_known_at=now,
        primary_catalyst_confirmed=True,
        catalyst_same_day=True,
        catalyst_fresh=True,
        immediate_supply_risk=False,
        supply_resolution_status="clear",
        research_status="complete",
        unresolved_supply=False,
        source_authority_sufficient=True,
        immutable_fingerprint="f" * 64,
    )
    values.update(updates)
    return StrategyResearchFeatures(**values)


def _validation(feature: str, recommendation: str) -> ResearchValidationReport:
    now = datetime.now(timezone.utc)
    result = ValidationFeatureResult(
        feature=feature,
        sample_size=200,
        exact_sample_size=120,
        in_sample_effect_r="0.25",
        out_of_sample_effect_r="0.20",
        win_probability_delta="0.08",
        confidence_interval_low="0.04",
        confidence_interval_high="0.31",
        recommendation=recommendation,
        reason="reviewed fixture",
    )
    return ResearchValidationReport(
        validation_id=f"reviewed-{feature}-{recommendation}",
        policy_version="trading-research-1",
        generated_at=now,
        sample_size=200,
        exact_sample_size=120,
        feature_results=(result,),
        promotion_allowed=True,
        notes=("explicit reviewed promotion fixture",),
        immutable_fingerprint="v" * 64,
    )


def test_trading_hermes_contract_blocks_order_operation():
    with pytest.raises(ValidationError):
        ResearchActionProposal.model_validate({"operation": "place_order", "args": {"symbol": "XYZ"}, "reason": "buy"})


def test_trading_hermes_contract_allows_exactly_one_semantic_action():
    decision = TradingHermesNextActionDecision.model_validate({
        "action": {"operation": "sec_find_filings", "args": {"forms": "8-K,S-3"}, "reason": "financing clue"},
        "rationale": "check primary source",
    })
    assert decision.action.operation == "sec_find_filings"


def test_request_enforces_hard_budgets():
    with pytest.raises(ValidationError):
        _request(max_steps=21)
    with pytest.raises(ValidationError):
        _request(max_queries=21)


def test_research_status_distinguishes_timeout_and_total_source_failure():
    assert _research_status(
        coverage=ResearchCoverage(), actions=[], evidence_count=0, stop_reason="deadline_exhausted",
    ) == "timed_out"
    failed = [
        _failed_source(0, "sec_find_filings"),
        _failed_source(1, "company_find_releases"),
        _failed_source(2, "web_search"),
    ]
    assert _research_status(
        coverage=ResearchCoverage(sec="failed", company_ir="failed", recent_news="failed"),
        actions=failed, evidence_count=0, stop_reason="planner_stop",
    ) == "failed"
    completed_without_sources = [
        item.model_copy(update={"status": "completed"}) for item in failed
    ]
    assert _research_status(
        coverage=ResearchCoverage(sec="complete", company_ir="complete", recent_news="complete"),
        actions=completed_without_sources, evidence_count=0, stop_reason="planner_stop",
    ) == "failed"


def test_legacy_strategy_research_is_never_authoritative():
    decision = evaluate_research_policy(strategy_version="1.1.0", features=None, validation=None)
    quality = apply_research_policy_to_quality(
        decision,
        base_quality_score=7,
        minimum_quality_score=7,
    )
    assert decision.allowed is True and decision.authoritative is False
    assert quality.allowed is True
    assert quality.adjusted_quality_score == 7
    assert quality.score_adjustment == 0


def test_v12_fails_closed_without_reviewed_validation():
    decision = evaluate_research_policy(strategy_version="1.2.0", features=None, validation=None)
    quality = apply_research_policy_to_quality(
        decision,
        base_quality_score=9,
        minimum_quality_score=7,
    )
    assert decision.allowed is False and decision.reason_code == "RESEARCH_POLICY_NOT_VALIDATED"
    assert quality.allowed is False


def test_score_only_changes_deterministic_quality_boundary():
    positive = evaluate_research_policy(
        strategy_version="1.2.0",
        features=_features(primary_catalyst_confirmed=True),
        validation=_validation("primary_catalyst_confirmed", "score_only"),
    )
    negative = evaluate_research_policy(
        strategy_version="1.2.0",
        features=_features(primary_catalyst_confirmed=False),
        validation=_validation("primary_catalyst_confirmed", "score_only"),
    )
    positive_quality = apply_research_policy_to_quality(
        positive, base_quality_score=7, minimum_quality_score=7, score_adjustment_enabled=True,
    )
    negative_quality = apply_research_policy_to_quality(
        negative, base_quality_score=7, minimum_quality_score=7, score_adjustment_enabled=True,
    )
    assert positive.score_adjustment == 1
    assert positive_quality.allowed is True and positive_quality.adjusted_quality_score == 8
    assert negative.score_adjustment == -1
    assert negative_quality.allowed is False
    assert negative_quality.reason_code == "RESEARCH_ADJUSTED_QUALITY_BELOW_MINIMUM"


def test_soft_gate_is_stronger_than_score_only_but_not_a_direct_hard_gate():
    decision = evaluate_research_policy(
        strategy_version="1.2.0",
        features=_features(unresolved_supply=True),
        validation=_validation("unresolved_supply", "soft_gate"),
    )
    assert decision.allowed is True
    assert decision.score_adjustment == -2
    marginal = apply_research_policy_to_quality(
        decision, base_quality_score=8, minimum_quality_score=7, score_adjustment_enabled=True,
    )
    exceptional = apply_research_policy_to_quality(
        decision, base_quality_score=10, minimum_quality_score=7, score_adjustment_enabled=True,
    )
    assert marginal.allowed is False and marginal.adjusted_quality_score == 6
    assert exceptional.allowed is True and exceptional.adjusted_quality_score == 8


def test_hard_gate_fails_closed_when_required_evidence_is_missing():
    decision = evaluate_research_policy(
        strategy_version="1.2.0",
        features=_features(immediate_supply_risk=None),
        validation=_validation("immediate_supply_risk", "hard_gate"),
    )
    assert decision.allowed is False
    assert decision.reason_code == "RESEARCH_HARD_GATE_IMMEDIATE_SUPPLY_RISK"


def test_research_never_changes_the_score_unless_the_configuration_opts_in():
    """WP-8.3: research may block an entry, but changes its score only by opt-in."""
    positive = evaluate_research_policy(
        strategy_version="1.2.0",
        features=_features(primary_catalyst_confirmed=True),
        validation=_validation("primary_catalyst_confirmed", "score_only"),
    )
    quality = apply_research_policy_to_quality(positive, base_quality_score=6, minimum_quality_score=7)
    assert quality.adjusted_quality_score == 6
    assert quality.score_adjustment == 0
    assert quality.proposed_score_adjustment == 1
    assert quality.score_adjustment_enabled is False
    # The +1 that would have lifted a marginal setup over the threshold does not apply.
    assert quality.allowed is False

    opted_in = apply_research_policy_to_quality(
        positive, base_quality_score=6, minimum_quality_score=7, score_adjustment_enabled=True,
    )
    assert opted_in.adjusted_quality_score == 7 and opted_in.allowed is True


def test_the_research_score_opt_in_is_off_by_default_and_part_of_the_strategy_configuration():
    from app.apps.trading.strategies.models import GapPullbackConfig

    assert GapPullbackConfig().research_score_adjustment_enabled is False
    assert GapPullbackConfig(research_score_adjustment_enabled=True).research_score_adjustment_enabled is True


def test_the_opt_in_leaves_qualified_profile_fingerprints_unchanged_while_off():
    """Qualification evidence is bound to the profile fingerprint; the new field must not move it."""
    from app.apps.trading.strategy_v2_qualification import (
        FROZEN_V2_PROFILE_FINGERPRINT,
        frozen_v2_config,
        v2_profile_fingerprint,
    )

    config = frozen_v2_config()
    assert v2_profile_fingerprint(config) == FROZEN_V2_PROFILE_FINGERPRINT
    opted_in = config.model_copy(update={"research_score_adjustment_enabled": True})
    assert v2_profile_fingerprint(opted_in) != FROZEN_V2_PROFILE_FINGERPRINT
