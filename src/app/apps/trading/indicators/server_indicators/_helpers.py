"""Ports of the browser's indicator helpers (``tradingViewBuiltIns.ts``), operation for operation.

Results must match the browser bit for bit where possible, so these follow the
TypeScript order of operations exactly: sums are plain left-to-right loops,
``x ** 2`` is written ``x * x`` (V8's pow returns ``x * x`` for an exponent of
2), and ``js_max``/``js_min`` keep JavaScript's NaN and signed-zero rules.

``builtin`` registers a built-in indicator behind the preamble of
``calculateTradingViewBuiltInOutputs``; the ``builtins_*`` modules hold the branches.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property
from typing import Literal, TypeGuard

from ..registry import (
    BarSeries,
    IndicatorInputs,
    IndicatorOutputSeries,
    NumericClass,
    register,
    register_with_compare_series,
)
from ._sessions import epoch_ms

MaybeNumber = float | None
Values = Sequence[float]
Outputs = list[IndicatorOutputSeries]

EPSILON = sys.float_info.epsilon


def full(length: int) -> list[MaybeNumber]:
    return [None] * length


def safe_period(period: object, fallback: int = 14) -> int:
    if isinstance(period, float) and period.is_integer():
        period = int(period)
    return period if isinstance(period, int) and not isinstance(period, bool) and period > 0 else fallback


def js_sqrt(value: float) -> float:
    """JavaScript ``Math.sqrt``: NaN for negative input (rounding drift can produce -1e-17) instead of raising."""
    return math.sqrt(value) if value >= 0 else math.nan


def js_div(numerator: float, denominator: float) -> float:
    """JavaScript division: dividing by zero gives ±Infinity (NaN for 0 / 0) instead of raising."""
    if denominator != 0:
        return numerator / denominator
    if numerator == 0 or math.isnan(numerator):
        return math.nan
    return math.copysign(math.inf, numerator) * math.copysign(1.0, denominator)


def js_log(value: float) -> float:
    """JavaScript ``Math.log``: -Infinity at 0 and NaN below it, instead of raising."""
    if value > 0:
        return math.log(value)
    return -math.inf if value == 0 else math.nan


def js_round(value: float) -> int:
    """JavaScript ``Math.round``: halves round up (towards +infinity). Python's ``round`` rounds halves to even."""
    # Not floor(value + 0.5): that addition rounds 0.49999999999999994 up to 1.
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def js_sign(value: float) -> float:
    """JavaScript ``Math.sign``: keeps NaN and signed zero."""
    return 1.0 if value > 0 else -1.0 if value < 0 else value


def js_sum(values: Values) -> float:
    # Builtin sum() is compensated from Python 3.12 and would not match the browser's reduce().
    total = 0.0
    for value in values:
        total += value
    return total


def mean(values: Values) -> float:
    return js_sum(values) / len(values) if values else 0.0


def finite(value: MaybeNumber) -> TypeGuard[float]:
    return value is not None and math.isfinite(value)


def or_zero(values: Sequence[MaybeNumber]) -> list[float]:
    return [value if finite(value) else 0.0 for value in values]


def difference(first: Sequence[MaybeNumber], second: Sequence[MaybeNumber]) -> list[MaybeNumber]:
    return [a - b if finite(a) and finite(b) else None for a, b in zip(first, second, strict=True)]


def js_max(*values: float) -> float:
    result = -math.inf
    for value in values:
        if math.isnan(value):
            return math.nan
        if value > result or (value == 0 and result == 0 and math.copysign(1.0, result) < 0):
            result = value
    return result


def js_min(*values: float) -> float:
    result = math.inf
    for value in values:
        if math.isnan(value):
            return math.nan
        if value < result or (value == 0 and result == 0 and math.copysign(1.0, value) < 0):
            result = value
    return result


