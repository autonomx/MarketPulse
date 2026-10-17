"""Intrabar data loader (TVP-0.6): lower-timeframe bars inside a range of chart bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.api import create_trading_router
from app.apps.trading.intrabar import MAX_INTRABAR_BARS, intrabar_bars
from app.apps.trading.models import MarketBar

NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)


def minute_bar(start: datetime) -> MarketBar:
    return MarketBar(
        instrument_id="crypto:BINANCE:spot:BTC-USDT",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("3"),
        is_final=True,
        session="24x7",
        provider="binance",
        provider_event_id=start.isoformat(),
        received_at=start + timedelta(minutes=1),
    )


class Service:
    """The latest ``limit`` 1m bars up to NOW, from at most ``history`` minutes back."""

    def __init__(self, history: int = 10_000, history_complete: bool = False) -> None:
        self.history = history
        self.history_complete = history_complete
        self.calls: list[tuple[str, str, int, str | None, dict]] = []

    def bars(self, instrument_id, interval, limit=500, binding_id=None, *args, **kwargs):
        self.calls.append((instrument_id, interval, limit, binding_id, kwargs))
        count = min(limit, self.history)
        bars = [minute_bar(NOW - timedelta(minutes=count - i)) for i in range(count)]
        return SimpleNamespace(bars=list(reversed(bars)), provenance=SimpleNamespace(history_complete=self.history_complete))


def test_returns_the_lower_bars_inside_the_range_asking_for_enough_to_reach_its_start() -> None:
    service = Service()
    start = NOW - timedelta(hours=3)
    result = intrabar_bars(
        service, instrument_id="crypto:BINANCE:spot:BTC-USDT", interval="1h", lower_interval="1m",
        start=start, end=start + timedelta(hours=1), now=NOW, binding_id="b",
    )
    assert [bar.start_time for bar in result.bars] == [start + timedelta(minutes=i) for i in range(60)]
    assert result.complete is True
    assert service.calls == [("crypto:BINANCE:spot:BTC-USDT", "1m", 181, "b", {"alignment": "clock"})]


def test_a_range_older_than_the_provider_reach_is_partial() -> None:
    service = Service()
    start = NOW - timedelta(days=10)
    result = intrabar_bars(
        service, instrument_id="x:y", interval="1h", lower_interval="1m", start=start, end=start + timedelta(hours=2), now=NOW,
    )
    assert service.calls[0][2] == MAX_INTRABAR_BARS
    assert result.bars == []
    assert result.complete is False
    assert result.available_from == NOW - timedelta(minutes=MAX_INTRABAR_BARS)


def test_a_provider_without_older_history_is_complete() -> None:
    start = NOW - timedelta(hours=3)
    result = intrabar_bars(
        Service(history=30, history_complete=True), instrument_id="x:y", interval="1h", lower_interval="1m", start=start, end=NOW, now=NOW,
    )
    assert result.complete is True
    assert len(result.bars) == 30


def test_a_provider_that_caps_its_history_is_partial() -> None:
    # Fewer bars than asked for, but the provider keeps only a window (Yahoo's week of 1m bars): not complete.
    start = NOW - timedelta(hours=3)
    result = intrabar_bars(Service(history=30), instrument_id="x:y", interval="1h", lower_interval="1m", start=start, end=NOW, now=NOW)
    assert result.complete is False
    assert result.available_from == NOW - timedelta(minutes=30)


@pytest.mark.parametrize(
    ("interval", "lower", "start", "end", "message"),
    [
        ("1m", "1h", NOW - timedelta(hours=1), NOW, "shorter than the chart interval"),
        ("1h", "1h", NOW - timedelta(hours=1), NOW, "shorter than the chart interval"),
        ("1h", "1m", NOW, NOW, "end must be after start"),
        ("1d", "1m", NOW - timedelta(days=5), NOW, "more than 5000"),
        ("1h", "bogus", NOW - timedelta(hours=1), NOW, ""),
        # Bar intervals start at one minute.
        ("1m", "1s", NOW - timedelta(minutes=1), NOW, ""),
    ],
)
def test_invalid_requests_are_rejected(interval, lower, start, end, message) -> None:
    with pytest.raises(ValueError, match=message):
        intrabar_bars(Service(), instrument_id="x:y", interval=interval, lower_interval=lower, start=start, end=end, now=NOW)


def test_the_endpoint_answers_with_the_range_and_rejects_invalid_ones() -> None:
    app = FastAPI()
    app.include_router(create_trading_router(market_service_factory=Service, clock=lambda: NOW))
    client = TestClient(app)
    start = NOW - timedelta(minutes=30)
    params = {
        "instrument_id": "crypto:BINANCE:spot:BTC-USDT", "interval": "1h", "lower_interval": "1m",
        "start": start.isoformat(), "end": NOW.isoformat(),
    }
    response = client.get("/api/trading/bars/intrabar", params=params)
    assert response.status_code == 200
    body = response.json()
    assert len(body["bars"]) == 30
    assert body["complete"] is True
    assert body["lower_interval"] == "1m"
    assert client.get("/api/trading/bars/intrabar", params={**params, "lower_interval": "1h"}).status_code == 422
