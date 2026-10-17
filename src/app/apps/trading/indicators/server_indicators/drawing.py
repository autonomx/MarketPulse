"""Server ports of the indicators that draw (TVP-6.2), so alerts and the screener can read their lines (TVP-0.2).

Each function mirrors ``drawingIndicators.ts`` operation for operation, and the shared goldens check it. Outputs that
the chart paints rather than plots (bar colours, background shading, Chop Zone's columns) are a value of 1 on the bars
they mark, as the chart stores them. Two are not here: Visible Average Price depends on the bars in view, which only the
chart knows, and Seasonality's lines are named by year, so an alert or a screen could not name one ahead.
"""

from __future__ import annotations

from typing import cast

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ..registry import IndicatorOutputSeries
from ._helpers import Chart, MaybeNumber, Outputs, Values, atr, bollinger, builtin, ema, finite, full, highest, js_max, js_min, lowest
from ._sessions import DAY_MS, epoch_ms, session_clock, session_periods

# --- Swings ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Swing:
    index: int | float
    price: float
    high: bool


def zigzag(high: Values, low: Values, close: Values, depth: int, deviation: float) -> list[Swing]:
    """Alternating swing highs and lows: pivots over ``depth`` bars each side, a move of at least ``deviation`` x ATR(10)."""
    side = max(1, math.floor(depth / 2))
    swing_range = atr(high, low, close, 10)
    swings: list[Swing] = []
    for i in range(side, len(high) - side):
        is_high = True
        is_low = True
        for j in range(i - side, i + side + 1):
            if j == i:
                continue
            if high[j] > high[i] or (j < i and high[j] == high[i]):
                is_high = False
            if low[j] < low[i] or (j < i and low[j] == low[i]):
                is_low = False
        for found, price, high_swing in ((is_high, high[i], True), (is_low, low[i], False)):
            if not found:
                continue
            last = swings[-1] if swings else None
            if last is not None and last.high == high_swing:
                # The same side again: keep the more extreme one.
                if price > last.price if high_swing else price < last.price:
                    swings[-1] = Swing(i, price, high_swing)
                continue
            current = swing_range[i]
            threshold = deviation * (current if current is not None else 0)
            if last is not None and abs(price - last.price) < threshold:
                continue
            swings.append(Swing(i, price, high_swing))
    return swings


def _swing_lookback(depth: int) -> int:
    """Bars a swing tool reads back for alerts: enough for its last few swings at this depth (ATR(10) warms up first)."""
    return 400 + 2 * depth


def level_from(length: int, start: int | float, price: float) -> list[MaybeNumber]:
    return [price if index >= start else None for index in range(length)]


def ray_from(length: int, start: int | float, a: int | float, pa: float, b: int | float, pb: float) -> list[MaybeNumber]:
    """The line through (a, pa) and (b, pb) in bar index, from bar ``start`` to the last bar."""
    slope = 0 if b == a else (pb - pa) / (b - a)
    return [pa + slope * (index - a) if index >= start else None for index in range(length)]


_RETRACEMENT_LEVELS = ((0, "0"), (0.236, "0.236"), (0.382, "0.382"), (0.5, "0.5"), (0.618, "0.618"), (0.786, "0.786"), (1, "1"))
_EXTENSION_LEVELS = ((0, "0"), (0.382, "0.382"), (0.618, "0.618"), (1, "1"), (1.272, "1.272"), (1.618, "1.618"), (2.618, "2.618"))


def _auto_fib(chart: Chart, extension: bool) -> Outputs:
    swings = zigzag(chart.high, chart.low, chart.close, chart.period, chart.number_param("deviation", 3, 0))
    needed = 3 if extension else 2
    if len(swings) < needed:
        return []
    length = len(chart.bars)
    if extension:
        a, b, c = swings[-3:]
        return [chart.out(f"level-{label}", level_from(length, c.index, c.price + (b.price - a.price) * level)) for level, label in _EXTENSION_LEVELS]
    a, b = swings[-2:]
    return [chart.out(f"level-{label}", level_from(length, a.index, b.price + (a.price - b.price) * level)) for level, label in _RETRACEMENT_LEVELS]


