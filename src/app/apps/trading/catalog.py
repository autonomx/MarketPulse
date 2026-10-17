from __future__ import annotations

import re
import time
from collections import OrderedDict
from decimal import Decimal
from threading import RLock

from .models import (
    AdjustmentMode,
    AssetClass,
    CanonicalInstrument,
    FeedType,
    InstrumentType,
    ProviderBinding,
    ProviderPolicy,
    UsageScope,
)


def _policy(
    *,
    scope: UsageScope,
    official: bool,
    realtime: str,
    assets: tuple[AssetClass, ...],
    intervals: tuple[str, ...],
    terms: str,
    delay: int = 0,
) -> ProviderPolicy:
    return ProviderPolicy(
        usage_scope=scope,
        redistribution_allowed=False,
        authentication_required=False,
        is_official_api=official,
        realtime_scope=realtime,
        delay_seconds=delay,
        terms_reference=terms,
        supported_asset_classes=assets,
        supported_intervals=intervals,
        history_depth="provider_defined",
        rate_limit_policy=(
            "Omnix bounded provider semaphore with retry-after-aware exponential backoff"
        ),
    )


BINANCE_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=True,
    realtime="public spot market data",
    assets=(AssetClass.CRYPTO,),
    intervals=(
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "3d",
        "1w",
        "1mo",
    ),
    terms="https://www.binance.com/en/terms",
)
YAHOO_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=False,
    realtime="unofficial regular-session polling; availability not guaranteed",
    assets=(AssetClass.EQUITY, AssetClass.COMMODITY),
    intervals=("1m", "5m", "15m", "1h", "1d", "1w", "1mo"),
    terms="https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html",
)
ALPACA_IEX_POLICY = ProviderPolicy(
    usage_scope=UsageScope.PERSONAL_LOCAL,
    redistribution_allowed=False,
    authentication_required=True,
    is_official_api=True,
    realtime_scope="real-time IEX only; partial US market coverage; paper execution quotes",
    delay_seconds=0,
    terms_reference="https://docs.alpaca.markets/docs/about-market-data-api",
    supported_asset_classes=(AssetClass.EQUITY,),
    supported_intervals=(),
    history_depth="execution_quote_only",
    rate_limit_policy=(
        "Alpaca Basic REST limits with Omnix bounded provider semaphore and retry-after backoff"
    ),
)
IBKR_POLICY = ProviderPolicy(
    usage_scope=UsageScope.PERSONAL_LOCAL,
    redistribution_allowed=False,
    authentication_required=True,
    is_official_api=True,
    realtime_scope=(
        "IB Gateway/TWS API market data; live authority requires per-contract "
        "LIVE entitlement and fresh quotes"
    ),
    delay_seconds=0,
    terms_reference="https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/",
    supported_asset_classes=(AssetClass.EQUITY,),
    supported_intervals=("1m",),
    history_depth="provider_defined",
    rate_limit_policy=(
        "IBKR market-data line and historical pacing limits; Omnix deduplicates "
        "subscriptions and coalesces historical repair ranges"
    ),
)
STOOQ_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=False,
    realtime="historical daily only",
    assets=(AssetClass.EQUITY,),
    intervals=("1d",),
    terms="https://stooq.com/",
)
COINBASE_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=True,
    realtime="public spot REST market data",
    assets=(AssetClass.CRYPTO,),
    intervals=("1m", "5m", "15m", "1h", "1d"),
    terms="https://docs.cdp.coinbase.com/exchange/docs/welcome",
)
KRAKEN_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=True,
    realtime="public spot REST market data",
    assets=(AssetClass.CRYPTO,),
    intervals=("1m", "5m", "15m", "1h", "4h", "1d"),
    terms="https://docs.kraken.com/api/",
)
HYPERLIQUID_POLICY = _policy(
    scope=UsageScope.PERSONAL_LOCAL,
    official=True,
    realtime="public perpetual candle snapshots",
    assets=(AssetClass.CRYPTO,),
    intervals=("1m", "5m", "15m", "1h", "4h", "1d"),
    terms="https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api",
)
COINMARKETCAP_POLICY = ProviderPolicy(
    usage_scope=UsageScope.PERSONAL_LOCAL,
    redistribution_allowed=False,
    authentication_required=True,
    is_official_api=True,
    realtime_scope="daily historical crypto market-cap metrics",
    delay_seconds=300,
    terms_reference="https://coinmarketcap.com/api/documentation/",
    supported_asset_classes=(AssetClass.CRYPTO,),
    supported_intervals=("1d",),
    history_depth="provider_defined",
    rate_limit_policy=(
        "CoinMarketCap API credits with Omnix bounded provider semaphore and cache"
    ),
)

