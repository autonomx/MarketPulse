from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.execution import (
    ExecutionEligibilityPolicy,
    ExecutionObservation,
    assess_execution_observation,
    execution_observation_from_quote,
)
from app.apps.trading.providers.equity_execution import (
    YAHOO_EXECUTION_ELIGIBLE,
    yahoo_execution_observation,
)


NOW = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)


def observation(**overrides) -> ExecutionObservation:
    payload = {
        "instrument_id": "equity:NASDAQ:TEST",
        "binding_id": "yahoo:historical_polling:equity:NASDAQ:TEST",
        "provider": "fixture",
        "bid": Decimal("9.99"),
        "ask": Decimal("10.01"),
        "bid_size": Decimal("1000"),
        "ask_size": Decimal("1200"),
        "last": Decimal("10"),
        "bar_volume": Decimal("5000"),
        "cumulative_volume": Decimal("1000000"),
        "source_time": NOW - timedelta(milliseconds=200),
        "received_at": NOW,
        "session": "regular",
        "freshness_mode": "polled",
        "halted": False,
    }
    payload.update(overrides)
    return ExecutionObservation(**payload)


def test_execution_observation_is_fail_closed_and_spread_aware() -> None:
    policy = ExecutionEligibilityPolicy(max_age_seconds="2", max_spread_bps="100")
    good = assess_execution_observation(observation(), policy)
    assert good.execution_eligible is True
    assert good.rejection_reasons == ()
    assert good.spread_bps is not None

    stale = assess_execution_observation(
        observation(source_time=NOW - timedelta(seconds=10)),
        policy,
    )
    assert stale.execution_eligible is False
    assert "STALE_MARKET_DATA" in stale.rejection_reasons

    future = assess_execution_observation(
        observation(source_time=NOW + timedelta(seconds=5)),
        policy,
    )
    assert future.execution_eligible is False
    assert "SOURCE_TIME_IN_FUTURE" in future.rejection_reasons

    no_book = assess_execution_observation(observation(bid=None, ask=None), policy)
    assert no_book.execution_eligible is False
    assert "BID_ASK_UNAVAILABLE" in no_book.rejection_reasons

    wide = assess_execution_observation(
        observation(bid=Decimal("9"), ask=Decimal("11")),
        policy,
    )
    assert wide.execution_eligible is False
    assert "SPREAD_TOO_WIDE" in wide.rejection_reasons

    halted = assess_execution_observation(observation(halted=True), policy)
    assert halted.execution_eligible is False
    assert "MARKET_HALTED" in halted.rejection_reasons


def test_cached_or_unknown_prices_can_never_be_execution_eligible() -> None:
    cached = assess_execution_observation(observation(freshness_mode="cached"))
    assert cached.execution_eligible is False
    assert "NON_EXECUTION_FRESHNESS" in cached.rejection_reasons

    closed = assess_execution_observation(observation(session="closed"))
    assert closed.execution_eligible is False
    assert "SESSION_NOT_EXECUTABLE" in closed.rejection_reasons


def test_provider_quote_normalization_preserves_source_time_book_and_liquidity() -> None:
    quote = {
        "instrument_id": "equity:NASDAQ:TEST",
        "binding_id": "binding",
        "provider": "fixture",
        "bid": "1.99",
        "ask": "2.01",
        "bid_size": "800",
        "ask_size": "900",
        "last": "2.00",
        "high": "2.05",
        "low": "1.95",
        "bar_volume": "4500",
        "bar_start_time": (NOW - timedelta(minutes=1)).isoformat(),
        "cumulative_volume": "500000",
        "source_time": (NOW - timedelta(milliseconds=50)).isoformat(),
        "session": "extended_pre",
        "freshness_mode": "polled",
        "halted": False,
    }
    value = execution_observation_from_quote(
        quote,
        binding_id="binding",
        provider="fixture",
        received_at=NOW,
    )
    assert value.bid == Decimal("1.99")
    assert value.ask == Decimal("2.01")
    assert value.bid_size == Decimal("800")
    assert value.ask_size == Decimal("900")
    assert value.high == Decimal("2.05")
    assert value.low == Decimal("1.95")
    assert value.bar_volume == Decimal("4500")
    assert value.last == Decimal("2.00")
    assert value.session == "extended_pre"
    assert value.age_seconds == Decimal("0.05")


class _FixtureResponse:
    def json(self):
        return {
            "quoteResponse": {
                "result": [
                    {
                        "regularMarketPrice": 10,
                        "regularMarketTime": int(NOW.timestamp()),
                        "regularMarketVolume": 1_000_000,
                        "bid": 9.99,
                        "ask": 10.01,
                        "marketState": "REGULAR",
                    }
                ]
            }
        }


class _FixtureRuntime:
    def get(self, *args, **kwargs):
        return _FixtureResponse()


class _FixtureYahooProvider:
    runtime = _FixtureRuntime()

    def get_binding(self, instrument_id):
        return SimpleNamespace(
            provider_symbol="TEST",
            binding_id=f"yahoo:historical_polling:{instrument_id}",
        )


def test_unofficial_yahoo_equity_quote_is_never_execution_grade() -> None:
    assert YAHOO_EXECUTION_ELIGIBLE is False
    value = yahoo_execution_observation(
        _FixtureYahooProvider(),
        "equity:NASDAQ:TEST",
        policy=ExecutionEligibilityPolicy(max_age_seconds=300, max_spread_bps=100),
    )
    assert value.execution_eligible is False
    assert "PROVIDER_NOT_EXECUTION_GRADE" in value.rejection_reasons