def sma(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    if len(values) < p:
        return result
    running = js_sum(values[:p])
    result[p - 1] = running / p
    for i in range(p, len(values)):
        running += values[i] - values[i - p]
        result[i] = running / p
    return result


def ema(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    if len(values) < p:
        return result
    current = mean(values[:p])
    result[p - 1] = current
    alpha = 2 / (p + 1)
    for i in range(p, len(values)):
        current += alpha * (values[i] - current)
        result[i] = current
    return result


def rma(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    if len(values) < p:
        return result
    current = mean(values[:p])
    result[p - 1] = current
    for i in range(p, len(values)):
        current = (current * (p - 1) + values[i]) / p
        result[i] = current
    return result


def wma(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    denominator = p * (p + 1) / 2
    for i in range(p - 1, len(values)):
        weighted = 0.0
        for j in range(p):
            weighted += values[i - p + 1 + j] * (j + 1)
        result[i] = weighted / denominator
    return result


def rolling(values: Values, period: int, reducer: Callable[[Sequence[float]], float]) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    for i in range(p - 1, len(values)):
        result[i] = reducer(values[i - p + 1 : i + 1])
    return result


def highest(values: Values, period: int) -> list[MaybeNumber]:
    return rolling(values, period, lambda window: js_max(*window))


def lowest(values: Values, period: int) -> list[MaybeNumber]:
    return rolling(values, period, lambda window: js_min(*window))


def _median_of(window: Sequence[float]) -> float:
    ordered = sorted(window)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def median(values: Values, period: int) -> list[MaybeNumber]:
    return rolling(values, period, _median_of)


def _population_deviation(window: Sequence[float]) -> float:
    m = mean(window)
    return math.sqrt(mean([(value - m) * (value - m) for value in window]))


def stdev(values: Values, period: int) -> list[MaybeNumber]:
    return rolling(values, period, _population_deviation)


def rolling_sum(values: Values, period: int) -> list[MaybeNumber]:
    return rolling(values, period, js_sum)


def true_range(high: Values, low: Values, close: Values) -> list[float]:
    return [
        value - low[i]
        if i == 0
        else js_max(value - low[i], abs(value - close[i - 1]), abs(low[i] - close[i - 1]))
        for i, value in enumerate(high)
    ]


def atr(high: Values, low: Values, close: Values, period: int) -> list[MaybeNumber]:
    return rma(true_range(high, low, close), period)


def rsi(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    gains = [0.0 if i == 0 else js_max(0.0, value - values[i - 1]) for i, value in enumerate(values)]
    losses = [0.0 if i == 0 else js_max(0.0, values[i - 1] - value) for i, value in enumerate(values)]
    result: list[MaybeNumber] = []
    for gain, loss in zip(rma(gains, p), rma(losses, p), strict=True):
        if not finite(gain) or not finite(loss):
            result.append(None)
        elif loss == 0:
            result.append(100.0)
        else:
            result.append(100 - 100 / (1 + gain / loss))
    return result


def roc(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    return [None if i < p or values[i - p] == 0 else (value / values[i - p] - 1) * 100 for i, value in enumerate(values)]


def bollinger(values: Values, period: int, deviations: float = 2) -> tuple[list[MaybeNumber], list[MaybeNumber], list[MaybeNumber]]:
    middle = sma(values, period)
    deviation = stdev(values, period)
    upper: list[MaybeNumber] = []
    lower: list[MaybeNumber] = []
    for m, d in zip(middle, deviation, strict=True):
        upper.append(m + d * deviations if finite(m) and finite(d) else None)
        lower.append(m - d * deviations if finite(m) and finite(d) else None)
    return middle, upper, lower


def dmi(high: Values, low: Values, close: Values, period: int) -> tuple[list[MaybeNumber], list[MaybeNumber], list[MaybeNumber]]:
    p = safe_period(period)
    tr = true_range(high, low, close)
    plus_dm = [
        0.0 if i == 0 else js_max(value - high[i - 1] if value - high[i - 1] > low[i - 1] - low[i] else 0.0, 0.0)
        for i, value in enumerate(high)
    ]
    minus_dm = [
        0.0 if i == 0 else js_max(low[i - 1] - value if low[i - 1] - value > high[i] - high[i - 1] else 0.0, 0.0)
        for i, value in enumerate(low)
    ]
    tr_smoothed = rma(tr, p)

    def directional(smoothed: list[MaybeNumber]) -> list[MaybeNumber]:
        return [
            100 * s / t if finite(t) and t != 0 and finite(s) else None
            for t, s in zip(tr_smoothed, smoothed, strict=True)
        ]

    plus = directional(rma(plus_dm, p))
    minus = directional(rma(minus_dm, p))
    dx = [
        100 * abs(pv - mv) / (pv + mv) if finite(pv) and finite(mv) and pv + mv != 0 else 0.0
        for pv, mv in zip(plus, minus, strict=True)
    ]
    return plus, minus, rma(dx, p)


def linear_regression(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    if p > len(values):
        return result
    x_mean = (p - 1) / 2
    x_variance = 0.0
    for i in range(p):
        x_variance = x_variance + (i - x_mean) * (i - x_mean)
    for i in range(p - 1, len(values)):
        window = values[i - p + 1 : i + 1]
        y_mean = mean(window)
        covariance = 0.0
        for j in range(p):
            covariance += (j - x_mean) * (window[j] - y_mean)
        slope = 0.0 if x_variance == 0 else covariance / x_variance
        intercept = y_mean - slope * x_mean
        result[i] = intercept + slope * (p - 1)
    return result


def correlation_with_index(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    if p > len(values):
        return result
    x = [float(i) for i in range(p)]
    mx = mean(x)
    vx = js_sum([(value - mx) * (value - mx) for value in x])
    for i in range(p - 1, len(values)):
        y = values[i - p + 1 : i + 1]
        my = mean(y)
        vy = js_sum([(value - my) * (value - my) for value in y])
        if vy <= EPSILON or vx <= EPSILON:
            result[i] = 0.0
        else:
            result[i] = js_sum([(x[j] - mx) * (value - my) for j, value in enumerate(y)]) / math.sqrt(vx * vy)
    return result


def spearman(values: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    result = full(len(values))
    for i in range(p - 1, len(values)):
        window = values[i - p + 1 : i + 1]
        order = sorted(range(p), key=lambda index: window[index])
        rank = [0] * p
        for position, index in enumerate(order):
            rank[index] = position + 1
        d2 = js_sum([float((value - (index + 1)) * (value - (index + 1))) for index, value in enumerate(rank)])
        result[i] = 0.0 if p <= 1 else (1 - 6 * d2 / (p * (p * p - 1))) * 100
    return result


def output(indicator_id: str, suffix: str, values: Sequence[MaybeNumber]) -> IndicatorOutputSeries:
    """Like the browser's ``output()``: keeps only finite values."""
    return IndicatorOutputSeries(
        key=f"{indicator_id}:{suffix}",
        points=tuple((index, value) for index, value in enumerate(values) if finite(value)),
    )


def stochastic(high: Values, low: Values, close: Values, period: int) -> list[MaybeNumber]:
    hh = highest(high, period)
    ll = lowest(low, period)
    return [
        100 * (value - lo) / (h - lo) if finite(h) and finite(lo) and h != lo else None
        for value, h, lo in zip(close, hh, ll, strict=True)
    ]


def cci(high: Values, low: Values, close: Values, period: int) -> list[MaybeNumber]:
    typical = [(high[i] + low[i] + value) / 3 for i, value in enumerate(close)]
    basis = sma(typical, period)
    p = safe_period(period)
    result: list[MaybeNumber] = []
    for i, b in enumerate(basis):
        if not finite(b):
            result.append(None)
            continue
        deviation = mean([abs(value - b) for value in typical[i - p + 1 : i + 1]])
        result.append(0.0 if deviation == 0 else js_div(typical[i] - b, 0.015 * deviation))
    return result


def true_strength(close: Values, long_period: int = 25, short_period: int = 13) -> list[MaybeNumber]:
    momentum = [0.0 if i == 0 else value - close[i - 1] for i, value in enumerate(close)]
    absolute = [abs(value) for value in momentum]
    second = ema(or_zero(ema(momentum, long_period)), short_period)
    absolute_second = ema(or_zero(ema(absolute, long_period)), short_period)
    return [
        100 * s / a if finite(s) and finite(a) and a != 0 else None
        for s, a in zip(second, absolute_second, strict=True)
    ]


@dataclass(frozen=True)
class PatternPivot:
    index: int
    price: float
    type: Literal["high", "low"]


def find_pattern_pivots(bars: BarSeries, strength: int = 3) -> list[PatternPivot]:
    """Port of ``findPatternPivots`` in ``autoPatterns.ts``."""
    r = min(8, max(2, math.trunc(strength) or 3))
    candidates: list[PatternPivot] = []
    for index in range(r, len(bars) - r):
        maximum = js_max(*bars.high[index - r : index + r + 1])
        minimum = js_min(*bars.low[index - r : index + r + 1])
        if bars.high[index] >= maximum:
            candidates.append(PatternPivot(index, bars.high[index], "high"))
        if bars.low[index] <= minimum:
            candidates.append(PatternPivot(index, bars.low[index], "low"))
    alternating: list[PatternPivot] = []
    # Candidates are generated in (index, high before low) order, which is the browser's sort order.
    for candidate in candidates:
        if not alternating:
            alternating.append(candidate)
            continue
        previous = alternating[-1]
        if candidate.index == previous.index:
            previous_close = bars.close[previous.index]
            if abs(candidate.price - previous_close) > abs(previous.price - previous_close):
                alternating[-1] = candidate
            continue
        if candidate.type == previous.type:
            more_extreme = candidate.price >= previous.price if candidate.type == "high" else candidate.price <= previous.price
            if more_extreme:
                alternating[-1] = candidate
            continue
        alternating.append(candidate)
    return alternating


def hl2(bars: BarSeries) -> list[float]:
    return [(high + low) / 2 for high, low in zip(bars.high, bars.low, strict=True)]


def typical_price(bars: BarSeries) -> list[float]:
    return [(high + low + close) / 3 for high, low, close in zip(bars.high, bars.low, bars.close, strict=True)]


@dataclass(frozen=True)
class Chart:
    """One built-in's inputs after the browser's preamble: ``period`` is already ``safePeriod(period, default)``."""

    id: str
    period: int
    inputs: IndicatorInputs
    bars: BarSeries
    # The second series, for indicators registered with ``builtin_with_compare_series``.
    compare: BarSeries | None = None

    @property
    def open(self) -> Values:
        return self.bars.open

    @property
    def high(self) -> Values:
        return self.bars.high

    @property
    def low(self) -> Values:
        return self.bars.low

    @property
    def close(self) -> Values:
        return self.bars.close

    @property
    def volume(self) -> Values:
        return self.bars.volume

    def out(self, suffix: str, values: Sequence[MaybeNumber]) -> IndicatorOutputSeries:
        return output(self.id, suffix, values)

    @cached_property
    def compare_close(self) -> list[MaybeNumber]:
        """The second series' close at each bar: the close of its latest bar starting at or before the bar's start, so gaps
        carry the last close forward (the browser's ``alignedCompareCloses``). None everywhere without a second series."""
        if self.compare is None or len(self.compare) == 0:
            return full(len(self.bars))
        # A stable sort by start time, like the browser's Array.prototype.sort.
        series = sorted(
            zip((epoch_ms(start) for start in self.compare.start_times), self.compare.close, strict=True),
            key=lambda item: item[0],
        )
        j = -1
        result: list[MaybeNumber] = []
        for start in self.bars.start_times:
            time = epoch_ms(start)
            while j + 1 < len(series) and series[j + 1][0] <= time:
                j += 1
            result.append(series[j][1] if j >= 0 else None)
        return result

    def number_param(self, key: str, default: int | float, minimum: float, integer: bool = False) -> float:
        """A ``params`` number like the browser's ``numberParam``: finite, at least ``minimum``, whole when ``integer``."""
        value = self.inputs.param(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= minimum
            and (not integer or float(value).is_integer())
        ):
            return int(value) if integer else value
        return default

    def select_param(self, key: str, default: str, options: Sequence[str]) -> str:
        value = self.inputs.param(key)
        return value if isinstance(value, str) and value in options else default


Builder = Callable[[Chart], Outputs]


def builtin(
    indicator_id: str,
    name: str,
    numeric_class: NumericClass,
    default_period: int,
    signal_warmup: int | None = None,
    lookback: Callable[[int], int] | None = None,
    lookback_time: Callable[[int, IndicatorInputs], timedelta] | None = None,
) -> Callable[[Builder], Builder]:
    """Registers a branch with the browser's preamble: no bars gives no outputs, and the period falls back to the default.

    ``signal_warmup``: the outputs are signals with values only where they appear; ``lookback(period)``: the bars an
    indicator anchored near the latest bar reads back; ``lookback_time(period, inputs)``: the time an indicator over
    sessions or days reads back (see ``ServerIndicator``)."""

    def decorate(build: Builder) -> Builder:
        def compute(bars: BarSeries, inputs: IndicatorInputs) -> Outputs:
            if len(bars) == 0:
                return []
            return build(Chart(indicator_id, safe_period(inputs.period, default_period), inputs, bars))

        bars_back = None if lookback is None else (lambda inputs: lookback(safe_period(inputs.period, default_period)))
        time_back = None if lookback_time is None else (lambda inputs: lookback_time(safe_period(inputs.period, default_period), inputs))
        register(indicator_id, name, numeric_class, signal_warmup, bars_back, time_back)(compute)
        return build

    return decorate


def builtin_with_compare_series(
    indicator_id: str, name: str, numeric_class: NumericClass, default_period: int
) -> Callable[[Builder], Builder]:
    """Like ``builtin`` for an indicator that also reads a second series, given to the builder as ``chart.compare``.

    The browser passes the compare symbol's bars when ``compareSymbol`` is set, so the server uses the second series only
    when ``inputs.compare_symbol`` is set too."""

    def decorate(build: Builder) -> Builder:
        def compute(bars: BarSeries, inputs: IndicatorInputs, compare: BarSeries | None) -> Outputs:
            if len(bars) == 0:
                return []
            series = compare if inputs.compare_symbol else None
            return build(Chart(indicator_id, safe_period(inputs.period, default_period), inputs, bars, series))

        register_with_compare_series(indicator_id, name, numeric_class)(compute)
        return build

    return decorate
