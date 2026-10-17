"""request.security() in Omnix Scripts (TVP-11.1): another symbol's or timeframe's values on the chart's bars."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.apps.trading.indicators.registry import BarSeries
from app.apps.trading.scripts import ScriptLimitError, ScriptRun, ScriptUnsupportedError, compile_script, run_script
from app.apps.trading.scripts.runtime import ScriptLimits, SecurityBars
from app.apps.trading.scripts_security import load_script_securities, resolve_script_symbol, resolve_script_timeframe, script_securities_requested

DAY = datetime(2026, 3, 2, tzinfo=timezone.utc)  # a Monday


def series(starts: list[datetime], closes: list[float]) -> BarSeries:
    return BarSeries(
        start_times=tuple(starts), open=tuple(closes), high=tuple(value + 1 for value in closes), low=tuple(value - 1 for value in closes),
        close=tuple(closes), volume=tuple(100.0 for _ in closes),
    )


# Three days of 6-hour bars (12 bars), and the same three days as daily bars with closes 10, 20, 30.
SIX_HOURS = series([DAY + timedelta(hours=6 * index) for index in range(12)], [float(index) for index in range(12)])
DAILY = series([DAY + timedelta(days=index) for index in range(3)], [10.0, 20.0, 30.0])


def script(body: str) -> str:
    return f'//@version=6\nindicator("Security")\n{body}\n'


def plot(source: str, title: str = "v", **contexts: SecurityBars) -> list:
    result = run_script(source, SIX_HOURS, timeframe="6h", securities={key.replace("_", "|"): value for key, value in contexts.items()})
    return next(item for item in result.plots if item.title == title).values


def test_a_daily_value_reaches_the_bars_after_its_day_closes() -> None:
    values = plot(script('plot(request.security(syminfo.tickerid, "D", close), "v")'), _D=SecurityBars(DAILY, "x", "1d"))
    # A bar sees the last day closed by its own close: day 1 from its last 6-hour bar (which closes with the day), and
    # so on; the last bar sees the day in progress (30), as a realtime bar does.
    assert values == [None] * 3 + [10.0] * 4 + [20.0] * 4 + [30.0]


def test_a_lower_timeframe_reads_its_bar_that_closes_with_the_charts() -> None:
    source = script('plot(request.security(syminfo.tickerid, "360", close), "v")')
    result = run_script(source, DAILY, timeframe="1d", securities={"|360": SecurityBars(SIX_HOURS, "x", "6h")})
    # Each day sees its last 6-hour bar (closes 3, 7, 11); the last day, in progress, its latest one.
    assert next(item for item in result.plots if item.title == "v").values == [3.0, 7.0, 11.0]


def test_lookahead_and_gaps_change_which_bars_read_it() -> None:
    contexts = {"_D": SecurityBars(DAILY, "x", "1d")}
    ahead = plot(script('plot(request.security(syminfo.tickerid, "D", close, lookahead=barmerge.lookahead_on), "v")'), **contexts)
    assert ahead == [10.0] * 4 + [20.0] * 4 + [30.0] * 4
    gaps = plot(script('plot(request.security(syminfo.tickerid, "D", close, gaps=barmerge.gaps_on), "v")'), **contexts)
    assert gaps == [None] * 3 + [10.0] + [None] * 3 + [20.0] + [None] * 3 + [30.0]


def test_the_expression_runs_in_the_requested_context() -> None:
    values = plot(script('plot(request.security(syminfo.tickerid, "D", ta.sma(close, 2)), "v")'), _D=SecurityBars(DAILY, "x", "1d"))
    # SMA(2) of the daily closes: na, 15, 25.
    assert values[-1] == 25.0 and values[-2] == 15.0 and values[3] is None
    pair = plot(script('[c, h] = request.security(syminfo.tickerid, "D", [close, high])\nplot(h - c, "v")'), _D=SecurityBars(DAILY, "x", "1d"))
    assert pair[-1] == 1.0


def test_the_charts_own_context_is_the_expression_itself() -> None:
    values = plot(script('plot(request.security(syminfo.tickerid, timeframe.period, close), "v")'))
    assert values == [float(index) for index in range(12)]


def test_a_context_without_bars_reads_na() -> None:
    assert plot(script('plot(request.security("NASDAQ:QQQ", "D", close), "v")')) == [None] * 12


def test_contexts_come_from_literals_or_inputs() -> None:
    program = compile_script(script('tf = input.timeframe("D", "Timeframe")\nx = request.security(syminfo.tickerid, tf, close)\n'
                                    'y = request.security("NASDAQ:QQQ", "W", close)\nz = request.security(input.symbol("SPY", "Benchmark"), "D", close)'))
    assert program.securities == ["|{input:Timeframe}", "NASDAQ:QQQ|W", "{input:Benchmark}|D"]
    run = ScriptRun(program, SIX_HOURS, {"Timeframe": "240"}, ScriptLimits(), "", "6h")
    assert run.security_key("|{input:Timeframe}") == "|240"
    assert run.security_key("{input:Benchmark}|D") == "SPY|D"
    with pytest.raises(ScriptUnsupportedError, match="request.security"):
        compile_script(script('tf = close > open ? "D" : "W"\nx = request.security(syminfo.tickerid, tf, close)'))
    # A context is fixed for the run: an input variable that is reassigned isn't the input's value any more.
    with pytest.raises(ScriptUnsupportedError, match="request.security"):
        compile_script(script('tf = input.timeframe("D", "Timeframe")\ntf := "W"\nx = request.security(syminfo.tickerid, tf, close)'))
    with pytest.raises(ScriptUnsupportedError, match="can't be reassigned"):
        compile_script(script('tf = input.timeframe("D", "Timeframe")\nx = request.security(syminfo.tickerid, tf, close)\ntf := "W"'))
    # A function's own variable of the same name is not the input.
    with pytest.raises(ScriptUnsupportedError, match="request.security"):
        compile_script(script('tf = input.timeframe("D", "Timeframe")\nf(x) =>\n    tf = x > 0 ? "D" : "W"\n'
                              '    request.security(syminfo.tickerid, tf, close)\ny = f(close)'))
    many = "\n".join(f'x{index} = request.security(syminfo.tickerid, "{index + 1}D", close)' for index in range(6))
    with pytest.raises(ScriptLimitError, match="at most 5"):
        compile_script(script(many))


def test_a_script_with_contexts_runs_again_in_full() -> None:
    run = ScriptRun(compile_script(script('plot(request.security(syminfo.tickerid, "D", close), "v")')), SIX_HOURS, {}, ScriptLimits(), "", "6h")
    assert not run.can_extend


def test_fill_and_hline_keep_colours_written_as_expressions() -> None:
    result = run_script(script(
        'a = plot(close, "a")\nb = plot(open, "b")\nfill(a, b, color=color.new(color.teal, 85), title="Band")\n'
        'h = hline(5, "Mid", color=color.new(color.red, 50))\nfill(h, b, color=#FF000033)'
    ), SIX_HOURS, timeframe="6h")
    band, level = result.fills
    assert band["title"] == "Band" and band["color"] is not None and band["color"] != level["color"]
    assert result.hlines[0]["color"] is not None and result.hlines[0]["title"] == "Mid"
    assert band["from"] == 0 and band["to"] == 1 and level["from"] == ("hline", 0)


def test_the_server_names_symbols_and_timeframes_as_omnix_does() -> None:
    assert [resolve_script_timeframe(text, "1h") for text in ("", "60", "240", "15", "D", "1W", "M", "30S", "4h", "bad")] == [
        "1h", "1h", "4h", "15m", "1d", "1w", "1mo", "30s", "4h", None,
    ]
    assert resolve_script_symbol("", "equity:NASDAQ:MSFT") == "equity:NASDAQ:MSFT"
    assert resolve_script_symbol("NASDAQ:AAPL", "equity:NASDAQ:MSFT") == "equity:NASDAQ:AAPL"
    assert resolve_script_symbol("BINANCE:BTCUSDT", "crypto:BINANCE:spot:ETH-USDT") == "crypto:BINANCE:spot:BTC-USDT"
    assert resolve_script_symbol("ZZZZZZQ", "equity:NASDAQ:MSFT") is None


def test_the_server_loads_each_context_with_the_runs_inputs() -> None:
    calls = []

    class Market:
        def bars(self, instrument_id, interval, limit, binding_id=None, *, alignment="count"):
            calls.append((instrument_id, interval, limit, alignment))
            starts = [DAY + timedelta(days=index) for index in range(3)]
            return SimpleNamespace(bars=[SimpleNamespace(start_time=start, open=1, high=2, low=0, close=1, volume=5) for start in starts])

    source = script('tf = input.timeframe("D", "Timeframe")\nplot(request.security("NASDAQ:AAPL", tf, close), "v")')
    chart = [SimpleNamespace(start_time=DAY + timedelta(hours=6 * index)) for index in range(12)]
    securities = load_script_securities(source, "equity:NASDAQ:MSFT", "6h", chart, Market(), {"Timeframe": "W"})
    assert list(securities) == ["NASDAQ:AAPL|W"]
    assert securities["NASDAQ:AAPL|W"].symbol == "equity:NASDAQ:AAPL" and securities["NASDAQ:AAPL|W"].timeframe == "1w"
    assert calls[0][:2] == ("equity:NASDAQ:AAPL", "1w") and calls[0][3] == "clock"
    assert script_securities_requested(source, {"Timeframe": "W"}) == ["NASDAQ:AAPL|W"]
    # A script that doesn't compile requests nothing (the run reports it).
    assert script_securities_requested("plot(close)") == []