FRED_POLICY = ProviderPolicy(
    usage_scope=UsageScope.PERSONAL_LOCAL,
    redistribution_allowed=False,
    authentication_required=True,
    is_official_api=True,
    realtime_scope="published economic observations",
    delay_seconds=86_400,
    terms_reference="https://fred.stlouisfed.org/docs/api/terms_of_use.html",
    supported_asset_classes=(AssetClass.ECONOMIC,),
    supported_intervals=("1d",),
    history_depth="full",
    rate_limit_policy="FRED API request limits with Omnix bounded provider semaphore and cache",
)

POLICIES = {
    "binance": BINANCE_POLICY,
    "yahoo": YAHOO_POLICY,
    "ibkr": IBKR_POLICY,
    "alpaca_iex": ALPACA_IEX_POLICY,
    "stooq": STOOQ_POLICY,
    "coinbase": COINBASE_POLICY,
    "kraken": KRAKEN_POLICY,
    "hyperliquid": HYPERLIQUID_POLICY,
    "coinmarketcap": COINMARKETCAP_POLICY,
    "fred": FRED_POLICY,
}


def _crypto(
    venue: str,
    base: str,
    quote: str,
    instrument_type: InstrumentType = InstrumentType.SPOT,
) -> CanonicalInstrument:
    return CanonicalInstrument(
        instrument_id=f"crypto:{venue}:{instrument_type.value}:{base}-{quote}",
        asset_class=AssetClass.CRYPTO,
        instrument_type=instrument_type,
        venue=venue,
        venue_symbol=f"{base}-{quote}",
        display_symbol=f"{base}{quote}",
        base_currency=base,
        quote_currency=quote,
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )


def _equity(venue: str, symbol: str) -> CanonicalInstrument:
    return CanonicalInstrument(
        instrument_id=f"equity:{venue}:{symbol}",
        asset_class=AssetClass.EQUITY,
        instrument_type=InstrumentType.EQUITY,
        venue=venue,
        venue_symbol=symbol,
        display_symbol=symbol,
        base_currency=None,
        quote_currency="USD",
        exchange_timezone="America/New_York",
        session_calendar="XNYS",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )


def _commodity(symbol: str, provider_symbol: str) -> CanonicalInstrument:
    return CanonicalInstrument(
        instrument_id=f"commodity:YAHOO:{symbol}",
        asset_class=AssetClass.COMMODITY,
        instrument_type=InstrumentType.PERPETUAL,
        venue="YAHOO",
        venue_symbol=provider_symbol,
        display_symbol=symbol,
        base_currency=None,
        quote_currency="USD",
        exchange_timezone="America/Chicago",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
    )


def _crypto_cap(symbol: str) -> CanonicalInstrument:
    is_dominance = symbol.endswith(".D")
    return CanonicalInstrument(
        instrument_id=f"index:CRYPTOCAP:{symbol}",
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.INDEX,
        venue="CRYPTOCAP",
        venue_symbol=symbol,
        display_symbol=symbol,
        base_currency=symbol.removesuffix(".D"),
        quote_currency="%" if is_dominance else "USD",
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.0001" if is_dominance else "0.01"),
    )