@builtin("tv-auto-fib-retracement", "Auto Fib Retracement", "exact", 10, lookback=_swing_lookback)
def auto_fib_retracement(chart: Chart) -> Outputs:
    return _auto_fib(chart, False)


@builtin("tv-auto-fib-extension", "Auto Fib Extension", "exact", 10, lookback=_swing_lookback)
def auto_fib_extension(chart: Chart) -> Outputs:
    return _auto_fib(chart, True)


@builtin("tv-auto-pitchfork", "Auto Pitchfork", "exact", 10, lookback=_swing_lookback)
def auto_pitchfork(chart: Chart) -> Outputs:
    swings = zigzag(chart.high, chart.low, chart.close, chart.period, chart.number_param("deviation", 3, 0))
    if len(swings) < 3:
        return []
    a, b, c = swings[-3:]
    length = len(chart.bars)
    # The median from A through the middle of B-C; tines through B and C, parallel to it.
    middle_index = (b.index + c.index) / 2
    middle_price = (b.price + c.price) / 2
    slope = 0 if middle_index == a.index else (middle_price - a.price) / (middle_index - a.index)

    def parallel(anchor: Swing) -> list[MaybeNumber]:
        return ray_from(length, anchor.index, anchor.index, anchor.price, anchor.index + 1, anchor.price + slope)

    return [
        chart.out("median", ray_from(length, a.index, a.index, a.price, middle_index, middle_price)),
        chart.out("upper", parallel(b)),
        chart.out("lower", parallel(c)),
    ]


@builtin("tv-auto-trendlines", "Auto Trendlines", "exact", 5, lookback=lambda period: _swing_lookback(period * 2))
def auto_trendlines(chart: Chart) -> Outputs:
    # Resistance through the last two swing highs, support through the last two swing lows, to the last bar.
    swings = zigzag(chart.high, chart.low, chart.close, chart.period * 2, 0)
    highs = [swing for swing in swings if swing.high][-2:]
    lows = [swing for swing in swings if not swing.high][-2:]
    length = len(chart.bars)
    outputs: Outputs = []
    if len(highs) == 2:
        outputs.append(chart.out("resistance", ray_from(length, highs[0].index, highs[0].index, highs[0].price, highs[1].index, highs[1].price)))
    if len(lows) == 2:
        outputs.append(chart.out("support", ray_from(length, lows[0].index, lows[0].index, lows[0].price, lows[1].index, lows[1].price)))
    return outputs


def key_levels(high: Values, low: Values, close: Values, lookback: int, count: int) -> list[float]:
    """Swing prices within the lookback, grouped within half an ATR, the most touched first."""
    start = max(0, len(high) - lookback)
    swings = [swing for swing in zigzag(high, low, close, 6, 0) if swing.index >= start]
    ranges = atr(high, low, close, 14)
    last_range = ranges[-1] if ranges and ranges[-1] is not None else 0
    groups: list[list[float]] = []  # [total, touches]
    for swing in swings:
        group = next((item for item in groups if abs(item[0] / item[1] - swing.price) <= last_range / 2), None)
        if group is not None:
            group[0] += swing.price
            group[1] += 1
        else:
            groups.append([swing.price, 1])
    return [total / touches for total, touches in sorted(groups, key=lambda item: -item[1])[:count]]


@builtin("tv-auto-key-levels", "Auto key levels", "exact", 200, lookback=lambda period: period + 14)
def auto_key_levels(chart: Chart) -> Outputs:
    count = int(chart.number_param("count", 5, 1, integer=True))
    length = len(chart.bars)
    prices = key_levels(chart.high, chart.low, chart.close, chart.period, count)
    return [chart.out(f"level-{index + 1}", level_from(length, max(0, length - chart.period), price)) for index, price in enumerate(prices)]


