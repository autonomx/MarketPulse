"""Current-only Finviz Top Gainers discovery for the Omnix gapper workflow.

Finviz is used only to decide *which symbols belong to the morning cohort*.
Candidate price/share identity is independently enriched from Yahoo while the
versioned premarket-liquidity policy prefers same-feed Alpaca IEX evidence.
Finviz never becomes execution authority.

The adapter is intentionally current-only. Historical Finviz screener pages are
not reconstructed later because doing so would introduce survivorship/look-ahead
bias. Freeze the returned universe at capture time and reuse that immutable
snapshot for research/backtests.
"""

from __future__ import annotations

import logging

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .catalog import register_instrument
from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot, freeze_gapper_universe, time_of_day_relative_volume
from .instrument_catalog_service import _dynamic_bindings, _equity_instrument
from .models import AssetClass, CanonicalInstrument, InstrumentType
from .market_evidence import (
    DEFAULT_MARKET_EVIDENCE_POLICY,
    MARKET_EVIDENCE_POLICY_VERSION,
    YAHOO_HARDENED_EVIDENCE_POLICY_VERSION,
    YAHOO_RELATIVE_VOLUME,
    PremarketLiquidityEvidence,
    premarket_evidence_feature_compatible,
    SourceMemberDisposition,
)
from .premarket_liquidity import alpaca_premarket_liquidity_evidence
from .providers.alpaca_iex import AlpacaIexExecutionProvider, alpaca_iex_configured
from .providers.errors import ProviderContractError, ProviderDataUnavailableError
from .providers.equity import fetch_yahoo_chart_result
from .providers.http_runtime import ProviderHttpRuntime
from .yahoo_evidence import default_yahoo_evidence_store
from .strategy_data_integrity import (
    FINVIZ_ATOMIC_FIRST_PAGE_MAX,
    finviz_atomic_source_locator,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time

logger = logging.getLogger(__name__)


FINVIZ_TOP_GAINERS_URL = "https://finviz.com/screener"
FINVIZ_TOP_GAINERS_SOURCE_URL = "https://finviz.com/screener?v=340&s=ta_topgainers"
FINVIZ_ATOMIC_SOURCE_LOCATOR = finviz_atomic_source_locator(FINVIZ_TOP_GAINERS_SOURCE_URL)
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_FALLBACK_EVIDENCE_POLICY_VERSION = YAHOO_HARDENED_EVIDENCE_POLICY_VERSION

_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_ALLOWED_DISCOVERY_SKEW_SECONDS = 120
_TICKER_PATHS = {"/quote", "/quote.ashx", "/stock"}
_HREF_RE = re.compile(r"href\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)


def _raw(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("raw")
    return value


def _decimal(value: Any) -> Decimal | None:
    value = _raw(value)
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _provisional_us_equity(symbol: str) -> CanonicalInstrument:
    """Venue-neutral US equity identity used only when enrichment is unavailable.

    Provider bindings still address the immutable Finviz symbol directly. The
    venue is deliberately recorded as US rather than guessing NASDAQ/NYSE.
    """

    clean = symbol.strip().upper()
    return CanonicalInstrument(
        instrument_id=f"equity:US:{clean}",
        asset_class=AssetClass.EQUITY,
        instrument_type=InstrumentType.EQUITY,
        venue="US",
        venue_symbol=clean,
        display_symbol=clean,
        base_currency=None,
        quote_currency="USD",
        exchange_timezone="America/New_York",
        session_calendar="XNAS",
        price_scale=100,
        minimum_tick=Decimal("0.01"),
        status="active",
    )


def _spread_bps(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
        return None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * Decimal("10000")


def parse_finviz_top_gainer_symbols(html: str) -> list[str]:
    """Extract the ordered, de-duplicated ticker cohort from a Finviz screener page."""

    symbols: list[str] = []
    seen: set[str] = set()
    for match in _HREF_RE.finditer(html or ""):
        href = unescape(match.group(2).strip())
        parsed = urlsplit(href)
        path = f"/{parsed.path.lstrip('/')}".lower()
        if path not in _TICKER_PATHS:
            continue
        values = parse_qs(parsed.query).get("t", [])
        if not values:
            continue
        symbol = values[0].strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def _finviz_symbols(
    runtime: ProviderHttpRuntime,
    *,
    count: int,
) -> tuple[list[str], datetime]:
    """Capture one immutable first-page Finviz cohort with a single request."""

    response = runtime.get(
        FINVIZ_TOP_GAINERS_URL,
        params={"v": 340, "s": "ta_topgainers", "r": 1},
        headers={
            "User-Agent": "Mozilla/5.0 Omnix local research",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=20,
    )
    received_at = datetime.now(timezone.utc)
    page = parse_finviz_top_gainer_symbols(getattr(response, "text", ""))
    if not page:
        raise ProviderDataUnavailableError("Finviz Top Gainers returned no ticker rows")
    effective_count = min(count, FINVIZ_ATOMIC_FIRST_PAGE_MAX)
    return page[:effective_count], received_at


def _yahoo_exact_quote(runtime: ProviderHttpRuntime, symbol: str) -> dict[str, Any] | None:
    try:
        response = runtime.get(
            YAHOO_SEARCH_URL,
            params={"q": symbol, "quotesCount": 10, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
            timeout=10,
        )
        payload = response.json()
    except Exception:
        logger.debug("suppressed error in %s", "_yahoo_exact_quote", exc_info=True)
        return None
    quotes = payload.get("quotes") if isinstance(payload, dict) else None
    if not isinstance(quotes, list):
        return None
    for raw in quotes:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("symbol") or "").strip().upper() != symbol:
            continue
        if str(raw.get("quoteType") or "").strip().upper() not in {"EQUITY", "ETF"}:
            continue
        return raw
    return None


def _yahoo_chart_snapshot(
    runtime: ProviderHttpRuntime,
    symbol: str,
    evaluation_time: datetime,
) -> tuple[Decimal, Decimal, dict[str, Any], PremarketLiquidityEvidence]:
    """Capture canonical price/share basis plus diagnostic Yahoo liquidity evidence."""

    result, _ = fetch_yahoo_chart_result(
        runtime,
        symbol,
        interval="1m",
        range_value="8d",
        include_prepost=True,
        events="",
    )
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, dict):
        raise ProviderContractError("Yahoo Finviz-enrichment chart quote payload is malformed")

    yahoo_store = default_yahoo_evidence_store()
    yahoo_store.persist_chart_result(
        symbol,
        result,
        received_at=datetime.now(timezone.utc),
        cutoff=evaluation_time,
    )

    timestamps = result.get("timestamp") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    if not isinstance(timestamps, list) or not isinstance(closes, list) or not isinstance(volumes, list):
        raise ProviderContractError("Yahoo Finviz-enrichment chart arrays are malformed")

    evaluation_et = evaluation_time.astimezone(_ET)
    current_date = evaluation_et.date()
    same_clock = evaluation_et.timetz().replace(tzinfo=None)
    cumulative_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    dollar_volume_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    premarket_bar_count_by_date: dict[date, int] = defaultdict(int)
    nonzero_count_by_date: dict[date, int] = defaultdict(int)
    regular_closes_by_date: dict[date, list[tuple[datetime, Decimal]]] = defaultdict(list)
    latest_current: tuple[datetime, Decimal] | None = None
    premarket_volume_missing = False

    for index, raw_timestamp in enumerate(timestamps):
        if index >= len(closes):
            break
        close = _decimal(closes[index])
        raw_volume = volumes[index] if index < len(volumes) else None
        volume = _decimal(raw_volume)
        if close is None or close <= 0:
            continue
        observed = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc).astimezone(_ET)
        clock = observed.timetz().replace(tzinfo=None)
        if observed + timedelta(minutes=1) > evaluation_et:
            continue
        if _PREMARKET_OPEN <= clock < _REGULAR_OPEN and clock < same_clock:
            premarket_bar_count_by_date[observed.date()] += 1
            if volume is None:
                if observed.date() == current_date:
                    premarket_volume_missing = True
            else:
                cumulative_by_date[observed.date()] += volume
                dollar_volume_by_date[observed.date()] += volume * close
                if volume > 0:
                    nonzero_count_by_date[observed.date()] += 1
        if observed.date() == current_date and _PREMARKET_OPEN <= clock <= same_clock:
            latest_current = (observed, close)
        if observed.date() < current_date and _REGULAR_OPEN <= clock < regular_close_time(observed.date()):
            regular_closes_by_date[observed.date()].append((observed, close))

    if latest_current is None:
        meta_price = _decimal((result.get("meta") or {}).get("regularMarketPrice"))
        if meta_price is None or meta_price <= 0:
            raise ProviderDataUnavailableError(f"Yahoo returned no current price for Finviz symbol {symbol}")
        current_price = meta_price
    else:
        current_price = latest_current[1]

    prior_dates = sorted(regular_closes_by_date)
    previous_close: Decimal | None = None
    if prior_dates:
        previous_close = sorted(regular_closes_by_date[prior_dates[-1]], key=lambda item: item[0])[-1][1]
    if previous_close is None:
        meta = result.get("meta") or {}
        previous_close = _decimal(meta.get("chartPreviousClose")) or _decimal(meta.get("previousClose"))
    if previous_close is None or previous_close <= 0:
        raise ProviderDataUnavailableError(f"Yahoo returned no previous close for Finviz symbol {symbol}")

    current_volume = cumulative_by_date.get(current_date, Decimal("0"))
    current_dollar_volume = dollar_volume_by_date.get(current_date, Decimal("0"))
    current_bar_count = premarket_bar_count_by_date.get(current_date, 0)
    current_nonzero = nonzero_count_by_date.get(current_date, 0)

    # Prefer the durable same-feed baseline once it is at least as complete as
    # the current Yahoo response. During store warmup, preserve the response's
    # own same-clock historical baseline so hardening cannot regress availability.
    response_historical = [
        value
        for historical_date, value in sorted(
            cumulative_by_date.items(), key=lambda item: item[0]
        )
        if historical_date < current_date
        and value > 0
        and premarket_bar_count_by_date.get(historical_date, 0) > 0
    ]
    response_baseline_count = len(response_historical)
    response_denominator = (
        sum(response_historical, Decimal("0")) / Decimal(response_baseline_count)
        if response_baseline_count
        else None
    )
    relative = yahoo_store.premarket_relative_volume(
        symbol,
        evaluation_time,
        minimum_baseline_sessions=(
            DEFAULT_MARKET_EVIDENCE_POLICY.minimum_tod_rvol_baseline_sessions
        ),
    )
    if relative.current_bar_count > current_bar_count:
        current_volume = relative.current_volume
        current_dollar_volume = relative.current_dollar_volume
        current_bar_count = relative.current_bar_count
        current_nonzero = relative.current_nonzero_bar_count

    denominator: Decimal | None
    if (
        relative.baseline_mean_volume is not None
        and relative.baseline_session_count >= response_baseline_count
    ):
        baseline_count = relative.baseline_session_count
        denominator = relative.baseline_mean_volume
    else:
        baseline_count = response_baseline_count
        denominator = response_denominator
    tod_rvol = (
        current_volume / denominator
        if denominator is not None
        and denominator > 0
        and baseline_count
        >= DEFAULT_MARKET_EVIDENCE_POLICY.minimum_tod_rvol_baseline_sessions
        else None
    )
    elapsed_minutes = max(
        0,
        min(same_clock.hour * 60 + same_clock.minute, 9 * 60 + 29) - 4 * 60 + 1,
    )
    coverage_ratio = (
        Decimal(current_bar_count) / Decimal(elapsed_minutes)
        if elapsed_minutes > 0
        else None
    )
    issues: list[str] = []
    if current_bar_count == 0:
        issues.append("PREMARKET_BARS_MISSING")
    if premarket_volume_missing:
        issues.append("PREMARKET_VOLUME_MISSING")
    if current_bar_count > 0 and current_nonzero == 0:
        issues.append("PREMARKET_VOLUME_SUSPICIOUS_ZERO")
    if baseline_count < DEFAULT_MARKET_EVIDENCE_POLICY.minimum_tod_rvol_baseline_sessions:
        issues.append("TOD_RVOL_BASELINE_INSUFFICIENT")
    if tod_rvol is None:
        issues.append("TOD_RVOL_MISSING")

    yahoo_evidence = PremarketLiquidityEvidence(
        policy_version=YAHOO_FALLBACK_EVIDENCE_POLICY_VERSION,
        provider="yahoo",
        feed="extended_hours",
        observed_at=datetime.now(timezone.utc),
        current_premarket_volume=current_volume,
        current_premarket_dollar_volume=current_dollar_volume,
        tod_rvol=tod_rvol,
        tod_rvol_numerator=current_volume,
        tod_rvol_denominator_mean=denominator,
        baseline_session_count=baseline_count,
        premarket_bar_count=current_bar_count,
        nonzero_volume_bar_count=current_nonzero,
        coverage_ratio=coverage_ratio,
        ready=not issues,
        reason_codes=tuple(dict.fromkeys(issues)),
        volume_authority="provider_relative",
        volume_basis=YAHOO_RELATIVE_VOLUME,
        consolidated_volume_authority=False,
    )
    return current_price, previous_close, result.get("meta") or {}, yahoo_evidence


def discover_finviz_gappers(
    *,
    universe_id: str,
    evaluation_time: datetime,
    count: int = 50,
    minimum_gap_pct: Decimal = Decimal("20"),
    minimum_price: Decimal = Decimal("0.50"),
    maximum_price: Decimal = Decimal("20"),
    finviz_runtime: ProviderHttpRuntime | None = None,
    yahoo_runtime: ProviderHttpRuntime | None = None,
    execution_provider: Any | None = None,
    premarket_liquidity_provider: Callable[[str, datetime], PremarketLiquidityEvidence] | None = None,
    membership_only: bool = False,
) -> GapperUniverseSnapshot:
    """Discover Finviz Top Gainers and freeze point-in-time evidence."""

    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    if count < 1 or count > 100:
        raise ValueError("Finviz gapper discovery count must be between 1 and 100")
    evaluation = evaluation_time.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if abs((now - evaluation).total_seconds()) > _ALLOWED_DISCOVERY_SKEW_SECONDS:
        raise ValueError("Finviz gapper discovery is current-only; freeze historical universes at capture time")

    finviz = finviz_runtime or ProviderHttpRuntime("finviz_gapper_discovery", max_concurrency=1)
    yahoo = yahoo_runtime or ProviderHttpRuntime("finviz_yahoo_enrichment", max_concurrency=2)
    alpaca = execution_provider
    if alpaca is None and alpaca_iex_configured():
        alpaca = AlpacaIexExecutionProvider()
    liquidity_provider = premarket_liquidity_provider
    if liquidity_provider is None and alpaca_iex_configured():
        liquidity_provider = lambda symbol, observed_at: alpaca_premarket_liquidity_evidence(
            symbol,
            observed_at,
        )
    symbols, received_at = _finviz_symbols(finviz, count=count)

    candidates: list[GapperCandidate] = []
    dispositions: list[SourceMemberDisposition] = []
    for raw_rank, symbol in enumerate(symbols, start=1):
        search_quote = None
        chart_error_code: str | None = None
        provisional_identity = False
        try:
            price, previous_close, chart_meta, yahoo_liquidity = _yahoo_chart_snapshot(
                yahoo,
                symbol,
                evaluation,
            )
        except Exception as exc:
            chart_error_code = f"YAHOO_CHART_{type(exc).__name__.upper()}"
            if not membership_only:
                dispositions.append(
                    SourceMemberDisposition(
                        symbol=symbol,
                        source_rank=raw_rank,
                        status="enrichment_failed",
                        reason_codes=(chart_error_code,),
                    )
                )
                continue
            search_quote = _yahoo_exact_quote(yahoo, symbol)
            fallback_previous = (
                _decimal((search_quote or {}).get("regularMarketPreviousClose"))
                or _decimal((search_quote or {}).get("regularMarketPrice"))
                or Decimal("1")
            )
            fallback_price = (
                _decimal((search_quote or {}).get("preMarketPrice"))
                or _decimal((search_quote or {}).get("regularMarketPrice"))
                or fallback_previous
            )
            price = fallback_price
            previous_close = fallback_previous
            chart_meta = search_quote or {}
            fallback_at = datetime.now(timezone.utc)
            yahoo_liquidity = PremarketLiquidityEvidence(
                policy_version=YAHOO_FALLBACK_EVIDENCE_POLICY_VERSION,
                provider="yahoo_search" if search_quote else "finviz_membership",
                feed="quote" if search_quote else "membership_only",
                observed_at=fallback_at,
                current_premarket_volume=Decimal("0"),
                current_premarket_dollar_volume=Decimal("0"),
                tod_rvol=None,
                tod_rvol_numerator=Decimal("0"),
                tod_rvol_denominator_mean=None,
                baseline_session_count=0,
                premarket_bar_count=0,
                nonzero_volume_bar_count=0,
                coverage_ratio=None,
                ready=False,
                reason_codes=(chart_error_code,),
            )

        gap_pct = (price / previous_close - Decimal("1")) * Decimal("100")
        if not membership_only and gap_pct < minimum_gap_pct:
            dispositions.append(
                SourceMemberDisposition(
                    symbol=symbol,
                    source_rank=raw_rank,
                    status="filtered_gap",
                    reason_codes=("GAP_BELOW_MINIMUM",),
                )
            )
            continue
        if not membership_only and not minimum_price <= price <= maximum_price:
            dispositions.append(
                SourceMemberDisposition(
                    symbol=symbol,
                    source_rank=raw_rank,
                    status="filtered_price",
                    reason_codes=("PRICE_OUT_OF_RANGE",),
                )
            )
            continue

        if search_quote is None:
            search_quote = _yahoo_exact_quote(yahoo, symbol)
        enrichment_at = datetime.now(timezone.utc)
        quote_for_instrument = search_quote or {
            "symbol": symbol,
            "quoteType": "EQUITY",
            "exchange": chart_meta.get("exchangeName") or chart_meta.get("exchange") or "YAHOO",
        }
        instrument = _equity_instrument(quote_for_instrument)
        if membership_only and (
            instrument is None
            or instrument.session_calendar not in {"XNAS", "XNYS"}
        ):
            instrument = _provisional_us_equity(symbol)
            provisional_identity = True
        if instrument is None or instrument.session_calendar not in {"XNAS", "XNYS"}:
            dispositions.append(
                SourceMemberDisposition(
                    symbol=symbol,
                    source_rank=raw_rank,
                    status="unsupported_instrument",
                    reason_codes=("UNSUPPORTED_INSTRUMENT",),
                )
            )
            continue
        register_instrument(instrument, _dynamic_bindings(instrument))

        liquidity = yahoo_liquidity
        research_quality_flags: list[str] = []
        if chart_error_code is not None:
            research_quality_flags.append(chart_error_code)
        if provisional_identity:
            research_quality_flags.append("PROVISIONAL_US_INSTRUMENT_IDENTITY")
        if liquidity_provider is not None:
            try:
                provider_liquidity = liquidity_provider(symbol, evaluation)
            except Exception as exc:
                # Keep the source member visible with durable Yahoo same-feed
                # evidence. Yahoo may authorize provider-relative features but
                # never consolidated volume or execution.
                research_quality_flags.append(
                    f"PREMARKET_POLICY_PROVIDER_{type(exc).__name__.upper()}"
                )
            else:
                if provider_liquidity.ready:
                    liquidity = provider_liquidity
                elif premarket_evidence_feature_compatible(yahoo_liquidity):
                    liquidity = yahoo_liquidity
                    research_quality_flags.append(
                        "PREMARKET_POLICY_PROVIDER_DEGRADED_YAHOO_RECOVERY"
                    )
                else:
                    liquidity = max(
                        (provider_liquidity, yahoo_liquidity),
                        key=lambda item: (
                            item.premarket_bar_count,
                            item.baseline_session_count,
                            item.nonzero_volume_bar_count,
                        ),
                    )
        else:
            if premarket_evidence_feature_compatible(yahoo_liquidity):
                liquidity = yahoo_liquidity
                research_quality_flags.append("YAHOO_PREMARKET_PRIMARY_RECOVERY")
            else:
                research_quality_flags.append("PREMARKET_POLICY_PROVIDER_NOT_CONFIGURED")

        bid = _decimal(search_quote.get("bid")) if search_quote else None
        ask = _decimal(search_quote.get("ask")) if search_quote else None
        market_cap = _decimal(search_quote.get("marketCap")) if search_quote else None
        float_shares = _decimal(search_quote.get("floatShares")) if search_quote else None
        spread_bps = _spread_bps(bid, ask)
        evidence_times = {
            "finviz_top_gainers": received_at,
            ("yahoo_chart_enrichment" if chart_error_code is None else "membership_enrichment_fallback"): enrichment_at,
            f"premarket_liquidity:{liquidity.provider}:{liquidity.feed}": liquidity.observed_at,
        }

        # Frozen spread is a quality/research feature in market-evidence-v2.
        # Entry authority later requires a fresh execution observation and applies
        # the existing execution policy/spread limit at the actual decision time.
        if alpaca is not None:
            try:
                execution = alpaca.execution_observation(instrument.instrument_id)
            except Exception as exc:
                research_quality_flags.append(
                    f"FROZEN_SPREAD_{type(exc).__name__.upper()}"
                )
            else:
                if execution.spread_bps is not None:
                    spread_bps = execution.spread_bps
                alpaca_at = datetime.now(timezone.utc)
                evidence_times["alpaca_iex_research_quote"] = alpaca_at
                enrichment_at = max(enrichment_at, alpaca_at)

        if spread_bps is None:
            research_quality_flags.append("FROZEN_SPREAD_MISSING")

        data_quality_flags = list(liquidity.reason_codes)
        if chart_error_code is not None:
            data_quality_flags.append(chart_error_code)
        if provisional_identity:
            data_quality_flags.append("PROVISIONAL_US_INSTRUMENT_IDENTITY")
        if not premarket_evidence_feature_compatible(liquidity):
            data_quality_flags.append("MARKET_EVIDENCE_POLICY_MISMATCH")
        data_quality_flags = list(dict.fromkeys(data_quality_flags))
        market_data_complete = not data_quality_flags

        candidate = GapperCandidate(
            instrument_id=instrument.instrument_id,
            binding_id=f"yahoo:historical_polling:{instrument.instrument_id}",
            observed_at=enrichment_at,
            evidence_observed_at=evidence_times,
            previous_close=previous_close,
            premarket_price=price,
            gap_pct=gap_pct,
            premarket_volume=liquidity.current_premarket_volume,
            premarket_dollar_volume=liquidity.current_premarket_dollar_volume,
            premarket_bar_count=liquidity.premarket_bar_count,
            tod_rvol=liquidity.tod_rvol,
            premarket_liquidity=liquidity,
            market_evidence_policy_version=liquidity.policy_version,
            market_data_complete=market_data_complete,
            data_quality_flags=tuple(data_quality_flags),
            research_quality_flags=tuple(dict.fromkeys(research_quality_flags)),
            market_cap=market_cap if market_cap is not None and market_cap >= 0 else None,
            float_shares=float_shares if float_shares is not None and float_shares > 0 else None,
            spread_bps=spread_bps,
            discovery_rank=raw_rank,
        )
        candidates.append(candidate)
        dispositions.append(
            SourceMemberDisposition(
                symbol=symbol,
                source_rank=raw_rank,
                status="materialized",
                reason_codes=(),
                instrument_id=instrument.instrument_id,
            )
        )

    freeze_time = datetime.now(timezone.utc)
    freeze_et = freeze_time.astimezone(_ET)
    return freeze_gapper_universe(
        universe_id=universe_id,
        session_date=freeze_et.date(),
        evaluation_time=freeze_time,
        discovery_source="finviz",
        source_locator=FINVIZ_ATOMIC_SOURCE_LOCATOR,
        source_candidate_symbols=symbols,
        source_member_dispositions=dispositions,
        candidates=candidates,
        allow_empty=not candidates,
    )


__all__ = [
    "FINVIZ_ATOMIC_SOURCE_LOCATOR",
    "FINVIZ_TOP_GAINERS_SOURCE_URL",
    "discover_finviz_gappers",
    "parse_finviz_top_gainer_symbols",
]
