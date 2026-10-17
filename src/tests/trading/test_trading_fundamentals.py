"""Financial statements and ratios from SEC company facts (TVP-10.2)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.company_profiles import CompanyProfile
from app.apps.trading.fundamentals import FinancialsService, create_trading_fundamentals_router, normalise_company_facts, ratios

NOW = datetime(2026, 8, 10, tzinfo=timezone.utc)


def fact(start, end, val, form="10-Q", fp="Q1", filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "form": form, "fp": fp, "filed": filed} if start else {"end": end, "val": val, "form": form, "fp": fp, "filed": filed}


PAYLOAD = {"facts": {"us-gaap": {
    # Revenue: quarters reported directly for Q1-Q3, the year in the 10-K (Q4 = year - Q1..Q3).
    "Revenues": {"units": {"USD": [
        fact("2025-01-01", "2025-03-31", 100), fact("2025-04-01", "2025-06-30", 110), fact("2025-07-01", "2025-09-30", 120),
        fact("2025-01-01", "2025-12-31", 460, "10-K", "FY", "2026-02-01"),
        fact("2025-01-01", "2025-06-30", 210),  # a year-to-date value as well
        fact("2026-01-01", "2026-03-31", 125), fact("2025-01-01", "2025-03-31", 999, "8-K"),  # other forms are ignored
    ]}},
    # A later concept fills only periods the first lacks.
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [fact("2024-01-01", "2024-12-31", 400, "10-K", "FY"), fact("2025-01-01", "2025-03-31", 5)]}},
    # Cash flow only year to date: quarters are the differences.
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
        fact("2025-01-01", "2025-03-31", 30), fact("2025-01-01", "2025-06-30", 70), fact("2025-01-01", "2025-09-30", 100),
        fact("2025-01-01", "2025-12-31", 150, "10-K", "FY"),
    ]}},
    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [fact("2025-01-01", "2025-12-31", 40, "10-K", "FY")]}},
    "NetIncomeLoss": {"units": {"USD": [fact("2025-01-01", "2025-03-31", 10), fact("2025-04-01", "2025-06-30", 11), fact("2025-07-01", "2025-09-30", 12), fact("2025-10-01", "2025-12-31", 13, "10-K", "FY")]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [fact("2025-01-01", "2025-03-31", 1.0), fact("2025-04-01", "2025-06-30", 1.1), fact("2025-07-01", "2025-09-30", 1.2), fact("2025-10-01", "2025-12-31", 1.3, "10-K", "FY")]}},
    # The year-end balance restated by a later 10-Q still counts as the year end.
    "StockholdersEquity": {"units": {"USD": [fact(None, "2025-12-31", 500, "10-Q", "Q1", "2026-05-01"), fact(None, "2025-09-30", 480)]}},
}}}


def test_statements_take_quarters_years_and_year_ends() -> None:
    statements = normalise_company_facts(PAYLOAD)
    income = {row["end"]: row["values"] for row in statements["quarterly"]["income"]}
    assert income["2025-03-31"]["revenue"] == 100 and income["2025-12-31"]["revenue"] == 130 and income["2026-03-31"]["revenue"] == 125
    annual_income = {row["end"]: row["values"]["revenue"] for row in statements["annual"]["income"]}
    assert annual_income == {"2024-12-31": 400, "2025-12-31": 460}
    cash = {row["end"]: row["values"]["operating_cash_flow"] for row in statements["quarterly"]["cash_flow"]}
    assert cash == {"2025-03-31": 30, "2025-06-30": 40, "2025-09-30": 30, "2025-12-31": 50}
    annual_cash = statements["annual"]["cash_flow"][-1]["values"]
    assert annual_cash["free_cash_flow"] == 110
    assert statements["annual"]["balance"] == [{"end": "2025-12-31", "values": {"equity": 500}}]


def test_ratios_use_the_trailing_twelve_months() -> None:
    statements = normalise_company_facts(PAYLOAD)
    values = ratios(statements, price=46.0, shares=10.0)
    assert values["revenue_ttm"] == 485  # Q2 2025 .. Q1 2026: 110 + 120 + 130 + 125
    assert values["eps_ttm"] == pytest.approx(4.6)
    assert values["pe_ratio"] == pytest.approx(10.0)
    assert values["market_cap"] == 460 and values["pb_ratio"] == pytest.approx(0.92)
    assert values["net_margin"] is not None and values["debt_to_equity"] is None
    assert ratios(statements, price=None, shares=None)["pe_ratio"] is None


class Profiles:
    def ensure(self, tickers, max_new=1):
        return {"AAPL": CompanyProfile("AAPL", "0000320193", "Apple Inc.", "3571", "Technology", "Computer Hardware", Decimal(10), None, NOW)}


class Source:
    calls = 0

    def _json(self, url):
        Source.calls += 1
        assert url.endswith("CIK0000320193.json")
        return PAYLOAD


class Repository:
    def __init__(self):
        self.rows = {}

    def get(self, cik):
        return self.rows.get(cik)

    def save(self, cik, statements):
        self.rows[cik] = (statements, NOW)


def test_the_api_serves_a_companys_statements_cached_for_a_day() -> None:
    repository = Repository()
    service = FinancialsService(repository_factory=lambda: repository, source_factory=Source, profiles=Profiles, price_of=lambda instrument: 46.0, clock=lambda: NOW)
    app = FastAPI()
    app.include_router(create_trading_fundamentals_router(lambda: service))
    client = TestClient(app)
    body = client.get("/api/trading/fundamentals", params={"instrument_id": "equity:NASDAQ:AAPL"}).json()
    assert body["name"] == "Apple Inc." and body["ratios"]["pe_ratio"] == pytest.approx(10.0) and body["labels"]["free_cash_flow"] == "Free cash flow"
    assert body["quarterly"]["income"][-1]["end"] == "2026-03-31"
    client.get("/api/trading/fundamentals", params={"instrument_id": "equity:NASDAQ:AAPL"})
    assert Source.calls == 1
    assert client.get("/api/trading/fundamentals", params={"instrument_id": "crypto:BINANCE:spot:BTC-USDT"}).status_code == 422