# --- Lines from the bars -----------------------------------------------------------------------------------------


def auto_anchored_vwap(high: Values, low: Values, close: Values, volume: Values, length: int, mode: str) -> list[MaybeNumber]:
    """VWAP from the bar of the lookback's highest high, lowest low or highest volume."""
    start = max(0, len(high) - length)
    series = low if mode == "lowest-low" else volume if mode == "highest-volume" else high
    anchor = start
    for i in range(start, len(series)):
        if series[i] < series[anchor] if mode == "lowest-low" else series[i] > series[anchor]:
            anchor = i
    values = full(len(high))
    price_volume = 0.0
    total_volume = 0.0
    for i in range(anchor, len(high)):
        typical = (high[i] + low[i] + close[i]) / 3
        price_volume += typical * volume[i]
        total_volume += volume[i]
        values[i] = typical if total_volume == 0 else price_volume / total_volume
    return values


@builtin("tv-vwap-auto-anchored", "VWAP Auto Anchored", "exact", 200, lookback=lambda period: period + 1)
def vwap_auto_anchored(chart: Chart) -> Outputs:
    mode = chart.select_param("anchor", "highest-high", ("highest-high", "lowest-low", "highest-volume"))
    return [chart.out("vwap", auto_anchored_vwap(chart.high, chart.low, chart.close, chart.volume, chart.period, mode))]


def _marks(chart: Chart, suffix: str, marked: list[bool]) -> IndicatorOutputSeries:
    """A painted output: 1 on each marked bar."""
    return chart.out(suffix, [1.0 if mark else None for mark in marked])


@builtin("tv-bollinger-bars", "Bollinger Bars", "exact", 20)
def bollinger_bars(chart: Chart) -> Outputs:
    _, upper, lower = bollinger(chart.close, chart.period, chart.number_param("deviations", 2, 0.1))
    marked = [
        (finite(upper[i]) and value > cast(float, upper[i])) or (finite(lower[i]) and value < cast(float, lower[i]))
        for i, value in enumerate(chart.close)
    ]
    return [_marks(chart, "bars", marked), chart.out("upper", upper), chart.out("lower", lower)]


def advance_decline_bars_ratio(open_: Values, close: Values, period: int) -> list[MaybeNumber]:
    """Rising bars (close above open) over falling bars in each window of ``period`` bars; the rising count when none fell."""
    length = max(1, round(period))
    up = 0
    down = 0
    values: list[MaybeNumber] = []
    for index, value in enumerate(close):
        direction = value - open_[index]
        if direction > 0:
            up += 1
        if direction < 0:
            down += 1
        if index >= length:
            old = close[index - length] - open_[index - length]
            if old > 0:
                up -= 1
            if old < 0:
                down -= 1
        if index < length - 1:
            values.append(None)
        else:
            values.append(float(up) if down == 0 else up / down)
    return values


@builtin("tv-advance-decline-ratio-bars", "Advance/Decline Ratio (Bars)", "exact", 9)
def advance_decline_ratio_bars(chart: Chart) -> Outputs:
    return [chart.out("ratio", advance_decline_bars_ratio(chart.open, chart.close, chart.period))]


@builtin("tv-chop-zone", "Chop Zone", "exact", 30)
def chop_zone(chart: Chart) -> Outputs:
    # The chart colours each column by the EMA(34) slope's zone; the column is there wherever a zone is defined.
    high, low, close = chart.high, chart.low, chart.close
    top = highest(high, chart.period)
    bottom = lowest(low, chart.period)
    ema34 = ema(close, 34)
    marked = []
    for i, value in enumerate(close):
        average = (high[i] + low[i] + value) / 3
        previous = ema34[i - 1] if i > 0 else None
        marked.append(finite(top[i]) and finite(bottom[i]) and finite(ema34[i]) and finite(previous) and top[i] != bottom[i] and average != 0)
    return [_marks(chart, "zone", marked)]


