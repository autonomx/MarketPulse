"""Incremental ``ta.*`` functions.

Each call site of a ``ta.*`` function keeps its own state (a ``Site``). A function is a pure ``step(state, *inputs)
-> (state, output)``: the site keeps the state committed at the end of the last bar it ran on and the pending state of
the current bar, so calling it again on the same bar (a loop, or a realtime bar that updates) recomputes that bar
instead of advancing twice.

Where a chart indicator computes the same thing, the step follows the server registry's order of operations
(``indicators/server_indicators``), so a script and the chart's indicator agree to the last bit: SMA keeps a running
sum, EMA and RMA seed with the mean of the first ``length`` values, RSI and ATR smooth with ``(avg * (n - 1) + x) / n``.
``na`` (None) inputs before a series starts are skipped, so a function of a function warms up like the chart's
indicators; an ``na`` inside a window makes the window ``na`` until it has passed.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from ..indicators.server_indicators._helpers import js_max, js_min, js_sum
from .errors import ScriptRuntimeError

Value = Any
Step = Callable[..., tuple[Any, Any]]


class Site:
    __slots__ = ("committed", "pending", "t")

    def __init__(self) -> None:
        self.t = -1
        self.committed: Any = None
        self.pending: Any = None

    def run(self, t: int, step: Step, *inputs: Value) -> Value:
        if t != self.t:
            self.committed = self.pending
            self.t = t
        self.pending, output = step(self.committed, *inputs)
        return output


def length_of(value: Value) -> int | None:
    """A length argument: na gives na, and anything but a positive whole number is an error, as in Pine."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ScriptRuntimeError(f"a length must be a positive whole number, got {value!r}")
    return value


