from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.strategy_stoch_execution_cost import (
    action_for_snapshot,
    build_execution_summary,
    requested_fraction_for_action,
    simulate_stoch_execution,
    spread_tier,
)
from app.apps.trading.strategy_stoch_trend_capture import StochTrendCaptureSnapshot


INSTRUMENT = "equity:NASDAQ:TEST"
BINDING = "alpaca_iex:TEST"
DECISION_AT = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


def _execution(
    *,
    bid: str,
    ask: str,
    last: str = "10",
    source_offset_seconds: int = 2,
    eligible: bool = True,
):
    return {
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "provider": "alpaca_iex",
        "last": Decimal(last),
        "bid": Decimal(bid),
        "ask": Decimal(ask),
        "bid_size": Decimal("1000"),
        "ask_size": Decimal("1000"),
        "source_time": DECISION_AT + timedelta(seconds=source_offset_seconds),
        "spread_bps": (
            (Decimal(ask) - Decimal(bid))
            / ((Decimal(ask) + Decimal(bid)) / Decimal("2"))
            * Decimal("10000")
        ),
        "execution_eligible": eligible,
        "freshness_mode": "live",
        "rejection_reasons": (),
        "halted": False,
    }


def test_market_buy_uses_ask_then_adverse_slippage() -> None:
    simulation = simulate_stoch_execution(
        _execution(bid="9.95", ask="10.05"),
        action="entry",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("1"),
    )

    assert simulation.fill_complete is True
    assert simulation.fill_price == Decimal("10.05") * Decimal("1.001")
    assert simulation.spread_bps == Decimal("100")
    assert simulation.spread_tier == "acceptable"
    assert simulation.estimated_round_trip_cost_bps == Decimal("120")


def test_market_sell_uses_bid_then_adverse_slippage() -> None:
    simulation = simulate_stoch_execution(
        _execution(bid="9.95", ask="10.05"),
        action="range_exit",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("1"),
    )

    assert simulation.fill_complete is True
    assert simulation.fill_price == Decimal("9.95") * Decimal("0.999")
    assert simulation.estimated_round_trip_cost_bps is None


def test_late_quote_is_not_backfilled_as_executable_fill() -> None:
    simulation = simulate_stoch_execution(
        _execution(
            bid="9.95",
            ask="10.05",
            source_offset_seconds=90,
        ),
        action="entry",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("1"),
    )

    assert simulation.fill_complete is False
    assert simulation.should_fill is False
    assert simulation.fill_price is None
    assert simulation.fill_reason == "execution_capture_too_late"
    assert simulation.capture_lag_seconds == Decimal("90")


def test_spread_tiers_make_expensive_execution_visible() -> None:
    assert spread_tier(Decimal("25")) == "tight"
    assert spread_tier(Decimal("75")) == "acceptable"
    assert spread_tier(Decimal("125")) == "expensive"
    assert spread_tier(Decimal("200")) == "extreme"
    assert spread_tier(None) == "unknown"


def test_weighted_partial_runner_summary_reports_net_execution_drag() -> None:
    entry = simulate_stoch_execution(
        _execution(bid="9.95", ask="10.05"),
        action="entry",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("1"),
    )
    partial = simulate_stoch_execution(
        _execution(bid="11.95", ask="12.05", last="12"),
        action="partial_exit",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("0.25"),
    )
    runner = simulate_stoch_execution(
        _execution(bid="12.95", ask="13.05", last="13"),
        action="runner_exit",
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        decision_at=DECISION_AT,
        requested_fraction=Decimal("0.75"),
    )
    snapshot = StochTrendCaptureSnapshot(
        state="trend_exited",
        reason_code="STOCH_TREND_BREAK_EXIT",
        three_minute_bar_count=100,
        entry_signal_time=DECISION_AT,
        entry_time=DECISION_AT + timedelta(minutes=3),
        entry_price=Decimal("10"),
        first_overbought_time=DECISION_AT + timedelta(minutes=30),
        trend_break_time=DECISION_AT + timedelta(hours=2),
        partial_exit_time=DECISION_AT + timedelta(minutes=33),
        partial_exit_price=Decimal("12"),
        runner_exit_time=DECISION_AT + timedelta(hours=2, minutes=3),
        runner_exit_price=Decimal("13"),
        combined_exit_price=Decimal("12.75"),
        return_pct=Decimal("27.5"),
    )

    summary = build_execution_summary(
        snapshot,
        entry_payload={"execution_simulation": entry.model_dump(mode="json")},
        action_payloads={
            "partial_exit": {
                "execution_simulation": partial.model_dump(mode="json"),
            },
            "runner_exit": {
                "execution_simulation": runner.model_dump(mode="json"),
            },
        },
    )

    expected_exit = (
        partial.fill_price * Decimal("0.25")
        + runner.fill_price * Decimal("0.75")
    )
    expected_net = (
        expected_exit / entry.fill_price - Decimal("1")
    ) * Decimal("100")
    assert summary.complete is True
    assert summary.reason_code == "STOCH_EXECUTION_NET_RETURN_READY"
    assert summary.weighted_exit_fill_price == expected_exit
    assert summary.net_execution_return_pct == expected_net
    assert summary.execution_drag_pct == Decimal("27.5") - expected_net
    assert summary.execution_drag_pct > 0


def test_summary_fails_closed_without_executable_entry() -> None:
    snapshot = StochTrendCaptureSnapshot(
        state="range_exited",
        reason_code="STOCH_TREND_RANGE_OVERBOUGHT_EXIT",
        three_minute_bar_count=50,
        entry_price=Decimal("10"),
        runner_exit_price=Decimal("11"),
        return_pct=Decimal("10"),
    )

    summary = build_execution_summary(
        snapshot,
        entry_payload={"execution_simulation": None},
        action_payloads={},
    )

    assert summary.complete is False
    assert summary.reason_code == "STOCH_EXECUTION_ENTRY_UNAVAILABLE"
    assert summary.net_execution_return_pct is None


def test_action_mapping_and_fraction_follow_trend_lifecycle() -> None:
    snapshot = StochTrendCaptureSnapshot(
        state="trend_exit_armed",
        reason_code="STOCH_TREND_BREAK_EXIT_ARMED",
        three_minute_bar_count=80,
        entry_signal_time=DECISION_AT,
        trend_break_time=DECISION_AT + timedelta(hours=2),
        partial_exit_time=DECISION_AT + timedelta(hours=1),
    )

    action = action_for_snapshot(snapshot)

    assert action == ("runner_exit", snapshot.trend_break_time)
    assert requested_fraction_for_action("runner_exit", snapshot) == Decimal("0.75")
