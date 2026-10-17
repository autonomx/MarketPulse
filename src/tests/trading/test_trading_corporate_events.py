"""Earnings, dividends and splits (TVP-10.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.company_profiles import CompanyProfile
from app.apps.trading.corporate_events import (
    CorporateEvent,
    CorporateEventsService,
    actions_from_alpaca,
    create_trading_corporate_events_router,
    earnings_from_submissions,
    estimated_next_earnings,
    fiscal_period,
    session_of,
)

NOW = datetime(2026, 10, 9, 14, tzinfo=timezone.utc)


def submissions(rows):
    keys = ("form", "items", "acceptanceDateTime", "accessionNumber", "primaryDocument")
    return {
        "cik": "0000320193", "fiscalYearEnd": "0926",
        "filings": {"recent": {key: [row[index] for row in rows] for index, key in enumerate(keys)}},
    }


SUBMISSIONS = submissions([
    ("8-K", "2.02,9.01", "2026-07-31T00:30:28.000Z", "0000320193-26-000018", "aapl-20260730.htm"),  # 20:30 New York
    ("8-K", "2.02,9.01", "2026-07-31T00:40:00.000Z", "0000320193-26-000019", "again.htm"),  # same day: once
    ("8-K/A", "2.02", "2026-06-01T12:00:00.000Z", "0000320193-26-000017", "amended.htm"),  # amendments: no
    ("8-K", "5.02", "2026-05-20T12:00:00.000Z", "0000320193-26-000016", "officer.htm"),  # other items: no
    ("8-K", "2.02,9.01", "2026-05-01T11:00:00.000Z", "0000320193-26-000011", "q2.htm"),  # 07:00 New York
    ("8-K", "2.02", "2026-01-29T17:00:00.000Z", "0000320193-26-000005", "q1.htm"),  # 12:00 New York (EST)
    ("8-K", "12.02", "2025-12-01T17:00:00.000Z", "0000320193-25-000090", "other.htm"),  # not item 2.02
    ("8-K", "2.02", "2025-10-30T20:30:00.000Z", "0000320193-25-000077", "q4.htm"),
])


def test_earnings_come_from_8k_item_2_02_with_their_session_and_quarter() -> None:
    events = earnings_from_submissions(SUBMISSIONS)
    assert [(event.date, event.timing, event.fiscal_period) for event in events] == [
        (date(2025, 10, 30), "after_close", "Q4 FY2025"),
        (date(2026, 1, 29), "during_market", "Q1 FY2026"),
        (date(2026, 5, 1), "before_open", "Q2 FY2026"),
        (date(2026, 7, 30), "after_close", "Q3 FY2026"),
    ]
    assert events[-1].link == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm"


def test_sessions_and_fiscal_quarters() -> None:
    assert session_of(datetime(2026, 3, 10, 13, 29, tzinfo=timezone.utc)) == (date(2026, 3, 10), "before_open")  # 09:29 EDT
    assert session_of(datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)) == (date(2026, 3, 10), "after_close")
    assert session_of(datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)) == (date(2026, 11, 27), "after_close")  # 14:00 on an early close
    assert fiscal_period(date(2026, 2, 5), "1231") == "Q4 FY2025"
    assert fiscal_period(date(2026, 3, 31), "1231") == "Q4 FY2025"  # March isn't over on its last day's report
    assert fiscal_period(date(2026, 5, 20), "0201") == "Q1 FY2027"  # a year ending in early February ends in January
    assert fiscal_period(date(2026, 5, 20), None) is None


def test_the_next_report_is_estimated_a_year_after_its_match() -> None:
    events = earnings_from_submissions(SUBMISSIONS)
    estimate = estimated_next_earnings(events, date(2026, 10, 9), "0926")
    assert estimate is not None and estimate.estimated
    assert (estimate.date, estimate.timing, estimate.fiscal_period) == (date(2026, 10, 29), "after_close", "Q4 FY2026")
    # Nothing a year back to match: no estimate.
    assert estimated_next_earnings(events[-1:], date(2026, 10, 9), "0926") is None


def test_dividends_and_splits_come_from_alpaca_corporate_actions() -> None:
    actions = actions_from_alpaca({"corporate_actions": {
        "cash_dividends": [
            {"symbol": "AAPL", "ex_date": "2026-08-11", "rate": 0.26, "special": False, "record_date": "2026-08-11", "payable_date": "2026-08-14"},
            {"symbol": "XYZ", "ex_date": None, "rate": 1},
        ],
        "forward_splits": [{"symbol": "AAPL", "ex_date": "2020-08-31", "old_rate": 1, "new_rate": 4}],
        "reverse_splits": [{"symbol": "XYZ", "ex_date": "2026-01-05", "old_rate": 10, "new_rate": 1}],
    }})
    dividend, split = actions["AAPL"]
    assert (dividend.kind, dividend.date, dividend.amount, dividend.payable_date) == ("dividend", date(2026, 8, 11), 0.26, date(2026, 8, 14))
    assert (split.kind, split.split_from, split.split_to) == ("split", 1.0, 4.0)
    assert [(event.kind, event.split_from, event.split_to) for event in actions["XYZ"]] == [("split", 10.0, 1.0)]


class Profiles:
    def ensure(self, tickers, max_new=1):
        return {"AAPL": CompanyProfile("AAPL", "0000320193", "Apple Inc.", "3571", "Technology", "Computer Hardware", Decimal(10), None, NOW)}


class Sec:
    calls = 0

    def submissions(self, cik):
        Sec.calls += 1
        assert cik == "0000320193"
        return SUBMISSIONS


class Alpaca:
    requests: list[list[str]] = []

    def actions(self, tickers, start, end):
        Alpaca.requests.append(list(tickers))
        return {"AAPL": [CorporateEvent(kind="dividend", date=date(2026, 8, 11), amount=0.26)], "SPY": [CorporateEvent(kind="dividend", date=date(2026, 9, 19), amount=1.8)]}


class Repository:
    def __init__(self):
        self.rows = {}

    def get(self, tickers):
        return {ticker: self.rows[ticker] for ticker in tickers if ticker in self.rows}

    def save(self, ticker, events):
        self.rows[ticker] = (events, NOW)


def client_for(service) -> TestClient:
    app = FastAPI()
    app.include_router(create_trading_corporate_events_router(lambda: service))
    return TestClient(app)


def test_a_stocks_events_are_served_and_cached_for_a_day() -> None:
    Sec.calls, Alpaca.requests = 0, []
    repository = Repository()
    client = client_for(CorporateEventsService(repository_factory=lambda: repository, sec_factory=Sec, alpaca_factory=Alpaca, profiles=Profiles, clock=lambda: NOW))
    body = client.get("/api/trading/corporate-events", params={"instrument_id": "equity:NASDAQ:AAPL"}).json()
    kinds = [(event["kind"], event["date"], event["estimated"]) for event in body["events"]]
    assert ("dividend", "2026-08-11", False) in kinds and ("earnings", "2026-07-30", False) in kinds and ("earnings", "2026-10-29", True) in kinds
    assert [event["date"] for event in body["events"]] == sorted(event["date"] for event in body["events"])
    client.get("/api/trading/corporate-events", params={"instrument_id": "equity:NASDAQ:AAPL"})
    assert Sec.calls == 1 and Alpaca.requests == [["AAPL"]]
    assert client.get("/api/trading/corporate-events", params={"instrument_id": "crypto:BINANCE:spot:BTC-USDT"}).status_code == 422


def test_the_calendar_lists_a_window_for_many_stocks_and_fills_over_calls() -> None:
    Sec.calls, Alpaca.requests = 0, []
    repository = Repository()
    service = CorporateEventsService(repository_factory=lambda: repository, sec_factory=Sec, alpaca_factory=Alpaca, profiles=Profiles, clock=lambda: NOW)
    client = client_for(service)
    params = {"start": "2026-07-27", "end": "2026-09-30", "instrument_id": ["equity:NASDAQ:AAPL", "equity:ARCA:SPY", "crypto:BINANCE:spot:BTC-USDT"]}
    body = client.get("/api/trading/corporate-events/calendar", params=params).json()
    assert [(event["ticker"], event["kind"], event["date"]) for event in body["events"]] == [
        ("AAPL", "earnings", "2026-07-30"), ("AAPL", "dividend", "2026-08-11"), ("SPY", "dividend", "2026-09-19"),
    ]
    assert body["events"][0]["instrument_id"] == "equity:NASDAQ:AAPL" and body["pending"] == []
    only_dividends = client.get("/api/trading/corporate-events/calendar", params={**params, "kind": ["dividend"]}).json()
    assert {event["kind"] for event in only_dividends["events"]} == {"dividend"}
    # Beyond the per-call fetch budget, the rest is pending until a later call.
    found, pending = CorporateEventsService(repository_factory=Repository, sec_factory=Sec, alpaca_factory=Alpaca, profiles=Profiles, clock=lambda: NOW).events(["AAPL", "SPY"], max_fetches=1)
    assert set(found) == {"AAPL"} and pending == ["SPY"]
    assert client.get("/api/trading/corporate-events/calendar", params={**params, "end": "2027-01-01"}).status_code == 422


class FailingAlpaca:
    def actions(self, tickers, start, end):
        raise RuntimeError("Alpaca is down")


def test_events_a_source_failed_to_give_are_served_but_not_cached() -> None:
    Sec.calls = 0
    repository = Repository()
    service = CorporateEventsService(repository_factory=lambda: repository, sec_factory=Sec, alpaca_factory=FailingAlpaca, profiles=Profiles, clock=lambda: NOW)
    events = service.company("equity:NASDAQ:AAPL").events
    assert events and {event.kind for event in events} == {"earnings"}
    assert repository.rows == {}
    service.company("equity:NASDAQ:AAPL")
    assert Sec.calls == 2  # asked again rather than cached without dividends
