"""Server ports of the chart's built-in indicators, batch C: the TVP-6.1 quick wins.

Each function mirrors one branch of ``quickWinOutputs`` in ``tradingViewBuiltIns.ts`` operation for operation;
the TypeScript helpers state each indicator's definition and ``BUILTIN_INPUTS`` there declares the params whose
defaults are repeated here (the goldens check both). Times are epoch milliseconds; sessions come from ``_sessions``.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import timedelta
from collections.abc import Sequence
from dataclasses import dataclass

from ._helpers import (
    Chart,
    MaybeNumber,
    Outputs,
    Values,
    builtin,
    builtin_with_compare_series,
    dmi,
    ema,
    finite,
    full,
    js_div,
    js_max,
    js_min,
    js_sqrt,
    rsi,
    stochastic,
)
from ..registry import IndicatorInputs
from ._sessions import DAY_MS, SessionPeriod, epoch_ms, session_clock, session_periods

PERIODS = ("D", "W", "M")
# What an indicator over sessions reads back, for alerts (``lookback_time``): calendar time, with a weekend or holiday
# of slack so the previous session is in reach.
_WEEKEND = timedelta(days=4)
_PERIOD_SPANS = {"D": timedelta(days=1), "W": timedelta(days=7), "M": timedelta(days=31), "240": timedelta(hours=4), "480": timedelta(hours=8)}


def _periods_back(count: int, inputs: IndicatorInputs, key: str, default: str) -> timedelta:
    value = inputs.param(key)
    span = _PERIOD_SPANS.get(str(value), _PERIOD_SPANS[default]) if value is not None else _PERIOD_SPANS[default]
    return span * count + _WEEKEND
PRICE_SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")


def _start_times(chart: Chart) -> list[int]:
    return [epoch_ms(start_time) for start_time in chart.bars.start_times]


@dataclass
class _PivotLevels:
    pp: list[MaybeNumber]
    r1: list[MaybeNumber]
    r2: list[MaybeNumber]
    r3: list[MaybeNumber]
    s1: list[MaybeNumber]
    s2: list[MaybeNumber]
    s3: list[MaybeNumber]


_Hlc = tuple[float, float, float]


def _set_pivots(levels: _PivotLevels, i: int, hlc: _Hlc) -> None:
    h, lo, c = hlc
    pp = (h + lo + c) / 3
    levels.pp[i] = pp
    levels.r1[i] = 2 * pp - lo
    levels.s1[i] = 2 * pp - h
    levels.r2[i] = pp + (h - lo)
    levels.s2[i] = pp - (h - lo)
    levels.r3[i] = h + 2 * (pp - lo)
    levels.s3[i] = lo - 2 * (h - pp)


def _extend(current: _Hlc | None, high: float, low: float, close: float) -> _Hlc:
    return (js_max(current[0], high), js_min(current[1], low), close) if current else (high, low, close)


def _period_pivots(
    keys: Sequence[int], counts: Sequence[bool], high: Values, low: Values, close: Values, developing: bool
) -> _PivotLevels:
    length = len(close)
    levels = _PivotLevels(*(full(length) for _ in range(7)))
    key = 0
    current: _Hlc | None = None
    previous: _Hlc | None = None
    for i in range(length):
        if i == 0 or keys[i] != key:
            if current:
                previous = current
            key = keys[i]
            current = None
        if counts[i]:
            current = _extend(current, high[i], low[i], close[i])
        source = current if developing else previous
        if source:
            _set_pivots(levels, i, source)
    return levels


def _missed_pivots(
    keys: Sequence[int], counts: Sequence[bool], high: Values, low: Values, close: Values, periods_back: int
) -> tuple[list[MaybeNumber], list[MaybeNumber]]:
    above = full(len(close))
    below = full(len(close))
    pending: list[tuple[float, int]] = []
    key = 0
    period = -1
    current: _Hlc | None = None
    for i in range(len(close)):
        if period < 0 or keys[i] != key:
            if current:
                pending.append(((current[0] + current[1] + current[2]) / 3, period + 1))
            period += 1
            key = keys[i]
            current = None
        if counts[i]:
            current = _extend(current, high[i], low[i], close[i])
        pending = [
            (value, level_period)
            for value, level_period in pending
            if level_period >= period - periods_back and not (low[i] <= value <= high[i])
        ]
        up: MaybeNumber = None
        down: MaybeNumber = None
        for value, level_period in pending:
            if level_period >= period:
                continue
            if value > close[i] and (up is None or value < up):
                up = value
            if value < close[i] and (down is None or value > down):
                down = value
        above[i] = up
        below[i] = down
    return above, below


def _relative_volume_at_time(
    keys: Sequence[int], since: Sequence[int], volume: Values, length: int, cumulative: bool
) -> list[MaybeNumber]:
    result = full(len(volume))
    history: dict[int, list[float]] = {}
    key = 0
    running = 0.0
    for i in range(len(volume)):
        if i == 0 or keys[i] != key:
            key = keys[i]
            running = 0.0
        running += volume[i]
        value = running if cumulative else volume[i]
        past = history.setdefault(since[i], [])
        if len(past) >= length:
            total = 0.0
            for k in range(len(past) - length, len(past)):
                total += past[k]
            average = total / length
            if average != 0:
                result[i] = js_div(value, average)
        past.append(value)
    return result


def _price_source(source: str, open_: Values, high: Values, low: Values, close: Values) -> list[float]:
    if source == "open":
        return list(open_)
    if source == "high":
        return list(high)
    if source == "low":
        return list(low)
    if source == "hl2":
        return [(h + lo) / 2 for h, lo in zip(high, low, strict=True)]
    if source == "hlc3":
        return [(h + lo + c) / 3 for h, lo, c in zip(high, low, close, strict=True)]
    if source == "ohlc4":
        return [(o + h + lo + c) / 4 for o, h, lo, c in zip(open_, high, low, close, strict=True)]
    return list(close)


def _rolling_day_volume(times: Sequence[int], volume: Values, price: Values) -> list[MaybeNumber]:
    result = full(len(volume))
    step: float = math.inf
    for i in range(1, len(times)):
        gap = times[i] - times[i - 1]
        if 0 < gap < step:
            step = gap
    if DAY_MS < step < math.inf:
        return result
    covered = min(step, DAY_MS)
    traded = [value * price[i] for i, value in enumerate(volume)]
    left = 0
    running = 0.0
    for i in range(len(volume)):
        running += traded[i]
        while left < i and times[left] <= times[i] - DAY_MS:
            running -= traded[left]
            left += 1
        if times[0] <= times[i] - DAY_MS + covered:
            result[i] = running
    return result


def _pearson(x: Values, y: Sequence[MaybeNumber], period: int) -> list[MaybeNumber]:
    result = full(len(x))
    for i in range(period - 1, len(x)):
        start = i - period + 1
        ys = [value for value in y[start : i + 1] if value is not None]
        if len(ys) < period:
            continue
        sx = 0.0
        sy = 0.0
        for j in range(period):
            sx += x[start + j]
            sy += ys[j]
        mx = sx / period
        my = sy / period
        sxx = syy = sxy = 0.0
        for j in range(period):
            dx = x[start + j] - mx
            dy = ys[j] - my
            sxx += dx * dx
            syy += dy * dy
            sxy += dx * dy
        result[i] = None if sxx == 0 or syy == 0 else js_div(sxy, js_sqrt(sxx * syy))
    return result


def _knoxville_divergence(
    high: Values, low: Values, close: Values, lookback: int, momentum_length: int, rsi_length: int
) -> tuple[list[MaybeNumber], list[MaybeNumber]]:
    bearish = full(len(close))
    bullish = full(len(close))
    momentum: list[MaybeNumber] = [
        value - close[i - momentum_length] if i >= momentum_length else None for i, value in enumerate(close)
    ]
    overbought: list[int] = []
    oversold: list[int] = []
    for i, value in enumerate(rsi(close, rsi_length)):
        overbought.append((overbought[i - 1] if i > 0 else 0) + (1 if finite(value) and value > 70 else 0))
        oversold.append((oversold[i - 1] if i > 0 else 0) + (1 if finite(value) and value < 30 else 0))

    def flagged(counts: list[int], start: int, end: int) -> bool:
        return counts[end] - (counts[start - 1] if start > 0 else 0) > 0

    highs: deque[int] = deque()
    lows: deque[int] = deque()
    for i in range(1, len(close)):
        while highs and high[highs[-1]] <= high[i - 1]:
            highs.pop()
        highs.append(i - 1)
        while lows and low[lows[-1]] >= low[i - 1]:
            lows.pop()
        lows.append(i - 1)
        while highs[0] < i - lookback:
            highs.popleft()
        while lows[0] < i - lookback:
            lows.popleft()
        now = momentum[i]
        if now is None:
            continue
        h = highs[0]
        lo = lows[0]
        at_high = momentum[h]
        at_low = momentum[lo]
        if high[i] > high[h] and at_high is not None and now < at_high and flagged(overbought, h, i):
            bearish[i] = high[i]
        if low[i] < low[lo] and at_low is not None and now > at_low and flagged(oversold, lo, i):
            bullish[i] = low[i]
    return bearish, bullish


def _complete_sma(values: Sequence[MaybeNumber], length: int) -> list[MaybeNumber]:
    result: list[MaybeNumber] = []
    for i in range(len(values)):
        if i < length - 1:
            result.append(None)
            continue
        total = 0.0
        complete = True
        for j in range(i - length + 1, i + 1):
            value = values[j]
            if not finite(value):
                complete = False
                break
            total += value
        result.append(total / length if complete else None)
    return result


@builtin("tv-24-hour-volume", "24-hour Volume", "exact", 1, lookback_time=lambda period, inputs: timedelta(hours=24))
def _volume_24_hours(chart: Chart) -> Outputs:
    source = chart.select_param("source", "close", PRICE_SOURCES)
    price = _price_source(source, chart.open, chart.high, chart.low, chart.close)
    return [chart.out("volume-24h", _rolling_day_volume(_start_times(chart), chart.volume, price))]


@builtin_with_compare_series("tv-correlation-coefficient-cc", "Correlation Coefficient (CC)", "exact", 20)
def _correlation_coefficient(chart: Chart) -> Outputs:
    return [chart.out("cc", _pearson(chart.close, chart.compare_close, chart.period))]


@builtin(
    "tv-relative-volume-at-time", "Relative Volume at Time", "exact", 5,
    lookback_time=lambda period, inputs: _periods_back(period + 1, inputs, "anchor", "D"),
)
def _relative_volume_at_time_builtin(chart: Chart) -> Outputs:
    anchor = chart.select_param("anchor", "D", PERIODS)
    cumulative = chart.select_param("mode", "cumulative", ("cumulative", "regular")) == "cumulative"
    keys, since = session_periods(session_clock(chart.bars, chart.inputs.session), anchor, chart.inputs.session)
    return [chart.out("rvol-at-time", _relative_volume_at_time(keys, since, chart.volume, chart.period, cumulative))]


# Breakouts are signals (markers on the bars where they happen), after ADX(14) and the box warm up.
@builtin("tv-rob-booker-adx-breakout", "Rob Booker - ADX Breakout", "recursive", 20, signal_warmup=60)
def _adx_breakout(chart: Chart) -> Outputs:
    high, low, close, lookback = chart.high, chart.low, chart.close, chart.period
    _, _, adx = dmi(high, low, close, int(chart.number_param("adxLength", 14, 1, integer=True)))
    level = chart.number_param("level", 18, 0)
    upper = full(len(close))
    lower = full(len(close))
    up = full(len(close))
    down = full(len(close))
    for i in range(lookback, len(close)):
        strength = adx[i]
        if not finite(strength) or not strength < level:
            continue
        top = high[i - lookback]
        bottom = low[i - lookback]
        for j in range(i - lookback + 1, i):
            top = js_max(top, high[j])
            bottom = js_min(bottom, low[j])
        upper[i] = top
        lower[i] = bottom
        if close[i] > top:
            up[i] = close[i]
        if close[i] < bottom:
            down[i] = close[i]
    return [
        chart.out("upper", upper),
        chart.out("lower", lower),
        chart.out("breakout-up", up),
        chart.out("breakout-down", down),
    ]


@builtin("tv-rob-booker-knoxville-divergence", "Rob Booker - Knoxville Divergence", "recursive", 150)
def _knoxville(chart: Chart) -> Outputs:
    momentum_length = int(chart.number_param("momentumLength", 20, 1, integer=True))
    rsi_length = int(chart.number_param("rsiLength", 21, 1, integer=True))
    bearish, bullish = _knoxville_divergence(chart.high, chart.low, chart.close, chart.period, momentum_length, rsi_length)
    return [chart.out("bearish", bearish), chart.out("bullish", bullish)]


def _pivot_outputs(chart: Chart, period: SessionPeriod, developing: bool, lines: Sequence[str]) -> Outputs:
    clock = session_clock(chart.bars, chart.inputs.session)
    keys, _ = session_periods(clock, period, chart.inputs.session)
    levels = _period_pivots(keys, clock.counts, chart.high, chart.low, chart.close, developing)
    return [chart.out(line, getattr(levels, line)) for line in lines]


@builtin(
    "tv-rob-booker-intraday-pivot-points", "Rob Booker Intraday Pivot Points", "exact", 1,
    lookback_time=lambda period, inputs: timedelta(hours=2 * period) + _WEEKEND,
)
def _intraday_pivots(chart: Chart) -> Outputs:
    # The period is the pivot period in hours (TradingView offers 1, 4 and 8).
    return _pivot_outputs(chart, chart.period, False, ("pp", "r1", "r2", "r3", "s1", "s2", "s3"))


@builtin(
    "tv-rob-booker-missed-pivot-points", "Rob Booker Missed Pivot Points", "exact", 10,
    lookback_time=lambda period, inputs: _periods_back(period + 1, inputs, "pivotPeriod", "D"),
)
def _missed_pivot_points(chart: Chart) -> Outputs:
    clock = session_clock(chart.bars, chart.inputs.session)
    keys, _ = session_periods(clock, chart.select_param("pivotPeriod", "D", PERIODS), chart.inputs.session)
    above, below = _missed_pivots(keys, clock.counts, chart.high, chart.low, chart.close, chart.period)
    return [chart.out("missed-above", above), chart.out("missed-below", below)]


# Reversals are signals, after MACD(12, 26) and the stochastic warm up.
@builtin("tv-rob-booker-reversal", "Rob Booker Reversal", "recursive", 14, signal_warmup=45)
def _reversal(chart: Chart) -> Outputs:
    close, high, low = chart.close, chart.high, chart.low
    fast = ema(close, int(chart.number_param("fastLength", 12, 1, integer=True)))
    slow = ema(close, int(chart.number_param("slowLength", 26, 1, integer=True)))
    slowing = int(chart.number_param("slowing", 3, 1, integer=True))
    k = _complete_sma(stochastic(high, low, close, chart.period), slowing)
    upper = chart.number_param("upper", 70, 0)
    lower = chart.number_param("lower", 30, 0)
    macd = [f - s if finite(f) and finite(s) else None for f, s in zip(fast, slow, strict=True)]
    bullish = full(len(close))
    bearish = full(len(close))
    for i in range(1, len(close)):
        now, before, stoch = macd[i], macd[i - 1], k[i]
        if now is None or before is None or not finite(stoch):
            continue
        if before > 0 and now <= 0 and stoch > upper:
            bearish[i] = high[i]
        if before < 0 and now >= 0 and stoch < lower:
            bullish[i] = low[i]
    return [chart.out("bullish", bullish), chart.out("bearish", bearish)]


_GHOST_PERIODS: dict[str, SessionPeriod] = {"240": 4, "480": 8, "W": "W", "M": "M"}


@builtin(
    "tv-rob-booker-ziv-ghost-pivots", "Rob Booker Ziv Ghost Pivots", "exact", 1,
    lookback_time=lambda period, inputs: _periods_back(2, inputs, "pivotPeriod", "W"),
)
def _ziv_ghost_pivots(chart: Chart) -> Outputs:
    period = _GHOST_PERIODS[chart.select_param("pivotPeriod", "W", tuple(_GHOST_PERIODS))]
    return _pivot_outputs(chart, period, True, ("pp", "r1", "s1"))
