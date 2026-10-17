"""The economic calendar from FRED (TVP-10.5)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import market_data_api
from app.apps.trading.economic_calendar import FredCalendar, create_trading_economic_calendar_router, headline, importance_of

TODAY = date(2026, 8, 10)


def test_headlines_are_levels_changes_or_percent_changes() -> None:
    observations = [{"value": "100"}, {"value": "102"}, {"value": "."}, {"value": "103.02"}]
    assert headline(observations, "level") == (103.02, 102.0)
    assert headline(observations, "change") == pytest.approx((1.02, 2.0))
    assert headline(observations, "percent_change") == pytest.approx((1.0, 2.0))
    assert headline([], "level") == (None, None)
    assert importance_of("Employment Situation")[1] == "PAYEMS" and importance_of("Some Regional Survey") is None


class Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Runtime:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        if url.endswith("releases/dates"):
            return Response({"release_dates": [
                {"release_id": 50, "release_name": "Employment Situation", "date": "2026-08-07"},
                {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-08-12"},
                {"release_id": 999, "release_name": "Some Regional Survey", "date": "2026-08-11"},
            ]})
        if url.endswith("series/observations"):
            assert params["series_id"] == "PAYEMS" and params["realtime_start"] == params["realtime_end"] == "2026-08-07"
            # Newest first, as asked: the payroll level, so the change is the jobs added.
            return Response({"observations": [{"value": "159200"}, {"value": "159000"}, {"value": "158900"}]})
        raise AssertionError(url)


def test_the_calendar_marks_important_releases_and_shows_first_published_values() -> None:
    runtime = Runtime()
    calendar = FredCalendar(key=lambda: "key", runtime=runtime, clock=lambda: 0.0)
    high = calendar.calendar(date(2026, 8, 3), date(2026, 8, 16), importance="high", today=TODAY)
    payrolls, cpi = high.events
    assert (payrolls.name, payrolls.importance, payrolls.actual, payrolls.previous, payrolls.unit) == ("Employment Situation", "high", 200.0, 100.0, "K jobs")
    # A release still to come has no value yet.
    assert cpi.date == date(2026, 8, 12) and cpi.actual is None and cpi.link.endswith("rid=10")
    everything = calendar.calendar(date(2026, 8, 3), date(2026, 8, 16), importance="all", today=TODAY)
    assert [event.importance for event in everything.events] == ["high", "high", "normal"]
    # The calendar and the vintage are cached.
    assert sum(1 for url, _ in runtime.calls if url.endswith("series/observations")) == 1
    assert sum(1 for url, _ in runtime.calls if url.endswith("releases/dates")) == 1
    assert runtime.calls[0][1]["api_key"] == "key"


def test_the_api_says_when_fred_is_not_set_up_and_bounds_the_window() -> None:
    calendar = FredCalendar(key=lambda: "", runtime=Runtime())
    app = FastAPI()
    app.include_router(create_trading_economic_calendar_router(lambda: calendar, today=lambda: TODAY))
    client = TestClient(app)
    body = client.get("/api/trading/economic-calendar").json()
    assert body["configured"] is False and body["start"] == "2026-08-10" and body["end"] == "2026-08-23"
    assert client.get("/api/trading/economic-calendar", params={"start": "2026-01-01", "end": "2026-12-31"}).status_code == 422


def test_the_fred_key_status_never_returns_the_key(monkeypatch) -> None:
    monkeypatch.setattr(market_data_api, "load_trading_provider_secrets", lambda: {"fred": {"api_key": "fake-fred-key-1234"}})
    monkeypatch.setattr(market_data_api, "trading_provider_credential_sources", lambda provider: {"api_key": "os_protected_store"})
    app = FastAPI()
    app.include_router(market_data_api.create_trading_market_data_router())
    body = TestClient(app).get("/api/trading/market-data/providers/fred/credentials").json()
    assert body["configured"] is True and body["api_key_masked"] == "***1234" and "fake-fred" not in str(body)
