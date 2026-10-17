from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.strategy_backtest import GapPullbackBacktestTrade
from app.apps.trading.strategy_partial_runner_research import (
    partial_target_quantity,
    replay_partial_profit_runner,
)
from app.apps.trading.strategy_v2_qualification import frozen_v2_config


def _bars(*, stop_and_target_same_bar: bool = False) -> list[MarketBar]:
    start = datetime(2026, 1, 5, 14, 35, tzinfo=timezone.utc)
    bars: list[MarketBar] = []
    for index in range(65):
        ts = start + timedelta(minutes=index)
        if index < 5:
            open_price = Decimal("10")
            high = Decimal("10.10")
            low = Decimal("9.90")
            close = Decimal("10")
        elif index == 5:
            open_price = Decimal("10.80")
            high = Decimal("11.20") if stop_and_target_same_bar else Decimal("11")
            low = Decimal("8.80") if stop_and_target_same_bar else Decimal("10.70")
            close = Decimal("10.80")
        else:
            open_price = Decimal("10.80")
            high = Decimal("10.85")
            low = Decimal("10.70")
            close = Decimal("10.80")
        bars.append(
            MarketBar(
                instrument_id="equity:test",
                interval="1m",
                start_time=ts,
                end_time=ts + timedelta(minutes=1),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=Decimal("10000"),
                provider="test",
            )
        )
    return bars


def _baseline(entry_time: datetime) -> GapPullbackBacktestTrade:
    return GapPullbackBacktestTrade.model_construct(
        instrument_id="equity:test",
        entry_time=entry_time,
        exit_time=entry_time + timedelta(minutes=60),
        entry_price=Decimal("10"),
        stop_price=Decimal("9"),
        entry_fill_quantity=Decimal("10"),
    )


def _candidate() -> GapperCandidate:
    return GapperCandidate.model_construct(
        instrument_id="equity:test",
        binding_id=None,
    )


def _policy() -> PaperExecutionPolicy:
    return PaperExecutionPolicy(
        slippage_bps=Decimal("0"),
        stop_slippage_bps=Decimal("0"),
        max_volume_participation_pct=Decimal("1"),
    )


def test_partial_target_quantity_is_exactly_half() -> None:
    assert partial_target_quantity(Decimal("10")) == Decimal("5")
    assert partial_target_quantity(Decimal("3.5")) == Decimal("1.75")


def test_partial_at_one_r_then_force_flat_runner_combines_both_legs() -> None:
    bars = _bars()
    trade = replay_partial_profit_runner(
        candidate=_candidate(),
        bars=bars,
        baseline_trade=_baseline(bars[0].start_time),
        config=frozen_v2_config(),
        policy=_policy(),
        assumed_spread_bps=Decimal("0"),
    )

    assert trade.partial_target_price == Decimal("11")
    assert trade.partial_filled_quantity == Decimal("5")
    assert trade.partial_fill_vwap == Decimal("11")
    assert trade.runner_exit_reason == "force_flat"
    assert trade.combined_exit_price == Decimal("10.90")
    assert trade.r_multiple == Decimal("0.90")
    assert trade.weighted_hold_minutes < trade.final_hold_minutes


def test_stop_has_priority_when_stop_and_partial_target_share_a_bar() -> None:
    bars = _bars(stop_and_target_same_bar=True)
    trade = replay_partial_profit_runner(
        candidate=_candidate(),
        bars=bars,
        baseline_trade=_baseline(bars[0].start_time),
        config=frozen_v2_config(),
        policy=_policy(),
        assumed_spread_bps=Decimal("0"),
    )

    assert trade.partial_filled_quantity == 0
    assert trade.runner_exit_reason == "stop"
    assert trade.combined_exit_price == Decimal("9")
    assert trade.r_multiple == Decimal("-1")