# --- Moon phases (Meeus, Astronomical Algorithms ch. 49) ---------------------------------------------------------

_SYNODIC_DAYS = 29.530588861
_RADIANS = math.pi / 180
_MOON_EPOCH_MS = 947_116_800_000  # Date.UTC(2000, 0, 6)
_PHASE_TERMS = (
    (-0.40720, -0.40614, 0, 0, 1, 0, 0), (0.17241, 0.17302, 1, 1, 0, 0, 0), (0.01608, 0.01614, 0, 0, 2, 0, 0), (0.01039, 0.01043, 0, 0, 0, 2, 0),
    (0.00739, 0.00734, 1, -1, 1, 0, 0), (-0.00514, -0.00515, 1, 1, 1, 0, 0), (0.00208, 0.00209, 2, 2, 0, 0, 0), (-0.00111, -0.00111, 0, 0, 1, -2, 0),
    (-0.00057, -0.00057, 0, 0, 1, 2, 0), (0.00056, 0.00056, 1, 1, 2, 0, 0), (-0.00042, -0.00042, 0, 0, 3, 0, 0), (0.00042, 0.00042, 1, 1, 0, 2, 0),
    (0.00038, 0.00038, 1, 1, 0, -2, 0), (-0.00024, -0.00024, 1, -1, 2, 0, 0), (-0.00017, -0.00017, 0, 0, 0, 0, 1), (-0.00007, -0.00007, 0, 2, 1, 0, 0),
    (0.00004, 0.00004, 0, 0, 2, -2, 0), (0.00004, 0.00004, 0, 3, 0, 0, 0), (0.00003, 0.00003, 0, 1, 1, -2, 0), (0.00003, 0.00003, 0, 0, 2, 2, 0),
    (-0.00003, -0.00003, 0, 1, 1, 2, 0), (0.00003, 0.00003, 0, -1, 1, 2, 0), (-0.00002, -0.00002, 0, -1, 1, -2, 0), (-0.00002, -0.00002, 0, 1, 3, 0, 0),
    (0.00002, 0.00002, 0, 0, 4, 0, 0),
)


def _power(value: float, exponent: int) -> float:
    # V8's Math.pow returns x * x for an exponent of 2 (see _helpers); 0 and 1 are exact.
    if exponent == 0:
        return 1.0
    if exponent == 1:
        return value
    if exponent == 2:
        return value * value
    return math.pow(value, exponent)


def moon_phase_time(k: float) -> float:
    """The instant (ms) of lunation ``k``: a whole k is a new moon, k + 0.5 the full moon after it; k = 0 is 2000-01-06."""
    t = k / 1236.85
    jde = 2451550.09766 + _SYNODIC_DAYS * k + 0.00015437 * _power(t, 2) - 0.00000015 * _power(t, 3) + 0.00000000073 * _power(t, 4)
    e = 1 - 0.002516 * t - 0.0000074 * _power(t, 2)
    m = (2.5534 + 29.1053567 * k - 0.0000014 * _power(t, 2) - 0.00000011 * _power(t, 3)) * _RADIANS
    mp = (201.5643 + 385.81693528 * k + 0.0107582 * _power(t, 2) + 0.00001238 * _power(t, 3) - 0.000000058 * _power(t, 4)) * _RADIANS
    f = (160.7108 + 390.67050284 * k - 0.0016118 * _power(t, 2) - 0.00000227 * _power(t, 3) + 0.000000011 * _power(t, 4)) * _RADIANS
    omega = (124.7746 - 1.56375588 * k + 0.0020672 * _power(t, 2) + 0.00000215 * _power(t, 3)) * _RADIANS
    is_full = not float(k).is_integer()
    correction = 0.0
    for new_coefficient, full_coefficient, power, cm, cmp, cf, co in _PHASE_TERMS:
        coefficient = full_coefficient if is_full else new_coefficient
        correction += coefficient * _power(e, power) * math.sin(cm * m + cmp * mp + cf * f + co * omega)
    return (jde + correction - 2440587.5) * 86_400_000


