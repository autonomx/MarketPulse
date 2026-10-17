from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.apps.trading.prospective_prediction_v4 import (
    GrossReturnDistribution,
    NetReturnDistribution,
)
from app.apps.trading.prospective_prediction_v42 import (
    V42Forecast,
    V42MechanismHeads,
    V42ReturnDistribution,
    V42RiskInteractions,
)
from app.apps.trading.prospective_prediction_v42_action import (
    V42ActionSnapshot,
    V42AuthorizationReceipt,
)
from app.apps.trading.prospective_prediction_v43 import (
    V43CohortRegime,
    V43ExtensionExhaustionOverlay,
    V43Forecast,
)
from app.apps.trading.prospective_prediction_v43_action import (
    authorize_v43_action,
    build_portfolio_g,
    classify_v43_watch,
    evaluate_v43_post_open_action,
)


NOW = datetime(2026, 9, 28, 13, 42, tzinfo=timezone.utc)


def _base_v42() -> V42Forecast:
    return V42Forecast(
        instrument_id="equity:US:AAA",
        session_date=date(2026, 9, 28),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        model_spec_fingerprint="v42-spec",
        feature_fingerprint="v42-features",
        frozen_at=datetime(2026, 9, 28, 13, 29, tzinfo=timezone.utc),
        p_close_above_open=Decimal("0.64"),
        mechanisms=V42MechanismHeads(
            fundamental_reprice_score=Decimal("0.70"),
            theme_squeeze_score=Decimal("0.20"),
            low_information_technical_score=Decimal("0.10"),
            continuation_demand_score=Decimal("0.80"),
            remaining_upside_score=Decimal("0.65"),
            opening_exhaustion_score=Decimal("0.25"),
            supply_fade_score=Decimal("0.10"),
        ),
        interactions=V42RiskInteractions(
            extension_x_supply=Decimal("0.05"),
            extension_x_low_liquidity=Decimal("0.05"),
            extension_x_weak_finality=Decimal("0.05"),
        ),
        return_distribution=V42ReturnDistribution(
            q10=Decimal("-0.03"),
            q50=Decimal("0.05"),
            q90=Decimal("0.16"),
            expected_return=Decimal("0.055"),
            expected_shortfall_10pct=Decimal("-0.06"),
            p_return_gt_2pct=Decimal("0.67"),
            p_return_lt_minus_5pct=Decimal("0.18"),
            expected_mae=Decimal("-0.06"),
            expected_mfe=Decimal("0.14"),
        ),
        uncertainty="moderate",
    )


def _overlay(
    *,
    exhaustion: str = "0.35",
    repricing: str = "0.40",
    demand: str = "0.75",
) -> V43ExtensionExhaustionOverlay:
    return V43ExtensionExhaustionOverlay(
        gap_extension_score=Decimal("0.35"),
        vwap_extension_score=Decimal("0.25"),
        distance_from_low_score=Decimal("0.30"),
        range_position_score=Decimal("0.70"),
        multi_day_extension_score=Decimal("0.20"),
        float_turnover_score=Decimal("0.30"),
        late_deceleration_score=Decimal("0.20"),
        late_volume_fade_score=Decimal("0.15"),
        composite_exhaustion_score=Decimal(exhaustion),
        demand_resilience_score=Decimal(demand),
        premarket_repricing_complete_score=Decimal(repricing),
        used_components=(
            "gap_extension_score",
            "vwap_extension_score",
            "distance_from_low_score",
            "range_position_score",
            "multi_day_extension_score",
            "float_turnover_score",
            "late_deceleration_score",
            "late_volume_fade_score",
        ),
        missing_components=(),
        input_fingerprint="overlay-fp",
    )


def _regime(kind: str = "NORMAL") -> V43CohortRegime:
    risk = {
        "NORMAL": "0.25",
        "CAUTIOUS": "0.52",
        "HIGH_EXHAUSTION": "0.70",
    }[kind]
    return V43CohortRegime(
        classification=kind,
        member_count=10,
        median_gap_pct=Decimal("45"),
        fraction_gap_ge_50=Decimal("0.40"),
        fraction_high_opening_exhaustion=Decimal("0.20"),
        fraction_low_information_technical=Decimal("0.20"),
        mean_remaining_upside=Decimal("0.60"),
        risk_score=Decimal(risk),
        cohort_fingerprint=f"regime:{kind}",
    )


