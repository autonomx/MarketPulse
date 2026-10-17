"""Server ports of the chart's built-in indicators, batch B (``calculateTradingViewBuiltInOutputs``).

Each function mirrors one branch of the browser engine in ``tradingViewBuiltIns.ts``
operation for operation; see ``_helpers.py`` for the shared rules.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..registry import BarSeries, TradingSession
from ._helpers import (
    EPSILON,
    Chart,
    MaybeNumber,
    Outputs,
    Values,
    atr,
    builtin,
    cci,
    correlation_with_index,
    difference,
    dmi,
    ema,
    find_pattern_pivots,
    finite,
    full,
    highest,
    hl2,
    js_max,
    js_div,
    js_min,
    js_round,
    js_sign,
    js_sqrt,
    js_sum,
    lowest,
    mean,
    or_zero,
    rma,
    roc,
    rolling_sum,
    rsi,
    safe_period,
    sma,
    spearman,
    stdev,
    stochastic,
    true_range,
    true_strength,
    typical_price,
    wma,
)
from ._sessions import session_clock


def _money_flow_index(high: Values, low: Values, close: Values, volume: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    typical = [(high[i] + low[i] + value) / 3 for i, value in enumerate(close)]
    positive = [value * volume[i] if i > 0 and value > typical[i - 1] else 0.0 for i, value in enumerate(typical)]
    negative = [value * volume[i] if i > 0 and value < typical[i - 1] else 0.0 for i, value in enumerate(typical)]
    result: list[MaybeNumber] = []
    for ps, ns in zip(rolling_sum(positive, p), rolling_sum(negative, p), strict=True):
        if not finite(ps) or not finite(ns):
            result.append(None)
        elif ns == 0:
            result.append(100.0)
        else:
            result.append(100 - js_div(100, 1 + ps / ns))
    return result


def _stochastic_momentum(high: Values, low: Values, close: Values, period: int) -> list[MaybeNumber]:
    hh = highest(high, period)
    ll = lowest(low, period)
    midpoint_delta: list[float] = []
    spread: list[float] = []
    for value, h, lo in zip(close, hh, ll, strict=True):
        midpoint_delta.append(value - (h + lo) / 2 if finite(h) and finite(lo) else 0.0)
        spread.append(h - lo if finite(h) and finite(lo) else 0.0)
    numerator = ema(or_zero(ema(midpoint_delta, 3)), 3)
    denominator = ema(or_zero(ema(spread, 3)), 3)
    return [
        200 * n / d if finite(n) and finite(d) and d != 0 else None
        for n, d in zip(numerator, denominator, strict=True)
    ]


def _parabolic_sar(high: Values, low: Values) -> list[MaybeNumber]:
    result = full(len(high))
    if len(high) < 2:
        return result
    up = True
    af = 0.02
    ep = high[0]
    sar = low[0]
    result[0] = sar
    for i in range(1, len(high)):
        sar += af * (ep - sar)
        if up:
            sar = js_min(sar, low[i - 1], low[i - 2] if i > 1 else low[i - 1])
            if low[i] < sar:
                up = False
                sar = ep
                ep = low[i]
                af = 0.02
            elif high[i] > ep:
                ep = high[i]
                af = js_min(0.2, af + 0.02)
        else:
            sar = js_max(sar, high[i - 1], high[i - 2] if i > 1 else high[i - 1])
            if high[i] > sar:
                up = True
                sar = ep
                ep = high[i]
                af = 0.02
            elif low[i] < ep:
                ep = low[i]
                af = js_min(0.2, af + 0.02)
        result[i] = sar
    return result


def _supertrend(high: Values, low: Values, close: Values, period: int, factor: float = 3) -> list[MaybeNumber]:
    line = full(len(close))
    upper = 0.0
    lower = 0.0
    trend = 1
    for i, a in enumerate(atr(high, low, close, period)):
        if not finite(a):
            continue
        mid = (high[i] + low[i]) / 2
        basic_upper = mid + factor * a
        basic_lower = mid - factor * a
        upper = basic_upper if i == 0 or close[i - 1] > upper else js_min(basic_upper, upper)
        lower = basic_lower if i == 0 or close[i - 1] < lower else js_max(basic_lower, lower)
        if trend > 0 and close[i] < lower:
            trend = -1
        elif trend < 0 and close[i] > upper:
            trend = 1
        line[i] = lower if trend > 0 else upper
    return line


def _triple_ema(values: Values, period: int) -> list[MaybeNumber]:
    e1 = ema(values, period)
    first = values[0] if values else 0.0
    e1_numeric = [value if finite(value) else first for value in e1]
    e2 = ema(e1_numeric, period)
    e2_numeric = [value if finite(value) else e1_numeric[i] for i, value in enumerate(e2)]
    e3 = ema(e2_numeric, period)
    return [
        3 * a - 3 * b + c if finite(a) and finite(b) and finite(c) else None
        for a, b, c in zip(e1, e2, e3, strict=True)
    ]


def _ratio(numerators: Sequence[MaybeNumber], denominators: Sequence[MaybeNumber]) -> list[MaybeNumber]:
    return [n / d if finite(n) and finite(d) and d != 0 else None for n, d in zip(numerators, denominators, strict=True)]


def _relative_vigor(open_: Values, high: Values, low: Values, close: Values, period: int) -> tuple[list[MaybeNumber], list[MaybeNumber]]:
    numerator = [value - open_[i] for i, value in enumerate(close)]
    denominator = [js_max(EPSILON, value - low[i]) for i, value in enumerate(high)]
    rvi = [
        n / d * 100 if finite(n) and finite(d) and d != 0 else None
        for n, d in zip(sma(numerator, period), sma(denominator, period), strict=True)
    ]
    return rvi, sma(or_zero(rvi), 4)


def _relative_volatility(close: Values, period: int) -> list[MaybeNumber]:
    p = safe_period(period)
    deviation = or_zero(stdev(close, p))
    up = [value if i > 0 and close[i] > close[i - 1] else 0.0 for i, value in enumerate(deviation)]
    down = [value if i > 0 and close[i] < close[i - 1] else 0.0 for i, value in enumerate(deviation)]
    return [
        100 * u / (u + d) if finite(u) and finite(d) and u + d != 0 else None
        for u, d in zip(rma(up, p), rma(down, p), strict=True)
    ]


def _ultimate_oscillator(high: Values, low: Values, close: Values) -> list[MaybeNumber]:
    buying = [value - low[i] if i == 0 else value - js_min(low[i], close[i - 1]) for i, value in enumerate(close)]
    ranges = [
        high[i] - low[i] if i == 0 else js_max(high[i], close[i - 1]) - js_min(low[i], close[i - 1])
        for i in range(len(close))
    ]

    def average(period: int) -> list[MaybeNumber]:
        return _ratio(rolling_sum(buying, period), rolling_sum(ranges, period))

    return [
        100 * (4 * x + 2 * y + z) / 7 if finite(x) and finite(y) and finite(z) else None
        for x, y, z in zip(average(7), average(14), average(28), strict=True)
    ]


def _vortex(high: Values, low: Values, close: Values, period: int) -> tuple[list[MaybeNumber], list[MaybeNumber]]:
    vm_plus = [0.0 if i == 0 else abs(value - low[i - 1]) for i, value in enumerate(high)]
    vm_minus = [0.0 if i == 0 else abs(value - high[i - 1]) for i, value in enumerate(low)]
    tr_sum = rolling_sum(true_range(high, low, close), period)
    return _ratio(rolling_sum(vm_plus, period), tr_sum), _ratio(rolling_sum(vm_minus, period), tr_sum)


def _volume_weighted_ma(close: Values, volume: Values, period: int) -> list[MaybeNumber]:
    price_volume = [value * volume[i] for i, value in enumerate(close)]
    return _ratio(rolling_sum(price_volume, period), rolling_sum(volume, period))


def _session_twap(bars: BarSeries, typical: Values, session: TradingSession | None) -> list[MaybeNumber]:
    # Sessions are the shared session days (UTC dates without a session calendar), like the browser's sessionClock.
    result = full(len(bars))
    day = session_clock(bars, session).day
    running = 0.0
    count = 0
    for i in range(len(bars)):
        if i == 0 or day[i] != day[i - 1]:
            running = 0.0
            count = 0
        running += typical[i]
        count += 1
        result[i] = running / count
    return result


def _technical_rating(high: Values, low: Values, close: Values) -> list[MaybeNumber]:
    r = rsi(close, 14)
    st = stochastic(high, low, close, 14)
    c = cci(high, low, close, 20)
    momentum = roc(close, 10)
    ma20 = sma(close, 20)
    ma50 = sma(close, 50)
    plus, minus, adx = dmi(high, low, close, 14)
    result: list[MaybeNumber] = []
    for i, value in enumerate(close):
        signals: list[float] = []
        if finite(ri := r[i]):
            signals.append(1.0 if ri > 55 else -1.0 if ri < 45 else 0.0)
        if finite(si := st[i]):
            signals.append(1.0 if si > 60 else -1.0 if si < 40 else 0.0)
        if finite(ci := c[i]):
            signals.append(1.0 if ci > 50 else -1.0 if ci < -50 else 0.0)
        if finite(mi := momentum[i]):
            signals.append(js_sign(mi))
        if finite(m20 := ma20[i]):
            signals.append(1.0 if value > m20 else -1.0)
        if finite(m50 := ma50[i]):
            signals.append(1.0 if value > m50 else -1.0)
        a, p, m = adx[i], plus[i], minus[i]
        if finite(a) and finite(p) and finite(m) and a > 20:
            signals.append(1.0 if p > m else -1.0)
        result.append(mean(signals) if signals else None)
    return result


@builtin("tv-money-flow-mfi", "Money Flow (MFI)", "exact", 14)
def _mfi(chart: Chart) -> Outputs:
    return [chart.out("mfi", _money_flow_index(chart.high, chart.low, chart.close, chart.volume, chart.period))]


@builtin("tv-moving-average-ribbon", "Moving Average Ribbon", "exact", 20)
def _moving_average_ribbon(chart: Chart) -> Outputs:
    return [chart.out(f"sma-{p}", sma(chart.close, p)) for p in (20, 50, 100, 200)]


@builtin("tv-moving-averages", "Moving Averages", "recursive", 20)
def _moving_averages(chart: Chart) -> Outputs:
    return [chart.out("sma", sma(chart.close, chart.period)), chart.out("ema", ema(chart.close, chart.period))]


def _volume_index(chart: Chart, positive: bool) -> list[MaybeNumber]:
    close, volume = chart.close, chart.volume
    current = 1000.0
    values: list[MaybeNumber] = []
    for i, value in enumerate(close):
        if i > 0 and (volume[i] > volume[i - 1] if positive else volume[i] < volume[i - 1]) and close[i - 1] != 0:
            current *= 1 + (value - close[i - 1]) / close[i - 1]
        values.append(current)
    return values


@builtin("tv-negative-volume-index-nvi", "Negative Volume Index (NVI)", "exact", 1)
def _nvi(chart: Chart) -> Outputs:
    return [chart.out("nvi", _volume_index(chart, positive=False))]


@builtin("tv-positive-volume-index-pvi", "Positive Volume Index (PVI)", "exact", 1)
def _pvi(chart: Chart) -> Outputs:
    return [chart.out("pvi", _volume_index(chart, positive=True))]


def _net_volume(chart: Chart) -> list[MaybeNumber]:
    close = chart.close
    return [
        0.0 if i == 0 else value if close[i] > close[i - 1] else -value if close[i] < close[i - 1] else 0.0
        for i, value in enumerate(chart.volume)
    ]


@builtin("tv-net-volume", "Net Volume", "exact", 1)
def _net_volume_indicator(chart: Chart) -> Outputs:
    return [chart.out("net-volume", _net_volume(chart))]


@builtin("tv-up-down-volume", "Up/Down Volume", "exact", 1)
def _up_down_volume(chart: Chart) -> Outputs:
    return [chart.out("net-volume", _net_volume(chart))]


@builtin("tv-on-balance-volume-obv", "On Balance Volume (OBV)", "exact", 1)
def _obv(chart: Chart) -> Outputs:
    close = chart.close
    current = 0.0
    values: list[MaybeNumber] = []
    for i, value in enumerate(chart.volume):
        if i > 0:
            current += value if close[i] > close[i - 1] else -value if close[i] < close[i - 1] else 0.0
        values.append(current)
    return [chart.out("obv", values)]


@builtin("tv-parabolic-sar-sar", "Parabolic SAR (SAR)", "recursive", 2)
def _parabolic_sar_indicator(chart: Chart) -> Outputs:
    return [chart.out("sar", _parabolic_sar(chart.high, chart.low))]


def _percentage_oscillator(chart: Chart, source: Values) -> Outputs:
    inputs = chart.inputs
    fast = ema(source, safe_period(12 if inputs.fast_period is None else inputs.fast_period))
    slow = ema(source, safe_period(26 if inputs.slow_period is None else inputs.slow_period))
    line = [100 * (f - s) / s if finite(f) and finite(s) and s != 0 else None for f, s in zip(fast, slow, strict=True)]
    signal_period = safe_period(9 if inputs.signal_period is None else inputs.signal_period)
    return [chart.out("line", line), chart.out("signal", ema(or_zero(line), signal_period))]


@builtin("tv-percentage-price-oscillator-ppo", "Percentage Price Oscillator (PPO)", "recursive", 9)
def _ppo(chart: Chart) -> Outputs:
    return _percentage_oscillator(chart, chart.close)


@builtin("tv-percentage-volume-oscillator-pvo", "Percentage Volume Oscillator (PVO)", "recursive", 9)
def _pvo(chart: Chart) -> Outputs:
    return _percentage_oscillator(chart, chart.volume)


@builtin("tv-performance", "Performance", "exact", 1)
def _performance(chart: Chart) -> Outputs:
    anchor = chart.close[0] or 1.0
    return [chart.out("performance", [(value / anchor - 1) * 100 for value in chart.close])]


@builtin("tv-pivot-points-high-low", "Pivot Points High Low", "exact", 10)
def _pivot_points_high_low(chart: Chart) -> Outputs:
    strength = max(2, min(8, js_round(chart.period / 3)))
    by_index = {pivot.index: pivot for pivot in find_pattern_pivots(chart.bars, strength)}
    highs = full(len(chart.bars))
    lows = full(len(chart.bars))
    h: MaybeNumber = None
    lo: MaybeNumber = None
    for i in range(len(chart.bars)):
        pivot = by_index.get(i)
        if pivot is not None and pivot.type == "high":
            h = pivot.price
        if pivot is not None and pivot.type == "low":
            lo = pivot.price
        highs[i] = h
        lows[i] = lo
    return [chart.out("pivot-high", highs), chart.out("pivot-low", lows)]


@builtin("tv-pivot-points-standard", "Pivot Points Standard", "exact", 20)
def _pivot_points_standard(chart: Chart) -> Outputs:
    high, low, close = chart.high, chart.low, chart.close
    pp, r1, s1 = full(len(close)), full(len(close)), full(len(close))
    for i in range(1, len(close)):
        pivot = (high[i - 1] + low[i - 1] + close[i - 1]) / 3
        pp[i] = pivot
        r1[i] = 2 * pivot - low[i - 1]
        s1[i] = 2 * pivot - high[i - 1]
    return [chart.out("pp", pp), chart.out("r1", r1), chart.out("s1", s1)]


@builtin("tv-price-momentum-oscillator-pmo", "Price Momentum Oscillator (PMO)", "recursive", 35)
def _pmo(chart: Chart) -> Outputs:
    one_roc = [value * 10 if finite(value) else 0.0 for value in roc(chart.close, 1)]
    pmo = ema(or_zero(ema(one_roc, 35)), 20)
    return [chart.out("pmo", pmo), chart.out("signal", ema(or_zero(pmo), 10))]


@builtin("tv-price-volume-trend-pvt", "Price Volume Trend (PVT)", "exact", 1)
def _pvt(chart: Chart) -> Outputs:
    close, volume = chart.close, chart.volume
    current = 0.0
    values: list[MaybeNumber] = []
    for i, value in enumerate(close):
        if i > 0 and close[i - 1] != 0:
            current += volume[i] * (value - close[i - 1]) / close[i - 1]
        values.append(current)
    return [chart.out("pvt", values)]


_SPECIAL_K_COMPONENTS = (
    (10, 10, 1), (15, 10, 2), (20, 10, 3), (30, 15, 4), (40, 20, 1), (65, 30, 2),
    (75, 30, 3), (100, 40, 4), (195, 65, 1), (265, 65, 2), (390, 100, 3), (530, 130, 4),
)


@builtin("tv-pring-s-special-k", "Pring's Special K", "exact", 30)
def _special_k(chart: Chart) -> Outputs:
    components = [
        [value * weight if finite(value) else 0.0 for value in sma(or_zero(roc(chart.close, r)), s)]
        for r, s, weight in _SPECIAL_K_COMPONENTS
    ]
    values = [js_sum([series[i] for series in components]) for i in range(len(chart.bars))]
    return [chart.out("special-k", values)]


@builtin("tv-rank-correlation-index-rci", "Rank Correlation Index (RCI)", "exact", 9)
def _rci(chart: Chart) -> Outputs:
    return [chart.out("rci", spearman(chart.close, chart.period))]


@builtin("tv-rci-ribbon", "RCI Ribbon", "exact", 9)
def _rci_ribbon(chart: Chart) -> Outputs:
    return [chart.out(f"rci-{p}", spearman(chart.close, p)) for p in (9, 26, 52)]


@builtin("tv-rate-of-change-roc", "Rate of Change (ROC)", "exact", 9)
def _roc(chart: Chart) -> Outputs:
    return [chart.out("roc", roc(chart.close, chart.period))]


@builtin("tv-relative-vigor-index", "Relative Vigor Index", "exact", 10)
def _relative_vigor_index(chart: Chart) -> Outputs:
    rvi, signal = _relative_vigor(chart.open, chart.high, chart.low, chart.close, chart.period)
    return [chart.out("rvi", rvi), chart.out("signal", signal)]


@builtin("tv-relative-volatility-index", "Relative Volatility Index", "recursive", 14)
def _relative_volatility_index(chart: Chart) -> Outputs:
    return [chart.out("rvol", _relative_volatility(chart.close, chart.period))]


def _smi_ergodic(close: Values) -> tuple[list[MaybeNumber], list[MaybeNumber]]:
    tsi = true_strength(close, 20, 5)
    return tsi, ema(or_zero(tsi), 5)


@builtin("tv-smi-ergodic-indicator", "SMI Ergodic Indicator", "recursive", 20)
def _smi_ergodic_indicator(chart: Chart) -> Outputs:
    tsi, signal = _smi_ergodic(chart.close)
    return [chart.out("smi", tsi), chart.out("signal", signal)]


@builtin("tv-smi-ergodic-oscillator", "SMI Ergodic Oscillator", "recursive", 20)
def _smi_ergodic_oscillator(chart: Chart) -> Outputs:
    tsi, signal = _smi_ergodic(chart.close)
    return [chart.out("oscillator", difference(tsi, signal))]


@builtin("tv-smoothed-moving-average", "Smoothed Moving Average", "recursive", 20)
def _smoothed_moving_average(chart: Chart) -> Outputs:
    return [chart.out("smma", rma(chart.close, chart.period))]


@builtin("tv-stochastic-stoch", "Stochastic (STOCH)", "exact", 14)
def _stochastic_indicator(chart: Chart) -> Outputs:
    k = stochastic(chart.high, chart.low, chart.close, chart.period)
    return [chart.out("k", k), chart.out("d", sma(or_zero(k), 3))]


@builtin("tv-stochastic-momentum-index-smi", "Stochastic Momentum Index (SMI)", "recursive", 14)
def _stochastic_momentum_index(chart: Chart) -> Outputs:
    smi = _stochastic_momentum(chart.high, chart.low, chart.close, chart.period)
    return [chart.out("smi", smi), chart.out("signal", ema(or_zero(smi), 3))]


@builtin("tv-supertrend", "Supertrend", "recursive", 10)
def _supertrend_indicator(chart: Chart) -> Outputs:
    return [chart.out("supertrend", _supertrend(chart.high, chart.low, chart.close, chart.period))]


@builtin("tv-technical-ratings", "Technical Ratings", "recursive", 14)
def _technical_ratings(chart: Chart) -> Outputs:
    return [chart.out("rating", _technical_rating(chart.high, chart.low, chart.close))]


@builtin("tv-time-weighted-average-price", "Time Weighted Average Price", "exact", 1)
def _twap(chart: Chart) -> Outputs:
    return [chart.out("twap", _session_twap(chart.bars, typical_price(chart.bars), chart.inputs.session))]


@builtin("tv-trend-strength-index", "Trend Strength Index", "exact", 20)
def _trend_strength_index(chart: Chart) -> Outputs:
    return [chart.out("trend-strength", correlation_with_index(chart.close, chart.period))]


@builtin("tv-triple-ema", "Triple EMA", "recursive", 9)
def _triple_ema_indicator(chart: Chart) -> Outputs:
    return [chart.out("tema", _triple_ema(chart.close, chart.period))]


@builtin("tv-trix", "TRIX", "recursive", 18)
def _trix(chart: Chart) -> Outputs:
    def smoothed(source: Values) -> list[float]:
        return [value if finite(value) else source[i] for i, value in enumerate(ema(source, chart.period))]

    e3 = smoothed(smoothed(smoothed(chart.close)))
    values: list[MaybeNumber] = [None if i == 0 or e3[i - 1] == 0 else (value / e3[i - 1] - 1) * 100 for i, value in enumerate(e3)]
    return [chart.out("trix", values)]


@builtin("tv-true-strength-index", "True Strength Index", "recursive", 25)
def _true_strength_index(chart: Chart) -> Outputs:
    inputs = chart.inputs
    long_period = safe_period(25 if inputs.slow_period is None else inputs.slow_period)
    short_period = safe_period(13 if inputs.fast_period is None else inputs.fast_period)
    signal_period = safe_period(13 if inputs.signal_period is None else inputs.signal_period)
    tsi = true_strength(chart.close, long_period, short_period)
    return [chart.out("tsi", tsi), chart.out("signal", ema(or_zero(tsi), signal_period))]


@builtin("tv-ulcer-index", "Ulcer Index", "exact", 14)
def _ulcer_index(chart: Chart) -> Outputs:
    squared: list[float] = []
    for value, h in zip(chart.close, highest(chart.close, chart.period), strict=True):
        if finite(h) and h != 0:
            drawdown = (value - h) / h * 100
            squared.append(drawdown * drawdown)
        else:
            squared.append(0.0)
    average = sma(squared, chart.period)
    return [chart.out("ulcer", [js_sqrt(v) if finite(v) else None for v in average])]


@builtin("tv-ultimate-oscillator-uo", "Ultimate Oscillator (UO)", "exact", 28)
def _ultimate_oscillator_indicator(chart: Chart) -> Outputs:
    return [chart.out("uo", _ultimate_oscillator(chart.high, chart.low, chart.close))]


@builtin("tv-volatility-stop", "Volatility Stop", "recursive", 20)
def _volatility_stop(chart: Chart) -> Outputs:
    close, period = chart.close, chart.period
    average_range = atr(chart.high, chart.low, close, period)
    values: list[MaybeNumber] = []
    for value, a, h, lo in zip(close, average_range, highest(close, period), lowest(close, period), strict=True):
        if finite(a) and finite(h) and finite(lo):
            values.append(h - 2 * a if value >= (h + lo) / 2 else lo + 2 * a)
        else:
            values.append(None)
    return [chart.out("vstop", values)]


@builtin("tv-volume", "Volume", "exact", 1)
def _volume(chart: Chart) -> Outputs:
    return [chart.out("volume", chart.volume)]


@builtin("tv-volume-weighted-moving-average-vwma", "Volume-Weighted Moving Average (VWMA)", "exact", 20)
def _vwma(chart: Chart) -> Outputs:
    return [chart.out("vwma", _volume_weighted_ma(chart.close, chart.volume, chart.period))]


@builtin("tv-vortex-indicator", "Vortex Indicator", "exact", 14)
def _vortex_indicator(chart: Chart) -> Outputs:
    plus, minus = _vortex(chart.high, chart.low, chart.close, chart.period)
    return [chart.out("plus", plus), chart.out("minus", minus)]


@builtin("tv-weighted-moving-average", "Weighted Moving Average", "exact", 9)
def _weighted_moving_average(chart: Chart) -> Outputs:
    return [chart.out("wma", wma(chart.close, chart.period))]


@builtin("tv-williams-r-r", "Williams %R (%R)", "exact", 14)
def _williams_r(chart: Chart) -> Outputs:
    hh = highest(chart.high, chart.period)
    ll = lowest(chart.low, chart.period)
    values = [
        -100 * (h - value) / (h - lo) if finite(h) and finite(lo) and h != lo else None
        for value, h, lo in zip(chart.close, hh, ll, strict=True)
    ]
    return [chart.out("williams-r", values)]


@builtin("tv-williams-alligator", "Williams Alligator", "recursive", 13)
def _williams_alligator(chart: Chart) -> Outputs:
    median_price = hl2(chart.bars)
    return [
        chart.out("jaw", rma(median_price, 13)),
        chart.out("teeth", rma(median_price, 8)),
        chart.out("lips", rma(median_price, 5)),
    ]


@builtin("tv-williams-fractal", "Williams Fractal", "exact", 2)
def _williams_fractal(chart: Chart) -> Outputs:
    high, low = chart.high, chart.low
    radius = max(2, min(8, chart.period))
    up = full(len(chart.bars))
    down = full(len(chart.bars))
    for i in range(radius, len(chart.bars) - radius):
        if high[i] == js_max(*high[i - radius : i + radius + 1]):
            up[i] = high[i]
        if low[i] == js_min(*low[i - radius : i + radius + 1]):
            down[i] = low[i]
    return [chart.out("up-fractal", up), chart.out("down-fractal", down)]


@builtin("tv-woodies-cci", "Woodies CCI", "exact", 14)
def _woodies_cci(chart: Chart) -> Outputs:
    return [
        chart.out("trend-cci", cci(chart.high, chart.low, chart.close, chart.period)),
        chart.out("entry-cci", cci(chart.high, chart.low, chart.close, 6)),
    ]


@builtin("tv-zig-zag", "Zig Zag", "exact", 5)
def _zig_zag(chart: Chart) -> Outputs:
    values = full(len(chart.bars))
    for pivot in find_pattern_pivots(chart.bars, max(2, min(8, chart.period))):
        values[pivot.index] = pivot.price
    return [chart.out("zigzag", values)]
