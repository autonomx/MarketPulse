from __future__ import annotations

import threading
import time
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Any

from .catalog import (
    ECONOMIC_SERIES,
    _binding,
    _commodity,
    register_economic_series,
    register_instrument,
    search_instruments,
)
from .models import (
    AssetClass,
    CanonicalInstrument,
    FeedType,
    InstrumentType,
    ProviderBinding,
)
from .providers.http_runtime import ProviderHttpRuntime


BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CATALOG_TTL_SECONDS = 15 * 60
QUERY_CACHE_SIZE = 256
YAHOO_QUOTE_TYPES = {"EQUITY", "ETF", "INDEX"}
YAHOO_COMMODITY_QUOTE_TYPES = {"COMMODITY", "FUTURE"}
YAHOO_EXCHANGES = {
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NCM": "NASDAQ",
    "NYQ": "NYSE",
    "PCX": "ARCA",
    "ASE": "AMEX",
}


def _decimal_or_default(value: Any, default: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
        return parsed if parsed > 0 else Decimal(default)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _price_scale(tick: Decimal) -> int:
    raw_exponent = tick.as_tuple().exponent
    exponent = max(0, -raw_exponent) if isinstance(raw_exponent, int) else 0
    return max(1, 10**min(exponent, 8))


def _crypto_instrument(symbol: dict[str, Any]) -> CanonicalInstrument | None:
    base = str(symbol.get("baseAsset") or "").strip().upper()
    quote = str(symbol.get("quoteAsset") or "").strip().upper()
    venue_symbol = str(symbol.get("symbol") or "").strip().upper()
    if not base or not quote or not venue_symbol:
        return None
    tick = next(
        (
            _decimal_or_default(item.get("tickSize"), "0.00000001")
            for item in symbol.get("filters", [])
            if isinstance(item, dict) and item.get("filterType") == "PRICE_FILTER"
        ),
        Decimal("0.00000001"),
    )
    return CanonicalInstrument(
        instrument_id=f"crypto:BINANCE:spot:{base}-{quote}",
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="BINANCE",
        venue_symbol=f"{base}-{quote}",
        display_symbol=venue_symbol,
        base_currency=base,
        quote_currency=quote,
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=_price_scale(tick),
        minimum_tick=tick,
        status="active",
    )


def _yahoo_instrument(quote: dict[str, Any]) -> CanonicalInstrument | None:
    symbol = str(quote.get("symbol") or "").strip().upper()
    quote_type = str(quote.get("quoteType") or "").strip().upper()
    if not symbol:
        return None
    if quote_type in YAHOO_COMMODITY_QUOTE_TYPES:
        return _commodity(symbol, symbol)
    if quote_type not in YAHOO_QUOTE_TYPES:
        return None
    exchange_code = str(quote.get("exchange") or "YAHOO").strip().upper()
    venue = YAHOO_EXCHANGES.get(exchange_code, exchange_code or "YAHOO")
    instrument_type = InstrumentType.INDEX if quote_type == "INDEX" else InstrumentType.EQUITY
    calendar = "XNAS" if venue == "NASDAQ" else "XNYS" if venue in {"NYSE", "ARCA", "AMEX"} else "24x7"
    timezone = "America/New_York" if calendar != "24x7" else "UTC"
    return CanonicalInstrument(
        instrument_id=f"equity:{venue}:{symbol}",
        asset_class=AssetClass.EQUITY,
        instrument_type=instrument_type,
        venue=venue,
        venue_symbol=symbol,
        display_symbol=symbol,
        base_currency=None,
        quote_currency="USD",
        exchange_timezone=timezone,
        session_calendar=calendar,
        price_scale=100,
        minimum_tick=Decimal("0.01"),
        status="active",
    )


def _equity_instrument(quote: dict[str, Any]) -> CanonicalInstrument | None:
    """Keep equity-only discovery callers from accepting commodity quotes."""
    quote_type = str(quote.get("quoteType") or "").strip().upper()
    return _yahoo_instrument(quote) if quote_type in YAHOO_QUOTE_TYPES else None


def _dynamic_bindings(instrument: CanonicalInstrument) -> tuple[ProviderBinding, ...]:
    if instrument.asset_class is AssetClass.CRYPTO:
        return (
            _binding(
                instrument,
                "binance",
                instrument.display_symbol,
                FeedType.WEBSOCKET_AND_REST,
            ),
        )
    if instrument.asset_class is AssetClass.COMMODITY:
        return (
            _binding(
                instrument,
                "yahoo",
                instrument.venue_symbol,
                FeedType.HISTORICAL_POLLING,
            ),
        )
    return (
        _binding(
            instrument,
            "yahoo",
            instrument.display_symbol,
            FeedType.HISTORICAL_POLLING,
        ),
        _binding(
            instrument,
            "ibkr",
            instrument.display_symbol,
            FeedType.SOCKET,
        ),
        _binding(
            instrument,
            "alpaca_iex",
            instrument.display_symbol,
            FeedType.REST,
        ),
        _binding(
            instrument,
            "stooq",
            f"{instrument.display_symbol}.US",
            FeedType.HISTORICAL_DAILY,
        ),
    )


class ProviderBackedInstrumentCatalog:
    """Searchable instrument metadata backed by public provider catalogs.

    Provider metadata is cached in memory and discovered instruments are registered
    in the process catalog so the normal chart/binding path can consume them.
    """

    def __init__(
        self,
        *,
        binance_runtime: ProviderHttpRuntime | None = None,
        yahoo_runtime: ProviderHttpRuntime | None = None,
        fred_search: Any = None,
        ttl_seconds: float = CATALOG_TTL_SECONDS,
        query_cache_size: int = QUERY_CACHE_SIZE,
    ) -> None:
        self._fred_search = fred_search
        self._fred_query_cache: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
        self.binance_runtime = binance_runtime or ProviderHttpRuntime("binance_catalog", max_concurrency=1)
        self.yahoo_runtime = yahoo_runtime or ProviderHttpRuntime("yahoo_catalog", max_concurrency=2)
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.query_cache_size = max(8, int(query_cache_size))
        self._lock = threading.RLock()
        self._binance_symbols: list[CanonicalInstrument] = []
        self._binance_loaded_at = 0.0
        self._yahoo_query_cache: OrderedDict[str, list[CanonicalInstrument]] = OrderedDict()

    def search(self, query: str = "") -> list[CanonicalInstrument]:
        clean = query.strip().upper()
        static_matches = search_instruments(clean)
        if len(clean) < 2:
            return static_matches

        discovered: list[CanonicalInstrument] = []

        for instrument in self._search_binance(clean):
            register_instrument(instrument, _dynamic_bindings(instrument))
            discovered.append(instrument)
        for instrument in self._search_yahoo(clean):
            register_instrument(instrument, _dynamic_bindings(instrument))
            discovered.append(instrument)
        discovered.extend(self._search_economic(clean))

        unique: dict[str, CanonicalInstrument] = {}
        for instrument in [*static_matches, *discovered]:
            unique.setdefault(instrument.instrument_id, instrument)
        static_ids = {instrument.instrument_id for instrument in static_matches}
        static_results = [instrument for instrument in unique.values() if instrument.instrument_id in static_ids]
        discovered_results = [instrument for instrument in unique.values() if instrument.instrument_id not in static_ids]
        discovered_results.sort(key=lambda instrument: self._discovered_sort_key(instrument, clean))
        return [*static_results, *discovered_results]

    @staticmethod
    def _discovered_sort_key(instrument: CanonicalInstrument, query: str) -> tuple[int, str, str]:
        symbol = instrument.display_symbol.upper()
        rank = 0 if symbol == query else 1 if symbol.startswith(query) else 2
        return rank, symbol, instrument.venue

    def _search_binance(self, query: str) -> list[CanonicalInstrument]:
        with self._lock:
            now = time.monotonic()
            if self._binance_loaded_at <= 0.0 or now - self._binance_loaded_at >= self.ttl_seconds:
                self._refresh_binance()
            return [
                instrument
                for instrument in self._binance_symbols
                if query in instrument.display_symbol
                or query in instrument.venue_symbol
                or query in (instrument.base_currency or "")
            ]

    def _refresh_binance(self) -> None:
        self._binance_loaded_at = time.monotonic()
        try:
            response = self.binance_runtime.get(BINANCE_EXCHANGE_INFO_URL, timeout=10)
            payload = response.json()
        except Exception:
            self._binance_symbols = []
            return
        raw_symbols = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(raw_symbols, list):
            self._binance_symbols = []
            return
        symbols: list[CanonicalInstrument] = []
        for raw in raw_symbols:
            if not isinstance(raw, dict) or raw.get("status") not in {None, "TRADING"}:
                continue
            if raw.get("isSpotTradingAllowed") is False:
                continue
            instrument = _crypto_instrument(raw)
            if instrument is not None:
                symbols.append(instrument)
        self._binance_symbols = symbols

    def _search_yahoo(self, query: str) -> list[CanonicalInstrument]:
        with self._lock:
            cached = self._yahoo_query_cache.get(query)
            if cached is not None:
                self._yahoo_query_cache.move_to_end(query)
                return cached
        try:
            response = self.yahoo_runtime.get(
                YAHOO_SEARCH_URL,
                params={"q": query, "quotesCount": 25, "newsCount": 0},
                headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
                timeout=10,
            )
            payload = response.json()
        except Exception:
            results: list[CanonicalInstrument] = []
        else:
            raw_quotes = payload.get("quotes") if isinstance(payload, dict) else None
            results = []
            if isinstance(raw_quotes, list):
                for raw in raw_quotes:
                    if isinstance(raw, dict):
                        instrument = _yahoo_instrument(raw)
                        if instrument is not None:
                            results.append(instrument)
        with self._lock:
            self._yahoo_query_cache[query] = results
            self._yahoo_query_cache.move_to_end(query)
            while len(self._yahoo_query_cache) > self.query_cache_size:
                self._yahoo_query_cache.popitem(last=False)
        return results


    def _search_economic(self, query: str) -> list[CanonicalInstrument]:
        """FRED series (TVP-10.5): the curated ones whose id or title matches, then FRED's own search when keyed."""
        words = query.lower().split()
        matches = [
            (series_id, title) for series_id, title in ECONOMIC_SERIES.items()
            if query.replace("FRED:", "") in series_id or all(word in title.lower() for word in words)
        ]
        with self._lock:
            cached = self._fred_query_cache.get(query)
        if cached is None:
            try:
                search = self._fred_search or _default_fred_search
                cached = search(query)
            except Exception:
                cached = []
            with self._lock:
                self._fred_query_cache[query] = cached
                while len(self._fred_query_cache) > self.query_cache_size:
                    self._fred_query_cache.popitem(last=False)
        instruments = []
        for series_id, title in dict([*cached, *matches]).items():
            instrument = register_economic_series(series_id, title or None)
            if instrument is not None:
                instruments.append(instrument)
        return instruments


_fred_provider: Any = None


def _default_fred_search(query: str) -> list[tuple[str, str]]:
    global _fred_provider
    if _fred_provider is None:
        from .providers.fred import FredSeriesProvider

        _fred_provider = FredSeriesProvider()
    return _fred_provider.search(query)


_default_catalog: ProviderBackedInstrumentCatalog | None = None
_default_catalog_lock = threading.Lock()


def default_instrument_catalog() -> ProviderBackedInstrumentCatalog:
    global _default_catalog
    if _default_catalog is None:
        with _default_catalog_lock:
            if _default_catalog is None:
                _default_catalog = ProviderBackedInstrumentCatalog()
    return _default_catalog