def moon_events(start: float, end: float) -> list[tuple[float, bool]]:
    """The new and full moons between two instants (ms), as (time, full)."""
    events: list[tuple[float, bool]] = []
    k = math.floor((start - _MOON_EPOCH_MS) / (_SYNODIC_DAYS * 86_400_000)) - 1
    while moon_phase_time(k) <= end:
        for phase, is_full in ((k, False), (k + 0.5, True)):
            time = moon_phase_time(phase)
            if start <= time < end:
                events.append((time, is_full))
        k += 1
    return events


@builtin("tv-moon-phases", "Moon Phases", "exact", 1, signal_warmup=1)
def moon_phases(chart: Chart) -> Outputs:
    times = [epoch_ms(start) for start in chart.bars.start_times]
    step = times[-1] - times[-2] if len(times) > 1 else DAY_MS
    full_moon = full(len(times))
    new_moon = full(len(times))
    i = 0
    for time, is_full in moon_events(times[0], times[-1] + step):
        while i + 1 < len(times) and times[i + 1] <= time:
            i += 1
        if is_full:
            full_moon[i] = chart.high[i]
        else:
            new_moon[i] = chart.low[i]
    return [chart.out("full", full_moon), chart.out("new", new_moon)]


# --- Sessions and periods ----------------------------------------------------------------------------------------

_TRADING_SESSIONS = (
    ("Tokyo", ZoneInfo("Asia/Tokyo"), 9 * 60, 15 * 60),
    ("London", ZoneInfo("Europe/London"), 8 * 60, 16 * 60 + 30),
    ("New York", ZoneInfo("America/New_York"), 9 * 60 + 30, 16 * 60),
)


def _minute_of_day(time_ms: int, zone: ZoneInfo) -> int:
    moment = datetime.fromtimestamp(time_ms / 1000, tz=UTC).astimezone(zone)
    return moment.hour * 60 + moment.minute


@builtin("tv-trading-sessions", "Trading Sessions", "exact", 1)
def trading_sessions(chart: Chart) -> Outputs:
    # Each bar inside Tokyo, London or New York hours, on intraday bars only.
    times = [epoch_ms(start) for start in chart.bars.start_times]
    step = min((later - earlier for earlier, later in zip(times, times[1:], strict=False) if later > earlier), default=math.inf)
    if not step < DAY_MS:
        return [_marks(chart, "sessions", [False] * len(times))]
    marked = [
        any(open_ <= _minute_of_day(time, zone) < close for _, zone, open_, close in _TRADING_SESSIONS)
        for time in times
    ]
    return [_marks(chart, "sessions", marked)]


@builtin("tv-multi-time-period-charts-indicator", "Multi-Time Period Charts indicator", "exact", 1)
def multi_time_period_charts(chart: Chart) -> Outputs:
    period = chart.select_param("period", "D", ("D", "W", "M"))
    session = chart.inputs.session
    keys, _ = session_periods(session_clock(chart.bars, session), period, session)
    length = len(chart.bars)
    period_high = full(length)
    period_low = full(length)
    period_open = full(length)
    start = 0
    for i in range(length + 1):
        if i < length and keys[i] == keys[start]:
            continue
        # One higher-timeframe candle: its range and open as levels.
        top = js_max(*chart.high[start:i])
        bottom = js_min(*chart.low[start:i])
        for j in range(start, i):
            period_high[j] = top
            period_low[j] = bottom
            period_open[j] = chart.open[start]
        start = i
    return [
        _marks(chart, "candles", [True] * length),
        chart.out("high", period_high),
        chart.out("low", period_low),
        chart.out("open", period_open),
    ]
