"""Indicator on indicator (TVP-6.5): an indicator computed on another indicator's output.

The browser does the same in ``web/src/features/trading/indicators/indicatorSources.ts``: the source output becomes
bars whose open, high, low and close are its value (volume and times kept), on the bars where it has a finite value,
and the target indicator runs on those bars. Only indicators that read the close alone take a source, so the result
is the indicator of the source series. The shared fixture ``resources/trading/indicator_goldens/sources.json`` proves
the two agree.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .registry import BarSeries, IndicatorInputs, IndicatorOutputSeries, compute_indicator

# The ids whose indicator reads only the close; ``SOURCE_TARGET_IDS`` in indicatorSources.ts lists the same.
SOURCE_TARGET_IDS: frozenset[str] = frozenset({
    "sma", "ema", "rsi", "macd", "bollinger", "stochastic-rsi",
    "tv-arnaud-legoux-moving-average", "tv-chande-momentum-oscillator-cmo", "tv-connors-rsi-crsi", "tv-coppock-curve",
    "tv-detrended-price-oscillator-dpo", "tv-double-exponential-moving-average-ema", "tv-historical-volatility",
    "tv-hull-moving-average", "tv-know-sure-thing-kst", "tv-mcginley-dynamic", "tv-momentum", "tv-moving-average-ribbon",
    "tv-moving-averages", "tv-price-momentum-oscillator-pmo", "tv-rci-ribbon", "tv-rate-of-change-roc",
    "tv-relative-volatility-index", "tv-smi-ergodic-oscillator", "tv-smoothed-moving-average", "tv-trix",
    "tv-trend-strength-index", "tv-triple-ema", "tv-true-strength-index", "tv-weighted-moving-average",
})


def accepts_source(indicator_id: str) -> bool:
    return indicator_id in SOURCE_TARGET_IDS


def source_series(bars: BarSeries, output: IndicatorOutputSeries) -> tuple[BarSeries, list[int]]:
    """The bars an output's values make (OHLC all the value), and each one's index in ``bars``."""
    values = {index: value for index, value in output.points if math.isfinite(value)}
    indices = [index for index in range(len(bars)) if index in values]
    prices = tuple(values[index] for index in indices)
    sessions = None if bars.sessions is None else tuple(bars.sessions[index] for index in indices)
    return (
        BarSeries(
            start_times=tuple(bars.start_times[index] for index in indices),
            open=prices,
            high=prices,
            low=prices,
            close=prices,
            volume=tuple(bars.volume[index] for index in indices),
            sessions=sessions,
        ),
        indices,
    )


def compute_on_source(
    indicator_id: str, bars: BarSeries, inputs: IndicatorInputs, source: IndicatorOutputSeries
) -> list[IndicatorOutputSeries]:
    """``indicator_id`` on the source output's series, with points back at the chart bars' indices."""
    if not accepts_source(indicator_id):
        raise ValueError(f"indicator {indicator_id!r} does not take another indicator as its source")
    series, indices = source_series(bars, source)
    outputs = compute_indicator(indicator_id, series, inputs)
    return [
        IndicatorOutputSeries(key=output.key, points=tuple((indices[index], value) for index, value in output.points))
        for output in outputs
    ]


def find_output(outputs: Sequence[IndicatorOutputSeries], key: str) -> IndicatorOutputSeries | None:
    """The source output a key names: that key, else the line with the same name (keys carry the inputs, so
    ``macd:12:26:histogram`` is ``macd:10:26:histogram`` after a change), else the first line, like the browser's
    ``resolveSourceOutput``."""
    exact = next((output for output in outputs if output.key == key), None)
    if exact is not None:
        return exact
    name = key.split(":")[-1]
    return next((output for output in outputs if output.key.split(":")[-1] == name), outputs[0] if outputs else None)
