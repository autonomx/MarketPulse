"""The Omnix Scripts interpreter (TVP-11.0): Pine syntax, series semantics, limits and incremental runs."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.apps.trading.indicators.registry import BarSeries
from app.apps.trading.scripts import (
    ScriptLimitError,
    ScriptLimits,
    ScriptRun,
    ScriptSyntaxError,
    ScriptUnsupportedError,
    compile_script,
    run_script,
)
from app.apps.trading.scripts.errors import ScriptRuntimeError
from app.apps.trading.scripts.runtime import SecurityBars, resolve_security_key

ROOT = Path(__file__).resolve().parents[3]
IDIOMS = ROOT / "resources" / "trading" / "script_corpus" / "idioms"
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _bars(closes: list[float], hours: int = 1) -> BarSeries:
    return BarSeries(
        start_times=tuple(T0 + timedelta(hours=hours * index) for index in range(len(closes))),
        open=tuple(closes),
        high=tuple(value + 1 for value in closes),
        low=tuple(value - 1 for value in closes),
        close=tuple(closes),
        volume=tuple(1000.0 + index for index in range(len(closes))),
    )


WAVE = [100 + 10 * math.sin(index / 5) + index * 0.1 for index in range(200)]


def _script(body: str, version: int = 6) -> str:
    return f'//@version={version}\nindicator("Test")\n{body}\n'


def _plot(source: str, title: str, bars: BarSeries | None = None, **kwargs: Any) -> list[Any]:
    result = run_script(source, bars or _bars(WAVE), **kwargs)
    (plot,) = [plot for plot in result.plots if plot.title == title]
    return plot.values


def test_errors_name_their_line() -> None:
    with pytest.raises(ScriptSyntaxError) as unknown:
        compile_script(_script("x = foo(close)"))
    assert unknown.value.line == 3
    assert "foo" in unknown.value.message
    with pytest.raises(ScriptSyntaxError) as indented:
        compile_script(_script("if close > open\n  x = 1"))
    assert indented.value.line == 4
    with pytest.raises(ScriptSyntaxError):
        compile_script(_script('plot(close, "unterminated)'))
    with pytest.raises(ScriptSyntaxError, match="declared"):
        compile_script(_script("x := 1"))
    with pytest.raises(ScriptSyntaxError, match="indicator"):
        compile_script("//@version=6\nplot(close)\n")
    for source, feature in (
        # request.security() runs (TVP-11.1), but its context must be known before the run.
        ('tf = close > open ? "D" : "W"\nx = request.security(syminfo.tickerid, tf, close)', "request.security"),
        ("import user/lib/1", "libraries"),
        ("type Point\n    float x", "user-defined types"),
    ):
        with pytest.raises(ScriptUnsupportedError, match=feature):
            compile_script(_script(source))
    with pytest.raises(ScriptUnsupportedError, match="v4"):
        compile_script('//@version=4\nstudy("Old")\n')


def test_series_history_var_and_na() -> None:
    source = _script(
        "\n".join([
            "var float total = 0.0",
            "total := total + 1",
            "plot(total, \"total\")",
            "plot(close[1], \"previous close\")",
            "plot(total[2], \"total two bars ago\")",
            "x = bar_index > 2 ? close : na",
            "plot(nz(x, -1), \"nz\")",
            "plot(x + 1, \"na arithmetic\")",
            "plot(na(x) ? 1 : 0, \"is na\")",
        ])
    )
    assert _plot(source, "total")[:3] == [1.0, 2.0, 3.0]
    assert _plot(source, "previous close")[:2] == [None, WAVE[0]]
    assert _plot(source, "total two bars ago")[:4] == [None, None, 1.0, 2.0]
    assert _plot(source, "nz")[:4] == [-1, -1, -1, WAVE[3]]
    assert _plot(source, "na arithmetic")[2] is None
    assert _plot(source, "is na")[:4] == [1, 1, 1, 0]


def test_each_function_call_keeps_its_own_series() -> None:
    source = _script(
        "\n".join([
            "smooth(x, n) => ta.ema(x, n)",
            "plot(smooth(close, 3), \"fast\")",
            "plot(smooth(close, 10), \"slow\")",
            "plot(ta.ema(close, 3), \"direct\")",
        ])
    )
    assert _plot(source, "fast") == _plot(source, "direct")
    assert _plot(source, "slow")[8] is None and _plot(source, "slow")[9] is not None


def test_a_ta_call_advances_only_on_the_bars_it_runs() -> None:
    # Pine semantics: inside an if, ta.sma sees only the bars where the branch ran.
    source = _script(
        "\n".join([
            "var float last = na",
            "if bar_index % 2 == 0",
            "    last := ta.sma(close, 2)",
            "plot(last, \"every other\")",
        ])
    )
    values = _plot(source, "every other")
    assert values[0] is None
    assert values[2] == pytest.approx((WAVE[0] + WAVE[2]) / 2)
    assert values[3] == values[2]


def test_a_ta_call_run_twice_on_a_bar_counts_once() -> None:
    source = _script(
        "\n".join([
            "float s = na",
            "for i = 0 to 2",
            "    s := ta.sma(close, 3)",
            "plot(s, \"in loop\")",
            "plot(ta.sma(close, 3), \"once\")",
        ])
    )
    assert _plot(source, "in loop") == _plot(source, "once")


def test_control_flow_switch_loops_and_arrays() -> None:
    source = _script(
        "\n".join([
            'kind = input.string("b", "Kind", options=["a", "b"])',
            "value = switch kind",
            '    "a" => 1',
            '    "b" => 2',
            "    => 3",
            "plot(value, \"switch\")",
            "label = if close > open",
            "    1",
            "else if close < open",
            "    -1",
            "else",
            "    0",
            "plot(label, \"if expression\")",
            "total = 0",
            "for i = 1 to 10",
            "    if i > 4",
            "        break",
            "    total += i",
            "plot(total, \"for\")",
            "n = 0",
            "while n < 7",
            "    n += 2",
            "plot(n, \"while\")",
            "arr = array.from(3.0, 1.0, 2.0)",
            "arr.push(5.0)",
            "s = 0.0",
            "for [i, item] in arr",
            "    s += item * i",
            "plot(s, \"for in\")",
            "plot(array.max(arr) + arr.size(), \"array methods\")",
            'plot(str.length(str.format("{0}-{1}", 1, "x")), "strings")',
        ])
    )
    result = run_script(source, _bars(WAVE[:3]), inputs={"Kind": "a"})
    values = {plot.title: plot.values for plot in result.plots}
    assert values["switch"] == [1, 1, 1]
    assert values["if expression"] == [0, 0, 0]
    assert values["for"] == [10, 10, 10]
    assert values["while"] == [8, 8, 8]
    assert values["for in"][0] == 0 * 3.0 + 1 * 1.0 + 2 * 2.0 + 3 * 5.0
    assert values["array methods"][0] == 9.0
    assert values["strings"][0] == 3
    assert [item.title for item in result.inputs] == ["Kind"]
    assert result.inputs[0].options["options"] == ["a", "b"]


def test_integer_division_follows_the_script_version() -> None:
    assert _plot(_script('plot(7 / 2, "half")', version=5), "half")[0] == 3
    assert _plot(_script('plot(7 / 2, "half")', version=6), "half")[0] == 3.5
    assert _plot(_script('plot(-7 / 2, "half")', version=5), "half")[0] == -3
    assert _plot(_script('plot(6 / 2, "half")', version=6), "half")[0] == 3


def test_invalid_lengths_are_errors() -> None:
    with pytest.raises(ScriptRuntimeError, match="whole number"):
        run_script(_script("plot(ta.sma(close, 2.5))"), _bars(WAVE))
    assert _plot(_script('plot(ta.sma(close, na), "na length")'), "na length") == [None] * len(WAVE)


def test_inputs_change_the_run() -> None:
    source = _script('n = input.int(3, "Length")\nsrc = input.source(close, "Source")\nplot(ta.sma(src, n), "sma")')
    default = _plot(source, "sma")
    longer = _plot(source, "sma", inputs={"Length": 5})
    assert default[2] is not None and longer[2] is None and longer[4] is not None
    from_high = _plot(source, "sma", inputs={"Source": "high"})
    assert from_high[2] == pytest.approx(default[2] + 1)


def test_limits() -> None:
    with pytest.raises(ScriptLimitError, match="loop"):
        run_script(_script("n = 0\nwhile true\n    n += 1\nplot(n)"), _bars(WAVE[:2]))
    with pytest.raises(ScriptLimitError, match="bars"):
        run_script(_script("plot(close)"), _bars(WAVE), limits=ScriptLimits(max_bars=50))
    # Drawings past the script's maximum drop the oldest, like Pine.
    result = run_script(
        '//@version=6\nindicator("Labels", max_labels_count=5)\nlabel.new(bar_index, close, str.tostring(bar_index))\n',
        _bars(WAVE[:20]),
    )
    assert [drawing.fields["text"] for drawing in result.drawings] == ["15", "16", "17", "18", "19"]


def test_new_bars_extend_a_run_like_a_full_run() -> None:
    source = _script(
        "\n".join([
            "var float peak = na",
            "peak := na(peak) ? high : math.max(peak, high)",
            "[m, s, h] = ta.macd(close, 5, 10, 3)",
            "plot(peak, \"peak\")",
            "plot(h, \"hist\")",
            "plot(ta.rsi(close, 7)[1], \"rsi prev\")",
        ])
    )
    bars = _bars(WAVE)
    program = compile_script(source)
    full = run_script(program, bars)
    head = BarSeries(*(field[:-5] for field in (bars.start_times, bars.open, bars.high, bars.low, bars.close, bars.volume)))
    run = ScriptRun(program, head, {}, ScriptLimits())
    run.run_all()
    for index in range(len(WAVE) - 5, len(WAVE)):
        run.append_bar(
            SimpleNamespace(
                start_time=bars.start_times[index], open=bars.open[index], high=bars.high[index], low=bars.low[index],
                close=bars.close[index], volume=bars.volume[index],
            )
        )
    incremental = run.result()
    assert [plot.values for plot in incremental.plots] == [plot.values for plot in full.plots]


def test_session_vwap_resets_each_day() -> None:
    bars = _bars(WAVE[:60])
    values = _plot(_script('plot(ta.vwap(hlc3), "vwap")'), "vwap", bars)
    # A new UTC day starts at bar 24: the VWAP is that bar's typical price again.
    assert values[24] == pytest.approx((bars.high[24] + bars.low[24] + bars.close[24]) / 3)


def _random_walk() -> BarSeries:
    raw = json.loads((ROOT / "resources" / "trading" / "indicator_goldens" / "datasets" / "random-walk-300.json").read_text(encoding="utf-8"))["bars"]
    return BarSeries(
        tuple(datetime.fromisoformat(bar["start_time"].replace("Z", "+00:00")) for bar in raw),
        *(tuple(float(bar[key]) for bar in raw) for key in ("open", "high", "low", "close", "volume")),
    )


OUTSIDE_THE_SUBSET = {
    "order_blocks_udt": "user-defined types",
}


def _contexts(source: str, bars: BarSeries) -> dict[str, SecurityBars]:
    """request.security() contexts on the same bars, so idioms that request one have values to plot."""
    program = compile_script(source)
    keys = {resolve_security_key(template, program, {}) for template in program.securities}
    return {key: SecurityBars(bars, "", "60") for key in keys}


@pytest.mark.parametrize("path", sorted(IDIOMS.glob("*.pine")), ids=lambda path: path.stem)
def test_community_idioms_run(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if path.stem in OUTSIDE_THE_SUBSET:
        with pytest.raises(ScriptUnsupportedError, match=OUTSIDE_THE_SUBSET[path.stem]):
            run_script(source, _random_walk(), timeframe="60")
        return
    result = run_script(source, _random_walk(), timeframe="60", securities=_contexts(source, _random_walk()))
    produced = sum(1 for plot in result.plots for value in plot.values if value is not None) + len(result.drawings)
    # A strategy (TVP-11.5) produces trades.
    produced += len((result.strategy or {}).get("trades", [])) + len((result.strategy or {}).get("open_trades", []))
    assert produced > 0


def test_idioms_agree_with_the_built_ins() -> None:
    bars = _random_walk()

    def plots(name: str) -> dict[str, list[Any]]:
        result = run_script((IDIOMS / f"{name}.pine").read_text(encoding="utf-8"), bars, timeframe="60")
        return {plot.title: plot.values for plot in result.plots}

    vwap = plots("vwap_bands")
    for manual, built_in in zip(vwap["VWAP"], vwap["Built-in VWAP"], strict=True):
        assert manual == pytest.approx(built_in, rel=1e-12)
    hull = plots("hull_ma")
    assert [value for value in hull["MHULL"] if value is not None] == pytest.approx([value for value in hull["Built-in HMA"] if value is not None], rel=1e-12)