def _forecast(
    *,
    regime: str = "NORMAL",
    probability: str = "0.64",
    expected_return: str = "0.04",
    edge: str = "0.26",
    remaining: str = "0.65",
    exhaustion: str = "0.35",
    repricing: str = "0.40",
) -> tuple[V42Forecast, V43Forecast]:
    base = _base_v42()
    overlay = _overlay(exhaustion=exhaustion, repricing=repricing)
    forecast = V43Forecast(
        instrument_id=base.instrument_id,
        session_date=base.session_date,
        cohort_id=base.cohort_id,
        cohort_fingerprint=base.cohort_fingerprint,
        model_spec_fingerprint="v43-spec",
        frozen_at=base.frozen_at,
        base_v42_forecast_fingerprint=base.immutable_fingerprint,
        base_v42_probability=base.p_close_above_open,
        p_close_above_open=Decimal(probability),
        frozen_climatology_probability=Decimal(probability) - Decimal(edge),
        probability_edge_over_climatology=Decimal(edge),
        expected_return=Decimal(expected_return),
        remaining_upside_score=Decimal(remaining),
        opening_exhaustion_score=Decimal("0.25"),
        premarket_repricing_complete_score=Decimal(repricing),
        extension_overlay=overlay,
        cohort_regime=_regime(regime),
        uncertainty="moderate",
    )
    return base, forecast


def _net_distribution() -> NetReturnDistribution:
    gross = GrossReturnDistribution(
        q10=Decimal("-0.03"),
        q50=Decimal("0.05"),
        q90=Decimal("0.14"),
        expected_return=Decimal("0.045"),
        expected_shortfall_10pct=Decimal("-0.05"),
        p_return_gt_2pct=Decimal("0.65"),
        p_return_lt_minus_5pct=Decimal("0.18"),
    )
    return NetReturnDistribution(
        gross=gross,
        observed_spread_bps=Decimal("40"),
        entry_spread_cost_bps=Decimal("20"),
        expected_exit_spread_cost_bps=Decimal("20"),
        entry_slippage_bps=Decimal("10"),
        expected_exit_slippage_bps=Decimal("10"),
        entry_impact_bps=Decimal("5"),
        expected_exit_impact_bps=Decimal("5"),
        commission_bps=Decimal("0"),
        total_cost_bps=Decimal("70"),
        q10=Decimal("-0.037"),
        q50=Decimal("0.043"),
        q90=Decimal("0.133"),
        expected_return=Decimal("0.038"),
        expected_shortfall_10pct=Decimal("-0.057"),
    )


def _base_action(
    base: V42Forecast,
    *,
    higher_low: bool = True,
    confirmation: str = "0.90",
    trade_quality: str = "0.50",
) -> V42ActionSnapshot:
    net = _net_distribution()
    return V42ActionSnapshot(
        instrument_id=base.instrument_id,
        forecast_fingerprint=base.immutable_fingerprint,
        evaluated_at=NOW,
        decision_window="PRIMARY",
        state="STRUCTURE_CONFIRMED",
        watch_classification="HIGH_PRIORITY_WATCH",
        finalized_bar_count=12,
        open_price=Decimal("10"),
        current_price=Decimal("10.20"),
        session_vwap=Decimal("10.05"),
        opening_range_low=Decimal("9.70"),
        opening_range_high=Decimal("10.30"),
        higher_low=higher_low,
        vwap_held_or_reclaimed=True,
        pullback_high_broken=True,
        opening_range_support=True,
        volume_ratio=Decimal("1.20"),
        five_minute_return=Decimal("0.02"),
        ten_minute_return=Decimal("0.03"),
        confirmation_strength=Decimal(confirmation),
        timing_quality=Decimal("0.95"),
        remaining_upside_quality=Decimal("0.70"),
        execution_quality=Decimal("0.85"),
        trade_quality=Decimal(trade_quality),
        gross_remaining_distribution=net.gross,
        net_remaining_distribution=net,
        evidence_bar_ids=("bar-1", "bar-2"),
        reasons=("BASE_V42_CONFIRMED",),
    )


