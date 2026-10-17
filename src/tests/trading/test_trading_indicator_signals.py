from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.indicator_signals import (
    indicator_entry_confirmation,
    multi_timeframe_indicator_context,
)
from app.apps.trading.models import MarketBar


def _bar(index: int, close: Decimal, *, session_day: int = 20) -> MarketBar:
    start = datetime(2026, 8, session_day, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=index)
    return MarketBar(
        instrument_id="equity:TEST",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=close - Decimal("0.01"),
        high=close + Decimal("0.02"),
        low=close - Decimal("0.02"),
        close=close,
        volume=Decimal("10000") + Decimal(index * 10),
        is_final=True,
        session="regular",
        provider="fixture",
        provider_event_id=f"bar-{session_day}-{index}",
        received_at=start + timedelta(minutes=1),
    )


def _accelerating_close(index: int) -> Decimal:
    return Decimal("5") + Decimal(index * index) * Decimal("0.0005")


def test_indicator_context_confirms_a_mature_uptrend_without_fabricating_5m_long_warmups() -> None:
    bars = [_bar(index, _accelerating_close(index)) for index in range(60)]

    context = multi_timeframe_indicator_context(bars)
    allowed, reasons = indicator_entry_confirmation(context)

    assert context.one_minute.ema9 is not None
    assert context.one_minute.ema20 is not None
    assert context.one_minute.macd_bullish is True
    assert context.one_minute.stochastic_rsi_bullish is True
    assert context.five_minute.ema9 is not None
    assert context.five_minute.ema20 is None
    assert context.five_minute.macd is None
    assert context.five_minute.stochastic_rsi_k is None
    assert allowed is True
    assert reasons == ()


def test_indicator_context_rejects_bearish_entry_confluence() -> None:
    bars = [_bar(index, Decimal("8") - Decimal(index) * Decimal("0.02")) for index in range(60)]

    context = multi_timeframe_indicator_context(bars)
    allowed, reasons = indicator_entry_confirmation(context)

    assert allowed is False
    assert "INDICATOR_1M_PRICE_BELOW_EMA9" in reasons
    assert "INDICATOR_1M_EMA_STACK_BEARISH" in reasons
    assert "INDICATOR_1M_MACD_BEARISH" in reasons
    assert "INDICATOR_5M_PRICE_BELOW_EMA9" in reasons


def test_indicator_context_uses_only_latest_et_session() -> None:
    previous = [_bar(index, Decimal("100") + Decimal(index), session_day=19) for index in range(60)]
    current = [_bar(index, _accelerating_close(index), session_day=20) for index in range(60)]

    context = multi_timeframe_indicator_context([*previous, *current])

    assert context.session_date == "2026-08-20"
    assert context.source_bar_count == 60
    assert context.one_minute.close == current[-1].close
