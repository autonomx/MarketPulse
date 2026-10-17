"""Omnix Scripts: limits on untrusted scripts, errors, and Pine semantics fixed in the TVP-11.0 review."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.apps.trading.indicators.registry import BarSeries
from app.apps.trading.scripts import ScriptError, ScriptLimitError, ScriptLimits, ScriptRun, compile_script, run_script
from app.apps.trading.scripts.errors import ScriptRuntimeError, ScriptSyntaxError

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _bars(count: int) -> BarSeries:
    closes = [100 + 10 * math.sin(index / 5) + index * 0.1 for index in range(count)]
    return BarSeries(
        tuple(T0 + timedelta(hours=index) for index in range(count)),
        tuple(closes),
        tuple(value + 1 for value in closes),
        tuple(value - 1 for value in closes),
        tuple(closes),
        tuple(1000.0 + index for index in range(count)),
    )


def _random_walk() -> BarSeries:
    raw = json.loads((ROOT / "resources" / "trading" / "indicator_goldens" / "datasets" / "random-walk-300.json").read_text(encoding="utf-8"))["bars"]
    return BarSeries(
        tuple(datetime.fromisoformat(bar["start_time"].replace("Z", "+00:00")) for bar in raw),
        *(tuple(float(bar[key]) for bar in raw) for key in ("open", "high", "low", "close", "volume")),
    )


def _script(body: str, version: int = 6) -> str:
    return f'//@version={version}\nindicator("Test")\n{body}\n'


def _plots(source: str, bars: BarSeries | None = None, **kwargs: Any) -> dict[str, list[Any]]:
    result = run_script(source, bars or _bars(40), **kwargs)
    return {plot.title: plot.values for plot in result.plots}


# Limits


def test_wall_time_is_enforced_inside_a_bar() -> None:
    import time

    # Thousands of sorts in one bar: the check inside the loop stops it long before the bar ends.
    source = _script("var a = array.new_float(20000, 1.0)\nfor i = 0 to 5000\n    a.sort()\nplot(close)")
    began = time.perf_counter()
    with pytest.raises(ScriptLimitError, match="ran for more than"):
        run_script(source, _bars(1), limits=ScriptLimits(max_seconds=0.1))
    assert time.perf_counter() - began < 2.0


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("var x = 3\nx := x * x\nplot(close)", "integer overflow"),
        ('s = "x" * 5\nplot(close)', "can't apply"),
        ('s = str.repeat("x", 50000000)\nplot(close)', "string"),
        ('var s = "xx"\ns := s + s\nplot(close)', "string"),
        ("var a = array.from(1.0, 2.0)\na := a.concat(a)\nplot(close)", "array"),
    ],
)
def test_values_cannot_grow_without_bound(body: str, error: str) -> None:
    with pytest.raises(ScriptError, match=error):
        run_script(_script(body), _bars(40))


def test_collections_and_alerts_are_capped() -> None:
    limits = ScriptLimits(max_collection_size=10, max_alerts=5)
    with pytest.raises(ScriptLimitError, match="array"):
        run_script(_script("var a = array.new_float()\na.unshift(1.0)\nplot(close)"), _bars(20), limits=limits)
    with pytest.raises(ScriptLimitError, match="array"):
        run_script(_script("var m = map.new<int, float>()\nm.put(bar_index, close)\nplot(close)"), _bars(20), limits=limits)
    result = run_script(_script('alert("bar " + str.tostring(bar_index))\nplot(close)'), _bars(20), limits=limits)
    assert [alert["message"] for alert in result.alerts] == [f"bar {index}" for index in range(15, 20)]
    budget = ScriptLimits(max_allocated_items=1_000)
    with pytest.raises(ScriptLimitError, match="created more than"):
        run_script(_script("a = array.new_float(100, 1.0)\nplot(close)"), _bars(20), limits=budget)


def test_joins_stop_at_the_string_limit() -> None:
    limits = ScriptLimits(max_string_length=1_000)
    for body in (
        'a = array.new_string(1000, "xxxxxxxxxx")\ns = a.join(",")',
        'a = array.new_string(1000, "xxxxxxxxxx")\ns = str.tostring(a)',
        's = str.format(str.repeat("{0}", 500), "xxxxxxxxxx")',
    ):
        with pytest.raises(ScriptLimitError, match="string"):
            run_script(_script(body + "\nplot(close)"), _bars(1), limits=limits)


def test_format_reads_its_template_once() -> None:
    values = _plots(_script('plot(str.length(str.format("{0,a}", "{0,b}")), "n")'), _bars(2))
    assert values["n"] == [5, 5]
    assert _plots(_script('plot(math.round(1.23456, 1000), "r")'), _bars(1))["r"] == [1.23456]


@pytest.mark.parametrize(
    "body",
    [
        "a = array.from(1)\nb = a + 1",
        'x = -"a"',
        'x = "a" < 1',
        "for x in 5\n    y = x",
        'x = "a" % 2',
        "a = array.new_float()\na.insert(na, 1)",
        "a = array.from(1)\narray.push(id=a, value=1)",
        "a = array.from(1)\na.push()",
        'x = int("abc")',
        "a = array.from(1)\nb = a.slice(na, 1)",
        "x = ta.bb(close, 5, \"a\")",
        "x = " + "(" * 200 + "1" + ")" * 200,
        "x = " + "+".join(["1"] * 5000),
        "x = " + "-" * 1000 + "1",
    ],
)
def test_every_failure_is_a_script_error(body: str) -> None:
    try:
        run_script(_script(body + "\nplot(close)"), _bars(3))
    except ScriptError as error:
        assert error.line >= 0
    except Exception as error:  # noqa: BLE001
        pytest.fail(f"{type(error).__name__} escaped: {error}")


def test_scripts_without_a_version_or_with_a_stray_break_are_refused() -> None:
    with pytest.raises(ScriptError, match="version"):
        compile_script('indicator("No version")\nplot(close)')
    with pytest.raises(ScriptSyntaxError, match="loop"):
        compile_script(_script("break"))
    with pytest.raises(ScriptSyntaxError, match="already declared"):
        compile_script(_script("x = 1\nx = 2"))


def test_inputs_are_validated() -> None:
    source = _script('n = input.int(3, "Length", minval=1, maxval=10)\nk = input.string("a", "Kind", options=["a", "b"])\nplot(n)')
    for inputs in ({"Length": 0}, {"Length": 2.5}, {"Length": "9"}, {"Kind": "c"}, {"Unknown": 1}, {"Kind": ["a"]}):
        with pytest.raises(ScriptRuntimeError):
            run_script(source, _bars(2), inputs=inputs)
    assert _plots(source, _bars(1), inputs={"Length": 7})["plot 1"] == [7]


# Pine semantics


def test_v5_evaluates_and_or_strictly_and_v6_lazily() -> None:
    body = "c = bar_index % 2 == 0 and ta.change(bar_index) == 1\nplot(c ? 1 : 0, \"c\")"
    # v5: ta.change runs on every bar (always 1), so c holds on even bars.
    assert _plots(_script(body, version=5), _bars(6))["c"] == [0, 0, 1, 0, 1, 0]
    # v6: it runs only on even bars, where the change since its last run is 2.
    assert _plots(_script(body, version=6), _bars(6))["c"] == [0, 0, 0, 0, 0, 0]


def test_v5_truncates_only_constant_int_division() -> None:
    assert _plots(_script('plot(bar_index / 2, "half")', version=5), _bars(3))["half"] == [0, 0.5, 1]
    assert _plots(_script('plot(7 / 2, "const")', version=5), _bars(1))["const"] == [3]


def test_v6_for_loops_check_their_bound_each_time() -> None:
    body = "n = 3\ncount = 0\nfor i = 0 to n\n    count += 1\n    n := 1\nplot(count, \"count\")"
    assert _plots(_script(body, version=6), _bars(1))["count"] == [2]
    assert _plots(_script(body, version=5), _bars(1))["count"] == [4]


def test_function_history_advances_per_call() -> None:
    # x[1] in a function is its previous call's value (Pine), not the previous bar's.
    body = 'f(a) => a[1]\nplot(bar_index % 2 == 0 ? f(close) : na, "f")'
    bars = _bars(6)
    values = _plots(_script(body), bars)["f"]
    assert values[0] is None
    assert values[2] == bars.close[0]
    assert values[4] == bars.close[2]


def test_switch_on_na_takes_the_default() -> None:
    body = "x = bar_index > 0 ? 1 : na\ny = switch x\n    na => 5\n    1 => 1\n    => 9\nplot(y, \"y\")"
    assert _plots(_script(body), _bars(2))["y"] == [9, 1]


def test_named_arguments_of_optional_source_functions() -> None:
    values = _plots(_script('plot(ta.highest(close, length=3), "a")\nplot(ta.highest(close, 3), "b")\nplot(ta.pivothigh(high, leftbars=2, rightbars=2), "p")'), _bars(20))
    assert values["a"] == values["b"]


def test_scripts_reading_the_last_bar_run_again_in_full() -> None:
    program = compile_script(_script('if barstate.islast\n    label.new(bar_index, close, "last")\nplot(close)'))
    run = ScriptRun(program, _bars(5), {}, ScriptLimits())
    run.run_all()
    assert not run.can_extend
    with pytest.raises(RuntimeError):
        run.append_bar(object())
    plain = ScriptRun(compile_script(_script("plot(close)")), _bars(5), {}, ScriptLimits())
    assert plain.can_extend


def test_time_close_is_the_bar_start_plus_the_interval() -> None:
    values = _plots(_script('plot(time_close - time, "span")'), _bars(3), timeframe="60")
    assert values["span"] == [3_600_000] * 3
    # Without a fixed interval it is na, never the next bar's start.
    assert _plots(_script('plot(time_close, "close")'), _bars(3))["close"] == [None] * 3


def test_results_do_not_share_the_program_state() -> None:
    program = compile_script(_script('n = input.int(3, "Length")\nplot(n)'))
    result = run_script(program, _bars(1))
    result.declaration["title"] = "changed"
    result.inputs[0].default = 99
    again = run_script(program, _bars(1))
    assert again.declaration["title"] == "Test" and again.inputs[0].default == 3


# ta.* functions against Pine's documented reference implementations, run through the interpreter.

REFERENCES = {
    "sar": (
        "ta.sar(0.02, 0.02, 0.2)",
        """pine_sar(start, inc, max) =>
    var float result = na
    var float maxMin = na
    var float acceleration = na
    var bool isBelow = false
    bool isFirstTrendBar = false
    if bar_index == 1
        if close > close[1]
            isBelow := true
            maxMin := high
            result := low[1]
        else
            isBelow := false
            maxMin := low
            result := high[1]
        isFirstTrendBar := true
        acceleration := start
    result := result + acceleration * (maxMin - result)
    if isBelow
        if result > low
            isFirstTrendBar := true
            isBelow := false
            result := math.max(high, maxMin)
            maxMin := low
            acceleration := start
    else
        if result < high
            isFirstTrendBar := true
            isBelow := true
            result := math.min(low, maxMin)
            maxMin := high
            acceleration := start
    if not isFirstTrendBar
        if isBelow
            if high > maxMin
                maxMin := high
                acceleration := math.min(acceleration + inc, max)
        else
            if low < maxMin
                maxMin := low
                acceleration := math.min(acceleration + inc, max)
    if isBelow
        result := math.min(result, low[1])
        if bar_index > 1
            result := math.min(result, low[2])
    else
        result := math.max(result, high[1])
        if bar_index > 1
            result := math.max(result, high[2])
    result