def is_na(value: Value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


# Windows: (length, values) holding the last `length` non-na values; an na clears it.


def _window(state: Any, value: Value, length: int) -> tuple[int, tuple[float, ...]] | None:
    if is_na(value):
        return None
    values: tuple[float, ...] = state[1] if state is not None and state[0] == length else ()
    values = values + (value,) if len(values) < length else values[1:] + (value,)
    return length, values


def _full(state: tuple[int, tuple[float, ...]] | None) -> tuple[float, ...] | None:
    return state[1] if state is not None and len(state[1]) == state[0] else None


def sma(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None or is_na(value):
        return None, None
    if state is None or state[0] != n:
        state = (n, (), None)
    _, values, total = state
    if total is None:
        values = values + (value,)
        if len(values) < n:
            return (n, values, None), None
        total = js_sum(values)
        return (n, values, total), total / n
    total = total + (value - values[0])
    return (n, values[1:] + (value,), total), total / n


def _smoothed(state: Any, value: Value, length: Value, update: Callable[[float, float, int], float]) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    if is_na(value):
        # After warm-up an na bar is na and leaves the average as it was; before it, it is skipped.
        return state, None
    if state is None or state[0] != n:
        state = (n, (), None)
    _, seed, average = state
    if average is None:
        seed = seed + (value,)
        if len(seed) < n:
            return (n, seed, None), None
        average = js_sum(seed) / n
        return (n, (), average), average
    average = update(average, value, n)
    return (n, (), average), average


def _ema_update(average: float, value: float, n: int) -> float:
    return (value - average) * (2 / (n + 1)) + average


def _rma_update(average: float, value: float, n: int) -> float:
    return (average * (n - 1) + value) / n


def ema(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _smoothed(state, value, length, _ema_update)


def rma(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _smoothed(state, value, length, _rma_update)


def wma(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    weighted = 0.0
    for j, item in enumerate(values):
        weighted += item * (j + 1)
    return state, weighted / (n * (n + 1) / 2)


def vwma(state: Any, value: Value, volume: Value, length: Value) -> tuple[Any, Value]:
    # sma(src * volume) / sma(volume), as Pine defines it.
    price_state, volume_state = state or (None, None)
    pv = None if is_na(value) or is_na(volume) else value * volume
    price_state, numerator = sma(price_state, pv, length)
    volume_state, denominator = sma(volume_state, None if pv is None else volume, length)
    if numerator is None or denominator is None or denominator == 0:
        return (price_state, volume_state), None
    return (price_state, volume_state), numerator / denominator


def hma(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    half, full, outer = state or (None, None, None)
    half, a = wma(half, value, max(1, n // 2))
    full, b = wma(full, value, n)
    raw = None if a is None or b is None else 2 * a - b
    outer, result = wma(outer, raw, max(1, int(math.floor(math.sqrt(n)))))
    return (half, full, outer), result


def rsi(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    if state is None or state[0] != n:
        state = (n, None, (), (), None, None)
    _, previous, gains, losses, average_gain, average_loss = state
    if is_na(value):
        return (n, None, gains, losses, average_gain, average_loss), None
    if previous is None:
        return (n, value, gains, losses, average_gain, average_loss), None
    change = value - previous
    gain = js_max(change, 0.0)
    loss = js_max(-change, 0.0)
    if average_gain is None:
        gains = gains + (gain,)
        losses = losses + (loss,)
        if len(gains) < n:
            return (n, value, gains, losses, None, None), None
        average_gain = js_sum(gains) / n
        average_loss = js_sum(losses) / n
    else:
        average_gain = (average_gain * (n - 1) + gain) / n
        average_loss = (average_loss * (n - 1) + loss) / n
    result = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    return (n, value, (), (), average_gain, average_loss), result


def true_range(previous_close: Value, high: Value, low: Value, handle_na: bool) -> Value:
    if is_na(high) or is_na(low):
        return None
    if is_na(previous_close):
        return high - low if handle_na else None
    return js_max(high - low, abs(high - previous_close), abs(low - previous_close))


def tr(state: Any, high: Value, low: Value, close: Value, handle_na: bool) -> tuple[Any, Value]:
    # state: the previous bar's close.
    return close, true_range(state, high, low, handle_na)


def atr(state: Any, high: Value, low: Value, close: Value, length: Value) -> tuple[Any, Value]:
    previous_close, smoothing = state or (None, None)
    smoothing, result = rma(smoothing, true_range(previous_close, high, low, True), length)
    return (close, smoothing), result


def stdev(state: Any, value: Value, length: Value, biased: Value = True) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    center = js_sum(values) / n
    squares = js_sum([(item - center) * (item - center) for item in values])
    divisor = n if biased or n == 1 else n - 1
    return state, math.sqrt(squares / divisor)


def variance(state: Any, value: Value, length: Value, biased: Value = True) -> tuple[Any, Value]:
    state, deviation = stdev(state, value, length, biased)
    return state, None if deviation is None else deviation * deviation


def dev(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    # Mean absolute deviation.
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    center = js_sum(values) / n
    return state, js_sum([abs(item - center) for item in values]) / n


def _extreme(state: Any, value: Value, length: Value, pick: Callable[..., float]) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    return state, None if values is None else pick(*values)


def highest(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _extreme(state, value, length, js_max)


def lowest(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _extreme(state, value, length, js_min)


def _extreme_bars(state: Any, value: Value, length: Value, better: Callable[[float, float], bool]) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    best = len(values) - 1
    for index in range(len(values) - 1, -1, -1):
        if better(values[index], values[best]):
            best = index
    # Offset back from the current bar (0 or negative), as Pine returns it.
    return state, best - (len(values) - 1)


def highestbars(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _extreme_bars(state, value, length, lambda a, b: a > b)


def lowestbars(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    return _extreme_bars(state, value, length, lambda a, b: a < b)


def change(state: Any, value: Value, length: Value = 1) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    history: tuple[Value, ...] = state or ()
    history = (history + (value,))[-(n + 1) :]
    if len(history) <= n or is_na(value) or is_na(history[0]):
        return history, None
    if isinstance(value, bool):
        return history, value != history[0]
    return history, value - history[0]


def roc(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    state, difference = change(state, value, length)
    if difference is None:
        return state, None
    previous = state[0]
    return state, None if previous == 0 else 100 * difference / previous


def cross(state: Any, a: Value, b: Value, direction: int) -> tuple[Any, Value]:
    previous = state or (None, None)
    now = (a, b)
    if any(is_na(item) for item in (*previous, a, b)):
        return now, False
    # Neither side is na past the check above.
    last = cast(Any, previous)
    up = a > b and last[0] <= last[1]
    down = a < b and last[0] >= last[1]
    return now, up if direction > 0 else down if direction < 0 else up or down


def crossover(state: Any, a: Value, b: Value) -> tuple[Any, Value]:
    return cross(state, a, b, 1)


def crossunder(state: Any, a: Value, b: Value) -> tuple[Any, Value]:
    return cross(state, a, b, -1)


def any_cross(state: Any, a: Value, b: Value) -> tuple[Any, Value]:
    return cross(state, a, b, 0)


def _pivot(state: Any, value: Value, left: Value, right: Value, higher: bool) -> tuple[Any, Value]:
    l_count, r_count = length_of(left), length_of(right)
    if l_count is None or r_count is None:
        return None, None
    history: tuple[Value, ...] = state or ()
    history = (history + (value,))[-(l_count + r_count + 1) :]
    if len(history) < l_count + r_count + 1 or any(is_na(item) for item in history):
        return history, None
    center = history[l_count]
    before, after = history[:l_count], history[l_count + 1 :]
    if higher:
        found = all(center > item for item in before) and all(center >= item for item in after)
    else:
        found = all(center < item for item in before) and all(center <= item for item in after)
    return history, center if found else None


def pivothigh(state: Any, value: Value, left: Value, right: Value) -> tuple[Any, Value]:
    return _pivot(state, value, left, right, True)


def pivotlow(state: Any, value: Value, left: Value, right: Value) -> tuple[Any, Value]:
    return _pivot(state, value, left, right, False)


def stoch(state: Any, value: Value, high: Value, low: Value, length: Value) -> tuple[Any, Value]:
    high_state, low_state = state or (None, None)
    high_state, top = highest(high_state, high, length)
    low_state, bottom = lowest(low_state, low, length)
    if top is None or bottom is None or is_na(value) or top == bottom:
        return (high_state, low_state), None
    return (high_state, low_state), 100 * (value - bottom) / (top - bottom)


def cum(state: Any, value: Value) -> tuple[Any, Value]:
    total = 0.0 if state is None else state
    if not is_na(value):
        total = total + value
    return total, total


def vwap(state: Any, value: Value, volume: Value, start: datetime) -> tuple[Any, Value]:
    """Anchored at each UTC day, as Pine's ``ta.vwap`` resets at each session (24x7 markets: UTC days)."""
    day = start.date()
    session, price_volume, total_volume = state if state is not None and state[0] == day else (day, 0.0, 0.0)
    if is_na(value) or is_na(volume):
        return (session, price_volume, total_volume), None
    price_volume = price_volume + value * volume
    total_volume = total_volume + volume
    result = value if total_volume == 0 else price_volume / total_volume
    return (session, price_volume, total_volume), result


def macd(state: Any, value: Value, fast: Value, slow: Value, signal: Value) -> tuple[Any, Value]:
    fast_state, slow_state, signal_state = state or (None, None, None)
    fast_state, fast_value = ema(fast_state, value, fast)
    slow_state, slow_value = ema(slow_state, value, slow)
    line = None if fast_value is None or slow_value is None else fast_value - slow_value
    signal_state, signal_value = ema(signal_state, line, signal)
    histogram = None if line is None or signal_value is None else line - signal_value
    return (fast_state, slow_state, signal_state), (line, signal_value, histogram)


def bb(state: Any, value: Value, length: Value, mult: Value) -> tuple[Any, Value]:
    mean_state, deviation_state = state or (None, None)
    mean_state, basis = sma(mean_state, value, length)
    deviation_state, deviation = stdev(deviation_state, value, length)
    if basis is None or deviation is None or is_na(mult):
        return (mean_state, deviation_state), (basis, None, None)
    spread = mult * deviation
    return (mean_state, deviation_state), (basis, basis + spread, basis - spread)


def kc(state: Any, value: Value, high: Value, low: Value, close: Value, length: Value, mult: Value, use_true_range: Value = True) -> tuple[Any, Value]:
    mean_state, range_state, previous_close = state or (None, None, None)
    mean_state, basis = ema(mean_state, value, length)
    span = true_range(previous_close, high, low, True) if use_true_range else (None if is_na(high) or is_na(low) else high - low)
    range_state, average_range = ema(range_state, span, length)
    if basis is None or average_range is None or is_na(mult):
        return (mean_state, range_state, close), (basis, None, None)
    return (mean_state, range_state, close), (basis, basis + mult * average_range, basis - mult * average_range)


def linreg(state: Any, value: Value, length: Value, offset: Value = 0) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    # Least squares over x = 0..n-1 (oldest first); the value at x = n - 1 - offset.
    sum_x = n * (n - 1) / 2
    sum_y = js_sum(values)
    sum_xy = 0.0
    sum_xx = 0.0
    for x, y in enumerate(values):
        sum_xy += x * y
        sum_xx += x * x
    denominator = n * sum_xx - sum_x * sum_x
    slope = 0.0 if denominator == 0 else (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    return state, intercept + slope * (n - 1 - (offset or 0))


def correlation(state: Any, a: Value, b: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    pairs_state = _window(state, None if is_na(a) or is_na(b) else (a, b), n)
    # The window holds (a, b) pairs.
    pairs: Any = _full(pairs_state)
    if pairs is None:
        return pairs_state, None
    mean_a = js_sum([p[0] for p in pairs]) / n
    mean_b = js_sum([p[1] for p in pairs]) / n
    covariance = js_sum([(p[0] - mean_a) * (p[1] - mean_b) for p in pairs])
    spread_a = js_sum([(p[0] - mean_a) ** 2 for p in pairs])
    spread_b = js_sum([(p[1] - mean_b) ** 2 for p in pairs])
    denominator = math.sqrt(spread_a * spread_b)
    return pairs_state, None if denominator == 0 else covariance / denominator


def math_sum(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    return state, None if values is None else js_sum(values)


def median(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, None
    state = _window(state, value, n)
    values = _full(state)
    if values is None:
        return state, None
    ordered = sorted(values)
    middle = n // 2
    return state, ordered[middle] if n % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def percentrank(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    # Percent of the previous `length` values that are less than or equal to the current one.
    n = length_of(length)
    if n is None:
        return None, None
    history: tuple[Value, ...] = state or ()
    history = (history + (value,))[-(n + 1) :]
    if len(history) <= n or any(is_na(item) for item in history):
        return history, None
    current = history[-1]
    return history, 100 * sum(1 for item in history[:-1] if item <= current) / n


def rising(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, False
    history: tuple[Value, ...] = state or ()
    history = (history + (value,))[-(n + 1) :]
    if len(history) <= n or any(is_na(item) for item in history):
        return history, False
    return history, all(history[-1] > item for item in history[:-1])


def falling(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    n = length_of(length)
    if n is None:
        return None, False
    history: tuple[Value, ...] = state or ()
    history = (history + (value,))[-(n + 1) :]
    if len(history) <= n or any(is_na(item) for item in history):
        return history, False
    return history, all(history[-1] < item for item in history[:-1])


def barssince(state: Any, condition: Value) -> tuple[Any, Value]:
    count = state
    if condition:
        return 0, 0
    if count is None:
        return None, None
    return count + 1, count + 1


def valuewhen(state: Any, condition: Value, value: Value, occurrence: Value) -> tuple[Any, Value]:
    n = length_of((occurrence or 0) + 1)
    if n is None:
        return state, None
    history: tuple[Value, ...] = state or ()
    if condition:
        history = ((value,) + history)[: max(n, len(history) + 1)]
        history = history[:n]
    return history, history[n - 1] if len(history) >= n else None


def cci(state: Any, value: Value, length: Value) -> tuple[Any, Value]:
    mean_state, deviation_state = state or (None, None)
    mean_state, mean = sma(mean_state, value, length)
    deviation_state, deviation = dev(deviation_state, value, length)
    if mean is None or deviation is None or deviation == 0:
        return (mean_state, deviation_state), None
    return (mean_state, deviation_state), (value - mean) / (0.015 * deviation)


def mfi(state: Any, value: Value, volume: Value, length: Value) -> tuple[Any, Value]:
    previous, up_state, down_state = state or (None, None, None)
    if is_na(value) or is_na(volume):
        return (previous, up_state, down_state), None
    flow = value * volume
    moved = None if previous is None else value - previous
    # As Pine's reference: `change <= 0 ? 0 : flow`, where a comparison with na (the first bar) is false, so the
    # first bar's flow counts on both sides.
    up_state, up = math_sum(up_state, flow if moved is None or moved > 0 else 0.0, length)
    down_state, down = math_sum(down_state, flow if moved is None or moved < 0 else 0.0, length)
    if up is None or down is None:
        return (value, up_state, down_state), None
    return (value, up_state, down_state), 100.0 if down == 0 else 100 - 100 / (1 + up / down)


def obv(state: Any, close: Value, volume: Value) -> tuple[Any, Value]:
    previous, total = state or (None, 0.0)
    if is_na(close) or is_na(volume):
        return (previous, total), total
    if previous is not None:
        total = total + (volume if close > previous else -volume if close < previous else 0.0)
    return (close, total), total


def dmi(state: Any, high: Value, low: Value, close: Value, di_length: Value, adx_smoothing: Value) -> tuple[Any, Value]:
    """Pine's reference: +DI and -DI are fixnan'd (a zero true range keeps the last values), ADX smooths them."""
    previous, plus_state, minus_state, range_state, adx_state, last = state or ((None, None, None), None, None, None, None, (None, None))
    previous_high, previous_low, previous_close = previous
    if previous_high is None:
        up = down = None
    else:
        up = high - previous_high
        down = previous_low - low
    plus_dm = None if up is None else (up if up > down and up > 0 else 0.0)
    minus_dm = None if down is None else (down if down > up and down > 0 else 0.0)
    span = None if previous_close is None else true_range(previous_close, high, low, False)
    range_state, smoothed_range = rma(range_state, span, di_length)
    plus_state, smoothed_plus = rma(plus_state, plus_dm, di_length)
    minus_state, smoothed_minus = rma(minus_state, minus_dm, di_length)
    plus, minus = last
    if smoothed_range and smoothed_plus is not None and smoothed_minus is not None:
        plus = 100 * smoothed_plus / smoothed_range
        minus = 100 * smoothed_minus / smoothed_range
    adx = None
    if plus is not None and minus is not None:
        total = plus + minus
        adx_state, smoothed = rma(adx_state, abs(plus - minus) / (1 if total == 0 else total), adx_smoothing)
        adx = None if smoothed is None else 100 * smoothed
    return ((high, low, close), plus_state, minus_state, range_state, adx_state, (plus, minus)), (plus, minus, adx)


def supertrend(state: Any, high: Value, low: Value, close: Value, factor: Value, atr_period: Value) -> tuple[Any, Value]:
    atr_state, previous_close, previous_upper, previous_lower, previous_trend, previous_direction = state or (None, None, None, None, None, None)
    atr_state, average = atr(atr_state, high, low, close, atr_period)
    if average is None or is_na(factor):
        return (atr_state, close, None, None, None, None), (None, None)
    middle = (high + low) / 2
    upper = middle + factor * average
    lower = middle - factor * average
    if previous_lower is not None and not (lower > previous_lower or previous_close < previous_lower):
        lower = previous_lower
    if previous_upper is not None and not (upper < previous_upper or previous_close > previous_upper):
        upper = previous_upper
    if previous_trend is None:
        direction = 1
    elif previous_trend == previous_upper:
        direction = -1 if close > upper else 1
    else:
        direction = 1 if close < lower else -1
    trend = lower if direction == -1 else upper
    return (atr_state, close, upper, lower, trend, direction), (trend, direction)


def sar(state: Any, high: Value, low: Value, close: Value, start: Value, increment: Value, maximum: Value) -> tuple[Any, Value]:
    """Parabolic SAR: Pine's reference (`pine_sar` in the ta.sar documentation), line by line."""
    if state is None:
        # The first bar has no SAR; the trend is chosen on the second from the closes.
        return (1, None, None, None, None, high, low, close, None, None), None
    calls, result, extreme, acceleration, below, high1, low1, close1, high2, low2 = state
    first_trend_bar = False
    if calls == 1:
        if close > close1:
            below, extreme, result = True, high, low1
        else:
            below, extreme, result = False, low, high1
        first_trend_bar = True
        acceleration = start
    result = result + acceleration * (extreme - result)
    if below:
        if result > low:
            first_trend_bar, below, result, extreme, acceleration = True, False, js_max(high, extreme), low, start
    elif result < high:
        first_trend_bar, below, result, extreme, acceleration = True, True, js_min(low, extreme), high, start
    if not first_trend_bar:
        if below:
            if high > extreme:
                extreme, acceleration = high, js_min(acceleration + increment, maximum)
        elif low < extreme:
            extreme, acceleration = low, js_min(acceleration + increment, maximum)
    if below:
        result = js_min(result, low1)
        if calls > 1:
            result = js_min(result, low2)
    else:
        result = js_max(result, high1)
        if calls > 1:
            result = js_max(result, high2)
    return (calls + 1, result, extreme, acceleration, below, high, low, close, high1, low1), result
