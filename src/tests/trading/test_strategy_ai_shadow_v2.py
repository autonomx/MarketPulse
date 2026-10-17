from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.research.contracts import TradingEvidence
from app.apps.trading.strategy_ai_shadow_v2 import (
    AIShadowV2AlphaDecision,
    CatalystIntelligenceSnapshot,
    StructuredAlphaTrigger,
    alpha_prompt_snapshot,
    build_market_structure_snapshot,
    derive_catalyst_influence,
    deterministic_evidence_quality,
    deterministic_risk_geometry,
    evaluate_opportunity_episode,
    trigger_satisfied,
)


INSTRUMENT = "equity:NASDAQ:TEST"
START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, *, close: str, volume: str = "1000") -> MarketBar:
    price = Decimal(close)
    start = START + timedelta(minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=price - Decimal("0.02"),
        high=price + Decimal("0.05"),
        low=price - Decimal("0.05"),
        close=price,
        volume=Decimal(volume),
        session="regular",
        provider="fixture",
    )


def _evidence(source_type: str, tier: int, suffix: str) -> TradingEvidence:
    known = START - timedelta(minutes=30)
    return TradingEvidence(
        evidence_id=f"ev-{suffix}",
        instrument_id=INSTRUMENT,
        evidence_type="catalyst",
        source_type=source_type,
        source_locator=f"https://example.test/{suffix}",
        source_authority_tier=tier,
        source_published_at=known,
        source_available_at=known,
        captured_at=known,
        omnix_known_at=known,
        title="Fixture evidence",
        content="A causal fixture source.",
        content_hash="a" * 64,
        extraction_status="completed",
        metadata={},
        immutable_fingerprint=("b" if suffix == "1" else "c") * 64,
    )


def _decision() -> AIShadowV2AlphaDecision:
    return AIShadowV2AlphaDecision(
        instrument_id=INSTRUMENT,
        setup_family="trend_continuation",
        state="enter",
        quality_score=78,
        entry_zone_low=Decimal("10.00"),
        entry_zone_high=Decimal("10.20"),
        invalidation_price=Decimal("9.50"),
        target_1=Decimal("11.50"),
        target_2=Decimal("12.20"),
        extension_risk="low",
        evidence_for=("above VWAP", "higher lows"),
        evidence_against=(),
        thesis_changed=True,
        thesis="Trend continuation is confirmed.",
    )


def test_primary_source_verification_is_deterministic() -> None:
    quality, verified, score = deterministic_evidence_quality(
        [_evidence("sec", 1, "1"), _evidence("news", 2, "2")]
    )
    assert quality == "primary_verified"
    assert verified is True
    assert score > 0

    quality, verified, _ = deterministic_evidence_quality([_evidence("news", 3, "1")])
    assert quality == "secondary_only"
    assert verified is False


def test_durable_catalyst_lowers_alpha_hurdle_without_touching_risk() -> None:
    favorable = derive_catalyst_influence(
        persistence_class="high",
        primary_source_verified=True,
        supply_pressure="low",
        promotional_risk="low",
        gap_already_prices_in_news="low",
    )
    cautious = derive_catalyst_influence(
        persistence_class="low",
        primary_source_verified=False,
        supply_pressure="high",
        promotional_risk="high",
        gap_already_prices_in_news="high",
    )

    assert favorable.confirmation_hurdle < 60
    assert favorable.preferred_behavior == "seek_early_confirmation"
    assert cautious.confirmation_hurdle > 60
    assert cautious.preferred_behavior == "require_strong_confirmation"

    baseline = deterministic_risk_geometry(
        _decision(),
        entry_reference=Decimal("10.10"),
        estimated_cost_bps=Decimal("160"),
        minimum_net_r=Decimal("2"),
    )
    assert baseline.valid is True
    assert baseline.net_r is not None and baseline.net_r >= Decimal("2")
    assert "catalyst" not in baseline.model_dump()


def test_empirical_calibration_can_strengthen_but_not_remove_alpha_hurdle() -> None:
    calibrated = derive_catalyst_influence(
        persistence_class="high",
        primary_source_verified=True,
        supply_pressure="low",
        promotional_risk="low",
        gap_already_prices_in_news="low",
        empirical_persistence_rate=Decimal("0.70"),
        empirical_sample_size=150,
    )
    assert 40 <= calibrated.confirmation_hurdle < 50
    assert calibrated.empirical_sample_size == 150


def test_market_structure_is_descriptive_and_scores_confirmation() -> None:
    closes = [
        "10.00", "10.05", "10.08", "10.12", "10.18", "10.22",
        "10.28", "10.34", "10.38", "10.45", "10.52", "10.60",
    ]
    bars = [
        _bar(i, close=value, volume="1400" if i == len(closes) - 1 else "1000")
        for i, value in enumerate(closes)
    ]
    snapshot = build_market_structure_snapshot(bars)

    assert snapshot.current_price == Decimal("10.60")
    assert snapshot.session_return_pct > 0
    assert snapshot.higher_low_count_5 >= 3
    assert snapshot.ema9_slope_pct_5bars is not None
    assert snapshot.confirmation_score >= 50


