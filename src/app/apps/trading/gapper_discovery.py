from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .catalog import register_instrument
from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot, freeze_gapper_universe, time_of_day_relative_volume
from .instrument_catalog_service import _dynamic_bindings, _equity_instrument
from .providers.errors import ProviderContractError, ProviderDataUnavailableError
from .providers.http_runtime import ProviderHttpRuntime
from app.apps.trading.us_equity_calendar import EASTERN as _ET


YAHOO_GAINERS_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_ALLOWED_DISCOVERY_SKEW_SECONDS = 120


def _raw(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("raw")
    return value


def _decimal(value: Any) -> Decimal | None:
    value = _raw(value)
    if value in {None, ""}:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed


def _spread_bps(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
        return None
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        return None
    return (ask - bid) / midpoint * Decimal("10000")


def _yahoo_chart_volume_evidence(
    runtime: ProviderHttpRuntime,
    symbol: str,
    evaluation_time: datetime,
) -> tuple[Decimal, Decimal | None]:
    """Return point-in-time premarket volume and true cumulative TOD RVOL.

    Yahoo's 5-day one-minute chart is requested with pre/post data. Historical
    sessions are truncated at the same New York clock minute as the current
    observation, preventing the common error of comparing a partial morning with
    full-day average volume.
    """
    response = runtime.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={
            "range": "5d",
            "interval": "1m",
            "includePrePost": "true",
            "events": "",
        },
        headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderContractError("Yahoo returned invalid gapper chart JSON") from exc
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ProviderDataUnavailableError(f"Yahoo returned no gapper chart for {symbol}")
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    if not isinstance(quote, dict):
        raise ProviderContractError("Yahoo gapper chart quote payload is malformed")
    timestamps = result.get("timestamp") or []
    volumes = quote.get("volume") or []
    if not isinstance(timestamps, list) or not isinstance(volumes, list):
        raise ProviderContractError("Yahoo gapper chart volume payload is malformed")

    evaluation_et = evaluation_time.astimezone(_ET)
    current_date = evaluation_et.date()
    same_clock = evaluation_et.timetz().replace(tzinfo=None)
    cumulative_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    premarket_volume = Decimal("0")
    for index, raw_timestamp in enumerate(timestamps):
        if index >= len(volumes):
            break
        volume = _decimal(volumes[index])
        if volume is None or volume < 0:
            continue
        observed = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc).astimezone(_ET)
        clock = observed.timetz().replace(tzinfo=None)
        if observed.date() > current_date or clock < _PREMARKET_OPEN or clock > same_clock:
            continue
        cumulative_by_date[observed.date()] += volume
        if observed.date() == current_date and clock < _REGULAR_OPEN:
            premarket_volume += volume

    current = cumulative_by_date.get(current_date, Decimal("0"))
    historical = [
        value
        for session_date, value in sorted(cumulative_by_date.items(), key=lambda item: item[0])
        if session_date < current_date and value > 0
    ]
    return premarket_volume, time_of_day_relative_volume(current, historical)


def discover_yahoo_gappers(
    *,
    universe_id: str,
    evaluation_time: datetime,
    count: int = 30,
    minimum_gap_pct: Decimal = Decimal("20"),
    minimum_price: Decimal = Decimal("0.50"),
    maximum_price: Decimal = Decimal("20"),
    runtime: ProviderHttpRuntime | None = None,
) -> GapperUniverseSnapshot:
    """Discover and immediately freeze a current Yahoo top-gainer universe.

    This endpoint is intentionally current-only. Reconstructing a historical
    Yahoo screener later would introduce survivorship/look-ahead bias; historical
    research must reuse a universe that was frozen when the session occurred.
    """
    if evaluation_time.tzinfo is None:
        raise ValueError("evaluation_time must be timezone-aware")
    if count < 1 or count > 100:
        raise ValueError("Yahoo gapper discovery count must be between 1 and 100")
    evaluation = evaluation_time.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if abs((now - evaluation).total_seconds()) > _ALLOWED_DISCOVERY_SKEW_SECONDS:
        raise ValueError("Yahoo gapper discovery is current-only; freeze historical universes at capture time")

    active_runtime = runtime or ProviderHttpRuntime("yahoo_gapper_discovery", max_concurrency=2)
    response = active_runtime.get(
        YAHOO_GAINERS_URL,
        params={"scrIds": "day_gainers", "count": count, "start": 0},
        headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
        timeout=20,
    )
    received_at = datetime.now(timezone.utc)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderContractError("Yahoo returned invalid top-gainers JSON") from exc
    results = (payload.get("finance") or {}).get("result") if isinstance(payload, dict) else None
    first = results[0] if isinstance(results, list) and results else None
    raw_quotes = first.get("quotes") if isinstance(first, dict) else None
    if not isinstance(raw_quotes, list):
        raise ProviderDataUnavailableError("Yahoo returned no top-gainer universe")

    candidates: list[GapperCandidate] = []
    for raw_rank, quote in enumerate(raw_quotes, start=1):
        if not isinstance(quote, dict):
            continue
        instrument = _equity_instrument(quote)
        if instrument is None or instrument.session_calendar not in {"XNAS", "XNYS"}:
            continue
        previous_close = _decimal(quote.get("regularMarketPreviousClose"))
        price = _decimal(quote.get("preMarketPrice")) or _decimal(quote.get("regularMarketPrice"))
        if previous_close is None or previous_close <= 0 or price is None or price <= 0:
            continue
        gap_pct = (price / previous_close - Decimal("1")) * Decimal("100")
        if gap_pct < minimum_gap_pct or not minimum_price <= price <= maximum_price:
            continue

        register_instrument(instrument, _dynamic_bindings(instrument))
        try:
            premarket_volume, tod_rvol = _yahoo_chart_volume_evidence(
                active_runtime,
                instrument.display_symbol,
                evaluation,
            )
        except Exception:
            # Preserve the candidate rather than silently dropping a fade/failure;
            # missing RVOL remains explicit and the deterministic strategy rejects it.
            premarket_volume, tod_rvol = Decimal("0"), None

        bid = _decimal(quote.get("bid"))
        ask = _decimal(quote.get("ask"))
        market_cap = _decimal(quote.get("marketCap"))
        float_shares = _decimal(quote.get("floatShares"))
        candidates.append(
            GapperCandidate(
                instrument_id=instrument.instrument_id,
                binding_id=f"yahoo:historical_polling:{instrument.instrument_id}",
                observed_at=received_at,
                evidence_observed_at={
                    "yahoo_screener": received_at,
                    "yahoo_volume_chart": received_at,
                },
                previous_close=previous_close,
                premarket_price=price,
                gap_pct=gap_pct,
                premarket_volume=premarket_volume,
                premarket_dollar_volume=premarket_volume * price,
                tod_rvol=tod_rvol,
                market_cap=market_cap if market_cap is not None and market_cap >= 0 else None,
                float_shares=float_shares if float_shares is not None and float_shares > 0 else None,
                spread_bps=_spread_bps(bid, ask),
                discovery_rank=raw_rank,
            )
        )

    if not candidates:
        raise ProviderDataUnavailableError("Yahoo top-gainers produced no qualifying listed equities")
    evaluation_et = evaluation.astimezone(_ET)
    return freeze_gapper_universe(
        universe_id=universe_id,
        session_date=evaluation_et.date(),
        evaluation_time=evaluation,
        discovery_source="provider",
        candidates=candidates,
    )