ref = pine_sar(0.02, 0.02, 0.2)""",
    ),
    "supertrend": (
        "ta.supertrend(3, 10)",
        """pine_supertrend(factor, atrPeriod) =>
    src = hl2
    atr = ta.atr(atrPeriod)
    upperBand = src + factor * atr
    lowerBand = src - factor * atr
    prevLowerBand = nz(lowerBand[1])
    prevUpperBand = nz(upperBand[1])
    lowerBand := lowerBand > prevLowerBand or close[1] < prevLowerBand ? lowerBand : prevLowerBand
    upperBand := upperBand < prevUpperBand or close[1] > prevUpperBand ? upperBand : prevUpperBand
    int _direction = na
    float superTrend = na
    prevSuperTrend = superTrend[1]
    if na(atr[1])
        _direction := 1
    else if prevSuperTrend == prevUpperBand
        _direction := close > upperBand ? -1 : 1
    else
        _direction := close < lowerBand ? 1 : -1
    superTrend := _direction == -1 ? lowerBand : upperBand
    [superTrend, _direction]
[ref, refDirection] = pine_supertrend(3, 10)""",
    ),
    "dmi": (
        "ta.dmi(14, 14)",
        """up = ta.change(high)
down = -ta.change(low)
plusDM = na(up) ? na : (up > down and up > 0 ? up : 0)
minusDM = na(down) ? na : (down > up and down > 0 ? down : 0)
trur = ta.rma(ta.tr, 14)
plus = fixnan(100 * ta.rma(plusDM, 14) / trur)
minus = fixnan(100 * ta.rma(minusDM, 14) / trur)
sum = plus + minus
ref = 100 * ta.rma(math.abs(plus - minus) / (sum == 0 ? 1 : sum), 14)""",
    ),
    "wma": (
        "ta.wma(close, 9)",
        """pine_wma(x, y) =>
    norm = 0.0
    sum = 0.0
    for i = 0 to y - 1
        weight = (y - i) * y
        norm := norm + weight
        sum := sum + x[i] * weight
    sum / norm