INSTRUMENTS = (
    *tuple(_crypto("BINANCE", base, "USDT") for base in ("BTC", "ETH", "SOL")),
    *tuple(_crypto("COINBASE", base, "USD") for base in ("BTC", "ETH")),
    *tuple(_crypto("KRAKEN", base, "USD") for base in ("BTC", "ETH")),
    *tuple(
        _crypto("HYPERLIQUID", base, "USD", InstrumentType.PERPETUAL)
        for base in ("BTC", "ETH")
    ),
    *tuple(
        _crypto_cap(symbol)
        for symbol in (
            "TOTAL",
            "TOTAL2",
            "TOTAL3",
            "BTC",
            "ETH",
            "USDT",
            "USDC",
            "DAI",
            "BTC.D",
            "ETH.D",
            "USDT.D",
            "USDC.D",
            "DAI.D",
            "TOTAL.D",
            "TOTAL2.D",
            "TOTAL3.D",
        )
    ),
    _equity("NASDAQ", "AAPL"),
    _equity("NASDAQ", "NVDA"),
    _equity("NASDAQ", "TSLA"),
    _equity("ARCA", "SPY"),
    _commodity("USOIL", "CL=F"),
    _commodity("UKOIL", "BZ=F"),
    _commodity("XAUUSD", "GC=F"),
    _commodity("XAGUSD", "SI=F"),
    _commodity("NATGAS", "NG=F"),
    _commodity("COPPER", "HG=F"),
)


def _binding(
    instrument: CanonicalInstrument,
    provider: str,
    provider_symbol: str,
    feed_type: FeedType,
    *,
    adjustments: tuple[AdjustmentMode, ...] = (AdjustmentMode.RAW,),
) -> ProviderBinding:
    policy = POLICIES[provider]
    supported_intervals = policy.supported_intervals
    # Yahoo's chart endpoint generally exposes only end-of-day history for OTC
    # and other non-listed venues. Keep the binding capabilities honest so the
    # chart and watchlist do not probe unsupported intraday intervals.
    if provider == "yahoo" and instrument.asset_class is AssetClass.EQUITY and instrument.venue not in {
        "NASDAQ",
        "NYSE",
        "ARCA",
        "AMEX",
    }:
        supported_intervals = ("1d", "1w", "1mo")
    return ProviderBinding(
        binding_id=f"{provider}:{feed_type.value}:{instrument.instrument_id}",
        instrument_id=instrument.instrument_id,
        provider=provider,
        provider_symbol=provider_symbol,
        feed_type=feed_type,
        realtime_scope=policy.realtime_scope,
        delay_seconds=policy.delay_seconds,
        adjustment_capabilities=adjustments,
        supported_intervals=supported_intervals,
        usage_scope=policy.usage_scope,
        is_official_api=policy.is_official_api,
    )


BINDINGS: tuple[ProviderBinding, ...] = tuple(
    binding
    for instrument in INSTRUMENTS
    for binding in (
        (
            _binding(
                instrument,
                "binance",
                instrument.display_symbol,
                FeedType.WEBSOCKET_AND_REST,
            ),
        )
        if instrument.venue == "BINANCE"
        else (
            _binding(
                instrument,
                "coinbase",
                instrument.venue_symbol,
                FeedType.REST,
            ),
        )
        if instrument.venue == "COINBASE"
        else (
            _binding(
                instrument,
                "kraken",
                instrument.venue_symbol.replace("BTC", "XBT"),
                FeedType.REST,
            ),
        )
        if instrument.venue == "KRAKEN"
        else (
            _binding(
                instrument,
                "hyperliquid",
                instrument.base_currency or "",
                FeedType.REST,
            ),
        )
        if instrument.venue == "HYPERLIQUID"
        else (
            _binding(
                instrument,
                "coinmarketcap",
                instrument.venue_symbol,
                FeedType.HISTORICAL_DAILY,
            ),
        )
        if instrument.venue == "CRYPTOCAP"
        else (
            _binding(
                instrument,
                "yahoo",
                instrument.venue_symbol,
                FeedType.HISTORICAL_POLLING,
            ),
        )
        if instrument.asset_class is AssetClass.COMMODITY
        else (
            _binding(
                instrument,
                "yahoo",
                instrument.display_symbol,
                FeedType.HISTORICAL_POLLING,
                adjustments=(AdjustmentMode.RAW,),
            ),
            _binding(
                instrument,
                "ibkr",
                instrument.display_symbol,
                FeedType.SOCKET,
                adjustments=(AdjustmentMode.RAW,),
            ),
            _binding(
                instrument,
                "alpaca_iex",
                instrument.display_symbol,
                FeedType.REST,
                adjustments=(AdjustmentMode.RAW,),
            ),
            _binding(
                instrument,
                "stooq",
                f"{instrument.display_symbol}.US",
                FeedType.HISTORICAL_DAILY,
            ),
        )
    )
)

