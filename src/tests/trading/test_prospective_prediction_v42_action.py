from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import AdjustmentMode, MarketBar
from app.apps.trading.prospective_prediction_v4 import ExecutionCostInput
from app.apps.trading.prospective_prediction_v42 import (
    V42Forecast,
    V42MechanismHeads,
    V42ReturnDistribution,
    V42RiskInteractions,
)
from app.apps.trading.prospective_prediction_v42_action import (
    V42ActionPolicy,
    authorize_v42_action,
    build_portfolio_f,
    classify_v42_watch,
    evaluate_v42_post_open_action,
    remaining_distribution_from_current,
)


OPEN = datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc)


def _forecast(
    *,
    probability: str = "0.58",
    expected_return: str = "0.05",
    tail: str = "0.20",
) -> V42Forecast:
    return V42Forecast(
        instrument_id="equity:US:AAA",
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        model_spec_fingerprint="spec-fp",
        feature_fingerprint="feature-fp",
        frozen_at=datetime(2026, 9, 23, 13, 25, tzinfo=timezone.utc),
        p_close_above_open=Decimal(probability),
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
            extension_x_supply=Decimal("0"),
            extension_x_low_liquidity=Decimal("0.05"),
            extension_x_weak_finality=Decimal("0.05"),
        ),
        return_distribution=V42ReturnDistribution(
            q10=Decimal("-0.02"),
            q50=Decimal("0.05"),
            q90=Decimal("0.15"),
            expected_return=Decimal(expected_return),
            expected_shortfall_10pct=Decimal("-0.05"),
            p_return_gt_2pct=Decimal("0.65"),
            p_return_lt_minus_5pct=Decimal(tail),
            expected_mae=Decimal("-0.06"),
            expected_mfe=Decimal("0.12"),
        ),
        uncertainty="moderate",
    )


def _bar(
    minute: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "1000",
) -> MarketBar:
    start = OPEN + timedelta(minutes=minute)
    return MarketBar(
        instrument_id="equity:US:AAA",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        provider="alpaca_sip",
        provider_event_id=f"bar-{minute}",
        provider_sequence=minute,
        received_at=start + timedelta(minutes=1),
        session="regular",
        adjustment_mode=AdjustmentMode.RAW,
    )


def _strong_bars(count: int = 12) -> tuple[MarketBar, ...]:
    rows = [
        _bar(0, open_="10.00", high="10.20", low="9.80", close="10.05"),
        _bar(1, open_="10.05", high="10.25", low="9.90", close="10.15"),
        _bar(2, open_="10.15", high="10.30", low="10.00", close="10.20"),
        _bar(3, open_="10.20", high="10.35", low="10.05", close="10.25"),
        _bar(4, open_="10.25", high="10.40", low="10.10", close="10.30"),
        _bar(5, open_="10.30", high="10.42", low="10.18", close="10.32"),
        _bar(6, open_="10.32", high="10.45", low="10.22", close="10.38"),
        _bar(7, open_="10.38", high="10.50", low="10.28", close="10.42"),
        _bar(8, open_="10.42", high="10.48", low="10.30", close="10.36"),
        _bar(9, open_="10.36", high="10.44", low="10.31", close="10.40"),
        _bar(10, open_="10.40", high="10.46", low="10.34", close="10.43"),
        _bar(11, open_="10.43", high="10.65", low="10.40", close="10.62", volume="2500"),
    ]
    return tuple(rows[:count])


def _cost(decision_at: datetime, reference_price: str = "10.61") -> ExecutionCostInput:
    return ExecutionCostInput(
        symbol="AAA",
        decision_at=decision_at,
        notional=Decimal("200"),
        reference_price=Decimal(reference_price),
        observed_bid=Decimal("10.60"),
        observed_ask=Decimal("10.62"),
        estimated_slippage_bps=Decimal("10"),
        estimated_impact_bps=Decimal("5"),
        expected_exit_slippage_bps=Decimal("10"),
        expected_exit_impact_bps=Decimal("5"),
        estimated_round_trip_commission_bps=Decimal("0"),
    )


def test_watch_classification_separates_reject_watch_and_priority() -> None:
    priority = classify_v42_watch(_forecast())
    assert priority.classification == "HIGH_PRIORITY_WATCH"

    ordinary = classify_v42_watch(
        _forecast(probability="0.48", expected_return="0.005", tail="0.40")
    )
    assert ordinary.classification == "WATCH"

    rejected = classify_v42_watch(
        _forecast(probability="0.28", expected_return="-0.06", tail="0.65")
    )
    assert rejected.classification == "REJECT"


def test_first_five_minutes_are_observe_only_even_with_strong_structure() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=4, seconds=59)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(4),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=_cost(evaluated_at),
        shared_confirmation_state="CONFIRMED_LONG",
    )
    assert snapshot.decision_window == "OBSERVE"
    assert snapshot.state == "OBSERVE_ONLY"
    assert snapshot.trade_quality == Decimal("0")


def test_primary_window_confirms_strong_finalized_structure() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=12)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=_cost(evaluated_at),
        shared_confirmation_state="CONFIRMED_LONG",
    )
    assert snapshot.decision_window == "PRIMARY"
    assert snapshot.state == "STRUCTURE_CONFIRMED"
    assert snapshot.higher_low is True
    assert snapshot.vwap_held_or_reclaimed is True
    assert snapshot.pullback_high_broken is True
    assert snapshot.confirmation_strength >= Decimal("0.72")
    assert snapshot.trade_quality > Decimal("0")