def _base_authorization(base: V42Forecast) -> V42AuthorizationReceipt:
    return V42AuthorizationReceipt(
        instrument_id=base.instrument_id,
        forecast_fingerprint=base.immutable_fingerprint,
        decision_at=NOW,
        decision="LONG",
        watch_classification="HIGH_PRIORITY_WATCH",
        confirmation_strength=Decimal("0.90"),
        timing_quality=Decimal("0.95"),
        remaining_upside_quality=Decimal("0.70"),
        execution_quality=Decimal("0.85"),
        trade_quality=Decimal("0.50"),
        notional=Decimal("200"),
        reference_price=Decimal("10.21"),
        observed_spread_bps=Decimal("40"),
        total_cost_bps=Decimal("70"),
        net_expected_return=Decimal("0.038"),
        net_q10=Decimal("-0.037"),
        reasons=(),
    )


def test_v43_watch_requires_edge_and_rejects_saturated_repricing() -> None:
    _, good = _forecast()
    decision = classify_v43_watch(good)
    assert decision.classification == "HIGH_PRIORITY_WATCH"

    _, saturated = _forecast(
        repricing="0.90",
        exhaustion="0.90",
        remaining="0.20",
    )
    rejected = classify_v43_watch(saturated)
    assert rejected.classification == "REJECT"
    assert "PREMARKET_REPRICING_LARGELY_COMPLETE" in rejected.reasons


def test_v43_requires_higher_low_even_when_v42_structure_confirmed() -> None:
    base, forecast = _forecast()
    watch = classify_v43_watch(forecast)
    result = evaluate_v43_post_open_action(
        forecast=forecast,
        base_v42=base,
        watch=watch,
        base_snapshot=_base_action(base, higher_low=False),
    )
    assert result.state == "WATCH"
    assert "HIGHER_LOW_REQUIRED_FOR_V43" in result.reasons


def test_high_exhaustion_regime_requires_stronger_confirmation_and_sizes_smaller() -> None:
    base_normal, normal = _forecast(regime="NORMAL")
    normal_watch = classify_v43_watch(normal)
    normal_action = evaluate_v43_post_open_action(
        forecast=normal,
        base_v42=base_normal,
        watch=normal_watch,
        base_snapshot=_base_action(base_normal, confirmation="0.90"),
    )
    normal_auth = authorize_v43_action(
        forecast=normal,
        watch=normal_watch,
        snapshot=normal_action,
        base_authorization=_base_authorization(base_normal),
    )
    assert normal_auth.decision == "LONG"

    base_risk, risk = _forecast(regime="HIGH_EXHAUSTION")
    risk_watch = classify_v43_watch(risk)
    risk_action = evaluate_v43_post_open_action(
        forecast=risk,
        base_v42=base_risk,
        watch=risk_watch,
        base_snapshot=_base_action(base_risk, confirmation="0.90"),
    )
    risk_auth = authorize_v43_action(
        forecast=risk,
        watch=risk_watch,
        snapshot=risk_action,
        base_authorization=_base_authorization(base_risk),
    )
    assert risk_auth.decision == "LONG"
    assert risk_auth.notional < normal_auth.notional
    assert risk_auth.notional == Decimal("70.0000")


def test_portfolio_g_preserves_unused_equity_as_cash() -> None:
    base, forecast = _forecast(regime="CAUTIOUS")
    watch = classify_v43_watch(forecast)
    action = evaluate_v43_post_open_action(
        forecast=forecast,
        base_v42=base,
        watch=watch,
        base_snapshot=_base_action(base, confirmation="0.90"),
    )
    authorization = authorize_v43_action(
        forecast=forecast,
        watch=watch,
        snapshot=action,
        base_authorization=_base_authorization(base),
    )
    portfolio = build_portfolio_g((authorization,))
    assert len(portfolio.positions) == 1
    assert portfolio.cash > Decimal("800")
    assert (
        portfolio.cash + sum(position.allocation for position in portfolio.positions)
        == Decimal("1000")
    )
