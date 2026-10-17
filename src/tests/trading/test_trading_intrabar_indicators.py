"""Volume Delta and Cumulative Volume Delta on the server (TVP-6.4): the chart's values, alerts and validation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.apps.trading.alert_conditions import AlertConditionSpec, IndicatorSource, IndicatorSourceInputs, ValueTarget, validate_indicator_source
from app.apps.trading.alerts_evaluation import evaluate_conditions, required_bars
from app.apps.trading.indicators.intrabar import (
    auto_intrabar_interval, chosen_lower_interval, intrabar_values, trading_view_intrabar_interval,
)
from app.apps.trading.indicators.registry import TradingSession

FIXTURE = Path(__file__).parents[3] / "web/src/features/trading/indicators/fixtures/intrabarIndicatorContract.json"
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _contract():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chart = [
        SimpleNamespace(start_time=_time(start), end_time=_time(start) + HOUR, is_final=True, session=session, interval="1h", open=0, close=0, volume=0)
        for start, session in data["chartBars"]
    ]
    lower = [
        SimpleNamespace(start_time=_time(start), end_time=_time(start) + MINUTE, open=Decimal(str(o)), close=Decimal(str(c)), volume=Decimal(str(v)))
        for start, o, c, v in data["lowerBars"]
    ]
    return data, chart, lower


def _session(spec):
    if spec is None:
        return None
    return TradingSession(
        timezone=spec["timezone"], start_minute=spec["startMinute"], regular_start_minute=spec.get("regularStartMinute"),
        regular_only=bool(spec.get("regularOnly")),
    )


@pytest.mark.parametrize("case_index", range(7))
def test_the_server_reads_what_the_chart_draws(case_index: int) -> None:
    data, chart, lower = _contract()
    case = data["cases"][case_index]
    calls = []

    def load(lower_interval, start, end):
        calls.append((lower_interval, start, end))
        return [bar for bar in lower if start <= bar.start_time < end]

    params = {**case["params"], "lowerInterval": data["lowerInterval"]}
    values = intrabar_values(case["indicator"], chart, data["interval"], params, _session(case["session"]), load)
    expected = {next(i for i, bar in enumerate(chart) if bar.start_time == _time(time)): value for time, value in case["values"]}
    assert values == pytest.approx(expected)
    # The lower bars reach back 5,000 lower bars from the last closed bar's end, as on the chart.
    assert calls == [("1m", chart[-1].end_time - 5_000 * MINUTE, chart[-1].end_time)]


def test_lower_intervals_follow_the_chart() -> None:
    assert [auto_intrabar_interval(i) for i in ("1m", "15m", "1h", "4h", "1d", "1w")] == [None, "1m", "1m", "5m", "1h", "1d"]
    assert [trading_view_intrabar_interval(i) for i in ("1m", "1h", "4h", "1d", "1w")] == [None, "1m", "1m", "5m", "1h"]
    assert chosen_lower_interval({"lowerInterval": "15m"}, "1h") == "15m"
    assert chosen_lower_interval({"lowerInterval": "7m"}, "1h") == "1m"  # doesn't divide the hour: auto
    assert chosen_lower_interval({}, "1m") is None  # a 1m chart reads its own bars


def test_above_an_hour_the_latest_bars_read_the_finer_interval() -> None:
    start = datetime(2026, 3, 2, tzinfo=_time("2026-03-02T00:00:00Z").tzinfo)
    chart = [SimpleNamespace(start_time=start + 4 * HOUR * i, end_time=start + 4 * HOUR * (i + 1), is_final=True, session="regular") for i in range(4)]
    # 5m bars all buying, 1m bars (only for the last bar) all selling.
    def load(lower_interval, begin, end):
        step = {"5m": 5, "1m": 1}[lower_interval] * MINUTE
        bars, time = [], max(begin, chart[0].start_time)
        if lower_interval == "1m":
            time = max(begin, chart[-1].start_time)
        while time < end:
            up = lower_interval == "5m"
            bars.append(SimpleNamespace(start_time=time, open=Decimal(1 if up else 2), close=Decimal(2 if up else 1), volume=Decimal(1)))
            time += step
        return bars

    values = intrabar_values("tv-volume-delta", chart, "4h", {}, None, load)
    assert values == {0: 48.0, 1: 48.0, 2: 48.0, 3: -240.0}


def test_without_lower_bars_there_is_no_value() -> None:
    _, chart, _ = _contract()
    assert intrabar_values("tv-volume-delta", chart, "1h", {}, None, None) == {}
    def failing(*_):
        raise RuntimeError("provider down")
    assert intrabar_values("tv-volume-delta", chart, "1h", {}, None, failing) == {}


def _source(indicator_id: str, output: str, **params) -> IndicatorSource:
    return IndicatorSource(kind="indicator", indicator_id=indicator_id, inputs=IndicatorSourceInputs(params=params), output=output)


def test_alerts_validate_and_read_them() -> None:
    validate_indicator_source(_source("tv-volume-delta", "tv-volume-delta:delta"))
    validate_indicator_source(_source("tv-cumulative-volume-delta", "tv-cumulative-volume-delta:cvd", anchor="W", lowerInterval="5m"))
    with pytest.raises(ValueError, match="no output"):
        validate_indicator_source(_source("tv-volume-delta", "tv-volume-delta:cvd"))
    with pytest.raises(ValueError, match="anchor"):
        validate_indicator_source(_source("tv-cumulative-volume-delta", "tv-cumulative-volume-delta:cvd", anchor="Y"))
    # CVD reads back over its anchor period: a week of 1h bars.
    cvd = AlertConditionSpec(
        source=_source("tv-cumulative-volume-delta", "tv-cumulative-volume-delta:cvd", anchor="W"), operator="less_than", target=ValueTarget(kind="value", value=Decimal(0)),
    )
    assert required_bars([cvd], "1h") >= 7 * 24

    data, chart, lower = _contract()
    bars = [
        SimpleNamespace(start_time=bar.start_time, end_time=bar.end_time, is_final=True, session=bar.session, interval="1h",
                        open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=Decimal(1))
        for bar in chart
    ]
    below = AlertConditionSpec(
        source=_source("tv-volume-delta", "tv-volume-delta:delta", lowerInterval="1m"), operator="less_than", target=ValueTarget(kind="value", value=Decimal(0)),
    )
    load = lambda lower_interval, start, end: [bar for bar in lower if start <= bar.start_time < end]  # noqa: E731
    outcome = evaluate_conditions([below], bars, final_only=True, intrabar=load)
    last = data["cases"][0]["values"][-1][1]
    assert outcome is not None and outcome.met is (last < 0)
    # Without lower bars the condition has no value, so it doesn't fire.
    unloaded = evaluate_conditions([below], bars, final_only=True)
    assert unloaded is not None and unloaded.met is False


def test_the_loader_reads_each_range_once_through_the_intrabar_route() -> None:
    from app.apps.trading.indicator_context import FetchBarsService, intrabar_loader
    from app.apps.trading.models import MarketBar

    calls = []
    start = _time("2026-03-02T00:00:00Z")

    def fetch(instrument_id, interval, limit, binding_id):
        calls.append((instrument_id, interval, limit, binding_id))
        bars = [
            MarketBar(instrument_id=instrument_id, interval=interval, start_time=start + MINUTE * i, end_time=start + MINUTE * (i + 1),
                      open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(1), provider="fixture")
            for i in range(120)
        ]
        return SimpleNamespace(bars=bars, provenance=SimpleNamespace(history_complete=True))

    load = intrabar_loader(FetchBarsService(fetch), "crypto:BINANCE:spot:BTC-USDT", "1h", "binding-1")
    first = load("1m", start + HOUR, start + 2 * HOUR)
    again = load("1m", start + HOUR, start + 2 * HOUR)
    assert len(first) == 60 and again is first
    assert len(calls) == 1 and calls[0][1] == "1m" and calls[0][3] == "binding-1"
