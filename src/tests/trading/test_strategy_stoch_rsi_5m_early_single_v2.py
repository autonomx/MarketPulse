from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading import strategy_stoch_rsi_5m_early_single_v2 as v2
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mSnapshot, StochRsi5mTrade

# 2026-09-10 09:30 ET.
OPEN = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, *, open_: str, high: str, low: str, close: str) -> MarketBar:
    start = OPEN + timedelta(minutes=5 * index)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _flat(index: int, price: str = "10") -> MarketBar:
    return _bar(index, open_=price, high=price, low=price, close=price)


def _trade(*, entry_index: int, exit_index: int, entry: str = "10", exit_: str = "10.5") -> StochRsi5mTrade:
    entry_time = OPEN + timedelta(minutes=5 * entry_index)
    exit_time = OPEN + timedelta(minutes=5 * exit_index)
    return StochRsi5mTrade(
        oversold_arm_time=entry_time,
        momentum_cross_time=entry_time,
        entry_signal_time=entry_time,
        entry_time=entry_time,
        entry_price=Decimal(entry),
        exit_signal_time=exit_time,
        exit_time=exit_time,
        exit_price=Decimal(exit_),
        exit_reason_code="STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA",
        return_pct=(Decimal(exit_) - Decimal(entry)) / Decimal(entry) * Decimal("100"),
    )


def test_breakeven_stop_exits_a_trade_that_was_up_before_the_canonical_exit() -> None:
    bars = [
        _flat(0),
        _bar(1, open_="10", high="10.40", low="10", close="10.30"),  # +4% locks
        _bar(2, open_="10.20", high="10.20", low="9.80", close="9.85"),  # hits 10
        _flat(3, "9.50"),
        _flat(4, "9.50"),
    ]
    trade = _trade(entry_index=1, exit_index=4, exit_="9.50")

    managed, partial_time, _ = v2.manage_trade(bars, trade, v2.ExitPolicy())

    assert managed.exit_reason_code == "STOCH_RSI_5M_EARLY_SINGLE_V2_BREAKEVEN_STOP"
    assert managed.exit_time == bars[2].start_time
    assert managed.exit_price == Decimal("10")
    assert managed.return_pct == Decimal("0")
    assert partial_time is None


def test_stop_set_by_a_bar_does_not_act_inside_that_bar() -> None:
    bars = [
        _flat(0),
        # Reaches the lock trigger and trades below entry in the same bar.
        _bar(1, open_="10", high="10.40", low="9.70", close="10.20"),
        _flat(2, "10.20"),
    ]
    trade = _trade(entry_index=1, exit_index=2, exit_="10.20")

    managed, _, _ = v2.manage_trade(bars, trade, v2.ExitPolicy())

    assert managed == trade


def test_management_never_holds_past_the_canonical_exit() -> None:
    bars = [_flat(0), _bar(1, open_="10", high="11", low="10", close="11"), _flat(2, "10.5"), _flat(3, "13")]
    trade = _trade(entry_index=1, exit_index=2, exit_="10.5")

    managed, _, _ = v2.manage_trade(bars, trade, v2.ExitPolicy(trail_atr_multiple=Decimal("2"), atr_period=2))

    assert managed.exit_time == trade.exit_time
    assert managed.exit_price == trade.exit_price


def test_gap_through_stop_fills_at_open() -> None:
    bars = [
        _flat(0),
        _bar(1, open_="10", high="10.50", low="10", close="10.40"),
        _bar(2, open_="9.60", high="9.70", low="9.50", close="9.60"),
        _flat(3, "9.60"),
    ]
    trade = _trade(entry_index=1, exit_index=3, exit_="9.60")

    managed, _, _ = v2.manage_trade(bars, trade, v2.ExitPolicy())

    assert managed.exit_price == Decimal("9.60")


def test_trailing_stop_follows_the_high_by_atr_multiple() -> None:
    bars = [
        _bar(0, open_="10", high="10.10", low="9.90", close="10"),  # range 0.2
        _bar(1, open_="10", high="10.10", low="9.90", close="10"),  # entry; ATR(2)=0.2
        _bar(2, open_="10.80", high="11", low="10.80", close="10.90"),  # high 11, TR 1.0, ATR 0.6
        _bar(3, open_="10.80", high="10.80", low="9.70", close="9.70"),  # trades through the trailed stop
        _flat(4, "9"),
    ]
    trade = _trade(entry_index=1, exit_index=4, exit_="9")

    managed, _, _ = v2.manage_trade(bars, trade, v2.ExitPolicy(trail_atr_multiple=Decimal("0.5"), atr_period=2))

    # After bar 2: ATR(2) = (0.2 + 1.0) / 2 = 0.6, stop = max(entry, 11 - 0.3) = 10.70.
    assert managed.exit_reason_code == "STOCH_RSI_5M_EARLY_SINGLE_V2_TRAILING_STOP"
    assert managed.exit_time == bars[3].start_time
    assert managed.exit_price == Decimal("10.70")


