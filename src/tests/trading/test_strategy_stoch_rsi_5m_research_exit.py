from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_stoch_rsi_5m import StochRsi5mTrade
from app.apps.trading import strategy_stoch_rsi_5m_research_exit as research_exit


START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, *, open_: str, close: str) -> MarketBar:
    start = START + timedelta(minutes=5 * index)
    open_value = Decimal(open_)
    close_value = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_value,
        high=max(open_value, close_value) + Decimal("0.10"),
        low=min(open_value, close_value) - Decimal("0.10"),
        close=close_value,
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _trade(bars: list[MarketBar], *, entry_index: int, exit_index: int) -> StochRsi5mTrade:
    entry = bars[entry_index]
    exit_bar = bars[exit_index]
    return StochRsi5mTrade(
        oversold_arm_time=bars[0].end_time,
        momentum_cross_time=bars[1].end_time,
        entry_signal_time=bars[2].end_time,
        entry_time=entry.start_time,
        entry_price=entry.open,
        exit_signal_time=bars[exit_index - 1].end_time,
        exit_time=exit_bar.start_time,
        exit_price=exit_bar.open,
        exit_reason_code="PARENT_EXIT",
        return_pct=(exit_bar.open - entry.open) / entry.open * Decimal("100"),
    )


def test_recompute_exit_starts_at_delayed_entry(monkeypatch) -> None:
    bars = [
        _bar(0, open_="10", close="10"),
        _bar(1, open_="10", close="10"),
        _bar(2, open_="10", close="10"),
        _bar(3, open_="10", close="10"),
        _bar(4, open_="10", close="10.2"),
        _bar(5, open_="10.2", close="10.3"),
        _bar(6, open_="10.3", close="9.0"),
        _bar(7, open_="8.9", close="9.0"),
    ]
    trade = _trade(bars, entry_index=5, exit_index=7)
    monkeypatch.setattr(
        research_exit,
        "exponential_moving_average",
        lambda closes, period: [Decimal("9.5")] * (len(bars) - period + 1),
    )
    monkeypatch.setattr(
        research_exit,
        "_stochastic_rsi_aligned",
        lambda closes, **kwargs: (
            [Decimal("50")] * len(bars),
            [Decimal("40")] * len(bars),
        ),
    )

    result = research_exit.recompute_stoch_rsi_5m_exit_from_entry(bars, trade)

    assert result.entry_time == bars[5].start_time
    assert result.exit_signal_time == bars[6].end_time
    assert result.exit_time == bars[7].start_time
    assert result.exit_price == Decimal("8.9")
    assert result.exit_reason_code == "STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA"


def test_recompute_exit_does_not_use_pre_entry_signal(monkeypatch) -> None:
    bars = [
        _bar(0, open_="10", close="10"),
        _bar(1, open_="10", close="10"),
        _bar(2, open_="10", close="8"),
        _bar(3, open_="8", close="10.2"),
        _bar(4, open_="10.2", close="10.3"),
        _bar(5, open_="10.3", close="10.4"),
        _bar(6, open_="10.4", close="9"),
        _bar(7, open_="8.9", close="9"),
    ]
    trade = _trade(bars, entry_index=4, exit_index=7)
    monkeypatch.setattr(
        research_exit,
        "exponential_moving_average",
        lambda closes, period: [Decimal("9.5")] * (len(bars) - period + 1),
    )
    monkeypatch.setattr(
        research_exit,
        "_stochastic_rsi_aligned",
        lambda closes, **kwargs: (
            [Decimal("50")] * len(bars),
            [Decimal("40")] * len(bars),
        ),
    )

    result = research_exit.recompute_stoch_rsi_5m_exit_from_entry(bars, trade)

    assert result.exit_signal_time == bars[6].end_time
    assert result.exit_time == bars[7].start_time
