from __future__ import annotations

"""Deterministic indicator snapshots for trading research and telemetry.

The Trading UI already renders EMA/SMA, MACD and Stochastic RSI.  This module
provides a server-side Decimal implementation over finalized MarketBar prefixes
so prospective SHADOW evidence and cache-only research can use the same classes
of signals without giving indicators order authority.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import MarketBar
from .strategy_timeframes import resample_final_bars
from app.apps.trading.us_equity_calendar import EASTERN as _ET


Interval = Literal["1m", "5m"]


class IndicatorSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interval: Interval
    as_of: datetime | None = None
    bar_count: int = 0
    close: Decimal | None = None
    ema9: Decimal | None = None
    ema20: Decimal | None = None
    ema9_change: Decimal | None = None
    macd: Decimal | None = None
    macd_signal: Decimal | None = None
    macd_histogram: Decimal | None = None
    stochastic_rsi_k: Decimal | None = None
    stochastic_rsi_d: Decimal | None = None
    price_above_ema9: bool | None = None
    ema9_above_ema20: bool | None = None
    ema9_rising: bool | None = None
    macd_bullish: bool | None = None
    stochastic_rsi_bullish: bool | None = None


class MultiTimeframeIndicatorContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_bar_count: int
    session_date: str | None = None
    one_minute: IndicatorSnapshot
    five_minute: IndicatorSnapshot


def _ema_aligned(values: list[Decimal], period: int) -> list[Decimal | None]:
    if period < 1:
        raise ValueError("EMA period must be positive")
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    average = sum(values[:period], Decimal("0")) / Decimal(period)
    out[period - 1] = average
    multiplier = Decimal("2") / Decimal(period + 1)
    for index in range(period, len(values)):
        average = (values[index] - average) * multiplier + average
        out[index] = average
    return out


def _rsi_aligned(values: list[Decimal], period: int) -> list[Decimal | None]:
    if period < 1:
        raise ValueError("RSI period must be positive")
    out: list[Decimal | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(max(-change, Decimal("0")))
    average_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
    average_loss = sum(losses[:period], Decimal("0")) / Decimal(period)

    def value() -> Decimal:
        if average_loss == 0:
            return Decimal("100")
        rs = average_gain / average_loss
        return Decimal("100") - Decimal("100") / (Decimal("1") + rs)

    out[period] = value()
    for change_index in range(period, len(gains)):
        average_gain = (average_gain * Decimal(period - 1) + gains[change_index]) / Decimal(period)
        average_loss = (average_loss * Decimal(period - 1) + losses[change_index]) / Decimal(period)
        out[change_index + 1] = value()
    return out


def _sma_dense(values: list[Decimal], period: int) -> list[Decimal]:
    if period < 1:
        raise ValueError("SMA period must be positive")
    if len(values) < period:
        return []
    total = sum(values[:period], Decimal("0"))
    out = [total / Decimal(period)]
    for index in range(period, len(values)):
        total += values[index] - values[index - period]
        out.append(total / Decimal(period))
    return out


def _macd_aligned(
    values: list[Decimal],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[list[Decimal | None], list[Decimal | None], list[Decimal | None]]:
    if fast_period >= slow_period:
        raise ValueError("MACD fast period must be smaller than slow period")
    fast = _ema_aligned(values, fast_period)
    slow = _ema_aligned(values, slow_period)
    macd: list[Decimal | None] = [None] * len(values)
    dense: list[Decimal] = []
    dense_indexes: list[int] = []
    for index in range(len(values)):
        fast_value, slow_value = fast[index], slow[index]
        if fast_value is None or slow_value is None:
            continue
        value = fast_value - slow_value
        macd[index] = value
        dense.append(value)
        dense_indexes.append(index)
    signal_dense = _ema_aligned(dense, signal_period)
    signal: list[Decimal | None] = [None] * len(values)
    histogram: list[Decimal | None] = [None] * len(values)
    for dense_index, original_index in enumerate(dense_indexes):
        signal_value = signal_dense[dense_index]
        if signal_value is None:
            continue
        signal[original_index] = signal_value
        macd_value = macd[original_index]
        assert macd_value is not None
        histogram[original_index] = macd_value - signal_value
    return macd, signal, histogram


def _stochastic_rsi_aligned(
    values: list[Decimal],
    *,
    rsi_period: int = 14,
    stochastic_period: int = 14,
    smoothing_period: int = 3,
    signal_period: int = 3,
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    rsi = _rsi_aligned(values, rsi_period)
    raw_values: list[Decimal] = []
    raw_indexes: list[int] = []
    for index in range(len(values)):
        start = index - stochastic_period + 1
        if start < 0:
            continue
        window = rsi[start : index + 1]
        if len(window) != stochastic_period or any(value is None for value in window):
            continue
        defined = [value for value in window if value is not None]
        low = min(defined)
        high = max(defined)
        current = rsi[index]
        assert current is not None
        raw = Decimal("50") if high == low else (current - low) / (high - low) * Decimal("100")
        raw_values.append(raw)
        raw_indexes.append(index)

    k_dense = _sma_dense(raw_values, smoothing_period)
    k_indexes = raw_indexes[smoothing_period - 1 :] if k_dense else []
    d_dense = _sma_dense(k_dense, signal_period)
    d_indexes = k_indexes[signal_period - 1 :] if d_dense else []

    k: list[Decimal | None] = [None] * len(values)
    d: list[Decimal | None] = [None] * len(values)
    for index, value in zip(k_indexes, k_dense, strict=True):
        k[index] = max(Decimal("0"), min(Decimal("100"), value))
    for index, value in zip(d_indexes, d_dense, strict=True):
        d[index] = max(Decimal("0"), min(Decimal("100"), value))
    return k, d


def _session_bars(bars: list[MarketBar] | tuple[MarketBar, ...]) -> list[MarketBar]:
    finalized = sorted((bar for bar in bars if bar.is_final), key=lambda bar: bar.start_time)
    if not finalized:
        return []
    session_date = finalized[-1].start_time.astimezone(_ET).date()
    return [bar for bar in finalized if bar.start_time.astimezone(_ET).date() == session_date]


def indicator_snapshot(
    bars: list[MarketBar] | tuple[MarketBar, ...],
    interval: Interval,
) -> IndicatorSnapshot:
    session = _session_bars(bars)
    sampled = list(resample_final_bars(session, interval))
    if not sampled:
        return IndicatorSnapshot(interval=interval)
    values = [bar.close for bar in sampled]
    ema9 = _ema_aligned(values, 9)
    ema20 = _ema_aligned(values, 20)
    macd, signal, histogram = _macd_aligned(values)
    stoch_k, stoch_d = _stochastic_rsi_aligned(values)
    index = len(sampled) - 1
    current_ema9 = ema9[index]
    previous_ema9 = ema9[index - 1] if index > 0 else None
    current_ema20 = ema20[index]
    current_macd = macd[index]
    current_signal = signal[index]
    current_k = stoch_k[index]
    current_d = stoch_d[index]
    close = sampled[index].close
    return IndicatorSnapshot(
        interval=interval,
        as_of=sampled[index].end_time,
        bar_count=len(sampled),
        close=close,
        ema9=current_ema9,
        ema20=current_ema20,
        ema9_change=(current_ema9 - previous_ema9) if current_ema9 is not None and previous_ema9 is not None else None,
        macd=current_macd,
        macd_signal=current_signal,
        macd_histogram=histogram[index],
        stochastic_rsi_k=current_k,
        stochastic_rsi_d=current_d,
        price_above_ema9=(close > current_ema9) if current_ema9 is not None else None,
        ema9_above_ema20=(current_ema9 > current_ema20) if current_ema9 is not None and current_ema20 is not None else None,
        ema9_rising=(current_ema9 > previous_ema9) if current_ema9 is not None and previous_ema9 is not None else None,
        macd_bullish=(current_macd > current_signal) if current_macd is not None and current_signal is not None else None,
        stochastic_rsi_bullish=(current_k >= current_d) if current_k is not None and current_d is not None else None,
    )


def multi_timeframe_indicator_context(
    bars: list[MarketBar] | tuple[MarketBar, ...],
) -> MultiTimeframeIndicatorContext:
    session = _session_bars(bars)
    session_date = session[-1].start_time.astimezone(_ET).date().isoformat() if session else None
    return MultiTimeframeIndicatorContext(
        source_bar_count=len(session),
        session_date=session_date,
        one_minute=indicator_snapshot(session, "1m"),
        five_minute=indicator_snapshot(session, "5m"),
    )


def indicator_entry_confirmation(context: MultiTimeframeIndicatorContext) -> tuple[bool, tuple[str, ...]]:
    """Predeclared research confirmation for an existing structural long signal.

    1m indicators must all be available and bullish.  The 5m EMA9 must be
    available, rising and below price.  Longer-warmup 5m EMA20/MACD/Stoch RSI are
    vetoes when causally available, but missing evidence is neutral so old
    regular-session-only caches are not silently assigned synthetic history.
    """

    one = context.one_minute
    five = context.five_minute
    reasons: list[str] = []

    required_one_minute = {
        "INDICATOR_1M_EMA9_MISSING": one.ema9 is not None,
        "INDICATOR_1M_EMA20_MISSING": one.ema20 is not None,
        "INDICATOR_1M_MACD_MISSING": one.macd_bullish is not None,
        "INDICATOR_1M_STOCH_RSI_MISSING": one.stochastic_rsi_bullish is not None,
    }
    for reason, available in required_one_minute.items():
        if not available:
            reasons.append(reason)
    if reasons:
        return False, tuple(reasons)

    if one.price_above_ema9 is not True:
        reasons.append("INDICATOR_1M_PRICE_BELOW_EMA9")
    if one.ema9_above_ema20 is not True:
        reasons.append("INDICATOR_1M_EMA_STACK_BEARISH")
    if one.ema9_rising is not True:
        reasons.append("INDICATOR_1M_EMA9_NOT_RISING")
    if one.macd_bullish is not True or (one.macd_histogram is not None and one.macd_histogram < 0):
        reasons.append("INDICATOR_1M_MACD_BEARISH")
    if one.stochastic_rsi_bullish is not True:
        reasons.append("INDICATOR_1M_STOCH_RSI_BEARISH")

    if five.ema9 is None or five.ema9_rising is None:
        reasons.append("INDICATOR_5M_EMA9_MISSING")
    else:
        if five.price_above_ema9 is not True:
            reasons.append("INDICATOR_5M_PRICE_BELOW_EMA9")
        if five.ema9_rising is not True:
            reasons.append("INDICATOR_5M_EMA9_NOT_RISING")

    if five.ema9_above_ema20 is False:
        reasons.append("INDICATOR_5M_EMA_STACK_BEARISH")
    if five.macd_bullish is False:
        reasons.append("INDICATOR_5M_MACD_BEARISH")
    if five.stochastic_rsi_bullish is False:
        reasons.append("INDICATOR_5M_STOCH_RSI_BEARISH")

    return not reasons, tuple(reasons)


__all__ = [
    "IndicatorSnapshot",
    "MultiTimeframeIndicatorContext",
    "indicator_entry_confirmation",
    "indicator_snapshot",
    "multi_timeframe_indicator_context",
]
