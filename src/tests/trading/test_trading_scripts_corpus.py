"""Omnix Scripts against the chart's indicators (TVP-11.0).

Every indicator's "view as Pine" template (``indicatorPine.ts``, exported to ``pineTemplateCorpus.json`` by
``indicatorPineCorpus.test.ts``) runs through the interpreter on the indicator golden datasets, and each plot is
compared with the server's native indicator (``indicators/registry.py``), or, where there is none, with a
brute-force reference computed here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.apps.trading.indicators.registry import BarSeries, IndicatorInputs, compute_indicator
from app.apps.trading.indicators.server_indicators.core import exponential_moving_average, simple_moving_average
from app.apps.trading.scripts import ScriptError, compile_script, run_script
from app.apps.trading.scripts.runtime import ScriptResult, SecurityBars

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "web" / "src" / "features" / "trading" / "indicators" / "fixtures" / "pineTemplateCorpus.json"
GOLDENS = ROOT / "resources" / "trading" / "indicator_goldens"

# The interpreter follows the registry's order of operations; the remaining differences are sums taken in a
# different order (a running SMA against a fresh window sum), a few ulps.
RELATIVE_TOLERANCE = 1e-11


@cache
def _corpus() -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in json.loads(CORPUS.read_text(encoding="utf-8"))["entries"]}


@cache
def _dataset(name: str) -> BarSeries:
    bars = json.loads((GOLDENS / "datasets" / f"{name}.json").read_text(encoding="utf-8"))["bars"]
    return BarSeries(
        start_times=tuple(datetime.fromisoformat(bar["start_time"].replace("Z", "+00:00")) for bar in bars),
        open=tuple(float(bar["open"]) for bar in bars),
        high=tuple(float(bar["high"]) for bar in bars),
        low=tuple(float(bar["low"]) for bar in bars),
        close=tuple(float(bar["close"]) for bar in bars),
        volume=tuple(float(bar["volume"]) for bar in bars),
    )


def _datasets() -> list[str]:
    return list(json.loads((GOLDENS / "index.json").read_text(encoding="utf-8"))["datasets"])


def _inputs(entry: dict[str, Any]) -> IndicatorInputs:
    raw = entry["inputs"]
    return IndicatorInputs(
        period=raw["period"],
        fast_period=raw["fastPeriod"],
        slow_period=raw["slowPeriod"],
        signal_period=raw["signalPeriod"],
        standard_deviations=raw["standardDeviations"],
    )


def _plot(result: ScriptResult, title: str) -> list[Any]:
    matches = [plot for plot in result.plots if plot.title == title]
    assert len(matches) == 1, f"one plot titled {title!r}: {[plot.title for plot in result.plots]}"
    values = list(matches[0].values)
    return values + [None] * (result.bars - len(values))


def _same(script: Any, expected: float | None) -> bool:
    if isinstance(expected, bool):
        return script is expected
    if expected is None or (isinstance(expected, float) and math.isnan(expected)):
        return script is None or (isinstance(script, float) and math.isnan(script))
    if script is None or isinstance(script, bool):
        return False
    return abs(script - expected) <= RELATIVE_TOLERANCE * max(1.0, abs(expected))


def _expected_from_native(indicator_id: str, key: str, bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
    outputs = {output.key: output for output in compute_indicator(indicator_id, bars, inputs)}
    assert key in outputs, f"{indicator_id} has no output {key!r}: {sorted(outputs)}"
    expected: list[float | None] = [None] * len(bars)
    for index, value in outputs[key].points:
        expected[index] = value
    return expected


def _assert_matches(
    script: Sequence[Any], expected: Sequence[float | None], what: str, from_start: bool, flat: Sequence[bool] = ()
) -> None:
    for index, value in enumerate(expected):
        if value is None and not from_start:
            # The native line is trimmed where the script already has values (MACD's line before its signal).
            continue
        if flat and flat[index] and script[index] is None:
            # Pine's ta.stoch is na on a flat window (a division by zero); the chart's Stochastic RSI shows 50.
            continue
        assert _same(script[index], value), f"{what} at bar {index}: script {script[index]!r}, expected {value!r}"


def _flat_stochastic(bars: BarSeries, inputs: IndicatorInputs) -> list[bool]:
    """Bars whose Stochastic RSI reads a flat RSI window: the window itself, or within the K and D smoothing after it."""
    keys = {output.key: dict(output.points) for output in compute_indicator("stochastic-rsi", bars, inputs)}
    rsi = dict(next(iter(compute_indicator("rsi", bars, inputs))).points)
    period = int(inputs.period)
    reach = int(inputs.fast_period or 3) + int(inputs.signal_period or 3)
    flat = [False] * len(bars)
    for index in range(len(bars)):
        window = [rsi.get(position) for position in range(index - period + 1, index + 1)]
        if all(value is not None for value in window) and max(window) == min(window):  # type: ignore[type-var]
            for later in range(index, min(len(bars), index + reach)):
                flat[later] = True
    assert keys  # the indicator ran
    return flat


@dataclass(frozen=True)
class Line:
    title: str
    expected: Callable[[BarSeries, IndicatorInputs], list[float | None]]
    from_start: bool = True


def native(indicator_id: str, key: Callable[[IndicatorInputs], str], **overrides: Any) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    def expected(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
        chosen = replace(inputs, **overrides) if overrides else inputs
        return _expected_from_native(indicator_id, key(chosen), bars, chosen)

    return expected


def _macd_keys(part: str) -> Callable[[IndicatorInputs], str]:
    return lambda inputs: f"macd:{inputs.fast_period or 12}:{inputs.slow_period or 26}:{part}"


def _log_bars(bars: BarSeries) -> BarSeries:
    return replace(bars, close=tuple(math.log(value) for value in bars.close))


def _log_macd(part: str) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    def expected(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
        return _expected_from_native("macd", _macd_keys(part)(inputs), _log_bars(bars), inputs)

    return expected


def _aligned(values: Sequence[float], length: int) -> list[float | None]:
    return [None] * (length - len(values)) + list(values)


def _dema(values: Sequence[float], period: int) -> list[float | None]:
    first = exponential_moving_average(values, period)
    second = exponential_moving_average(first, period)
    first_aligned = _aligned(first, len(values))
    second_aligned = _aligned(second, len(values))
    return [None if a is None or b is None else 2 * a - b for a, b in zip(first_aligned, second_aligned, strict=True)]


def _macd_dema(part: str) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    def expected(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
        fast = _dema(bars.close, int(inputs.fast_period or 12))
        slow = _dema(bars.close, int(inputs.slow_period or 26))
        line = [None if a is None or b is None else a - b for a, b in zip(fast, slow, strict=True)]
        valid = [value for value in line if value is not None]
        signal = _aligned(exponential_moving_average(valid, int(inputs.signal_period or 9)), len(line))
        if part == "line":
            return line
        if part == "signal":
            return signal
        return [None if a is None or b is None else a - b for a, b in zip(line, signal, strict=True)]

    return expected


def _vwap_by_day(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
    # Pine's ta.vwap starts again each session (UTC days for these datasets): the native VWAP anchored at each day.
    expected: list[float | None] = [None] * len(bars)
    start = 0
    while start < len(bars):
        day = bars.start_times[start].date()
        end = start
        while end < len(bars) and bars.start_times[end].date() == day:
            end += 1
        piece = BarSeries(*(field[start:end] for field in (bars.start_times, bars.open, bars.high, bars.low, bars.close, bars.volume)))
        (output,) = compute_indicator("vwap", piece, IndicatorInputs(period=1))
        for index, value in output.points:
            expected[start + index] = value
        start = end
    return expected


def _cross(fast_period: int, slow_period: int, up: bool) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    def expected(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
        fast = _aligned(simple_moving_average(bars.close, fast_period), len(bars))
        slow = _aligned(simple_moving_average(bars.close, slow_period), len(bars))
        result: list[float | None] = []
        for index in range(len(bars)):
            values = (fast[index], slow[index], fast[index - 1] if index else None, slow[index - 1] if index else None)
            if any(value is None for value in values):
                result.append(False)  # type: ignore[arg-type]
                continue
            a, b, previous_a, previous_b = values
            result.append((a > b and previous_a <= previous_b) if up else (a < b and previous_a >= previous_b))  # type: ignore[operator, arg-type]
        return result  # type: ignore[return-value]

    return expected


def _pivots(source: str, left: int, right: int, high: bool) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    # Brute force: the bar `right` bars back is a pivot if it is above (below) every bar `left` before it and at least
    # as high (low) as every bar `right` after it.
    def expected(bars: BarSeries, inputs: IndicatorInputs) -> list[float | None]:
        values = getattr(bars, source)
        result: list[float | None] = []
        for index in range(len(bars)):
            center = index - right
            if center - left < 0:
                result.append(None)
                continue
            value = values[center]
            before = values[center - left : center]
            after = values[center + 1 : index + 1]
            if high:
                found = all(value > item for item in before) and all(value >= item for item in after)
            else:
                found = all(value < item for item in before) and all(value <= item for item in after)
            result.append(value if found else None)
        return result

    return expected


def _found(series: Callable[[BarSeries, IndicatorInputs], list[float | None]]) -> Callable[[BarSeries, IndicatorInputs], list[float | None]]:
    return lambda bars, inputs: [value is not None for value in series(bars, inputs)]  # type: ignore[misc]


def _sma_key(period: int) -> Callable[[IndicatorInputs], str]:
    return lambda inputs: f"sma:{period}"


def _ema_key(period: int) -> Callable[[IndicatorInputs], str]:
    return lambda inputs: f"ema:{period}"


def _bollinger(part: str) -> Callable[[IndicatorInputs], str]:
    return lambda inputs: f"bollinger:{inputs.period}:{part}"


EXPECTATIONS: dict[str, list[Line]] = {
    "sma": [Line("SMA", native("sma", lambda i: f"sma:{i.period}"))],
    "ema": [Line("EMA", native("ema", lambda i: f"ema:{i.period}"))],
    "rsi": [Line("RSI", native("rsi", lambda i: f"rsi:{i.period}"))],
    "atr": [Line("ATR", native("atr", lambda i: f"atr:{i.period}"))],
    "macd": [
        Line("MACD", native("macd", _macd_keys("line")), from_start=False),
        Line("Signal", native("macd", _macd_keys("signal"))),
        Line("Histogram", native("macd", _macd_keys("histogram"))),
    ],
    "bollinger": [
        Line("Basis", native("bollinger", _bollinger("middle"))),
        Line("Upper", native("bollinger", _bollinger("upper"))),
        Line("Lower", native("bollinger", _bollinger("lower"))),
    ],
    "ideal-bb": [
        Line("Middle", native("bollinger", _bollinger("middle"))),
        Line("Upper", native("bollinger", _bollinger("upper"))),
        Line("Lower", native("bollinger", _bollinger("lower"))),
    ],
    "vwap": [Line("VWAP", _vwap_by_day)],
    "golden-cross": [
        Line("50 SMA", native("sma", _sma_key(50), period=50)),
        Line("200 SMA", native("sma", _sma_key(200), period=200)),
        Line("Golden Cross", _cross(50, 200, up=True)),
    ],
    "death-cross": [
        Line("50 SMA", native("sma", _sma_key(50), period=50)),
        Line("200 SMA", native("sma", _sma_key(200), period=200)),
        Line("Death Cross", _cross(50, 200, up=False)),
    ],
    "ema-stack": [Line(f"EMA {period}", native("ema", _ema_key(period), period=period)) for period in (9, 21, 50, 200)],
    "log-macd": [
        Line("MACD", _log_macd("line"), from_start=False),
        Line("Signal", _log_macd("signal")),
        Line("Histogram", _log_macd("histogram")),
    ],
    "macd-dema": [
        Line("MACD", _macd_dema("line")),
        Line("Signal", _macd_dema("signal")),
        Line("Histogram", _macd_dema("histogram")),
    ],
    "stochastic-rsi": [
        Line("K", native("stochastic-rsi", lambda i: "stochastic-rsi:k"), from_start=False),
        Line("D", native("stochastic-rsi", lambda i: "stochastic-rsi:d")),
    ],
    "rsi-divergence": [
        Line("RSI", native("rsi", lambda i: f"rsi:{i.period}")),
        Line("Bullish pivot", _found(_pivots("low", 5, 5, high=False))),
        Line("Bearish pivot", _found(_pivots("high", 5, 5, high=True))),
    ],
    "swing-liquidity": [
        Line("Swing High", _pivots("high", 5, 5, high=True)),
        Line("Swing Low", _pivots("low", 5, 5, high=False)),
    ],
}

# Templates that aren't (yet) runnable, and why.
NOT_RUNNABLE = {
    "volume-profile": ScriptError,  # not valid Pine: volume.profile_fixed doesn't exist
}
# Templates that read another timeframe (request.security, TVP-11.1): they run, given that timeframe's bars.
OTHER_TIMEFRAME = {"bull-market-band": "|W"}


def test_every_template_is_covered() -> None:
    assert set(_corpus()) == set(EXPECTATIONS) | set(NOT_RUNNABLE) | set(OTHER_TIMEFRAME) | {"fair-value-gap"}


@pytest.mark.parametrize(("template", "key"), sorted(OTHER_TIMEFRAME.items()))
def test_templates_on_another_timeframe_request_it(template: str, key: str) -> None:
    source = _corpus()[template]["source"]
    assert compile_script(source).securities == [key]
    bars = _dataset("random-walk-300")
    result = run_script(source, bars, timeframe="1d", securities={key: SecurityBars(bars, "", "1w")})
    assert any(value is not None for plot in result.plots for value in plot.values)


def _cases() -> list[tuple[str, str]]:
    return [(template, dataset) for template in sorted(EXPECTATIONS) for dataset in _datasets()]


@pytest.mark.parametrize(("template", "dataset"), _cases())
def test_template_matches_the_chart_indicator(template: str, dataset: str) -> None:
    entry = _corpus()[template]
    bars = _dataset(dataset)
    if template == "log-macd" and any(value <= 0 for value in bars.close):
        pytest.skip("log of a non-positive price")
    inputs = _inputs(entry)
    result = run_script(entry["source"], bars, timeframe="60")
    flat = _flat_stochastic(bars, inputs) if template == "stochastic-rsi" and len(bars) else []
    for line in EXPECTATIONS[template]:
        expected = line.expected(bars, inputs) if len(bars) else []
        _assert_matches(_plot(result, line.title), expected, f"{template} {line.title} on {dataset}", line.from_start, flat)


@pytest.mark.parametrize("dataset", ["random-walk-300", "gaps-flats-zero-volume-160", "mixed-90"])
def test_fair_value_gaps_are_boxes_on_every_gap(dataset: str) -> None:
    bars = _dataset(dataset)
    result = run_script(_corpus()["fair-value-gap"]["source"], bars)
    expected = []
    for index in range(2, len(bars)):
        if bars.low[index] > bars.high[index - 2]:
            expected.append((index - 2, bars.low[index], index, bars.high[index - 2]))
        if bars.high[index] < bars.low[index - 2]:
            expected.append((index - 2, bars.low[index - 2], index, bars.high[index]))
    boxes = [(box.fields["left"], box.fields["top"], box.fields["right"], box.fields["bottom"]) for box in result.drawings if box.kind == "box"]
    # max_boxes_count=200: the newest 200 stay.
    assert boxes == expected[-200:]


@pytest.mark.parametrize(("template", "error"), sorted(NOT_RUNNABLE.items()))
def test_templates_outside_the_subset_fail_with_a_reason(template: str, error: type[Exception]) -> None:
    with pytest.raises(error) as raised:
        run_script(_corpus()[template]["source"], _dataset("random-walk-300"))
    assert raised.value.line > 0
