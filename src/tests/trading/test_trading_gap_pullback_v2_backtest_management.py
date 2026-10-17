from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import app.apps.trading.strategy_backtest as backtest
from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.strategies.models import (
    GapPullbackConfig,
    GapPullbackFeatures,
    GapPullbackResult,
    StrategySignal,
)


INSTRUMENT = "equity:NASDAQ:V2MGMT"
OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, open_: str, high: str, low: str, close: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def _candidate() -> GapperCandidate:
    return GapperCandidate(
        instrument_id=INSTRUMENT,
        binding_id="fixture:V2MGMT",
        previous_close=Decimal("8"),
        premarket_price=Decimal("10"),
        gap_pct=Decimal("25"),
        premarket_volume=Decimal("100000"),
        premarket_dollar_volume=Decimal("1000000"),
        tod_rvol=Decimal("5"),
        spread_bps=Decimal("0"),
        discovery_rank=1,
    )


def _config() -> GapPullbackConfig:
    return GapPullbackConfig(
        strategy_version="2.0.0",
        structure_interval="1m",
        execution_interval="1m",
        reward_multiple=Decimal("1.5"),
        v2_profit_protection_trigger_r=Decimal("0.75"),
        v2_protected_stop_r=Decimal("0.25"),
        v2_max_hold_minutes=60,
    )


def test_v2_backtest_cannot_arm_protected_stop_on_same_bar(monkeypatch) -> None:
    bars = (
        _bar(0, "10", "10.1", "9.9", "10"),
        _bar(1, "10", "10.1", "9.9", "10"),
        # Entry occurs at this bar's open. It reaches +0.75R intrabar, but its
        # low is below the future +0.25R stop. Causal management must NOT let
        # this bar tighten its own stop.
        _bar(2, "10", "10.80", "9.50", "10.60"),
        # The prior finalized bar has now armed +0.25R. This bar crosses 10.25
        # while staying safely above the original structural stop at 9.00.
        _bar(3, "10.40", "10.50", "10.10", "10.20"),
        _bar(4, "10.20", "10.30", "10.00", "10.10"),
    )
    signal = StrategySignal(
        instrument_id=INSTRUMENT,
        state="entry_ready",
        entry_price=Decimal("10"),
        stop_price=Decimal("9"),
        target_price=Decimal("11.5"),
        risk_per_share=Decimal("1"),
        reason_code="TEST_V2_ENTRY",
    )

    def fake_evaluate(candidate, visible_bars, config):
        ready = len(visible_bars) >= 2
        return GapPullbackResult(
            instrument_id=candidate.instrument_id,
            state="entry_ready" if ready else "qualified_gap",
            reason_code="TEST_V2_ENTRY" if ready else "WAITING",
            features=GapPullbackFeatures(gap_pct=candidate.gap_pct),
            transitions=("discovered", "entry_ready") if ready else ("discovered", "qualified_gap"),
            signal=signal if ready else None,
            evaluated_bar_count=len(visible_bars),
        )

    monkeypatch.setattr(backtest, "evaluate_gap_pullback", fake_evaluate)
    attempt = backtest._find_trade(
        _candidate(),
        bars,
        _config(),
        PaperExecutionPolicy(
            slippage_bps=Decimal("0"),
            stop_slippage_bps=Decimal("0"),
            max_volume_participation_pct=Decimal("1"),
            latency_ms=0,
        ),
        assumed_spread_bps=Decimal("0"),
        max_hold_minutes=90,
    )

    assert attempt.trade is not None
    assert attempt.trade.entry_bar_index == 2
    assert attempt.trade.exit_time == bars[3].end_time
    assert attempt.trade.exit_reason == "stop"
    assert attempt.trade.exit_price == Decimal("10.25")
    assert attempt.trade.r_multiple == Decimal("0.25")