_catalog_lock = RLock()
MAX_DYNAMIC_CATALOG_ENTRIES = 4096
DYNAMIC_CATALOG_TTL_SECONDS = 24 * 60 * 60.0
_dynamic_instruments: OrderedDict[str, tuple[CanonicalInstrument, float]] = OrderedDict()
_dynamic_bindings: OrderedDict[str, tuple[ProviderBinding, float]] = OrderedDict()
_CANONICAL_EQUITY_TOKEN = re.compile(r"^[A-Z0-9._^=-]+$")
_CANONICAL_CRYPTO_TOKEN = re.compile(r"^[A-Z0-9]+$")
_CANONICAL_COMMODITY_TOKEN = re.compile(r"^[A-Z0-9._=^/-]+$")


def _restore_dynamic_crypto(instrument_id: str) -> CanonicalInstrument | None:
    """Rehydrate a Binance spot symbol persisted from a previous process."""
    parts = instrument_id.split(":")
    if len(parts) != 4 or parts[:3] != ["crypto", "BINANCE", "spot"]:
        return None
    pair = parts[3].split("-")
    if len(pair) != 2 or any(not _CANONICAL_CRYPTO_TOKEN.fullmatch(value) for value in pair):
        return None
    base, quote = pair
    instrument = CanonicalInstrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        instrument_type=InstrumentType.SPOT,
        venue="BINANCE",
        venue_symbol=f"{base}-{quote}",
        display_symbol=f"{base}{quote}",
        base_currency=base,
        quote_currency=quote,
        exchange_timezone="UTC",
        session_calendar="24x7",
        price_scale=100,
        minimum_tick=Decimal("0.00000001"),
        status="active",
    )
    register_instrument(
        instrument,
        (_binding(instrument, "binance", instrument.display_symbol, FeedType.WEBSOCKET_AND_REST),),
    )
    return instrument


def _restore_dynamic_equity(instrument_id: str) -> CanonicalInstrument | None:
    """Rehydrate a discovered equity saved in a workspace after a restart.

    Provider-discovered catalog entries are process-local, but workspace documents
    persist their canonical IDs.  Equity bindings are deterministic from that ID,
    so a saved symbol can be restored without requiring a search request first.
    """
    parts = instrument_id.split(":")
    if len(parts) != 3 or parts[0] != "equity":
        return None
    _, venue, symbol = parts
    if not venue or not symbol or any(
        not _CANONICAL_EQUITY_TOKEN.fullmatch(value)
        for value in (venue, symbol)
    ):
        return None

    instrument = CanonicalInstrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.EQUITY,
        instrument_type=InstrumentType.EQUITY,
        venue=venue,
        venue_symbol=symbol,
        display_symbol=symbol,
        base_currency=None,
        quote_currency="USD",
        exchange_timezone="America/New_York",
        session_calendar="XNYS",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
        status="active",
    )
    register_instrument(
        instrument,
        (
            _binding(
                instrument,
                "yahoo",
                symbol,
                FeedType.HISTORICAL_POLLING,
            ),
            _binding(
                instrument,
                "ibkr",
                symbol,
                FeedType.SOCKET,
            ),
            _binding(
                instrument,
                "alpaca_iex",
                symbol,
                FeedType.REST,
            ),
            _binding(
                instrument,
                "stooq",
                f"{symbol}.US",
                FeedType.HISTORICAL_DAILY,
            ),
        ),
    )
    return instrument


def _restore_dynamic_commodity(instrument_id: str) -> CanonicalInstrument | None:
    """Rehydrate a Yahoo commodity/futures symbol saved from a previous process."""
    parts = instrument_id.split(":")
    if len(parts) != 3 or parts[0] != "commodity" or parts[1] != "YAHOO":
        return None
    symbol = parts[2].upper()
    if not symbol or not _CANONICAL_COMMODITY_TOKEN.fullmatch(symbol):
        return None
    instrument = _commodity(symbol, symbol)
    register_instrument(
        instrument,
        (_binding(instrument, "yahoo", symbol, FeedType.HISTORICAL_POLLING),),
    )
    return instrument


