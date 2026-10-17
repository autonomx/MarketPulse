"""Macro (TVP-10.5): the Treasury yield curve and FRED economic series as chart symbols."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import bindings_for_instrument, clear_dynamic_catalog_cache, instrument_by_id
from app.apps.trading.instrument_catalog_service import ProviderBackedInstrumentCatalog
from app.apps.trading.macro import TreasuryYieldCurves, create_trading_macro_router, parse_curve_csv, tenor_years
from app.apps.trading.models import AssetClass
from app.apps.trading.providers.fred import MISSING_KEY, FredSeriesProvider, observation_rows
from app.apps.trading.providers.registry import ProviderRegistry

CSV_2026 = """Date,"1 Mo","1.5 Month","3 Mo","2 Yr","10 Yr","30 Yr"
10/08/2026,4.14,4.14,4.23,4.75,5.22,5.60
10/01/2026,4.10,4.11,4.20,4.70,5.10,5.50
09/08/2026,4.00,,4.10,4.60,5.00,5.40
01/02/2026,3.72,3.71,3.65,3.47,4.19,4.86
"""
# The year before has no 1.5-month column: its days align by tenor, not by position.
CSV_2025 = """Date,"1 Mo","3 Mo","2 Yr","10 Yr","30 Yr"
10/08/2025,4.20,4.00,3.60,4.10,4.70
"""


def test_tenors_and_rows_parse_by_maturity() -> None:
    assert tenor_years("1 Mo") == pytest.approx(1 / 12) and tenor_years("1.5 Month") == pytest.approx(0.125) and tenor_years("30 Yr") == 30
    assert tenor_years("Date") is None
    tenors, days = parse_curve_csv(CSV_2026)
    assert tenors == ["1 Mo", "1.5 Month", "3 Mo", "2 Yr", "10 Yr", "30 Yr"]
    assert days[date(2026, 9, 8)]["1.5 Month"] is None and days[date(2026, 10, 8)]["10 Yr"] == 5.22


def curves(calls: list[int] | None = None) -> TreasuryYieldCurves:
    def fetch(year: int) -> str:
        if calls is not None:
            calls.append(year)
        return {2026: CSV_2026, 2025: CSV_2025}.get(year, 'Date,"1 Mo"\n')

    return TreasuryYieldCurves(fetch=fetch, clock=lambda: 0.0)


def test_the_curve_compares_a_week_a_month_and_a_year_earlier() -> None:
    calls: list[int] = []
    source = curves(calls)
    curve = source.curve(None, date(2026, 10, 9))
    assert [(line.label, line.date) for line in curve.curves] == [
        ("Latest", date(2026, 10, 8)), ("1 week earlier", date(2026, 10, 1)), ("1 month earlier", date(2026, 9, 8)), ("1 year earlier", date(2025, 10, 8)),
    ]
    assert curve.curves[-1].yields == [4.20, None, 4.00, 3.60, 4.10, 4.70]
    assert curve.spreads == {"10Y-2Y": 0.47, "10Y-3M": 0.99}
    assert curve.years[0] == pytest.approx(0.0833)
    source.curve(date(2026, 10, 1), date(2026, 10, 9))
    assert calls == [2026, 2025]  # each year read once while fresh


def test_the_api_serves_the_curve() -> None:
    app = FastAPI()
    app.include_router(create_trading_macro_router(lambda: curves(), today=lambda: date(2026, 10, 9)))
    client = TestClient(app)
    body = client.get("/api/trading/macro/yield-curve", params={"on": "2026-10-05"}).json()
    assert body["curves"][0] == {"label": "Selected", "date": "2026-10-01", "yields": [4.10, 4.11, 4.20, 4.70, 5.10, 5.50]}
    assert client.get("/api/trading/macro/yield-curve", params={"on": "2026-10-10"}).status_code == 422
    assert client.get("/api/trading/macro/yield-curve", params={"on": "2024-01-02"}).status_code == 404


class Runtime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("series/search"):
            return SimpleNamespace(json=lambda: {"seriess": [{"id": "cpiaucsl", "title": "CPI for All Urban Consumers"}, {"id": "CUSR0000SA0L1E", "title": "Core"}]})
        return SimpleNamespace(json=lambda: {"observations": [
            {"date": "2026-08-01", "value": "320.5"}, {"date": "2026-06-01", "value": "319.0"}, {"date": "2026-07-01", "value": "."}, {"date": "2026-09-01", "value": "321.25"},
        ]})


def test_a_fred_series_charts_as_daily_bars_at_its_observations() -> None:
    clear_dynamic_catalog_cache()
    runtime = Runtime()
    provider = FredSeriesProvider(cache=TradingMarketDataCache(), runtime=runtime, key=lambda: "k")
    instrument = instrument_by_id("economic:FRED:CPIAUCSL")
    assert instrument is not None and instrument.asset_class is AssetClass.ECONOMIC and instrument.name == "Consumer Price Index (CPI), all items"
    response = provider.get_bars("economic:FRED:CPIAUCSL", "1d", 2)
    assert [(bar.start_time.date().isoformat(), bar.open, bar.close) for bar in response.bars] == [
        ("2026-08-01", Decimal("319.0"), Decimal("320.5")), ("2026-09-01", Decimal("320.5"), Decimal("321.25")),
    ]
    assert response.bars[1].high == Decimal("321.25") and response.bars[1].low == Decimal("320.5") and not response.provenance.history_complete
    assert runtime.calls[0][1] == {"series_id": "CPIAUCSL", "api_key": "k", "file_type": "json"}
    provider.get_quote("economic:FRED:CPIAUCSL")
    assert len(runtime.calls) == 1  # observations cached
    assert observation_rows({"observations": [{"date": "x", "value": "1"}]}) == []
    with pytest.raises(ValueError, match="FRED API key"):
        FredSeriesProvider(cache=TradingMarketDataCache(), runtime=runtime, key=lambda: "").get_bars("economic:FRED:UNRATE", "1d", 10)
    assert MISSING_KEY.startswith("Add a FRED API key")


def test_economic_series_are_never_traded() -> None:
    registry = ProviderRegistry(factories={"fred": lambda: FredSeriesProvider(cache=TradingMarketDataCache(), runtime=Runtime(), key=lambda: "k")})
    assert bindings_for_instrument("economic:FRED:UNRATE")[0].provider == "fred"
    with pytest.raises(ValueError, match="never traded"):
        registry.resolve_execution_binding("economic:FRED:UNRATE")
    with pytest.raises(ValueError, match="never traded"):
        registry.execution_observation("economic:FRED:UNRATE")
    assert instrument_by_id("economic:FRED:bad id") is None


def test_symbol_search_finds_economic_series_by_id_or_title() -> None:
    clear_dynamic_catalog_cache()
    searched: list[str] = []
    catalog = ProviderBackedInstrumentCatalog(fred_search=lambda query: searched.append(query) or [("CPIAUCSL", "CPI for All Urban Consumers")])
    catalog._search_binance = lambda query: []  # type: ignore[method-assign]
    catalog._search_yahoo = lambda query: []  # type: ignore[method-assign]
    found = {item.instrument_id: item.name for item in catalog.search("unemployment")}
    assert found["economic:FRED:UNRATE"] == "Unemployment rate"
    by_id = {item.instrument_id for item in catalog.search("DGS1")}
    assert {"economic:FRED:DGS1", "economic:FRED:DGS10", "economic:FRED:DGS1MO"} <= by_id
    assert searched == ["UNEMPLOYMENT", "DGS1"]
    catalog.search("DGS1")
    assert searched == ["UNEMPLOYMENT", "DGS1"]  # cached per query
