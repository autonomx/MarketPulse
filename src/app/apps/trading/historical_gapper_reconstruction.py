from __future__ import annotations
from app.config.env import env_str as _env_str

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .catalog import register_instrument
from .gapper_dataset import GapperCandidate, GapperUniverseSnapshot, freeze_gapper_universe, time_of_day_relative_volume
from .instrument_catalog_service import _dynamic_bindings, _equity_instrument
from .providers.alpaca_iex import ALPACA_DATA_URL, alpaca_iex_auth_headers
from .providers.errors import ProviderContractError, ProviderDataUnavailableError
from .providers.http_runtime import ProviderHttpRuntime
from .strategies.models import GapPullbackConfig
from .us_equity_calendar import regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_PREMARKET_OPEN = time(4, 0)
_REGULAR_OPEN = time(9, 30)
_DEFAULT_TRADING_URL = "https://paper-api.alpaca.markets"
_ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA"}
_SYMBOL = re.compile(r"^[A-Z0-9.\-]+$")
_SCAN_SEED_LOOKBACK_MINUTES = 15
# A 15-minute seed window yields at most 15 one-minute bars per symbol.
# 1,000-symbol batches remain bounded by Alpaca pagination while reducing
# request count versus the old 200-symbol batching.
_SCAN_SEED_CHUNK_SIZE = 1000


@dataclass(frozen=True)
class HistoricalUniverseReconstruction:
    snapshot: GapperUniverseSnapshot | None
    fidelity: str
    warnings: tuple[str, ...]
    candidate_seed_count: int
    active_asset_count: int
    detail: str | None = None


def reconstructed_strategy_config(config: GapPullbackConfig) -> tuple[GapPullbackConfig, tuple[str, ...]]:
    """Return an explicit market-data-only variant for reconstructed sessions."""

    updates: dict[str, Any] = {}
    warnings: list[str] = []
    if config.require_catalyst_evidence:
        updates["require_catalyst_evidence"] = False
        warnings.append("historical catalyst gate unavailable; reconstructed session uses market-data-only catalyst fidelity")
    if config.reject_dilution_flags:
        updates["reject_dilution_flags"] = ()
        warnings.append("historical dilution/supply evidence unavailable; deterministic dilution veto is not replayed")
    if config.float_preference_mode == "require":
        updates["float_preference_mode"] = "score"
        warnings.append("historical float shares unavailable; required-float gate is downgraded to scoring")
    return (config.model_copy(update=updates) if updates else config, tuple(warnings))


