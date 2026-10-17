from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.apps.trading.strategy_discovery_replay import (
    DiscoveryOpportunityLabel,
    DiscoveryReplayObservation,
    replay_dynamic_discovery,
)
from app.apps.trading.strategy_dynamic_discovery import (
    AttributionStage,
    CandidateLifecycleState,
    DiscoveryEvent,
    DiscoveryTriggerType,
    DynamicCandidate,
    EvaluationTier,
    MarketAnomalyFeatures,
    ParentExposureProposal,
    advance_candidate_lifecycle,
    allocate_parent_exposure,
    apply_strategy_rankings,
    build_opportunity_characterization,
    catalyst_decay_multiplier,
    catalyst_discovery_event,
    evaluate_shadow_qualification,
    market_discovery_event,
    merge_discovery_event,
    tier_candidates,
)
from app.apps.trading.strategy_dynamic_discovery_learning import build_daily_discovery_report
from app.apps.trading.strategy_interday_attribution import attribution_stage_for_strategy_event
from app.apps.trading.strategy_repository import StrategyEvent

SESSION = date(2026, 9, 11)
T0 = datetime(2026, 9, 11, 13, 5, tzinfo=timezone.utc)


def _features(*, at: datetime = T0, gap: float = 30, rvol: float = 100, turnover: float = 1.2):
    return MarketAnomalyFeatures(
        observed_at=at,
        gap_pct=gap,
        tod_rvol=rvol,
        volume_to_float=turnover,
        dollar_volume=20_000_000,
        volume_acceleration_5m=5,
        price_acceleration_5m_pct=8,
    )


def _candidate(symbol: str, score: float, minutes: int = 0) -> DynamicCandidate:
    at = T0 + timedelta(minutes=minutes)
    event = DiscoveryEvent(
        event_id=(symbol.replace(":", "") + "x" * 40)[:32],
        session_date=SESSION,
        instrument_id=symbol,
        discovered_at=at,
        trigger_type=DiscoveryTriggerType.MARKET_ANOMALY,
        source="fixture",
        causal_as_of=at,
        attention_score=score,
    )
    return merge_discovery_event(None, event)


def test_extreme_market_attention_discovers_without_known_catalyst():
    event = market_discovery_event(
        "equity:NASDAQ:BDRX",
        _features(rvol=3000, turnover=5),
        session_date=SESSION,
        source="fixture",
        catalyst_known=False,
    )
    assert event is not None
    assert event.trigger_type == DiscoveryTriggerType.UNEXPLAINED_ATTENTION_SPIKE
    assert event.unexplained_attention is True
    assert event.execution_authority is False


def test_discovery_event_rejects_future_causal_timestamp():
    with pytest.raises(ValueError, match="discovery_cannot_precede"):
        DiscoveryEvent(
            event_id="x" * 32,
            session_date=SESSION,
            instrument_id="equity:NASDAQ:TEST",
            discovered_at=T0,
            trigger_type=DiscoveryTriggerType.MARKET_ANOMALY,
            source="fixture",
            causal_as_of=T0 + timedelta(seconds=1),
        )


def test_rich_catalyst_can_create_candidate_before_leaderboard_move():
    intelligence = SimpleNamespace(
        fundamental_materiality="very_high",
        materiality_to_company_size="extreme",
        catalyst_novelty="high",
        attention_strength="high",
        event_certainty="high",
        catalyst_strength="very_high",
        expected_attention_duration="session",
        evidence_ids=("e1",),
        catalyst_type="contract",
        intraday_persistence_class="durable",
    )
    event = catalyst_discovery_event(
        "equity:NASDAQ:XYZ",
        intelligence,
        session_date=SESSION,
        observed_at=T0,
    )
    assert event is not None
    assert event.trigger_type == DiscoveryTriggerType.CATALYST_DISCOVERY_EVENT
    assert event.catalyst_score > 60


def test_lifecycle_uses_retention_hysteresis_before_expiry():
    candidate = _candidate("equity:NASDAQ:TRUG", 90)
    cooling = advance_candidate_lifecycle(
        candidate,
        observed_at=T0 + timedelta(minutes=31),
        current_priority=30,
    )
    assert cooling.lifecycle == CandidateLifecycleState.COOLING
    recovered = advance_candidate_lifecycle(
        cooling,
        observed_at=T0 + timedelta(minutes=32),
        current_priority=55,
    )
    assert recovered.lifecycle == CandidateLifecycleState.ACTIVE
    expired = advance_candidate_lifecycle(
        candidate,
        observed_at=T0 + timedelta(minutes=61),
        current_priority=30,
    )
    assert expired.lifecycle == CandidateLifecycleState.EXPIRED
    assert expired.tier == EvaluationTier.EXPIRED


def test_tiered_monitoring_does_not_delete_rank_six():
    values = tuple(_candidate(f"equity:NASDAQ:T{index}", 100 - index, index) for index in range(1, 8))
    ranked = tier_candidates(values)
    assert len(ranked) == 7
    assert [row.tier for row in ranked[:5]] == [EvaluationTier.A] * 5
    assert ranked[5].tier == EvaluationTier.B