def test_partial_take_blends_target_and_remainder_returns() -> None:
    bars = [
        _flat(0),
        _bar(1, open_="10", high="11.20", low="10", close="11.10"),  # target 11 fills
        _bar(2, open_="11.10", high="11.30", low="11.05", close="11.20"),
        _flat(3, "11.20"),
    ]
    trade = _trade(entry_index=1, exit_index=3, exit_="11.20")
    policy = v2.ExitPolicy(partial_target_pct=Decimal("10"), partial_fraction=Decimal("0.5"))

    managed, partial_time, partial_price = v2.manage_trade(bars, trade, policy)

    assert partial_time == bars[1].start_time
    assert partial_price == Decimal("11")
    assert managed.return_pct == Decimal("11")  # 0.5 * 10% + 0.5 * 12%


def test_stop_wins_when_one_bar_touches_stop_and_target() -> None:
    bars = [
        _flat(0),
        _bar(1, open_="10", high="10.50", low="10", close="10.40"),
        _bar(2, open_="10.40", high="11.50", low="9.90", close="10"),
        _flat(3, "10"),
    ]
    trade = _trade(entry_index=1, exit_index=3, exit_="10")

    managed, partial_time, _ = v2.manage_trade(
        bars, trade, v2.ExitPolicy(partial_target_pct=Decimal("10"))
    )

    assert managed.exit_reason_code == "STOCH_RSI_5M_EARLY_SINGLE_V2_BREAKEVEN_STOP"
    assert partial_time is None


@pytest.mark.parametrize(
    ("entry_index", "entry_price", "weight", "reason"),
    [
        (2, "8.90", Decimal("0.5"), "CONTEXT_HALF_ENTRY_10PCT_BELOW_OPEN"),
        (12, "10.50", Decimal("0.5"), "CONTEXT_HALF_ENTRY_1030_1100"),
        (2, "10.50", Decimal("1"), "CONTEXT_FULL"),
    ],
)
def test_context_size_weight(entry_index: int, entry_price: str, weight: Decimal, reason: str) -> None:
    bars = [_flat(index) for index in range(14)]
    trade = _trade(entry_index=entry_index, exit_index=13, entry=entry_price)

    assert v2.context_size_weight(bars, trade) == (weight, reason)


def test_v2_passes_through_no_trade(monkeypatch) -> None:
    snapshot = StochRsi5mSnapshot(state="waiting_oversold", reason_code="STOCH_RSI_5M_WAITING_OVERSOLD_ARM")
    monkeypatch.setattr(v2, "evaluate_stoch_rsi_5m_early_single", lambda bars, config: snapshot)

    result = v2.evaluate_stoch_rsi_5m_early_single_v2([], "lock_and_sizing")

    assert result.trade is None
    assert result.reason_code == "STOCH_RSI_5M_WAITING_OVERSOLD_ARM"
    assert result.execution_authority is False


def test_lock_and_sizing_combines_management_and_weight(monkeypatch) -> None:
    bars = [
        _flat(0),
        _bar(1, open_="8.90", high="9.30", low="8.90", close="9.20"),  # 11% below open, locks
        _bar(2, open_="9.10", high="9.10", low="8.80", close="8.80"),
        _flat(3, "8.50"),
    ]
    trade = _trade(entry_index=1, exit_index=3, entry="8.90", exit_="8.50")
    snapshot = StochRsi5mSnapshot(state="exited", reason_code=trade.exit_reason_code, trades=(trade,))
    monkeypatch.setattr(v2, "evaluate_stoch_rsi_5m_early_single", lambda bars, config: snapshot)

    result = v2.evaluate_stoch_rsi_5m_early_single_v2(
        bars, "lock_and_sizing", policy=v2.ExitPolicy(partial_target_pct=Decimal("10"))
    )

    assert result.size_weight == Decimal("0.5")
    assert result.trade is not None
    assert result.trade.exit_price == Decimal("8.90")
    assert result.partial_exit_price is None
    assert result.canonical_return_pct == trade.return_pct