ref = pine_wma(close, 9)""",
    ),
    "cci": ("ta.cci(close, 20)", "ma = ta.sma(close, 20)\nref = (close - ma) / (0.015 * ta.dev(close, 20))"),
    "dev": (
        "ta.dev(close, 10)",
        """pine_dev(source, length) =>
    mean = ta.sma(source, length)
    sum = 0.0
    for i = 0 to length - 1
        val = source[i]
        sum := sum + math.abs(val - mean)
    sum / length
ref = pine_dev(close, 10)""",
    ),
    "vwma": ("ta.vwma(close, 10)", "ref = ta.sma(close * volume, 10) / ta.sma(volume, 10)"),
    "mfi": (
        "ta.mfi(hlc3, 14)",
        "upper = math.sum(volume * (ta.change(hlc3) <= 0 ? 0 : hlc3), 14)\nlower = math.sum(volume * (ta.change(hlc3) >= 0 ? 0 : hlc3), 14)\nref = 100.0 - (100.0 / (1.0 + upper / lower))",
    ),
    "obv": ("ta.obv", "ref = ta.cum(math.sign(ta.change(close)) * volume)"),
    "stoch": ("ta.stoch(close, high, low, 14)", "ref = 100 * (close - ta.lowest(low, 14)) / (ta.highest(high, 14) - ta.lowest(low, 14))"),
}


@pytest.mark.parametrize("name", sorted(REFERENCES))
def test_ta_matches_pine_reference(name: str) -> None:
    built_in, reference = REFERENCES[name]
    picks = {"supertrend": "[st, dir] = ", "dmi": "[dp, dm, adx] = "}
    target = {"supertrend": "st", "dmi": "adx"}.get(name, "x")
    body = f"{reference}\n{picks.get(name, 'x = ')}{built_in}\nplot({target}, \"built-in\")\nplot(ref, \"reference\")"
    values = _plots(_script(body), _random_walk())
    compared = 0
    for index, (actual, expected) in enumerate(zip(values["built-in"], values["reference"], strict=True)):
        if expected is None or (isinstance(expected, float) and math.isnan(expected)):
            continue
        # The supertrend reference reads nz() bands (0) while its ATR warms up; the built-in is na there.
        if name == "supertrend" and index < 10:
            continue
        assert actual is not None, f"{name} bar {index}: built-in na, reference {expected}"
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-9), f"{name} bar {index}"
        compared += 1
    assert compared > 200