def test_shared_vector_produces_strategy_specific_rankings():
    trend = _candidate("equity:NASDAQ:TREND", 95)
    reversal = _candidate("equity:NASDAQ:REV", 70, 1)
    trend_char = build_opportunity_characterization(
        trend,
        observed_at=T0,
        catalyst=SimpleNamespace(
            catalyst_strength="high", expected_attention_duration="session",
            intraday_persistence_class="durable", fundamental_materiality="high",
            materiality_to_company_size="high", event_certainty="high",
            supply_pressure="low", promotional_risk="low",
        ),
        market_structure=SimpleNamespace(confirmation_score=0.9),
        execution_quality=90,
    )
    reversal_char = build_opportunity_characterization(
        reversal,
        observed_at=T0,
        catalyst=None,
        market_structure=SimpleNamespace(confirmation_score=0.2),
        execution_quality=95,
    )
    rows = apply_strategy_rankings(
        (
            trend.model_copy(update={"characterization": trend_char}),
            reversal.model_copy(update={"characterization": reversal_char}),
        )
    )
    by_id = {row.instrument_id: row for row in rows}
    assert by_id["equity:NASDAQ:TREND"].strategy_ranks["stoch-trend-capture"] == 1
    assert by_id["equity:NASDAQ:TREND"].strategy_ranks["leader-momentum-continuation"] == 1
    assert set(by_id["equity:NASDAQ:TREND"].strategy_ranks) == {
        "deterministic-v2",
        "stoch-trend-capture",
        "leader-momentum-continuation",
        "ai-every-minute",
        "ai-event-driven",
        "stoch-rsi-5min",
        "stoch-rsi-5min-early-single",
        "gap-pullback-v2-prospective-20260825",
    }


def test_catalyst_decay_can_be_refreshed_by_market_confirmation():
    stale = catalyst_decay_multiplier("hours", elapsed_minutes=240)
    refreshed = catalyst_decay_multiplier("hours", elapsed_minutes=240, strong_market_confirmation=True)
    assert stale < refreshed
    assert refreshed >= 0.68


def test_shadow_qualification_never_self_authorizes_auto_paper():
    evidence = evaluate_shadow_qualification(
        {
            "independent_sessions": 30,
            "labeled_opportunities": 200,
            "discovery_recall": 0.8,
            "discovery_precision": 0.3,
            "execution_adjusted_expectancy_r": 0.4,
            "max_drawdown_r": -4,
            "data_reliability_fraction": 0.99,
            "causality_violations": 0,
        }
    )
    assert evidence.eligible_for_review is True
    assert evidence.auto_paper_authorized is False


def test_parent_exposure_treats_same_symbol_arms_as_correlated():
    allocations = allocate_parent_exposure(
        (
            ParentExposureProposal(instrument_id="equity:NASDAQ:TRUG", sub_strategy="ai-event-driven", desired_risk_fraction=0.008, conviction=0.9),
            ParentExposureProposal(instrument_id="equity:NASDAQ:TRUG", sub_strategy="stoch-trend-capture", desired_risk_fraction=0.009, conviction=0.8),
            ParentExposureProposal(instrument_id="equity:NASDAQ:TRUG", sub_strategy="leader-momentum-continuation", desired_risk_fraction=0.007, conviction=0.85),
        )
    )
    assert len(allocations) == 1
    assert allocations[0].allocated_risk_fraction <= 0.01
    assert len(allocations[0].contributing_substrategies) == 3


def test_discovery_replay_measures_false_positives_not_only_winners():
    observations = (
        DiscoveryReplayObservation(
            instrument_id="equity:NASDAQ:TRUG", observed_at=T0, source="fixture",
            market=_features(at=T0, gap=40, rvol=400, turnover=2),
        ),
        DiscoveryReplayObservation(
            instrument_id="equity:NASDAQ:LOSER", observed_at=T0, source="fixture",
            market=_features(at=T0, gap=35, rvol=250, turnover=2),
        ),
    )
    result = replay_dynamic_discovery(
        session_date=SESSION,
        observations=observations,
        labels=(
            DiscoveryOpportunityLabel(instrument_id="equity:NASDAQ:TRUG", opportunity=True, first_actionable_at=T0),
            DiscoveryOpportunityLabel(instrument_id="equity:NASDAQ:LOSER", opportunity=False),
        ),
    )
    assert result.discovery_recall == 1
    assert result.discovery_precision == 0.5
    assert result.false_positive_symbols == ("equity:NASDAQ:LOSER",)


def test_strategy_event_attribution_maps_execution_and_trade_stages():
    shadow = StrategyEvent(
        strategy_id="parent", event_id="e1", instrument_id="equity:NASDAQ:X",
        event_type="shadow_execution", state="entry_ready", observed_at=T0,
        idempotency_key="k1", payload={"execution": {"execution_eligible": True}},
    )
    trade = StrategyEvent(
        strategy_id="parent", event_id="e2", instrument_id="equity:NASDAQ:X",
        event_type="entry_order_submitted", state="entry_ready", observed_at=T0,
        idempotency_key="k2", payload={},
    )
    assert attribution_stage_for_strategy_event(shadow) == AttributionStage.EXECUTION_ELIGIBLE
    assert attribution_stage_for_strategy_event(trade) == AttributionStage.TRADED


def test_daily_report_keeps_frozen_and_dynamic_research_semantics_explicit():
    candidate = _candidate("equity:NASDAQ:TRUG", 90)
    report = build_daily_discovery_report(
        session_date=SESSION,
        generated_at=T0,
        candidates=(candidate,),
    )
    assert report.discovered_count == 1
    assert any("Frozen benchmark" in note for note in report.notes)
    assert any("research-only" in note for note in report.notes)