def test_alpha_snapshot_hides_execution_status_and_preserves_blind_control() -> None:
    bars = [_bar(i, close=str(Decimal("10") + Decimal(i) / Decimal("100"))) for i in range(12)]
    structure = build_market_structure_snapshot(bars)
    catalyst = CatalystIntelligenceSnapshot(
        snapshot_id="snapshot-1",
        instrument_id=INSTRUMENT,
        as_of=structure.observed_at,
        provider="fixture",
        evidence_fingerprint="d" * 64,
        evidence_ids=("ev-1",),
        evidence_quality="primary_verified",
        primary_source_verified=True,
        catalyst_type="contract",
        catalyst_summary="Signed material contract.",
        fundamental_materiality=90,
        materiality_to_company_size=95,
        revenue_or_cash_impact="transformative",
        catalyst_novelty="new",
        attention_strength=90,
        expected_attention_duration="multi_day",
        event_certainty=95,
        supply_pressure="low",
        promotional_risk="low",
        gap_already_prices_in_news="low",
        intraday_persistence_class="high",
        source_quality_score=95,
        catalyst_strength=90,
        ambiguity="low",
        influence=derive_catalyst_influence(
            persistence_class="high",
            primary_source_verified=True,
            supply_pressure="low",
            promotional_risk="low",
            gap_already_prices_in_news="low",
        ),
        reasoning="Primary sources support a durable catalyst.",
    )
    micro = {"bid": "10.10", "ask": "10.11", "spread_bps": "9.9"}

    aware = alpha_prompt_snapshot(
        instrument_id=INSTRUMENT,
        structure=structure,
        morning={"gap_pct": "35"},
        catalyst=catalyst,
        include_catalyst=True,
        trusted_microstructure=micro,
    )
    blind = alpha_prompt_snapshot(
        instrument_id=INSTRUMENT,
        structure=structure,
        morning={"gap_pct": "35"},
        catalyst=catalyst,
        include_catalyst=False,
        trusted_microstructure=micro,
    )

    assert aware["execution_status_visible"] is False
    assert "catalyst_intelligence" in aware
    assert aware["alpha_confirmation_hurdle"] < 60
    assert "catalyst_intelligence" not in blind
    assert blind["alpha_confirmation_hurdle"] == 60
    assert aware["market_structure"] == blind["market_structure"]
    assert aware["market_microstructure"] == blind["market_microstructure"]


def test_structured_armed_trigger_is_deterministically_observed() -> None:
    bars = [_bar(i, close=str(Decimal("10") + Decimal(i) / Decimal("100"))) for i in range(12)]
    structure = build_market_structure_snapshot(bars)
    trigger = StructuredAlphaTrigger(
        trigger_type="bar_close_above",
        price=structure.current_price - Decimal("0.01"),
        expiry_minutes=30,
    )
    assert trigger_satisfied(trigger, structure=structure) is True


def test_invalid_trade_geometry_is_vetoed_deterministically() -> None:
    decision = _decision().model_copy(update={"target_1": Decimal("10.30")})
    geometry = deterministic_risk_geometry(
        decision,
        entry_reference=Decimal("10.10"),
        estimated_cost_bps=Decimal("160"),
        minimum_net_r=Decimal("2"),
    )
    assert geometry.valid is False
    assert geometry.reason == "minimum_net_r_not_met"


def test_opportunity_episode_labels_counterfactual_outcome() -> None:
    bars = [
        _bar(0, close="10.00"),
        _bar(1, close="10.20"),
        _bar(2, close="10.55"),
        _bar(3, close="11.05"),
        _bar(4, close="11.20"),
        _bar(5, close="11.30"),
    ]
    outcome = evaluate_opportunity_episode(
        arm="morning_catalyst",
        instrument_id=INSTRUMENT,
        episode_id="episode-1",
        setup_family="trend_continuation",
        started_at=bars[0].end_time,
        ended_at=bars[-1].end_time,
        entry_price=Decimal("10"),
        invalidation_price=Decimal("9.50"),
        target_1=Decimal("11"),
        bars=bars,
        entered=False,
        catalyst_persistence_class="high",
    )

    assert outcome.entered is False
    assert outcome.mfe_pct is not None and outcome.mfe_pct > 10
    assert outcome.plus_two_r_before_minus_one_r is True
    assert outcome.positive_opportunity is True


def test_armed_decision_requires_machine_readable_trigger() -> None:
    try:
        AIShadowV2AlphaDecision(
            instrument_id=INSTRUMENT,
            setup_family="trend_continuation",
            state="armed",
            quality_score=70,
            thesis="Waiting for breakout.",
        )
    except ValueError as exc:
        assert "structured trigger" in str(exc)
    else:
        raise AssertionError("armed state accepted without trigger")