def test_original_premarket_thesis_expires_at_10_et() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=_cost(evaluated_at),
        shared_confirmation_state="CONFIRMED_LONG",
    )
    assert snapshot.decision_window == "EXPIRED"
    assert snapshot.state == "EXPIRED"


def test_failed_opening_range_and_vwap_invalidates_watch() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    bars = list(_strong_bars(6))
    bars[-1] = _bar(
        5,
        open_="9.95",
        high="9.98",
        low="9.50",
        close="9.60",
        volume="2000",
    )
    evaluated_at = OPEN + timedelta(minutes=6)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=tuple(bars),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=_cost(evaluated_at, "9.60"),
        shared_confirmation_state="OBSERVE_PULLBACK",
    )
    assert snapshot.state == "INVALIDATED"
    assert "OPENING_RANGE_AND_VWAP_STRUCTURE_FAILED" in snapshot.reasons


def test_remaining_distribution_penalizes_late_entry_after_price_rises() -> None:
    forecast = _forecast()
    at_open = remaining_distribution_from_current(
        forecast=forecast,
        open_price=Decimal("10"),
        current_price=Decimal("10"),
    )
    after_move = remaining_distribution_from_current(
        forecast=forecast,
        open_price=Decimal("10"),
        current_price=Decimal("10.80"),
    )
    assert after_move.expected_return is not None
    assert at_open.expected_return is not None
    assert after_move.expected_return < at_open.expected_return
    assert after_move.q90 < at_open.q90


def test_structure_confirmation_still_requires_net_economics_for_long() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=12)
    cost = _cost(evaluated_at)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=cost,
        shared_confirmation_state="CONFIRMED_LONG",
    )
    authorization = authorize_v42_action(
        forecast=forecast,
        watch=watch,
        snapshot=snapshot,
        execution_cost=cost,
        shared_confirmation_state="CONFIRMED_LONG",
    )
    assert snapshot.state == "STRUCTURE_CONFIRMED"
    assert authorization.decision in {"LONG", "NO_TRADE"}
    if authorization.decision == "LONG":
        assert authorization.net_expected_return is not None
        assert authorization.net_expected_return > Decimal("0.005")
        assert authorization.net_q10 is not None
        assert authorization.net_q10 >= Decimal("-0.08")


def test_portfolio_f_preserves_cash_and_caps_positions_at_twenty_percent() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=12)
    cost = _cost(evaluated_at)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=cost,
        shared_confirmation_state="CONFIRMED_LONG",
    )
    authorization = authorize_v42_action(
        forecast=forecast,
        watch=watch,
        snapshot=snapshot,
        execution_cost=cost,
        shared_confirmation_state="CONFIRMED_LONG",
    )
    if authorization.decision != "LONG":
        # Force a valid LONG-shaped receipt only to test portfolio conservation.
        authorization = authorization.model_copy(
            update={
                "decision": "LONG",
                "notional": Decimal("200"),
                "net_expected_return": Decimal("0.02"),
                "net_q10": Decimal("-0.05"),
                "reasons": (),
            }
        )
    portfolio = build_portfolio_f((authorization,), policy=V42ActionPolicy())
    assert len(portfolio.positions) == 1
    assert portfolio.positions[0].allocation == Decimal("200")
    assert portfolio.positions[0].weight == Decimal("0.20")
    assert portfolio.cash == Decimal("800")



def test_action_rejects_cross_session_or_mismatched_execution_time() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=12)

    with pytest.raises(ValueError, match="v42_action_session_date_mismatch"):
        evaluate_v42_post_open_action(
            forecast=forecast,
            watch=watch,
            bars=_strong_bars(),
            evaluated_at=evaluated_at + timedelta(days=1),
            data_quality_ok=True,
            execution_cost=None,
            shared_confirmation_state="OBSERVE_PULLBACK",
        )

    stale_cost = _cost(evaluated_at - timedelta(minutes=1))
    with pytest.raises(ValueError, match="v42_action_execution_cost_time_mismatch"):
        evaluate_v42_post_open_action(
            forecast=forecast,
            watch=watch,
            bars=_strong_bars(),
            evaluated_at=evaluated_at,
            data_quality_ok=True,
            execution_cost=stale_cost,
            shared_confirmation_state="OBSERVE_PULLBACK",
        )



def test_strong_diagnostics_cannot_bypass_shared_failed_selloff_authority() -> None:
    forecast = _forecast()
    watch = classify_v42_watch(forecast)
    evaluated_at = OPEN + timedelta(minutes=12)
    snapshot = evaluate_v42_post_open_action(
        forecast=forecast,
        watch=watch,
        bars=_strong_bars(),
        evaluated_at=evaluated_at,
        data_quality_ok=True,
        execution_cost=_cost(evaluated_at),
        shared_confirmation_state="OBSERVE_PULLBACK",
    )
    assert snapshot.confirmation_strength >= Decimal("0.72")
    assert snapshot.state == "WATCH"
    assert "CONFIRMATION_THRESHOLD_NOT_MET" in snapshot.reasons[0]
