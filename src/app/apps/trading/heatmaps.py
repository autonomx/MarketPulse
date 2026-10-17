"""Heatmaps (TVP-9.3): the day's moves of the most traded US stocks, grouped by sector, and of crypto pairs.

- **Stocks:** Alpaca snapshots of the active NYSE and Nasdaq stocks give each one's change since the previous close and
  its dollar volume; the ``limit`` most traded are shown, grouped by SEC sector (``company_profiles.py``) and sized by
  market capitalisation (SEC shares outstanding times the last price) or by dollar volume. Profiles the server has not
  fetched yet are filled a few per request and show as "Unclassified" until then.
- **Crypto:** Binance's 24-hour tickers for USDT pairs, sized by quote volume.

Results are cached for a few minutes; the snapshot requests spend the Alpaca budget, the tickers Binance's.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel


if TYPE_CHECKING:
    from .company_profiles import CompanyProfiles

CACHE_SECONDS = 300.0
SNAPSHOT_CHUNK = 200
STABLECOINS = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USDE", "PYUSD"}


class HeatmapTile(BaseModel):
    instrument_id: str
    symbol: str
    name: str = ""
    group: str
    industry: str = ""
    change_percent: float
    size: float
    price: float
    market_cap: float | None = None
    dollar_volume: float


class Heatmap(BaseModel):
    market: Literal["stocks", "crypto"]
    size_by: str
    tiles: list[HeatmapTile]
    # Stocks whose sector isn't known yet (filled on later requests).
    unclassified: int = 0
    as_of: float


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


class AlpacaSnapshotSource:
    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("alpaca_heatmap_snapshots", max_concurrency=2)
        self.runtime = runtime

    def universe(self) -> dict[str, str]:
        from .breadth import AlpacaBreadthSource

        return AlpacaBreadthSource(self.runtime).universe()

    def snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        from app.config.env import env_str

        from .providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers

        data_url = (env_str("OMNIX_ALPACA_DATA_URL", "") or ALPACA_DATA_URL).rstrip("/")
        feed = (env_str("OMNIX_TRADING_BREADTH_FEED", "sip") or "sip").strip().lower()
        output: dict[str, dict[str, Any]] = {}
        headers = alpaca_iex_auth_headers()
        for start in range(0, len(symbols), SNAPSHOT_CHUNK):
            chunk = symbols[start:start + SNAPSHOT_CHUNK]
            payload = self.runtime.get(f"{data_url}/v2/stocks/snapshots", params={"symbols": ",".join(chunk), "feed": feed}, headers=headers, timeout=30).json()
            if isinstance(payload, dict):
                output.update({str(symbol).upper(): value for symbol, value in payload.items() if isinstance(value, dict)})
        return output


class BinanceTickerSource:
    def __init__(self, runtime: Any = None) -> None:
        if runtime is None:
            from .providers.http_runtime import ProviderHttpRuntime

            runtime = ProviderHttpRuntime("binance", max_concurrency=1)
        self.runtime = runtime

    def tickers(self) -> list[dict[str, Any]]:
        payload = self.runtime.get("https://api.binance.com/api/v3/ticker/24hr", timeout=20).json()
        return payload if isinstance(payload, list) else []


def stock_tiles(universe: dict[str, str], snapshots: dict[str, dict[str, Any]], limit: int) -> list[HeatmapTile]:
    """The ``limit`` stocks with the most dollar volume today, with their change since the previous close."""
    tiles: list[HeatmapTile] = []
    for symbol, snapshot in snapshots.items():
        daily = snapshot.get("dailyBar") or {}
        previous = snapshot.get("prevDailyBar") or {}
        price = _number((snapshot.get("latestTrade") or {}).get("p")) or _number(daily.get("c"))
        previous_close = _number(previous.get("c"))
        volume = _number(daily.get("v")) or 0.0
        if not price or not previous_close or symbol not in universe:
            continue
        tiles.append(HeatmapTile(
            instrument_id=f"equity:{universe[symbol]}:{symbol}", symbol=symbol, group="Unclassified",
            change_percent=(price / previous_close - 1) * 100, size=price * volume, price=price, dollar_volume=price * volume,
        ))
    tiles.sort(key=lambda tile: tile.dollar_volume, reverse=True)
    return tiles[:limit]


def crypto_tiles(tickers: list[dict[str, Any]], limit: int) -> list[HeatmapTile]:
    tiles: list[HeatmapTile] = []
    for ticker in tickers:
        symbol = str(ticker.get("symbol", ""))
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        if base in STABLECOINS or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        price = _number(ticker.get("lastPrice"))
        change = _number(ticker.get("priceChangePercent"))
        quote_volume = _number(ticker.get("quoteVolume")) or 0.0
        if not price or change is None or quote_volume <= 0:
            continue
        tiles.append(HeatmapTile(
            instrument_id=f"crypto:BINANCE:spot:{base}-USDT", symbol=base, group="Crypto", change_percent=change,
            size=quote_volume, price=price, dollar_volume=quote_volume,
        ))
    tiles.sort(key=lambda tile: tile.dollar_volume, reverse=True)
    return tiles[:limit]


def _company_profiles() -> CompanyProfiles:
    from .company_profiles import default_company_profiles

    return default_company_profiles()


class HeatmapService:
    def __init__(
        self,
        *,
        stocks_source: Callable[[], Any] = AlpacaSnapshotSource,
        crypto_source: Callable[[], Any] = BinanceTickerSource,
        profiles: Callable[[], CompanyProfiles] = _company_profiles,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.stocks_source = stocks_source
        self.crypto_source = crypto_source
        self.profiles = profiles
        self.clock = clock
        self._cache: dict[tuple[Any, ...], tuple[float, Heatmap]] = {}
        self._universe: tuple[float, dict[str, str]] | None = None
        self._guard = threading.Lock()

    def _cached(self, key: tuple[Any, ...], build: Callable[[], Heatmap]) -> Heatmap:
        with self._guard:
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < CACHE_SECONDS:
                return hit[1]
        heatmap = build()
        with self._guard:
            self._cache[key] = (self.clock(), heatmap)
        return heatmap

    def stocks(self, *, size_by: str = "market_cap", limit: int = 500) -> Heatmap:
        def build() -> Heatmap:
            source = self.stocks_source()
            if self._universe is None or self.clock() - self._universe[0] > 86_400:
                self._universe = (self.clock(), source.universe())
            universe = self._universe[1]
            tiles = stock_tiles(universe, source.snapshots(sorted(universe)), limit)
            profiles = self.profiles().ensure([tile.symbol for tile in tiles])
            unclassified = 0
            for tile in tiles:
                profile = profiles.get(tile.symbol)
                if profile and profile.sector:
                    tile.group, tile.industry, tile.name = profile.sector, profile.industry or "", profile.name
                else:
                    unclassified += 1
                if profile and profile.shares_outstanding:
                    tile.market_cap = float(Decimal(profile.shares_outstanding) * Decimal(str(tile.price)))
                tile.size = tile.market_cap if size_by == "market_cap" and tile.market_cap else tile.dollar_volume
            return Heatmap(market="stocks", size_by=size_by, tiles=tiles, unclassified=unclassified, as_of=self.clock())

        return self._cached(("stocks", size_by, limit), build)

    def crypto(self, *, limit: int = 100) -> Heatmap:
        return self._cached(("crypto", limit), lambda: Heatmap(market="crypto", size_by="volume", tiles=crypto_tiles(self.crypto_source().tickers(), limit), as_of=self.clock()))


_service: HeatmapService | None = None


def default_heatmap_service() -> HeatmapService:
    global _service
    if _service is None:
        _service = HeatmapService()
    return _service


def create_trading_heatmaps_router(service_factory: Callable[[], HeatmapService] = default_heatmap_service) -> APIRouter:
    router = APIRouter(prefix="/api/trading/heatmaps", tags=["trading-heatmaps"])

    @router.get("/stocks", response_model=Heatmap)
    def stocks(size_by: Literal["market_cap", "dollar_volume"] = "market_cap", limit: int = Query(default=500, ge=10, le=1_000)) -> Heatmap:
        try:
            return service_factory().stocks(size_by=size_by, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"code": "heatmap_data_failed", "message": "The stock heatmap's market data could not load."}) from exc

    @router.get("/crypto", response_model=Heatmap)
    def crypto(limit: int = Query(default=100, ge=10, le=300)) -> Heatmap:
        try:
            return service_factory().crypto(limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"code": "heatmap_data_failed", "message": "The crypto heatmap's market data could not load."}) from exc

    return router


__all__ = ["Heatmap", "HeatmapService", "HeatmapTile", "create_trading_heatmaps_router", "crypto_tiles", "stock_tiles"]