# Economic series (TVP-10.5) offered by name before a FRED search: US prices, jobs, output, rates and money.
ECONOMIC_SERIES: dict[str, str] = {
    "CPIAUCSL": "Consumer Price Index (CPI), all items",
    "CPILFESL": "Core CPI (less food and energy)",
    "PCEPILFE": "Core PCE price index",
    "UNRATE": "Unemployment rate",
    "PAYEMS": "Nonfarm payrolls",
    "ICSA": "Initial jobless claims",
    "GDP": "Gross domestic product",
    "GDPC1": "Real gross domestic product",
    "INDPRO": "Industrial production",
    "RSAFS": "Retail sales",
    "HOUST": "Housing starts",
    "UMCSENT": "Consumer sentiment (University of Michigan)",
    "FEDFUNDS": "Federal funds effective rate",
    "DGS1MO": "1-month Treasury yield",
    "DGS3MO": "3-month Treasury yield",
    "DGS6MO": "6-month Treasury yield",
    "DGS1": "1-year Treasury yield",
    "DGS2": "2-year Treasury yield",
    "DGS5": "5-year Treasury yield",
    "DGS10": "10-year Treasury yield",
    "DGS30": "30-year Treasury yield",
    "T10Y2Y": "10-year minus 2-year Treasury spread",
    "T10Y3M": "10-year minus 3-month Treasury spread",
    "MORTGAGE30US": "30-year mortgage rate",
    "M2SL": "M2 money stock",
    "WALCL": "Federal Reserve total assets",
    "DTWEXBGS": "Broad US dollar index",
    "BAMLH0A0HYM2": "High-yield bond spread",
}
_FRED_SERIES_TOKEN = re.compile(r"^[A-Z0-9_]{1,40}$")


def economic_instrument(series_id: str, title: str | None = None) -> CanonicalInstrument:
    """A FRED series as a chart symbol, ``economic:FRED:<series>``: research data, never traded."""
    return CanonicalInstrument(
        instrument_id=f"economic:FRED:{series_id}",
        asset_class=AssetClass.ECONOMIC,
        instrument_type=InstrumentType.INDEX,
        venue="FRED",
        venue_symbol=series_id,
        display_symbol=series_id,
        exchange_timezone="America/New_York",
        session_calendar="24x7",
        price_scale=10_000,
        minimum_tick=Decimal("0.0001"),
        name=(title or ECONOMIC_SERIES.get(series_id) or None),
    )


def register_economic_series(series_id: str, title: str | None = None) -> CanonicalInstrument | None:
    clean = series_id.strip().upper()
    if not _FRED_SERIES_TOKEN.fullmatch(clean):
        return None
    instrument = economic_instrument(clean, title)
    return register_instrument(instrument, (_binding(instrument, "fred", clean, FeedType.HISTORICAL_DAILY),))


def _restore_dynamic_economic(instrument_id: str) -> CanonicalInstrument | None:
    parts = instrument_id.split(":")
    if len(parts) != 3 or parts[:2] != ["economic", "FRED"]:
        return None
    return register_economic_series(parts[2])


def register_instrument(
    instrument: CanonicalInstrument,
    bindings: tuple[ProviderBinding, ...] = (),
) -> CanonicalInstrument:
    """Cache provider-discovered metadata for bounded restoration and lookup."""
    expires_at = time.monotonic() + DYNAMIC_CATALOG_TTL_SECONDS
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        if not any(item.instrument_id == instrument.instrument_id for item in INSTRUMENTS):
            _dynamic_instruments[instrument.instrument_id] = (instrument, expires_at)
            _dynamic_instruments.move_to_end(instrument.instrument_id)
        for binding in bindings:
            if binding.instrument_id != instrument.instrument_id:
                raise ValueError("binding instrument_id must match the registered instrument")
            _dynamic_bindings[binding.binding_id] = (binding, expires_at)
            _dynamic_bindings.move_to_end(binding.binding_id)
        _prune_dynamic_catalog_locked(time.monotonic())
    return instrument


