from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.execution import ExecutionEligibilityPolicy
from app.apps.trading.providers.alpaca_iex import AlpacaIexExecutionProvider
from app.apps.trading.providers.errors import ProviderContractError, ProviderDataUnavailableError
from app.apps.trading.providers.registry import ProviderRegistry


NOW = datetime(2026, 8, 18, 14, 0, 0, 300000, tzinfo=timezone.utc)


class _FixtureResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def json(self):
        if self.payload is not None:
            return self.payload
        return {
            "latestQuote": {
                "t": "2026-08-18T14:00:00.100000Z",
                "bp": 9.99,
                "ap": 10.01,
                "bs": 300,
                "as": 400,
            },
            "latestTrade": {
                "t": "2026-08-18T14:00:00.050000Z",
                "p": 10.00,
                "s": 100,
            },
            "minuteBar": {
                "t": "2026-08-18T14:00:00Z",
                "o": 9.98,
                "h": 10.04,
                "l": 9.97,
                "c": 10.00,
                "v": 2500,
            },
            "dailyBar": {"v": 1_250_000},
        }


class _FixtureRuntime:
    session = object()

    def __init__(self, *, historical_payload=None, snapshot_payload=None):
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.historical_payload = historical_payload
        self.snapshot_payload = snapshot_payload

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/bars") and self.historical_payload is not None:
            return _FixtureResponse(self.historical_payload)
        if url.endswith("/snapshot") and self.snapshot_payload is not None:
            return _FixtureResponse(self.snapshot_payload)
        return _FixtureResponse()


def _credentials(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_ALPACA_API_KEY_ID", "paper-key")
    monkeypatch.setenv("OMNIX_ALPACA_API_SECRET_KEY", "paper-secret")


def _provider(runtime=None) -> AlpacaIexExecutionProvider:
    return AlpacaIexExecutionProvider(
        runtime=runtime or _FixtureRuntime(),
        clock=lambda: NOW,
    )


def _raw_bar(start: datetime, close: Decimal) -> dict[str, object]:
    return {
        "t": start.isoformat().replace("+00:00", "Z"),
        "o": str(close - Decimal("0.01")),
        "h": str(close + Decimal("0.02")),
        "l": str(close - Decimal("0.02")),
        "c": str(close),
        "v": 10000,
    }


def test_alpaca_iex_snapshot_produces_execution_eligible_book(monkeypatch) -> None:
    _credentials(monkeypatch)
    runtime = _FixtureRuntime()
    provider = _provider(runtime)

    value = provider.execution_observation(
        "equity:NASDAQ:AAPL",
        policy=ExecutionEligibilityPolicy(max_age_seconds="300", max_spread_bps="100"),
    )

    assert value.provider == "alpaca_iex"
    assert value.binding_id == "alpaca_iex:rest:equity:NASDAQ:AAPL"
    assert value.bid == Decimal("9.99")
    assert value.ask == Decimal("10.01")
    assert value.bid_size == Decimal("30000")
    assert value.ask_size == Decimal("40000")
    assert value.last == Decimal("10.0")
    assert value.high == Decimal("10.04")
    assert value.low == Decimal("9.97")
    assert value.bar_volume == Decimal("2500")
    assert value.bar_start_time == datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    assert value.cumulative_volume == Decimal("1250000")
    assert value.session == "regular"
    assert value.freshness_mode == "live"
    assert value.execution_eligible is True
    assert value.rejection_reasons == ()

    url, request = runtime.calls[0]
    assert url.endswith("/v2/stocks/AAPL/snapshot")
    assert request["params"] == {"feed": "iex"}
    assert request["headers"] == {
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }


def test_alpaca_iex_latest_trade_without_book_is_price_only_not_exception(monkeypatch) -> None:
    _credentials(monkeypatch)
    runtime = _FixtureRuntime(
        snapshot_payload={
            "latestTrade": {
                "t": "2026-08-18T14:00:00.050000Z",
                "p": 10.00,
                "s": 100,
            },
            "minuteBar": {
                "t": "2026-08-18T14:00:00Z",
                "o": 9.98,
                "h": 10.04,
                "l": 9.97,
                "c": 10.00,
                "v": 2500,
            },
            "dailyBar": {"v": 1_250_000},
        }
    )
    value = _provider(runtime).execution_observation(
        "equity:NASDAQ:AAPL",
        policy=ExecutionEligibilityPolicy(max_age_seconds="300", max_spread_bps="100"),
    )

    assert value.last == Decimal("10.0")
    assert value.bid is None
    assert value.ask is None
    assert value.execution_eligible is False
    assert "BID_ASK_UNAVAILABLE" in value.rejection_reasons
    assert value.freshness_mode == "live"


def test_alpaca_iex_indicator_history_starts_at_0400_et_and_excludes_open_bar(monkeypatch) -> None:
    _credentials(monkeypatch)
    cutoff = datetime(2026, 8, 18, 13, 35, 30, tzinfo=timezone.utc)
    payload = {
        "bars": [
            _raw_bar(datetime(2026, 8, 18, 7, 59, tzinfo=timezone.utc), Decimal("9.90")),
            _raw_bar(datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc), Decimal("10.00")),
            _raw_bar(datetime(2026, 8, 18, 13, 34, tzinfo=timezone.utc), Decimal("10.50")),
            _raw_bar(datetime(2026, 8, 18, 13, 35, tzinfo=timezone.utc), Decimal("10.60")),
        ]
    }
    runtime = _FixtureRuntime(historical_payload=payload)
    provider = _provider(runtime)

    bars = provider.indicator_bars_as_of("equity:NASDAQ:AAPL", as_of=cutoff)

    assert [bar.start_time for bar in bars] == [
        datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 13, 34, tzinfo=timezone.utc),
    ]
    assert bars[0].session == "extended_pre"
    assert bars[-1].session == "regular"
    assert all(bar.is_final and bar.end_time <= cutoff for bar in bars)
    assert all(bar.provider == "alpaca_iex" for bar in bars)

    url, request = runtime.calls[0]
    assert url.endswith("/v2/stocks/AAPL/bars")
    assert request["params"] == {
        "timeframe": "1Min",
        "start": "2026-08-18T08:00:00Z",
        "end": "2026-08-18T13:35:30Z",
        "adjustment": "raw",
        "feed": "iex",
        "sort": "asc",
        "limit": 1000,
    }
    assert request["headers"] == {
        "APCA-API-KEY-ID": "paper-key",
        "APCA-API-SECRET-KEY": "paper-secret",
    }


