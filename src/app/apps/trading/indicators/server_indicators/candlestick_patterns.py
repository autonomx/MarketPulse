"""Server port of the chart's candlestick pattern recognition (TVP-6.3), ``candlestickPatterns.ts`` operation for operation.

TradingView's built-in candlestick pattern definitions: a body is small or long against EMA(14) of the bodies, a shadow
counts above 5% of the body, a doji body is at most 5% of the range, and the trend is the close against SMA(50)
(optionally SMA(50) against SMA(200)). A value missing for lack of history makes a pattern false, like Pine's ``na``.
Each output marks the bars a pattern completes on, at the bar's low (bullish) or high (bearish, neutral).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ._helpers import Chart, MaybeNumber, Outputs, builtin, ema, finite, full, js_max, js_min, sma

SHADOW_PERCENT = 5
DOJI_BODY_PERCENT = 5
SHADOW_EQUALS_PERCENT = 100
FACTOR = 2
LONG_SHADOW_PERCENT = 75
SPINNING_TOP_PERCENT = 34


@dataclass(frozen=True)
class _Candles:
    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    body_hi: list[float]
    body_lo: list[float]
    body: list[float]
    body_avg: list[MaybeNumber]
    body_middle: list[float]
    small: list[bool]
    long: list[bool]
    up_shadow: list[float]
    dn_shadow: list[float]
    has_up_shadow: list[bool]
    has_dn_shadow: list[bool]
    white: list[bool]
    black: list[bool]
    range: list[float]
    doji_body: list[bool]
    doji: list[bool]
    marubozu: list[bool]
    up: list[bool]
    down: list[bool]


def _shadow_equals(upper: float, lower: float) -> bool:
    if upper == lower:
        return True
    # Pine: x / 0 is na, and a comparison with na is false.
    return (
        lower != 0
        and upper != 0
        and abs(upper - lower) / lower * 100 < SHADOW_EQUALS_PERCENT
        and abs(lower - upper) / upper * 100 < SHADOW_EQUALS_PERCENT
    )


def _trend(close: Sequence[float], trend: str) -> tuple[list[bool], list[bool]]:
    if trend == "none":
        return [True] * len(close), [True] * len(close)
    sma50 = sma(close, 50)
    sma200 = sma(close, 200) if trend == "sma50-sma200" else []
    up: list[bool] = []
    down: list[bool] = []
    for i, value in enumerate(close):
        average = sma50[i]
        longer = sma200[i] if trend == "sma50-sma200" else None
        if not finite(average):
            up.append(False)
            down.append(False)
            continue
        only_sma50 = trend == "sma50"
        up.append(value > average and (only_sma50 or (finite(longer) and average > longer)))
        down.append(value < average and (only_sma50 or (finite(longer) and average < longer)))
    return up, down


def _candles(open_: Sequence[float], high: Sequence[float], low: Sequence[float], close: Sequence[float], trend: str) -> _Candles:
    body_hi = [js_max(value, open_[i]) for i, value in enumerate(close)]
    body_lo = [js_min(value, open_[i]) for i, value in enumerate(close)]
    body = [value - body_lo[i] for i, value in enumerate(body_hi)]
    body_avg = ema(body, 14)
    up_shadow = [value - body_hi[i] for i, value in enumerate(high)]
    dn_shadow = [body_lo[i] - value for i, value in enumerate(low)]
    range_ = [value - low[i] for i, value in enumerate(high)]
    doji_body = [range_[i] > 0 and value <= range_[i] * DOJI_BODY_PERCENT / 100 for i, value in enumerate(body)]
    small = [finite(body_avg[i]) and value < body_avg[i] for i, value in enumerate(body)]  # type: ignore[operator]
    long = [finite(body_avg[i]) and value > body_avg[i] for i, value in enumerate(body)]  # type: ignore[operator]
    up, down = _trend(close, trend)
    return _Candles(
        open=open_,
        high=high,
        low=low,
        close=close,
        body_hi=body_hi,
        body_lo=body_lo,
        body=body,
        body_avg=body_avg,
        body_middle=[value / 2 + body_lo[i] for i, value in enumerate(body)],
        small=small,
        long=long,
        up_shadow=up_shadow,
        dn_shadow=dn_shadow,
        has_up_shadow=[value > SHADOW_PERCENT / 100 * body[i] for i, value in enumerate(up_shadow)],
        has_dn_shadow=[value > SHADOW_PERCENT / 100 * body[i] for i, value in enumerate(dn_shadow)],
        white=[value < close[i] for i, value in enumerate(open_)],
        black=[value > close[i] for i, value in enumerate(open_)],
        range=range_,
        doji_body=doji_body,
        doji=[value and _shadow_equals(up_shadow[i], dn_shadow[i]) for i, value in enumerate(doji_body)],
        marubozu=[
            value and up_shadow[i] <= SHADOW_PERCENT / 100 * body[i] and dn_shadow[i] <= SHADOW_PERCENT / 100 * body[i]
            for i, value in enumerate(long)
        ],
        up=up,
        down=down,
    )


def _hammer_shape(c: _Candles, i: int) -> bool:
    return (
        c.small[i] and c.body[i] > 0 and c.body_lo[i] > (c.high[i] + c.low[i]) / 2
        and c.dn_shadow[i] >= FACTOR * c.body[i] and not c.has_up_shadow[i]
    )


def _star_shape(c: _Candles, i: int) -> bool:
    return (
        c.small[i] and c.body[i] > 0 and c.body_hi[i] < (c.high[i] + c.low[i]) / 2
        and c.up_shadow[i] >= FACTOR * c.body[i] and not c.has_dn_shadow[i]
    )


def _harami(c: _Candles, i: int) -> bool:
    return c.long[i - 1] and c.high[i] <= c.body_hi[i - 1] and c.low[i] >= c.body_lo[i - 1]


def _spinning_top(c: _Candles, i: int) -> bool:
    return (
        c.dn_shadow[i] >= c.range[i] / 100 * SPINNING_TOP_PERCENT
        and c.up_shadow[i] >= c.range[i] / 100 * SPINNING_TOP_PERCENT
        and not c.doji_body[i]
    )


def _no_up_shadow(c: _Candles, i: int) -> bool:
    return c.range[i] * SHADOW_PERCENT / 100 > c.up_shadow[i]


def _no_dn_shadow(c: _Candles, i: int) -> bool:
    return c.range[i] * SHADOW_PERCENT / 100 > c.dn_shadow[i]


def _near(a: float, b: float, average: MaybeNumber) -> bool:
    return finite(average) and abs(a - b) <= average * 0.05


def _three_inside(c: _Candles, i: int, white: bool) -> bool:
    for k in (3, 2, 1):
        if not (c.small[i - k] and (c.white[i - k] if white else c.black[i - k])):
            return False
        if white and not (c.open[i - k] > c.low[i - 4] and c.close[i - k] < c.high[i - 4]):
            return False
        if not white and not (c.open[i - k] < c.high[i - 4] and c.close[i - k] > c.low[i - 4]):
            return False
    return True


Detector = Callable[[_Candles, int], bool]


def _abandoned_baby_bearish(c: _Candles, i: int) -> bool:
    return (
        c.up[i - 2] and c.white[i - 2] and c.doji_body[i - 1] and c.high[i - 2] < c.low[i - 1]
        and c.black[i] and c.low[i - 1] > c.high[i]
    )


def _abandoned_baby_bullish(c: _Candles, i: int) -> bool:
    return (
        c.down[i - 2] and c.black[i - 2] and c.doji_body[i - 1] and c.low[i - 2] > c.high[i - 1]
        and c.white[i] and c.high[i - 1] < c.low[i]
    )


def _dark_cloud_cover(c: _Candles, i: int) -> bool:
    return (
        c.up[i - 1] and c.white[i - 1] and c.long[i - 1] and c.black[i] and c.open[i] >= c.high[i - 1]
        and c.close[i] < c.body_middle[i - 1] and c.close[i] > c.open[i - 1]
    )


def _doji(c: _Candles, i: int) -> bool:
    dragonfly = c.doji_body[i] and c.up_shadow[i] <= c.body[i]
    gravestone = c.doji_body[i] and c.dn_shadow[i] <= c.body[i]
    return c.doji[i] and not dragonfly and not gravestone


def _doji_star_bearish(c: _Candles, i: int) -> bool:
    return c.up[i] and c.white[i - 1] and c.long[i - 1] and c.doji_body[i] and c.body_lo[i] > c.body_hi[i - 1]


def _doji_star_bullish(c: _Candles, i: int) -> bool:
    return c.down[i] and c.black[i - 1] and c.long[i - 1] and c.doji_body[i] and c.body_hi[i] < c.body_lo[i - 1]


def _downside_tasuki_gap(c: _Candles, i: int) -> bool:
    return (
        c.long[i - 2] and c.small[i - 1] and c.down[i] and c.black[i - 2] and c.body_hi[i - 1] < c.body_lo[i - 2]
        and c.black[i - 1] and c.white[i] and c.body_hi[i] <= c.body_lo[i - 2] and c.body_hi[i] >= c.body_hi[i - 1]
    )


def _engulfing_bearish(c: _Candles, i: int) -> bool:
    return (
        c.up[i] and c.black[i] and c.long[i] and c.white[i - 1] and c.small[i - 1]
        and c.close[i] <= c.open[i - 1] and c.open[i] >= c.close[i - 1]
        and (c.close[i] < c.open[i - 1] or c.open[i] > c.close[i - 1])
    )


def _engulfing_bullish(c: _Candles, i: int) -> bool:
    return (
        c.down[i] and c.white[i] and c.long[i] and c.black[i - 1] and c.small[i - 1]
        and c.close[i] >= c.open[i - 1] and c.open[i] <= c.close[i - 1]
        and (c.close[i] > c.open[i - 1] or c.open[i] < c.close[i - 1])
    )


def _evening(c: _Candles, i: int, middle: list[bool]) -> bool:
    return (
        c.long[i - 2] and middle[i - 1] and c.long[i] and c.up[i] and c.white[i - 2] and c.body_lo[i - 1] > c.body_hi[i - 2]
        and c.black[i] and c.body_lo[i] <= c.body_middle[i - 2] and c.body_lo[i] > c.body_lo[i - 2]
        and c.body_lo[i - 1] > c.body_hi[i]
    )


def _morning(c: _Candles, i: int, middle: list[bool]) -> bool:
    return (
        c.long[i - 2] and middle[i - 1] and c.long[i] and c.down[i] and c.black[i - 2] and c.body_hi[i - 1] < c.body_lo[i - 2]
        and c.white[i] and c.body_hi[i] >= c.body_middle[i - 2] and c.body_hi[i] < c.body_hi[i - 2]
        and c.body_hi[i - 1] < c.body_lo[i]
    )


def _three_black_crows(c: _Candles, i: int) -> bool:
    return (
        c.long[i] and c.long[i - 1] and c.long[i - 2] and c.black[i] and c.black[i - 1] and c.black[i - 2]
        and c.close[i] < c.close[i - 1] and c.close[i - 1] < c.close[i - 2]
        and c.open[i] > c.close[i - 1] and c.open[i] < c.open[i - 1] and c.open[i - 1] > c.close[i - 2] and c.open[i - 1] < c.open[i - 2]
        and _no_dn_shadow(c, i) and _no_dn_shadow(c, i - 1) and _no_dn_shadow(c, i - 2)
    )


def _three_white_soldiers(c: _Candles, i: int) -> bool:
    return (
        c.long[i] and c.long[i - 1] and c.long[i - 2] and c.white[i] and c.white[i - 1] and c.white[i - 2]
        and c.close[i] > c.close[i - 1] and c.close[i - 1] > c.close[i - 2]
        and c.open[i] < c.close[i - 1] and c.open[i] > c.open[i - 1] and c.open[i - 1] < c.close[i - 2] and c.open[i - 1] > c.open[i - 2]
        and _no_up_shadow(c, i) and _no_up_shadow(c, i - 1) and _no_up_shadow(c, i - 2)
    )


def _tweezer(c: _Candles, i: int, bottom: bool) -> bool:
    shape = not c.doji_body[i] or (c.has_up_shadow[i] and c.has_dn_shadow[i])
    if bottom:
        return (
            c.down[i - 1] and shape and _near(c.low[i], c.low[i - 1], c.body_avg[i])
            and c.black[i - 1] and c.white[i] and c.long[i - 1]
        )
    return (
        c.up[i - 1] and shape and _near(c.high[i], c.high[i - 1], c.body_avg[i])
        and c.white[i - 1] and c.black[i] and c.long[i - 1]
    )


# key, direction, lookback, detector; the order of candlestickPatterns.ts.
PATTERNS: tuple[tuple[str, str, int, Detector], ...] = (
    ("abandoned-baby-bearish", "bearish", 2, _abandoned_baby_bearish),
    ("abandoned-baby-bullish", "bullish", 2, _abandoned_baby_bullish),
    ("dark-cloud-cover", "bearish", 1, _dark_cloud_cover),
    ("doji", "neutral", 0, _doji),
    ("doji-star-bearish", "bearish", 1, _doji_star_bearish),
    ("doji-star-bullish", "bullish", 1, _doji_star_bullish),
    ("downside-tasuki-gap", "bearish", 2, _downside_tasuki_gap),
    ("dragonfly-doji", "bullish", 0, lambda c, i: c.doji_body[i] and c.up_shadow[i] <= c.body[i]),
    ("engulfing-bearish", "bearish", 1, _engulfing_bearish),
    ("engulfing-bullish", "bullish", 1, _engulfing_bullish),
    ("evening-doji-star", "bearish", 2, lambda c, i: _evening(c, i, c.doji_body)),
    ("evening-star", "bearish", 2, lambda c, i: _evening(c, i, c.small)),
    (
        "falling-three-methods", "bearish", 4,
        lambda c, i: c.down[i - 4] and c.long[i - 4] and c.black[i - 4] and _three_inside(c, i, True)
        and c.long[i] and c.black[i] and c.close[i] < c.close[i - 4],
    ),
    ("falling-window", "bearish", 1, lambda c, i: c.down[i - 1] and c.range[i] != 0 and c.range[i - 1] != 0 and c.high[i] < c.low[i - 1]),
    ("gravestone-doji", "bearish", 0, lambda c, i: c.doji_body[i] and c.dn_shadow[i] <= c.body[i]),
    ("hammer", "bullish", 0, lambda c, i: _hammer_shape(c, i) and c.down[i]),
    ("hanging-man", "bearish", 0, lambda c, i: _hammer_shape(c, i) and c.up[i]),
    ("harami-bearish", "bearish", 1, lambda c, i: _harami(c, i) and c.white[i - 1] and c.up[i - 1] and c.black[i] and c.small[i]),
    ("harami-bullish", "bullish", 1, lambda c, i: _harami(c, i) and c.black[i - 1] and c.down[i - 1] and c.white[i] and c.small[i]),
    ("harami-cross-bearish", "bearish", 1, lambda c, i: _harami(c, i) and c.white[i - 1] and c.up[i - 1] and c.doji_body[i]),
    ("harami-cross-bullish", "bullish", 1, lambda c, i: _harami(c, i) and c.black[i - 1] and c.down[i - 1] and c.doji_body[i]),
    ("inverted-hammer", "bullish", 0, lambda c, i: _star_shape(c, i) and c.down[i]),
    (
        "kicking-bearish", "bearish", 1,
        lambda c, i: c.marubozu[i - 1] and c.white[i - 1] and c.marubozu[i] and c.black[i] and c.low[i - 1] > c.high[i],
    ),
    (
        "kicking-bullish", "bullish", 1,
        lambda c, i: c.marubozu[i - 1] and c.black[i - 1] and c.marubozu[i] and c.white[i] and c.high[i - 1] < c.low[i],
    ),
    ("long-lower-shadow", "bullish", 0, lambda c, i: c.dn_shadow[i] > c.range[i] / 100 * LONG_SHADOW_PERCENT),
    ("long-upper-shadow", "bearish", 0, lambda c, i: c.up_shadow[i] > c.range[i] / 100 * LONG_SHADOW_PERCENT),
    ("marubozu-black", "bearish", 0, lambda c, i: c.marubozu[i] and c.black[i]),
    ("marubozu-white", "bullish", 0, lambda c, i: c.marubozu[i] and c.white[i]),
    ("morning-doji-star", "bullish", 2, lambda c, i: _morning(c, i, c.doji_body)),
    ("morning-star", "bullish", 2, lambda c, i: _morning(c, i, c.small)),
    (
        "on-neck", "bearish", 1,
        lambda c, i: c.down[i] and c.black[i - 1] and c.long[i - 1] and c.white[i] and c.open[i] < c.close[i - 1]
        and c.small[i] and c.range[i] != 0 and _near(c.close[i], c.low[i - 1], c.body_avg[i]),
    ),
    (
        "piercing", "bullish", 1,
        lambda c, i: c.down[i - 1] and c.black[i - 1] and c.long[i - 1] and c.white[i] and c.open[i] <= c.low[i - 1]
        and c.close[i] > c.body_middle[i - 1] and c.close[i] < c.open[i - 1],
    ),
    (
        "rising-three-methods", "bullish", 4,
        lambda c, i: c.up[i - 4] and c.long[i - 4] and c.white[i - 4] and _three_inside(c, i, False)
        and c.long[i] and c.white[i] and c.close[i] > c.close[i - 4],
    ),
    ("rising-window", "bullish", 1, lambda c, i: c.up[i - 1] and c.range[i] != 0 and c.range[i - 1] != 0 and c.low[i] > c.high[i - 1]),
    ("shooting-star", "bearish", 0, lambda c, i: _star_shape(c, i) and c.up[i]),
    ("spinning-top-black", "neutral", 0, lambda c, i: _spinning_top(c, i) and c.black[i]),
    ("spinning-top-white", "neutral", 0, lambda c, i: _spinning_top(c, i) and c.white[i]),
    ("three-black-crows", "bearish", 2, _three_black_crows),
    ("three-white-soldiers", "bullish", 2, _three_white_soldiers),
    (
        "tri-star-bearish", "bearish", 2,
        lambda c, i: c.doji[i - 2] and c.doji[i - 1] and c.doji[i] and c.up[i - 2]
        and c.body_hi[i - 2] < c.body_lo[i - 1] and c.body_lo[i - 1] > c.body_hi[i],
    ),
    (
        "tri-star-bullish", "bullish", 2,
        lambda c, i: c.doji[i - 2] and c.doji[i - 1] and c.doji[i] and c.down[i - 2]
        and c.body_lo[i - 2] > c.body_hi[i - 1] and c.body_hi[i - 1] < c.body_lo[i],
    ),
    ("tweezer-bottom", "bullish", 1, lambda c, i: _tweezer(c, i, True)),
    ("tweezer-top", "bearish", 1, lambda c, i: _tweezer(c, i, False)),
    (
        "upside-tasuki-gap", "bullish", 2,
        lambda c, i: c.long[i - 2] and c.small[i - 1] and c.up[i] and c.white[i - 2] and c.body_lo[i - 1] > c.body_hi[i - 2]
        and c.white[i - 1] and c.black[i] and c.body_lo[i] >= c.body_hi[i - 2] and c.body_lo[i] <= c.body_lo[i - 1],
    ),
)
PATTERN_KEYS = tuple(key for key, _, _, _ in PATTERNS)
SELECTIONS = ("all", "bullish", "bearish", "neutral", *PATTERN_KEYS)


def _selected(selection: str) -> list[tuple[str, str, int, Detector]]:
    if selection in ("bullish", "bearish", "neutral"):
        return [pattern for pattern in PATTERNS if pattern[1] == selection]
    one = [pattern for pattern in PATTERNS if pattern[0] == selection]
    return one or list(PATTERNS)


# Alerts read the default trend (SMA50): a pattern can first appear at bar 49 + its lookback of up to 4 bars.
SIGNAL_WARMUP = 54


@builtin("tv-all-candlestick-patterns", "All Candlestick Patterns", "recursive", 1, signal_warmup=SIGNAL_WARMUP)
def _all_candlestick_patterns(chart: Chart) -> Outputs:
    selection = chart.select_param("patterns", "all", SELECTIONS)
    trend = chart.select_param("trend", "sma50", ("sma50", "sma50-sma200", "none"))
    c = _candles(chart.open, chart.high, chart.low, chart.close, trend)
    outputs: Outputs = []
    for key, direction, lookback, detect in _selected(selection):
        values = full(len(chart.bars))
        for i in range(lookback, len(chart.bars)):
            if detect(c, i):
                values[i] = chart.low[i] if direction == "bullish" else chart.high[i]
        outputs.append(chart.out(key, values))
    return outputs
