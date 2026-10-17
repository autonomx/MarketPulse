"""Alerts and screens read indicators as the chart does (TVP-1.3): their params, compare symbol and session hours."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.alert_conditions import AlertConditionSpec
from app.apps.trading.alerts import TradingAlert, TradingAlertCreate
from app.apps.trading.alerts_evaluation import evaluate_conditions, required_bars, validate_conditions_can_fire
from app.apps.trading.alerts_monitor import TradingAlertMonitor
from app.apps.trading.catalog import all_instruments
from app.apps.trading.indicator_context import compare_bars_loader, instrument_session
from app.apps.trading.indicators.registry import UTC_SESSION, BarSeries, IndicatorInputs, compute_indicator

START = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
ANY = {"kind": "value", "value": "-1000000000000000000"}


def bars(count: int, *, step: timedelta = timedelta(hours=1), seed: float = 0.0):
    rows = []
    previous = 100.0
    for i in range(count):
        close = 100 + 8 * math.sin(i / 5 + seed) + 3 * math.sin(i / 1.7 + 2 * seed) + i * 0.05
        rows.append(SimpleNamespace(
            start_time=START + step * i, end_time=START + step * (i + 1),
            open=Decimal(str(round(previous, 6))), high=Decimal(str(round(max(previous, close) + 0.7 + (i % 3) * 0.2, 6))),
            low=Decimal(str(round(min(previous, close) - 0.6 - (i % 4) * 0.2, 6))), close=Decimal(str(round(close, 6))),
            volume=Decimal(1000 + (i * 37) % 500), is_final=True,
        ))
        previous = close
    return rows


def condition(indicator_id: str, output: str, **inputs) -> AlertConditionSpec:
    source = {"kind": "indicator", "indicator_id": indicator_id, "inputs": inputs, "output": output}
    return AlertConditionSpec.model_validate({"source": source, "operator": "greater_than", "target": ANY})


def last_value(indicator_id: str, output: str, rows, inputs: IndicatorInputs, compare=None) -> float:
    series = next(item for item in compute_indicator(indicator_id, BarSeries.from_bars(rows), inputs, compare) if item.key == output)
    index, value = series.points[-1]
    assert index == len(rows) - 1
    return value


def test_params_reach_the_indicator() -> None:
    rows = bars(260)
    lowest = condition("tv-vwap-auto-anchored", "tv-vwap-auto-anchored:vwap", period=100, params={"anchor": "lowest-low"})
    outcome = evaluate_conditions([lowest], rows, final_only=False)
    expected = last_value("tv-vwap-auto-anchored", "tv-vwap-auto-anchored:vwap", rows, IndicatorInputs(period=100, params={"anchor": "lowest-low"}))
    assert outcome.observations[0].source == Decimal(repr(expected))
    default = evaluate_conditions([condition("tv-vwap-auto-anchored", "tv-vwap-auto-anchored:vwap", period=100)], rows, final_only=False)
    assert default.observations[0].source != outcome.observations[0].source


def test_session_indicators_read_the_instruments_session_hours() -> None:
    equity = next(item for item in all_instruments() if item.asset_class.value == "equity")
    session = instrument_session(equity.instrument_id)
    assert session is not None and session.timezone == "America/New_York" and session.regular_only
    assert instrument_session("crypto:BINANCE:spot:BTC-USDT") == UTC_SESSION
    assert instrument_session("no:such:instrument") is None
    rows = bars(120, step=timedelta(minutes=30))
    pivots = condition("tv-rob-booker-intraday-pivot-points", "tv-rob-booker-intraday-pivot-points:pp", period=4)
    outcome = evaluate_conditions([pivots], rows, final_only=False, session=session)
    expected = last_value("tv-rob-booker-intraday-pivot-points", "tv-rob-booker-intraday-pivot-points:pp", rows, IndicatorInputs(period=4, session=session))
    assert outcome.observations[0].source == Decimal(repr(expected))


def test_a_compare_symbol_is_loaded_on_the_alerts_bars() -> None:
    rows = bars(60)
    other = BarSeries.from_bars(bars(60, seed=1.3))
    calls = []

    def fetch(symbol, limit):
        calls.append((symbol, limit))
        return bars(60, seed=1.3)

    loader = compare_bars_loader(fetch)
    cc = condition("tv-correlation-coefficient-cc", "tv-correlation-coefficient-cc:cc", period=20, compare_symbol="equity:NASDAQ:QQQ")
    outcome = evaluate_conditions([cc], rows, final_only=False, compare=loader)
    expected = last_value(
        "tv-correlation-coefficient-cc", "tv-correlation-coefficient-cc:cc", rows, IndicatorInputs(period=20, compare_symbol="equity:NASDAQ:QQQ"), other
    )
    assert outcome.met and outcome.observations[0].source == Decimal(repr(expected))
    assert calls == [("equity:NASDAQ:QQQ", 60)]  # the chart's span, once per bar list
    # Without the compare series there is no value, so the condition is false.
    assert not evaluate_conditions([cc], rows, final_only=False).met
    failing = compare_bars_loader(lambda symbol, limit: (_ for _ in ()).throw(RuntimeError("down")))
    assert not evaluate_conditions([cc], rows, final_only=False, compare=failing).met


def test_session_lookbacks_turn_into_bars_of_the_alerts_interval() -> None:
    rvat = condition("tv-relative-volume-at-time", "tv-relative-volume-at-time:rvol-at-time", period=5)
    # Six days back plus a weekend of slack: 10 days.
    assert required_bars([rvat], "1h") == 10 * 24 + 1 + 1
    assert required_bars([rvat], "1d") == 10 + 1 + 1
    # On 5-minute bars it is more than one history fetch: capped, and the alert evaluates once the bars cover it.
    assert required_bars([rvat], "5m") == 996 + 1
    validate_conditions_can_fire([rvat], "5m")
    volume = condition("tv-24-hour-volume", "tv-24-hour-volume:volume-24h", period=1)
    assert required_bars([volume], "15m") == 24 * 4 + 1 + 1
    # An anchored indicator's lines start where its swings are, not at a measured first value.
    fib = condition("tv-auto-fib-retracement", "tv-auto-fib-retracement:level-0.618", period=10)
    validate_conditions_can_fire([fib], "1h")
    assert required_bars([fib], "1h") == 400 + 20 + 1


def test_an_alert_saves_with_params_and_a_compare_symbol() -> None:
    alert = TradingAlertCreate.model_validate({
        "alert_id": "cc-1", "instrument_id": "equity:NASDAQ:AAPL", "condition_type": "conditions", "threshold": "0",
        "evaluation_policy": {"interval": "1h"},
        "conditions": [{"source": {"kind": "indicator", "indicator_id": "tv-correlation-coefficient-cc", "inputs": {"period": 20, "compare_symbol": "equity:NASDAQ:QQQ", "params": {"x": 1}}, "output": "tv-correlation-coefficient-cc:cc"}, "operator": "greater_than", "target": {"kind": "value", "value": "0.5"}}],
    })
    assert alert.conditions[0].source.inputs.compare_symbol == "equity:NASDAQ:QQQ"


class _Market:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def bars(self, instrument_id, interval, limit, binding_id=None):
        self.calls.append((instrument_id, limit))
        rows = bars(80, seed=0.0 if instrument_id.endswith("AAPL") else 1.3)
        return SimpleNamespace(bars=rows, binding=SimpleNamespace(binding_id="b", provider="p"))


class _Repository:
    def __init__(self, alerts) -> None:
        self.alerts = alerts
        self.recorded: list = []

    def list_alerts_report(self, limit):
        return SimpleNamespace(alerts=self.alerts, unreadable=[])

    def record_outcomes(self, context, outcomes):
        self.recorded.extend(outcomes)
        return []


def test_the_monitor_loads_compare_bars_for_an_alert() -> None:
    cc = condition("tv-correlation-coefficient-cc", "tv-correlation-coefficient-cc:cc", period=20, compare_symbol="equity:NASDAQ:QQQ")
    alert = TradingAlert(alert_id="cc", instrument_id="equity:NASDAQ:AAPL", condition_type="conditions", threshold=Decimal(0),
                         conditions=[cc], evaluation_policy={"interval": "1h", "allow_partial_bars": True})
    market = _Market()
    repository = _Repository([alert])
    asyncio.run(TradingAlertMonitor(repository_factory=lambda: repository, market_service_factory=lambda: market, interval_seconds=30).run_once())
    assert [symbol for symbol, _ in market.calls] == ["equity:NASDAQ:AAPL", "equity:NASDAQ:QQQ"]
    assert repository.recorded and repository.recorded[0].outcome.observations[0].source is not None
