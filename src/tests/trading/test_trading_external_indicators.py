"""External-data indicators on the server (TVP-0.2): alerts and the screener read the series the chart draws."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading import external_series as external_series_module
from app.apps.trading import scanner as scanner_module
from app.apps.trading.alert_conditions import AlertConditionSpec, IndicatorSource, validate_indicator_source
from app.apps.trading.alerts import TradingAlertCreate
from app.apps.trading.alerts_evaluation import evaluate_conditions, required_bars, validate_conditions_can_fire
from app.apps.trading.breadth import EXCHANGES
from app.apps.trading.external_series import ExternalSeries, align_points
from app.apps.trading.indicators.external import EXTERNAL_INDICATORS, external_available_for
from app.apps.trading.metric_data import BLOCKCHAIN_METRICS, MarketMetricPoint, MarketMetricResponse, MarketMetricSeries

from .test_trading_phase11_scanner import definition, response

FIXTURE = Path(__file__).parents[3] / "web/src/features/trading/indicators/fixtures/externalIndicatorContract.json"
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
BTC = "crypto:BINANCE:perpetual:BTC-USDT"


def hourly_bars(count: int, *, final: bool = True):
    return [
        SimpleNamespace(
            start_time=START + timedelta(hours=index),
            end_time=START + timedelta(hours=index + 1),
            open=Decimal(100), high=Decimal(101), low=Decimal(99), close=Decimal(100), volume=Decimal(1),
            is_final=final,
        )
        for index in range(count)
    ]


def metric(metric_name: str, series: dict[str, list[tuple[datetime, str]]]) -> MarketMetricResponse:
    return MarketMetricResponse(
        instrument_id=BTC, metric=metric_name, provider="test", interval="1h",
        series=[MarketMetricSeries(key=key, title=key, points=[MarketMetricPoint(time=time, value=Decimal(value)) for time, value in points]) for key, points in series.items()],
        received_at=START, freshness_mode="cached",
    )


class FakeLoader:
    def __init__(self, response: MarketMetricResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def __call__(self, metric_name, instrument_id, interval, limit, end_time):
        self.calls.append((metric_name, instrument_id, interval, limit, end_time))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


OPEN_INTEREST = {"kind": "indicator", "indicator_id": "tv-open-interest", "inputs": {}, "output": "tv-open-interest:open-interest"}


# --- The table ---------------------------------------------------------------------------------------------------


def test_the_server_table_matches_the_chart() -> None:
    entries = json.loads(FIXTURE.read_text(encoding="utf-8"))["entries"]
    server = [
        {"id": item.indicator_id, "metric": item.metric, "scope": item.scope, "outputs": list(item.output_keys)}
        for item in sorted(EXTERNAL_INDICATORS.values(), key=lambda item: item.indicator_id)
    ]
    assert server == entries


def test_series_keys_are_the_ones_the_adapters_produce() -> None:
    slugs = {config[0] for config in BLOCKCHAIN_METRICS.values()}
    for item in EXTERNAL_INDICATORS.values():
        if item.metric.startswith("blockchain."):
            assert item.series_keys == (BLOCKCHAIN_METRICS[item.metric][0],) and item.series_keys[0] in slugs
        if item.metric.startswith("breadth."):
            assert item.series_keys == tuple(exchange.lower() for exchange in EXCHANGES)


def test_scopes_follow_the_chart() -> None:
    assert external_available_for("tv-open-interest", BTC)
    assert not external_available_for("tv-open-interest", "equity:US:AAPL")
    assert external_available_for("tv-dividend-yield", "equity:US:AAPL")
    assert external_available_for("tv-hash-rate", "crypto:COINBASE:spot:BTC-USD")
    assert not external_available_for("tv-hash-rate", "crypto:BINANCE:spot:ETH-USDT")
    assert external_available_for("tv-advance-decline-line", "forex:OANDA:EUR-USD")


# --- Aligning a series to bars -----------------------------------------------------------------------------------


def test_a_bar_takes_the_latest_point_before_it_closes() -> None:
    bars = hourly_bars(4)
    points = [
        MarketMetricPoint(time=START, value=Decimal(1)),  # bar 0's start
        MarketMetricPoint(time=START + timedelta(hours=2), value=Decimal(2)),  # bar 1's end: bar 2's value
        MarketMetricPoint(time=START + timedelta(hours=2, minutes=30), value=Decimal(3)),
    ]
    assert align_points(points, bars) == {0: Decimal(1), 1: Decimal(1), 2: Decimal(3), 3: Decimal(3)}
    # Nothing before the first point.
    late = [MarketMetricPoint(time=START + timedelta(hours=1, minutes=5), value=Decimal(7)), MarketMetricPoint(time=START + timedelta(hours=3), value=Decimal(8))]
    assert align_points(late, bars) == {1: Decimal(7), 2: Decimal(7), 3: Decimal(8)}


def test_a_snapshot_holds_on_every_bar() -> None:
    snapshot = [MarketMetricPoint(time=START + timedelta(days=30), value=Decimal("2.5"))]
    assert align_points(snapshot, hourly_bars(3)) == {0: Decimal("2.5"), 1: Decimal("2.5"), 2: Decimal("2.5")}
    assert align_points([], hourly_bars(3)) == {}


# --- The loader --------------------------------------------------------------------------------------------------


def test_each_metric_is_fetched_once_per_bar_list() -> None:
    loader = FakeLoader(metric("binance.global_long_short_accounts", {"ratio": [(START, "1.5")], "long-percent": [(START, "60")]}))
    series = ExternalSeries(BTC, "1h", loader)
    bars = hourly_bars(3)
    assert series("tv-long-short-ratio-accounts", "tv-long-short-ratio-accounts:ratio", bars) == {0: Decimal("1.5"), 1: Decimal("1.5"), 2: Decimal("1.5")}
    assert series("tv-long-short-accounts", "tv-long-short-accounts:long-percent", bars)[2] == Decimal(60)
    assert len(loader.calls) == 1
    assert loader.calls[0] == ("binance.global_long_short_accounts", BTC, "1h", 3, bars[-1].end_time)
    # A series the response lacks, an output the indicator doesn't have, an instrument without the data: no values.
    assert series("tv-long-short-accounts", "tv-long-short-accounts:short-percent", bars) == {}
    assert series("tv-long-short-accounts", "tv-long-short-accounts:other", bars) == {}
    assert ExternalSeries("equity:US:AAPL", "1h", loader)("tv-open-interest", OPEN_INTEREST["output"], bars) == {}


def test_a_failing_provider_gives_no_values() -> None:
    loader = FakeLoader(RuntimeError("Binance unavailable"))
    series = ExternalSeries(BTC, "1h", loader)
    assert series("tv-open-interest", OPEN_INTEREST["output"], hourly_bars(2)) == {}
    assert series("tv-open-interest", OPEN_INTEREST["output"], hourly_bars(2)) == {}
    assert len(loader.calls) == 1  # the failure is remembered for the pass


# --- Alerts ------------------------------------------------------------------------------------------------------


def test_an_alert_crosses_open_interest() -> None:
    condition = AlertConditionSpec.model_validate({"source": OPEN_INTEREST, "operator": "crossing_up", "target": {"kind": "value", "value": "1000"}})
    bars = hourly_bars(3)
    loader = FakeLoader(metric("binance.open_interest", {"open-interest": [(START, "900"), (START + timedelta(hours=1), "950"), (START + timedelta(hours=2), "1100")]}))
    outcome = evaluate_conditions([condition], bars, final_only=False, external=ExternalSeries(BTC, "1h", loader))
    assert outcome is not None and outcome.met
    assert (outcome.observations[0].source_previous, outcome.observations[0].source) == (Decimal(950), Decimal(1100))
    # Without a loader (a pushed quote) there is no value, so the condition is false.
    assert not evaluate_conditions([condition], bars, final_only=False).met
    assert required_bars([condition]) == 2


def test_external_sources_are_validated_by_their_outputs() -> None:
    validate_indicator_source(IndicatorSource.model_validate(OPEN_INTEREST))
    validate_conditions_can_fire([AlertConditionSpec.model_validate({"source": OPEN_INTEREST, "operator": "greater_than", "target": {"kind": "value", "value": "1"}})])
    with pytest.raises(ValueError, match="has no output"):
        validate_indicator_source(IndicatorSource.model_validate({**OPEN_INTEREST, "output": "tv-open-interest:funding"}))
    with pytest.raises(ValueError, match="does not take a source"):
        validate_indicator_source(IndicatorSource.model_validate({**OPEN_INTEREST, "inputs": {"source": {"indicator_id": "rsi", "inputs": {"period": 14}, "output": "rsi:14"}}}))
    on_series = {"kind": "indicator", "indicator_id": "sma", "inputs": {"period": 5, "source": {"indicator_id": "tv-open-interest", "output": OPEN_INTEREST["output"]}}, "output": "sma:5"}
    with pytest.raises(ValueError, match="cannot read the data series"):
        validate_indicator_source(IndicatorSource.model_validate(on_series))


def _create(instrument_id: str) -> dict:
    return {
        "alert_id": "external-1", "instrument_id": instrument_id, "condition_type": "conditions", "threshold": "0",
        "conditions": [{"source": OPEN_INTEREST, "operator": "greater_than", "target": {"kind": "value", "value": "1"}}],
    }


def test_an_alert_needs_an_instrument_with_the_data() -> None:
    TradingAlertCreate.model_validate(_create(BTC))
    with pytest.raises(ValidationError, match="Binance crypto symbols only"):
        TradingAlertCreate.model_validate(_create("equity:US:AAPL"))
    # A list's members are checked as each is evaluated.
    TradingAlertCreate.model_validate(_create("watchlist:wl-1"))


def test_the_alert_indicator_list_includes_them() -> None:
    from app.apps.trading.alerts_api import create_trading_alert_router

    router = create_trading_alert_router()
    endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "").endswith("/indicators"))
    ids = endpoint().indicator_ids
    assert "tv-open-interest" in ids and "rsi" in ids and ids == sorted(ids)


# --- The screener ------------------------------------------------------------------------------------------------


def test_a_screen_filters_on_a_breadth_line(monkeypatch) -> None:
    symbol = "crypto:FIXTURE:spot:AAA-USD"
    bars = response(symbol).bars
    loader = FakeLoader(metric("breadth.ad_line", {"nyse": [(bars[0].start_time, "250")], "nasdaq": [(bars[0].start_time, "-40")]}))
    monkeypatch.setattr(external_series_module, "ExternalSeries", lambda instrument_id, interval: ExternalSeries(instrument_id, interval, loader))
    rule = {
        "rule_id": "breadth", "metric": "indicator", "operator": "gt", "threshold": "100",
        "source": {"kind": "indicator", "indicator_id": "tv-advance-decline-line", "inputs": {}, "output": "tv-advance-decline-line:nyse"},
    }
    result = scanner_module.evaluate_scanner_dataset(definition([symbol], rules=[rule]), "run", response(symbol), None)
    assert result is not None and result.metrics["rule:breadth"] == Decimal(250)
    nasdaq = {**rule, "source": {**rule["source"], "output": "tv-advance-decline-line:nasdaq"}}
    assert scanner_module.evaluate_scanner_dataset(definition([symbol], rules=[nasdaq]), "run", response(symbol), None) is None