def test_alpaca_iex_empty_indicator_history_is_an_unavailable_series(monkeypatch) -> None:
    _credentials(monkeypatch)
    runtime = _FixtureRuntime(historical_payload={"bars": []})
    provider = _provider(runtime)

    bars = provider.indicator_bars_as_of(
        "equity:NASDAQ:AAPL",
        as_of=datetime(2026, 8, 18, 13, 35, 30, tzinfo=timezone.utc),
    )

    assert bars == []


def test_alpaca_iex_nonempty_malformed_indicator_history_still_fails_closed(monkeypatch) -> None:
    _credentials(monkeypatch)
    runtime = _FixtureRuntime(
        historical_payload={
            "bars": [
                {
                    "t": "2026-08-18T13:34:00Z",
                    "o": "10.00",
                    "h": "10.02",
                    "l": "9.98",
                    "v": 100,
                }
            ]
        }
    )

    with pytest.raises(ProviderContractError, match="missing close"):
        _provider(runtime).indicator_bars_as_of(
            "equity:NASDAQ:AAPL",
            as_of=datetime(2026, 8, 18, 13, 35, 30, tzinfo=timezone.utc),
        )


def test_registry_indicator_history_uses_execution_iex_binding_for_yahoo_request(monkeypatch) -> None:
    _credentials(monkeypatch)
    cutoff = datetime(2026, 8, 18, 13, 35, 30, tzinfo=timezone.utc)
    runtime = _FixtureRuntime(
        historical_payload={
            "bars": [
                _raw_bar(cutoff - timedelta(minutes=2), Decimal("10.00")),
            ]
        }
    )
    provider = _provider(runtime)
    registry = ProviderRegistry(factories={"alpaca_iex": lambda: provider})
    yahoo_binding = "yahoo:historical_polling:equity:NASDAQ:AAPL"

    bars = registry.execution_indicator_bars(
        "equity:NASDAQ:AAPL",
        yahoo_binding,
        as_of=cutoff,
    )

    assert len(bars) == 1
    assert bars[0].provider == "alpaca_iex"
    assert runtime.calls[0][0].endswith("/v2/stocks/AAPL/bars")


def test_alpaca_iex_fails_closed_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("OMNIX_ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("OMNIX_ALPACA_API_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    provider = _provider()
    with pytest.raises(ProviderDataUnavailableError, match="credentials are not configured"):
        provider.execution_observation("equity:NASDAQ:AAPL")


def test_yahoo_history_binding_uses_alpaca_iex_for_execution(monkeypatch) -> None:
    _credentials(monkeypatch)
    provider = _provider()
    registry = ProviderRegistry(factories={"alpaca_iex": lambda: provider})
    yahoo_binding = "yahoo:historical_polling:equity:NASDAQ:AAPL"

    value = registry.execution_observation(
        "equity:NASDAQ:AAPL",
        yahoo_binding,
        policy=ExecutionEligibilityPolicy(max_age_seconds="300", max_spread_bps="100"),
    )

    # The persisted paper order keeps its Yahoo/history binding so old and new
    # orders remain matchable, while provider evidence proves the fill quote came
    # from Alpaca IEX.
    assert value.binding_id == yahoo_binding
    assert value.provider == "alpaca_iex"
    assert value.execution_eligible is True

def test_alpaca_iex_malformed_quote_timestamp_degrades_to_trade_only(monkeypatch) -> None:
    _credentials(monkeypatch)
    runtime = _FixtureRuntime(
        snapshot_payload={
            "latestQuote": {
                "t": "not-a-timestamp",
                "bp": 9.99,
                "ap": 10.01,
                "bs": 300,
                "as": 400,
            },
            "latestTrade": {
                "t": "2026-08-18T14:00:00.050000Z",
                "p": 10.00,
                "s": 100,
            },
            "minuteBar": {
                "t": "2026-08-18T14:00:00Z",
                "o": 9.98,
                "h": 10.04,
                "l": 9.97,
                "c": 10.00,
                "v": 2500,
            },
            "dailyBar": {"v": 1_250_000},
        }
    )

    value = _provider(runtime).execution_observation(
        "equity:NASDAQ:AAPL",
        policy=ExecutionEligibilityPolicy(max_age_seconds="300", max_spread_bps="100"),
    )

    assert value.last == Decimal("10.0")
    assert value.bid is None
    assert value.ask is None
    assert value.freshness_mode == "fallback"
    assert value.execution_eligible is False
    assert "BID_ASK_UNAVAILABLE" in value.rejection_reasons
    assert "NON_EXECUTION_FRESHNESS" in value.rejection_reasons
