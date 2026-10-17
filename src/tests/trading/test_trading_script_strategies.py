"""Strategy scripts' broker emulator (TVP-11.5): fills on the bar path, trades and the report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.apps.trading.indicators.registry import BarSeries
from app.apps.trading.scripts import ScriptError, run_script

START = datetime(2026, 1, 5, tzinfo=timezone.utc)


def bars(rows: list[tuple[float, float, float, float]]) -> BarSeries:
    """(open, high, low, close) per daily bar."""
    return BarSeries(
        start_times=tuple(START + timedelta(days=index) for index in range(len(rows))),
        open=tuple(row[0] for row in rows), high=tuple(row[1] for row in rows), low=tuple(row[2] for row in rows),
        close=tuple(row[3] for row in rows), volume=tuple(1.0 for _ in rows), sessions=tuple("regular" for _ in rows),
    )


def run(body: str, rows: list[tuple[float, float, float, float]], header: str = 'strategy("T", initial_capital=10000)'):
    result = run_script(f"//@version=5\n{header}\n{body}", bars(rows), timeframe="D")
    assert result.strategy is not None
    return result.strategy


FLAT = [(100, 101, 99, 100)] * 3


def test_a_market_entry_fills_at_the_next_open_and_the_bracket_on_the_bar_path() -> None:
    report = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=2)\nstrategy.exit("X", "L", limit=106, stop=97)',
        [(100, 101, 99, 100), (102, 103, 101, 102), (103, 107, 102, 106), (106, 106, 106, 106)],
    )
    [trade] = report["trades"]
    assert (trade["entry_bar"], trade["entry_price"], trade["exit_bar"], trade["exit_price"], trade["exit_id"]) == (1, 102, 2, 106, "X")
    assert trade["profit"] == pytest.approx(8) and trade["direction"] == "long"
    assert report["summary"]["all"]["net_profit"] == pytest.approx(8)
    assert report["equity"] == pytest.approx([10000, 10000, 10008, 10008])
    assert [fill["side"] for fill in report["fills"]] == ["buy", "sell"]


def test_the_bar_path_goes_to_the_nearer_extreme_first() -> None:
    entry = 'if bar_index == 0\n    strategy.entry("L", strategy.long)\nstrategy.exit("X", "L", limit=105, stop=95)'
    # Open 100, high 105 is nearer than low 94: the target fills first.
    assert run(entry, [(100, 100, 100, 100), (100, 105, 94, 100)])["trades"][0]["exit_price"] == 105
    # Open 100, low 96... high 106 farther than low 95: the stop fills first.
    assert run(entry, [(100, 100, 100, 100), (100, 106, 95, 100)])["trades"][0]["exit_price"] == 95


def test_gaps_fill_at_the_open_and_limits_at_their_price() -> None:
    report = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, limit=98)\nstrategy.exit("X", "L", stop=95)',
        [(100, 100, 100, 100), (99, 99, 97, 98), (90, 92, 89, 91)],
    )
    [trade] = report["trades"]
    # The limit fills at 98 when price comes down to it; the next bar gaps below the stop and fills at its open.
    assert (trade["entry_price"], trade["exit_price"]) == (98, 90)


def test_an_entry_reverses_the_position_and_pyramiding_limits_entries() -> None:
    report = run(
        'if bar_index == 0 or bar_index == 1\n    strategy.entry("L", strategy.long, qty=1)\nif bar_index == 2\n    strategy.entry("S", strategy.short, qty=3)',
        [(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102), (103, 103, 103, 103), (104, 104, 104, 104)],
    )
    # pyramiding=0: the second long entry is not filled; the short entry closes the long and goes short 3.
    [closed] = report["trades"]
    [short] = report["open_trades"]
    assert (closed["entry_price"], closed["exit_price"], closed["exit_id"]) == (101, 103, "S")
    assert (short["direction"], short["qty"], short["entry_price"]) == ("short", 3, 103)
    pyramid = run(
        'if bar_index < 3\n    strategy.entry("L", strategy.long, qty=1)',
        FLAT + [(100, 100, 100, 100)],
        header='strategy("T", pyramiding=2)',
    )
    assert len(pyramid["open_trades"]) == 2


def test_closes_immediately_or_at_the_close_and_fifo() -> None:
    report = run(
        'if bar_index == 0\n    strategy.order("a", strategy.long, qty=1)\nif bar_index == 1\n    strategy.order("b", strategy.long, qty=1)\n'
        'if bar_index == 3\n    strategy.order("c", strategy.short, qty=1.5)\nif bar_index == 4\n    strategy.close_all(immediately=true)',
        [(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102), (103, 103, 103, 103), (104, 104, 104, 104)],
    )
    trades = report["trades"]
    # Selling 1.5 closes "a" and half of "b" first in, first out; close_all closes the rest at bar 4's close.
    assert [(trade["entry_id"], trade["qty"], trade["exit_price"]) for trade in trades] == [("a", 1, 104), ("b", 0.5, 104), ("b", 0.5, 104)]
    on_close = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=1)',
        FLAT, header='strategy("T", process_orders_on_close=true)',
    )
    assert on_close["open_trades"][0]["entry_bar"] == 0


def test_oca_cancel_trailing_stop_commission_and_slippage() -> None:
    oca = run(
        'if bar_index == 0\n    strategy.entry("up", strategy.long, stop=105, oca_name="g", oca_type=strategy.oca.cancel)\n'
        '    strategy.entry("down", strategy.short, stop=95, oca_name="g", oca_type=strategy.oca.cancel)',
        [(100, 100, 100, 100), (100, 106, 99, 104), (104, 104, 90, 92)],
    )
    # The long stop fills at 105; the short stop in its group is cancelled.
    assert [trade["entry_id"] for trade in oca["open_trades"]] == ["up"]
    trail = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=1)\nstrategy.exit("T", "L", trail_points=100, trail_offset=200)',
        [(100, 100, 100, 100), (100, 100, 100, 100), (100, 105, 100, 105), (105, 105, 102, 102)],
    )
    # Active from 101 (100 ticks); the best price 105 sets the stop at 103, hit on the way down.
    assert trail["trades"][0]["exit_price"] == pytest.approx(103)
    costs = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=10)\nif bar_index == 1\n    strategy.close("L")',
        FLAT, header='strategy("T", commission_type=strategy.commission.cash_per_order, commission_value=2, slippage=5)',
    )
    [trade] = costs["trades"]
    assert (trade["entry_price"], trade["exit_price"]) == (pytest.approx(100.05), pytest.approx(99.95))
    assert trade["commission"] == 4 and trade["profit"] == pytest.approx(-1 - 4)


def test_the_script_reads_its_position_and_trades() -> None:
    result = run_script(
        '//@version=5\nstrategy("T")\nif bar_index == 0\n    strategy.entry("L", strategy.long, qty=2)\nif bar_index == 2\n    strategy.close("L")\n'
        'plot(strategy.position_size, "size")\nplot(strategy.closedtrades > 0 ? strategy.closedtrades.profit(0) : na, "profit")\n'
        'plot(strategy.opentrades > 0 ? strategy.opentrades.entry_price(0) : na, "entry")',
        bars([(100, 100, 100, 100), (101, 101, 101, 101), (102, 102, 102, 102), (104, 104, 104, 104)]), timeframe="D",
    )
    size, profit, entry = (plot.values for plot in result.plots)
    assert size == [0, 2, 2, 0] and profit == [None, None, None, 6] and entry == [None, 101, 101, None]


def test_strategy_errors_are_the_scripts() -> None:
    with pytest.raises(ScriptError, match="need a strategy"):
        run_script('//@version=5\nindicator("I")\nstrategy.entry("L", strategy.long)', bars(FLAT))
    with pytest.raises(ScriptError, match="initial_capital"):
        run_script('//@version=5\nstrategy("T", initial_capital=0)\nplot(close)', bars(FLAT))
    with pytest.raises(ScriptError, match="needs profit"):
        run_script('//@version=5\nstrategy("T")\nstrategy.exit("X")', bars(FLAT))
    with pytest.raises(ScriptError, match="not supported"):
        run_script('//@version=5\nstrategy("T")\nstrategy.risk.allow_entry_in(strategy.direction.long)', bars(FLAT))


def test_the_report_summarises_long_and_short_trades() -> None:
    report = run(
        'if bar_index == 0\n    strategy.entry("L", strategy.long, qty=1)\nif bar_index == 2\n    strategy.entry("S", strategy.short, qty=1)\n'
        'if bar_index == 4\n    strategy.close_all()',
        # Short from 110, the price rises to 115 at a close (a drawdown), then falls to 104.
        [(100, 100, 100, 100), (100, 100, 100, 100), (110, 110, 110, 110), (110, 115, 110, 115), (104, 104, 104, 104), (104, 104, 104, 104)],
    )
    summary = report["summary"]
    assert summary["long"]["net_profit"] == 10 and summary["short"]["net_profit"] == 6
    assert summary["all"]["total_closed_trades"] == 2 and summary["all"]["percent_profitable"] == 100
    assert summary["all"]["buy_hold_return"] == pytest.approx(400)
    assert report["drawdown"][-1] == 0 and min(report["drawdown"]) < 0
