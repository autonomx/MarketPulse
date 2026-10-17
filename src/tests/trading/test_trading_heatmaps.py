"""Heatmaps and SEC company profiles (TVP-9.3)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.company_profiles import CompanyProfile, CompanyProfiles, latest_frame_periods, sector_of
from app.apps.trading.heatmaps import HeatmapService, create_trading_heatmaps_router, crypto_tiles, stock_tiles

NOW = datetime(2026, 8, 10, 15, tzinfo=timezone.utc)


def snapshot(price: float, previous: float, volume: float) -> dict:
    return {"latestTrade": {"p": price}, "dailyBar": {"c": price, "v": volume}, "prevDailyBar": {"c": previous}}


UNIVERSE = {"AAPL": "NASDAQ", "XOM": "NYSE", "TINY": "NYSE"}
SNAPSHOTS = {"AAPL": snapshot(110, 100, 1_000_000), "XOM": snapshot(95, 100, 500_000), "TINY": snapshot(2, 2, 10), "OTC": snapshot(5, 4, 1e9)}


def test_stocks_are_the_most_traded_with_their_change() -> None:
    tiles = stock_tiles(UNIVERSE, SNAPSHOTS, 2)
    assert [(tile.symbol, tile.instrument_id, round(tile.change_percent, 6)) for tile in tiles] == [("AAPL", "equity:NASDAQ:AAPL", 10.0), ("XOM", "equity:NYSE:XOM", -5.0)]
    assert tiles[0].dollar_volume == 110_000_000


def test_crypto_tiles_skip_stablecoins_and_leveraged_tokens() -> None:
    tickers = [
        {"symbol": "BTCUSDT", "lastPrice": "60000", "priceChangePercent": "2.5", "quoteVolume": "9e9"},
        {"symbol": "USDCUSDT", "lastPrice": "1", "priceChangePercent": "0", "quoteVolume": "8e9"},
        {"symbol": "ETHUPUSDT", "lastPrice": "5", "priceChangePercent": "9", "quoteVolume": "1e8"},
        {"symbol": "ETHBTC", "lastPrice": "0.05", "priceChangePercent": "1", "quoteVolume": "1e3"},
        {"symbol": "SOLUSDT", "lastPrice": "150", "priceChangePercent": "-3", "quoteVolume": "2e9"},
    ]
    assert [(tile.instrument_id, tile.change_percent) for tile in crypto_tiles(tickers, 10)] == [("crypto:BINANCE:spot:BTC-USDT", 2.5), ("crypto:BINANCE:spot:SOL-USDT", -3.0)]


def test_sic_codes_map_to_sectors_and_frames_go_back_by_quarter() -> None:
    assert sector_of("3674") == ("Technology", "Semiconductors") and sector_of(2834)[0] == "Health Care" and sector_of(None) == ("Other", "")
    assert latest_frame_periods(date(2026, 8, 10)) == ["CY2026Q2I", "CY2026Q1I", "CY2025Q4I", "CY2025Q3I"]


class Repository:
    def __init__(self) -> None:
        self.rows: dict[str, CompanyProfile] = {}
        self.shares_at: datetime | None = None

    def get(self, tickers):
        return {ticker: self.rows[ticker] for ticker in tickers if ticker in self.rows}

    def save_profile(self, ticker, cik, name, sic, sic_description):
        sector, industry = sector_of(sic)
        old = self.rows.get(ticker)
        self.rows[ticker] = CompanyProfile(ticker, cik, name, sic, sector, industry, old.shares_outstanding if old else None, None, NOW)

    def save_shares(self, shares_by_cik, tickers_by_cik):
        for cik, (shares, as_of) in shares_by_cik.items():
            for ticker, name in tickers_by_cik.get(cik, []):
                old = self.rows.get(ticker)
                self.rows[ticker] = CompanyProfile(ticker, cik, old.name if old else name, old.sic if old else None, old.sector if old else None,
                                                   old.industry if old else None, shares, as_of, old.profile_fetched_at if old else None)
        self.shares_at = NOW
        return len(shares_by_cik)

    def shares_fetched_at(self):
        return self.shares_at


class Sec:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def tickers(self):
        self.requests.append("tickers")
        return {"AAPL": ("0000320193", "Apple Inc."), "XOM": ("0000034088", "Exxon Mobil")}

    def submissions(self, cik):
        self.requests.append(cik)
        return {"name": {"0000320193": "Apple Inc.", "0000034088": "Exxon Mobil Corp"}[cik], "sic": {"0000320193": "3571", "0000034088": "2911"}[cik]}

    def shares_frame(self, period):
        self.requests.append(period)
        return {"0000320193": (Decimal("15000000000"), date(2026, 6, 30))} if period == "CY2026Q2I" else {}


def test_profiles_are_fetched_once_within_the_limit() -> None:
    repository, sec = Repository(), Sec()
    profiles = CompanyProfiles(lambda: repository, lambda: sec, clock=lambda: NOW)
    first = profiles.ensure(["AAPL", "XOM", "NONE"], max_new=1)
    assert first["AAPL"].sector == "Technology" and "XOM" not in first or first["XOM"].sector is None
    assert first["AAPL"].shares_outstanding == Decimal("15000000000")
    second = profiles.ensure(["AAPL", "XOM"], max_new=5)
    assert second["XOM"].sector == "Energy" and sec.requests.count("0000320193") == 1
    # Shares are read again weekly, not on each call.
    assert sec.requests.count("CY2026Q2I") == 1


def test_the_stock_heatmap_groups_by_sector_and_sizes_by_market_cap() -> None:
    class Source:
        def universe(self):
            return UNIVERSE

        def snapshots(self, symbols):
            return SNAPSHOTS

    class Profiles:
        def ensure(self, tickers):
            return {"AAPL": CompanyProfile("AAPL", "1", "Apple Inc.", "3571", "Technology", "Computer Hardware", Decimal("15000000000"), None, NOW)}

    service = HeatmapService(stocks_source=Source, profiles=Profiles, crypto_source=lambda: None, clock=lambda: 1_000.0)
    heatmap = service.stocks(size_by="market_cap", limit=3)
    aapl, xom, tiny = heatmap.tiles
    assert (aapl.group, aapl.industry, aapl.market_cap, aapl.size) == ("Technology", "Computer Hardware", 1.65e12, 1.65e12)
    # Without a profile yet: unclassified, sized by dollar volume.
    assert (xom.group, xom.size) == ("Unclassified", 47_500_000) and heatmap.unclassified == 2
    app = FastAPI()
    app.include_router(create_trading_heatmaps_router(lambda: service))
    body = TestClient(app).get("/api/trading/heatmaps/stocks", params={"size_by": "market_cap", "limit": 10}).json()
    assert body["tiles"][0]["symbol"] == "AAPL" and body["unclassified"] == 2


def test_heatmaps_are_cached_for_a_few_minutes() -> None:
    calls: list[int] = []

    class Tickers:
        def tickers(self):
            calls.append(1)
            return [{"symbol": "BTCUSDT", "lastPrice": "60000", "priceChangePercent": "1", "quoteVolume": "1e9"}]

    clock = [0.0]
    service = HeatmapService(crypto_source=Tickers, clock=lambda: clock[0])
    service.crypto(limit=10)
    service.crypto(limit=10)
    clock[0] += timedelta(minutes=6).total_seconds()
    service.crypto(limit=10)
    assert len(calls) == 2
