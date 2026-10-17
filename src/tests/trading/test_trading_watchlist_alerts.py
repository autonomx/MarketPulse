"""Watchlist alerts (TVP-1.7): one alert on every symbol of a watchlist."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading.alerts import (
    TradingAlert,
    TradingAlertCreate,
    TradingAlertEvaluationPolicy,
    policy_json,
    watchlist_id_of,
    watchlist_symbols,
)
from app.apps.trading.alerts_monitor import TradingAlertMonitor
from app.apps.trading.alerts_watchlist import plan_watchlist_pass, provider_symbol_cap, watchlist_members, watchlist_symbol_cap

NOW = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
PRICE_ABOVE = [{"source": {"kind": "price", "field": "close"}, "operator": "greater_than", "target": {"kind": "value", "value": "100"}}]


def watchlist_alert(**fields) -> TradingAlert:
    return TradingAlert(
        alert_id=fields.pop("alert_id", "list-alert"),
        instrument_id=fields.pop("instrument_id", "watchlist:tech"),
        conditions=PRICE_ABOVE,
        evaluation_policy={"interval": "1m", "allow_partial_bars": True, **fields.pop("policy", {})},
        **fields,
    )


def test_a_watchlist_alert_names_its_watchlist_and_reads_its_symbols_in_order() -> None:
    assert watchlist_id_of("watchlist:tech") == "tech"
    assert watchlist_id_of("equity:NASDAQ:AAPL") is None
    assert watchlist_id_of("watchlist:") is None
    assert watchlist_alert().watchlist_id == "tech"
    assert watchlist_symbols({"instrumentIds": ["b", "a", "b", "watchlist:other", 3]}) == ["b", "a"]
    assert watchlist_symbols({"items": [{"type": "section", "id": "s"}, {"type": "symbol", "instrumentId": "x"}]}) == ["x"]
    assert watchlist_members({"status": "archived", "payload": {"instrumentIds": ["a"]}}) is None
    assert watchlist_members(None) is None


def test_the_symbols_evaluated_are_capped_by_the_limit_and_the_providers_budget() -> None:
    # Alpaca: 180 requests a minute; half of what a 30 s pass refills is 45 symbols.
    assert provider_symbol_cap("alpaca", 30) == 45
    # Binance: 4,800 weight a minute, 2 per bars request.
    assert provider_symbol_cap("binance", 30) == 600
    # No published limit (Yahoo): a fixed, conservative number per pass.
    assert provider_symbol_cap("yahoo", 30) == 25
    providers = {"equity:A": "alpaca_iex", "crypto:B": "binance"}
    assert watchlist_symbol_cap(["equity:A", "crypto:B"], providers.get, 30) == 45
    assert watchlist_symbol_cap(["crypto:B"], providers.get, 30) == 600
    members = {"tech": [f"s{i}" for i in range(150)]}

    def plan(*alerts, fetched=(), upstream=lambda symbol: "binance"):
        targets, status = plan_watchlist_pass(alerts, members, fetched, upstream, 30)
        return sorted(target[0] for target in targets), status

    assert len(plan(watchlist_alert())[0]) == 100
    assert plan(watchlist_alert(policy={"symbol_limit": 3}))[0] == ["s0", "s1", "s2"]
    symbols, status = plan(watchlist_alert(policy={"symbol_limit": 500}), upstream=lambda symbol: "alpaca")
    assert len(symbols) == 45 and status["list-alert"] == {"watchlist_missing": False, "members": 150, "evaluated": 45, "skipped": 105}


def test_one_budget_covers_every_alert_of_a_pass() -> None:
    members = {"tech": [f"s{i}" for i in range(40)], "other": [f"s{i}" for i in range(20, 60)], "gone": None}
    first = watchlist_alert(alert_id="a1")
    overlapping = watchlist_alert(alert_id="a2", instrument_id="watchlist:other")
    five_minutes = watchlist_alert(alert_id="a3", policy={"interval": "5m"})
    orphan = watchlist_alert(alert_id="a4", instrument_id="watchlist:gone")
    # Yahoo: 25 fetches a pass, 5 spent by ordinary alerts; shared histories cost nothing again.
    ordinary = [(f"s{i}", None, "1m") for i in range(5)]
    targets, status = plan_watchlist_pass(
        [overlapping, five_minutes, first, orphan], members, ordinary,
        lambda symbol: None if symbol == "s39" else "yahoo", 30,
    )
    assert status["a1"]["evaluated"] == 25 and status["a1"]["skipped"] == 15
    assert status["a2"]["evaluated"] == 5  # s20..s24, already planned for a1
    assert status["a3"]["evaluated"] == 0  # another interval is another history: the budget is spent
    assert status["a4"] == {"watchlist_missing": True, "members": 0, "evaluated": 0, "skipped": 0}
    fetched = {target for target in targets if target not in ordinary}
    assert len(fetched) == 20
    # A symbol without a provider is skipped, never fetched.
    _, status = plan_watchlist_pass([first], {"tech": ["s39"]}, (), lambda symbol: None, 30)
    assert status["a1"]["skipped"] == 1


def test_write_rules_for_watchlist_alerts() -> None:
    base = {"alert_id": "a", "conditions": PRICE_ABOVE, "evaluation_policy": {"interval": "1m"}}
    TradingAlertCreate(instrument_id="watchlist:tech", **base)
    trendline = [{"source": {"kind": "price", "field": "close"}, "operator": "crossing", "target": {"kind": "source", "source": {
        "kind": "trendline", "points": [{"time": "2026-01-01T00:00:00Z", "price": "1"}, {"time": "2026-01-02T00:00:00Z", "price": "2"}]}}}]
    with pytest.raises(ValidationError, match="trendline"):
        TradingAlertCreate(instrument_id="watchlist:tech", **{**base, "conditions": trendline})
    with pytest.raises(ValidationError, match="own feed"):
        TradingAlertCreate(instrument_id="watchlist:tech", binding_id="b", **base)
    with pytest.raises(ValidationError, match="names its watchlist"):
        TradingAlertCreate(instrument_id="watchlist:", **base)
    with pytest.raises(ValidationError, match="symbol_limit is for watchlist alerts"):
        TradingAlertCreate(instrument_id="equity:NASDAQ:AAPL", **{**base, "evaluation_policy": {"interval": "1m", "symbol_limit": 5}})


def test_a_policy_without_a_symbol_limit_is_stored_as_before() -> None:
    assert "symbol_limit" not in policy_json(TradingAlertEvaluationPolicy())
    assert '"symbol_limit":5' in policy_json(TradingAlertEvaluationPolicy(symbol_limit=5))


class _Repository:
    def __init__(self, alerts: list[TradingAlert]) -> None:
        self.alerts = alerts
        self.recorded: list[tuple[str, list[str]]] = []
        self.symbol_recorded: list[tuple[str, list[str]]] = []

    def list_alerts_report(self, limit: int):
        return SimpleNamespace(alerts=self.alerts, unreadable=[])

    def record_outcomes(self, context, outcomes):
        self.recorded.append((context.instrument_id, [record.alert_id for record in outcomes]))
        return []

    def record_watchlist_outcomes(self, context, outcomes):
        self.symbol_recorded.append((context.instrument_id, [record.alert_id for record in outcomes]))
        return []


class _Market:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.registry = SimpleNamespace(resolve_binding=lambda symbol: SimpleNamespace(provider="alpaca_iex" if symbol.startswith("equity") else "binance"))

    def bars(self, instrument_id, interval, limit, binding_id=None):
        self.calls.append(instrument_id)
        bars = [
            SimpleNamespace(start_time=NOW + timedelta(minutes=i), end_time=NOW + timedelta(minutes=i + 1), open=Decimal(99), high=Decimal(102),
                            low=Decimal(98), close=Decimal(101), volume=Decimal(1), is_final=True)
            for i in range(5)
        ]
        return SimpleNamespace(bars=bars, binding=SimpleNamespace(binding_id="b", provider="p"))


class _Documents:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    def get(self, record_type, record_id):
        assert (record_type, record_id) == ("watchlist", "tech")
        return {"status": "active", "payload": {"instrumentIds": list(self.symbols)}}


def test_the_monitor_evaluates_each_member_and_follows_membership_changes() -> None:
    ordinary = TradingAlert(alert_id="aapl", instrument_id="equity:NASDAQ:AAPL", conditions=PRICE_ABOVE, evaluation_policy={"interval": "1m", "allow_partial_bars": True})
    repository = _Repository([watchlist_alert(), ordinary])
    market = _Market()
    documents = _Documents(["equity:NASDAQ:AAPL", "crypto:BINANCE:spot:BTC-USDT"])
    monitor = TradingAlertMonitor(repository_factory=lambda: repository, market_service_factory=lambda: market, document_repository_factory=lambda: documents, interval_seconds=30)
    asyncio.run(monitor.run_once())
    # One history per symbol, shared by the ordinary alert on AAPL.
    assert sorted(market.calls) == ["crypto:BINANCE:spot:BTC-USDT", "equity:NASDAQ:AAPL"]
    assert repository.recorded == [("equity:NASDAQ:AAPL", ["aapl"])]
    assert sorted(repository.symbol_recorded) == [("crypto:BINANCE:spot:BTC-USDT", ["list-alert"]), ("equity:NASDAQ:AAPL", ["list-alert"])]
    assert monitor.diagnostics()["watchlist_alerts"] == {"list-alert": {"watchlist_missing": False, "members": 2, "evaluated": 2, "skipped": 0}}

    documents.symbols = ["crypto:BINANCE:spot:ETH-USDT"]
    repository.symbol_recorded.clear()
    asyncio.run(monitor.run_once())
    assert repository.symbol_recorded == [("crypto:BINANCE:spot:ETH-USDT", ["list-alert"])]
