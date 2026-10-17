from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.models import MarketBar
from app.apps.trading import strategy_stoch_rsi_5m as strategy


OPEN = datetime(2026, 9, 16, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def _bar(
    index: int,
    *,
    open_: str | None = None,
    close_: str | None = None,
) -> MarketBar:
    start = OPEN + timedelta(minutes=5 * index)
    open_price = (
        Decimal(open_)
        if open_ is not None
        else Decimal("10") + Decimal(index) * Decimal("0.01")
    )
    close = Decimal(close_) if close_ is not None else open_price + Decimal("0.05")
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="5m",
        start_time=start,
        end_time=start + timedelta(minutes=5),
        open=open_price,
        high=close + Decimal("0.02"),
        low=open_price - Decimal("0.02"),
        close=close,
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=5),
    )


def _flat_indicators(values, **_kwargs):
    count = len(list(values))
    return [Decimal("50")] * count, [Decimal("50")] * count


def test_old_gap_resumes_after_stoch_rsi_clean_five_minute_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 09:30-09:55, then an unrecoverable 20-minute hole, then 32 clean 5m bars.
    bars = [_bar(index) for index in range(6)]
    bars += [_bar(index) for index in range(10, 42)]
    monkeypatch.setattr(strategy, "_stochastic_rsi_aligned", _flat_indicators)

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state != "data_gap"
    assert snapshot.reason_code == "STOCH_RSI_5M_WAITING_OVERSOLD_ARM"
    assert snapshot.five_minute_bar_count == 32
    assert snapshot.data_gap_start == _bar(6).start_time
    assert snapshot.data_gap_resume == _bar(10).start_time
    assert snapshot.execution_authority is False


def test_old_gap_remains_blocked_until_post_gap_warmup_is_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = [_bar(index) for index in range(6)]
    bars += [_bar(index) for index in range(10, 41)]  # 31 clean bars
    monkeypatch.setattr(strategy, "_stochastic_rsi_aligned", _flat_indicators)

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "data_gap"
    assert snapshot.reason_code == "STOCH_RSI_5M_POST_GAP_STOCH_RSI_WARMUP"
    assert snapshot.five_minute_bar_count == 31
    assert snapshot.data_gap_resume == _bar(10).start_time


def test_open_position_spanning_gap_exits_on_first_usable_post_gap_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The prefix enters immediately before the gap. The first usable post-gap
    # close is below the 5m EMA, so the next observed bar is the exit.
    # Two missing buckets (55, 56): longer than the one-bucket bridge.
    bars = [_bar(index) for index in range(55)]
    bars[52] = _bar(52, open_="10.8", close_="11.0")
    bars[53] = _bar(53, open_="11.0", close_="11.1")
    bars += [
        _bar(57, open_="8.98", close_="9.0"),
        _bar(58, open_="9.0", close_="9.1"),
    ]
    bars.extend(_bar(index) for index in range(59, 62))

    def active_trade_indicators(values, **_kwargs):
        count = len(list(values))
        k = [Decimal("50")] * count
        d = [Decimal("40")] * count
        if count >= 55:
            k[49], d[49] = Decimal("5"), Decimal("8")
            k[50], d[50] = Decimal("9"), Decimal("7")
            k[51], d[51] = Decimal("22"), Decimal("15")
            k[52], d[52] = Decimal("40"), Decimal("30")
        return k, d

    monkeypatch.setattr(strategy, "_stochastic_rsi_aligned", active_trade_indicators)

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert snapshot.reason_code == "STOCH_RSI_5M_CLOSE_BELOW_5_5M_EMA"
    assert len(snapshot.trades) == 1
    assert snapshot.entry_time == _bar(52).start_time
    assert snapshot.entry_price == Decimal("10.8")
    assert snapshot.exit_signal_time == _bar(57).end_time
    assert snapshot.exit_time == _bar(58).start_time
    assert snapshot.exit_price == Decimal("9.0")
    assert snapshot.data_gap_start == _bar(55).start_time
    assert snapshot.data_gap_resume == _bar(57).start_time


def test_an_open_position_continues_across_a_single_missing_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only bucket 55 is missing (a short halt): no gap state, the trade just continues.
    bars = [_bar(index) for index in range(55)]
    bars[52] = _bar(52, open_="10.8", close_="11.0")
    bars[53] = _bar(53, open_="11.0", close_="11.1")
    bars += [
        _bar(56, open_="8.98", close_="9.0"),
        _bar(57, open_="9.0", close_="9.1"),
    ]
    bars.extend(_bar(index) for index in range(58, 61))

    def active_trade_indicators(values, **_kwargs):
        count = len(list(values))
        k = [Decimal("50")] * count
        d = [Decimal("40")] * count
        if count >= 55:
            k[49], d[49] = Decimal("5"), Decimal("8")
            k[50], d[50] = Decimal("9"), Decimal("7")
            k[51], d[51] = Decimal("22"), Decimal("15")
            k[52], d[52] = Decimal("40"), Decimal("30")
        return k, d

    monkeypatch.setattr(strategy, "_stochastic_rsi_aligned", active_trade_indicators)

    snapshot = strategy.evaluate_stoch_rsi_5m(bars)

    assert snapshot.state == "exited"
    assert snapshot.entry_time == _bar(52).start_time
    assert snapshot.exit_signal_time == _bar(56).end_time
    assert snapshot.exit_time == _bar(57).start_time
    assert snapshot.data_gap_start is None