def clear_dynamic_catalog_cache() -> None:
    """Invalidate provider-discovered catalog entries."""

    with _catalog_lock:
        _dynamic_instruments.clear()
        _dynamic_bindings.clear()


def _prune_dynamic_catalog_locked(now: float) -> None:
    for cache in (_dynamic_instruments, _dynamic_bindings):
        for key, (_value, expires_at) in list(cache.items()):
            if expires_at <= now:
                cache.pop(key, None)
        while len(cache) > MAX_DYNAMIC_CATALOG_ENTRIES:
            cache.popitem(last=False)


def all_instruments() -> list[CanonicalInstrument]:
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        return [*INSTRUMENTS, *(item[0] for item in _dynamic_instruments.values())]


def all_bindings() -> list[ProviderBinding]:
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        # Provider discovery can re-register a symbol that is already present in
        # the built-in catalog. Keep the first-seen ordering while letting the
        # discovered binding refresh the static entry's metadata.
        bindings_by_id = {binding.binding_id: binding for binding in BINDINGS}
        bindings_by_id.update(
            (binding_id, value[0]) for binding_id, value in _dynamic_bindings.items()
        )
        return list(bindings_by_id.values())


def search_instruments(query: str = "") -> list[CanonicalInstrument]:
    clean = query.strip().upper()
    instruments = all_instruments()
    if not clean:
        return instruments
    return [
        item
        for item in instruments
        if clean in item.display_symbol
        or clean in item.venue_symbol
        or clean in item.instrument_id.upper()
    ]


def instrument_by_id(instrument_id: str) -> CanonicalInstrument | None:
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        dynamic = _dynamic_instruments.get(instrument_id)
        if dynamic is not None:
            _dynamic_instruments.move_to_end(instrument_id)
            return dynamic[0]
    static = next((item for item in INSTRUMENTS if item.instrument_id == instrument_id), None)
    if static is not None:
        return static
    return (
        _restore_dynamic_equity(instrument_id)
        or _restore_dynamic_commodity(instrument_id)
        or _restore_dynamic_crypto(instrument_id)
        or _restore_dynamic_economic(instrument_id)
    )


def binding_by_id(binding_id: str) -> ProviderBinding | None:
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        dynamic = _dynamic_bindings.get(binding_id)
        if dynamic is not None:
            _dynamic_bindings.move_to_end(binding_id)
            return dynamic[0]
    binding = next((item for item in BINDINGS if item.binding_id == binding_id), None)
    if binding is not None:
        return binding

    # A persisted workspace may contain an explicit binding for a symbol that was
    # discovered in a previous process lifetime.  Recreate the dynamic catalog
    # entry before looking up that binding.
    parts = binding_id.split(":", 2)
    if len(parts) == 3 and parts[0] in {"yahoo", "ibkr", "alpaca_iex", "stooq"}:
        if parts[2].startswith("commodity:YAHOO:"):
            _restore_dynamic_commodity(parts[2])
        else:
            _restore_dynamic_equity(parts[2])
    elif len(parts) == 3 and parts[0] == "binance":
        _restore_dynamic_crypto(parts[2])
    elif len(parts) == 3 and parts[0] == "fred":
        _restore_dynamic_economic(parts[2])
    with _catalog_lock:
        _prune_dynamic_catalog_locked(time.monotonic())
        dynamic = _dynamic_bindings.get(binding_id)
        return dynamic[0] if dynamic is not None else None


def bindings_for_instrument(instrument_id: str) -> list[ProviderBinding]:
    bindings = [item for item in all_bindings() if item.instrument_id == instrument_id]
    if bindings:
        return bindings
    _restore_dynamic_equity(instrument_id)
    _restore_dynamic_commodity(instrument_id)
    _restore_dynamic_crypto(instrument_id)
    _restore_dynamic_economic(instrument_id)
    return [item for item in all_bindings() if item.instrument_id == instrument_id]


def default_binding(instrument_id: str) -> ProviderBinding | None:
    return next(iter(bindings_for_instrument(instrument_id)), None)