def _decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _trading_dates_before(session_date: date, count: int) -> list[date]:
    output: list[date] = []
    cursor = session_date - timedelta(days=1)
    while len(output) < count and cursor >= session_date - timedelta(days=30):
        if cursor.weekday() < 5 and cursor not in regular_holidays(cursor.year):
            output.append(cursor)
        cursor -= timedelta(days=1)
    output.reverse()
    return output


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _alpaca_assets(runtime: ProviderHttpRuntime, headers: dict[str, str]) -> list[dict[str, Any]]:
    trading_url = (_env_str("OMNIX_ALPACA_TRADING_URL") or _DEFAULT_TRADING_URL).rstrip("/")
    response = runtime.get(
        f"{trading_url}/v2/assets",
        params={"status": "active", "asset_class": "us_equity"},
        headers=headers,
        timeout=30,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderContractError("Alpaca returned invalid active-assets JSON") from exc
    if not isinstance(payload, list):
        raise ProviderContractError("Alpaca active-assets payload is malformed")
    output: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        exchange = str(raw.get("exchange") or "").strip().upper()
        if not symbol or not _SYMBOL.fullmatch(symbol):
            continue
        if exchange not in _ALLOWED_EXCHANGES:
            continue
        if raw.get("status") not in {None, "active"} or raw.get("tradable") is False:
            continue
        output.append(raw)
    if not output:
        raise ProviderDataUnavailableError("Alpaca returned no active listed US equities for reconstruction")
    return output


def _alpaca_bars(
    runtime: ProviderHttpRuntime,
    headers: dict[str, str],
    symbols: list[str],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_size: int,
    feed: Literal["iex", "sip"] = "iex",
) -> dict[str, list[dict[str, Any]]]:
    data_url = (_env_str("OMNIX_ALPACA_DATA_URL") or ALPACA_DATA_URL).rstrip("/")
    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in _chunks(symbols, chunk_size):
        page_token: str | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {
                "symbols": ",".join(chunk),
                "timeframe": timeframe,
                "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token
            response = runtime.get(
                f"{data_url}/v2/stocks/bars",
                params=params,
                headers=headers,
                timeout=30,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Alpaca returned invalid historical-bars JSON") from exc
            if not isinstance(payload, dict):
                raise ProviderContractError("Alpaca historical-bars payload is malformed")
            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, dict):
                raise ProviderContractError("Alpaca historical-bars response has no bars map")
            for symbol, bars in raw_bars.items():
                if isinstance(symbol, str) and isinstance(bars, list):
                    output[symbol.upper()].extend(item for item in bars if isinstance(item, dict))
            page_token = payload.get("next_page_token") if isinstance(payload.get("next_page_token"), str) else None
            pages += 1
            if not page_token:
                break
            if pages >= 100:
                raise ProviderDataUnavailableError("Alpaca historical-bars pagination exceeded safety limit")
    return dict(output)


def _previous_close_map(
    assets: list[dict[str, Any]],
    daily_bars: dict[str, list[dict[str, Any]]],
    *,
    session_date: date,
) -> dict[str, Decimal]:
    """Return closes strictly before the target session; never inspect target-day OHLC."""

    output: dict[str, Decimal] = {}
    for asset in assets:
        symbol = str(asset.get("symbol") or "").upper()
        prior: list[tuple[date, Decimal]] = []
        for bar in daily_bars.get(symbol, []):
            observed = _parse_timestamp(bar.get("t"))
            close_value = _decimal(bar.get("c"))
            if observed is None or close_value is None or close_value <= 0:
                continue
            bar_date = observed.astimezone(_ET).date()
            if bar_date < session_date:
                prior.append((bar_date, close_value))
        if prior:
            prior.sort(key=lambda item: item[0])
            output[symbol] = prior[-1][1]
    return output


def _scan_seed_symbols(
    scan_bars: dict[str, list[dict[str, Any]]],
    previous_close: dict[str, Decimal],
    *,
    session_date: date,
    scan_time: time,
    config: GapPullbackConfig,
) -> list[str]:
    """Causally seed likely gappers using only bars at or before the scan time."""

    ranked: list[tuple[Decimal, str]] = []
    broad_gap_floor = max(Decimal("5"), config.minimum_gap_pct * Decimal("0.75"))
    for symbol, previous in previous_close.items():
        latest: tuple[datetime, Decimal] | None = None
        for bar in scan_bars.get(symbol, []):
            observed = _parse_timestamp(bar.get("t"))
            close = _decimal(bar.get("c"))
            if observed is None or close is None or close <= 0:
                continue
            local = observed.astimezone(_ET)
            completed = local + timedelta(minutes=1)
            if local.date() != session_date or completed.timetz().replace(tzinfo=None) > scan_time:
                continue
            if latest is None or observed > latest[0]:
                latest = (observed, close)
        if latest is None:
            continue
        price = latest[1]
        gap = (price / previous - Decimal("1")) * Decimal("100")
        if gap < broad_gap_floor:
            continue
        if price < config.minimum_price * Decimal("0.5") or price > config.maximum_price * Decimal("1.5"):
            continue
        ranked.append((gap, symbol))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    seed_limit = min(300, max(config.universe_discovery_count * 4, 100))
    return [symbol for _, symbol in ranked[:seed_limit]]


def _minute_candidate(
    *,
    asset: dict[str, Any],
    symbol: str,
    bars: list[dict[str, Any]],
    session_date: date,
    scan_time: time,
    previous_close: Decimal,
    config: GapPullbackConfig,
    assumed_spread_bps: Decimal,
    observed_at: datetime,
    feed: Literal["iex", "sip"] = "iex",
) -> GapperCandidate | None:
    cumulative_by_date: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    current_premarket_volume = Decimal("0")
    current_price: Decimal | None = None
    for bar in bars:
        timestamp = _parse_timestamp(bar.get("t"))
        close = _decimal(bar.get("c"))
        volume = _decimal(bar.get("v"))
        if timestamp is None or close is None or volume is None or volume < 0:
            continue
        local = timestamp.astimezone(_ET)
        clock = local.timetz().replace(tzinfo=None)
        completed_clock = (local + timedelta(minutes=1)).timetz().replace(tzinfo=None)
        if clock < _PREMARKET_OPEN or completed_clock > scan_time:
            continue
        if local.date() > session_date:
            continue
        cumulative_by_date[local.date()] += volume
        if local.date() == session_date:
            current_price = close
            if clock < _REGULAR_OPEN:
                current_premarket_volume += volume
    if current_price is None or current_price <= 0:
        return None
    gap_pct = (current_price / previous_close - Decimal("1")) * Decimal("100")
    if gap_pct < config.minimum_gap_pct or not config.minimum_price <= current_price <= config.maximum_price:
        return None
    historical_dates = _trading_dates_before(session_date, 5)
    historical = [cumulative_by_date[value] for value in historical_dates if cumulative_by_date.get(value, Decimal("0")) > 0]
    tod_rvol = time_of_day_relative_volume(cumulative_by_date.get(session_date, Decimal("0")), historical)

    quote = {
        "symbol": symbol,
        "quoteType": "EQUITY",
        "exchange": str(asset.get("exchange") or "").upper(),
    }
    instrument = _equity_instrument(quote)
    if instrument is None:
        return None
    register_instrument(instrument, _dynamic_bindings(instrument))
    return GapperCandidate(
        instrument_id=instrument.instrument_id,
        binding_id=f"alpaca_{feed}:rest:{instrument.instrument_id}",
        observed_at=observed_at,
        evidence_observed_at={f"reconstructed_alpaca_{feed}_market_data": observed_at},
        previous_close=previous_close,
        premarket_price=current_price,
        gap_pct=gap_pct,
        premarket_volume=current_premarket_volume,
        premarket_dollar_volume=current_premarket_volume * current_price,
        tod_rvol=tod_rvol,
        market_cap=None,
        float_shares=None,
        spread_bps=assumed_spread_bps,
        catalyst_evidence_ids=(),
        dilution_flags=(),
    )


class AlpacaHistoricalGapperReconstructor:
    """Range-scoped, explicitly approximate historical candidate reconstruction.

    Current active assets and prior daily closes are cached for the date range.
    Each day then uses only a short pre-scan minute window ending at the configured
    scan time to identify likely gappers. Deeper same-time minute history is fetched
    only for those seeds to calculate gap, premarket volume and TOD RVOL. No target-
    day 09:30/open/close value is used to decide the 09:20 candidate seed.
    """

    def __init__(
        self,
        *,
        start_date: date,
        end_date: date,
        config: GapPullbackConfig,
        assumed_spread_bps: Decimal,
        max_age_days: int = 30,
        clock: datetime | None = None,
        runtime: ProviderHttpRuntime | None = None,
        feed: Literal["iex", "sip"] = "iex",
    ) -> None:
        self.start_date = start_date
        self.end_date = end_date
        self.config = config
        self.assumed_spread_bps = assumed_spread_bps
        self.max_age_days = max_age_days
        self.feed = feed
        self.clock = clock or datetime.now(timezone.utc)
        if self.clock.tzinfo is None:
            raise ValueError("historical reconstruction clock must be timezone-aware")
        self.runtime = runtime or ProviderHttpRuntime("alpaca_historical_gapper_reconstruction", max_concurrency=4)
        self._headers: dict[str, str] | None = None
        self._assets: list[dict[str, Any]] | None = None
        self._assets_by_symbol: dict[str, dict[str, Any]] = {}
        self._daily: dict[str, list[dict[str, Any]]] | None = None

    def _age_guard(self, session_date: date, max_age_days: int) -> HistoricalUniverseReconstruction | None:
        age_days = (self.clock.astimezone(_ET).date() - session_date).days
        if age_days < 0:
            raise ValueError("cannot reconstruct a future trading session")
        if age_days <= max_age_days:
            return None
        return HistoricalUniverseReconstruction(
            snapshot=None,
            fidelity="reconstruction_unavailable",
            warnings=("requested session is older than the configured reconstruction age limit",),
            candidate_seed_count=0,
            active_asset_count=0,
            detail=f"Historical reconstruction is limited to {max_age_days} calendar days; capture/archive older universes instead.",
        )

    def _prepare(self) -> None:
        if self._assets is not None and self._daily is not None:
            return
        self._headers = alpaca_iex_auth_headers()
        self._assets = _alpaca_assets(self.runtime, self._headers)
        self._assets_by_symbol = {str(asset["symbol"]).upper(): asset for asset in self._assets}
        symbols = list(self._assets_by_symbol)
        daily_start = datetime.combine(self.start_date - timedelta(days=10), time(0, 0), tzinfo=_ET).astimezone(timezone.utc)
        daily_end = datetime.combine(self.end_date + timedelta(days=1), time(0, 0), tzinfo=_ET).astimezone(timezone.utc)
        self._daily = _alpaca_bars(
            self.runtime,
            self._headers,
            symbols,
            timeframe="1Day",
            start=daily_start,
            end=daily_end,
            chunk_size=200,
            feed=self.feed,
        )

    def __call__(
        self,
        *,
        session_date: date,
        scan_time: time,
        config: GapPullbackConfig | None = None,
        assumed_spread_bps: Decimal | None = None,
        max_age_days: int | None = None,
    ) -> HistoricalUniverseReconstruction:
        active_config = config or self.config
        spread_bps = assumed_spread_bps if assumed_spread_bps is not None else self.assumed_spread_bps
        age_limit = max_age_days if max_age_days is not None else self.max_age_days
        unavailable = self._age_guard(session_date, age_limit)
        if unavailable is not None:
            return unavailable
        self._prepare()
        assert self._headers is not None and self._assets is not None and self._daily is not None

        previous_close = _previous_close_map(self._assets, self._daily, session_date=session_date)
        if not previous_close:
            return HistoricalUniverseReconstruction(
                snapshot=None,
                fidelity="reconstruction_unavailable",
                warnings=("no prior-close history was available for the current active listing set",),
                candidate_seed_count=0,
                active_asset_count=len(self._assets),
                detail="Historical reconstruction could not establish previous closes.",
            )

        scan_at = datetime.combine(session_date, scan_time, tzinfo=_ET).astimezone(timezone.utc)
        premarket_start = datetime.combine(session_date, _PREMARKET_OPEN, tzinfo=_ET).astimezone(timezone.utc)
        seed_start = max(premarket_start, scan_at - timedelta(minutes=_SCAN_SEED_LOOKBACK_MINUTES))
        scan_window = _alpaca_bars(
            self.runtime,
            self._headers,
            list(previous_close),
            timeframe="1Min",
            start=seed_start,
            end=scan_at,
            chunk_size=_SCAN_SEED_CHUNK_SIZE,
            feed=self.feed,
        )
        seed_symbols = _scan_seed_symbols(
            scan_window,
            previous_close,
            session_date=session_date,
            scan_time=scan_time,
            config=active_config,
        )
        base_warnings = (
            "candidate universe reconstructed from today's active Alpaca listings; survivorship/listing bias is possible",
            f"Alpaca {self.feed.upper()} historical evidence is used; it is not a substitute for a point-in-time scanner archive",
            f"candidate seed uses the final {_SCAN_SEED_LOOKBACK_MINUTES} minutes before scan time; an inactive or unlisted gapper can be omitted",
            "historical spread is replaced by the backtest assumed spread",
            "historical catalyst/dilution/float evidence is unavailable and reconstructed sessions use explicit market-data-only fidelity adjustments",
        )
        if not seed_symbols:
            return HistoricalUniverseReconstruction(
                snapshot=None,
                fidelity=f"reconstructed_current_listings_{self.feed}",
                warnings=base_warnings,
                candidate_seed_count=0,
                active_asset_count=len(self._assets),
                detail="Historical pre-scan market-data window found no broad gap candidates.",
            )

        minute_start = datetime.combine(session_date - timedelta(days=10), _PREMARKET_OPEN, tzinfo=_ET).astimezone(timezone.utc)
        minute = _alpaca_bars(
            self.runtime,
            self._headers,
            seed_symbols,
            timeframe="1Min",
            start=minute_start,
            end=scan_at,
            chunk_size=25,
            feed=self.feed,
        )
        candidates: list[GapperCandidate] = []
        for symbol in seed_symbols:
            prior = previous_close.get(symbol)
            asset = self._assets_by_symbol.get(symbol)
            if prior is None or asset is None:
                continue
            candidate = _minute_candidate(
                asset=asset,
                symbol=symbol,
                bars=minute.get(symbol, []),
                session_date=session_date,
                scan_time=scan_time,
                previous_close=prior,
                config=active_config,
                assumed_spread_bps=spread_bps,
                observed_at=scan_at,
                feed=self.feed,
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.gap_pct, -item.premarket_dollar_volume, item.instrument_id))
        candidates = [
            item.model_copy(update={"discovery_rank": index})
            for index, item in enumerate(candidates[: active_config.universe_discovery_count], start=1)
        ]
        if not candidates:
            return HistoricalUniverseReconstruction(
                snapshot=None,
                fidelity=f"reconstructed_current_listings_{self.feed}",
                warnings=base_warnings,
                candidate_seed_count=len(seed_symbols),
                active_asset_count=len(self._assets),
                detail="Historical scan completed but no candidate met the configured gap/price requirements at scan time.",
            )

        snapshot = freeze_gapper_universe(
            universe_id=f"reconstructed-alpaca-{session_date.isoformat()}-{scan_time.strftime('%H%M')}",
            session_date=session_date,
            evaluation_time=scan_at,
            discovery_source="provider",
            candidates=candidates,
        )
        return HistoricalUniverseReconstruction(
            snapshot=snapshot,
            fidelity=f"reconstructed_current_listings_{self.feed}",
            warnings=base_warnings,
            candidate_seed_count=len(seed_symbols),
            active_asset_count=len(self._assets),
        )


def reconstruct_recent_alpaca_gapper_universe(
    *,
    session_date: date,
    scan_time: time,
    config: GapPullbackConfig,
    assumed_spread_bps: Decimal,
    max_age_days: int = 30,
    clock: datetime | None = None,
    runtime: ProviderHttpRuntime | None = None,
) -> HistoricalUniverseReconstruction:
    return AlpacaHistoricalGapperReconstructor(
        start_date=session_date,
        end_date=session_date,
        config=config,
        assumed_spread_bps=assumed_spread_bps,
        max_age_days=max_age_days,
        clock=clock,
        runtime=runtime,
    )(
        session_date=session_date,
        scan_time=scan_time,
        config=config,
        assumed_spread_bps=assumed_spread_bps,
        max_age_days=max_age_days,
    )


__all__ = [
    "AlpacaHistoricalGapperReconstructor",
    "HistoricalUniverseReconstruction",
    "reconstruct_recent_alpaca_gapper_universe",
    "reconstructed_strategy_config",
]
