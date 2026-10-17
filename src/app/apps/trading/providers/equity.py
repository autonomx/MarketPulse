from __future__ import annotations

import csv
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

import httpx

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import POLICIES, bindings_for_instrument, instrument_by_id
from app.apps.trading.models import AdjustmentMode, BarsResponse, DatasetProvenance, MarketBar, ProviderBinding

from .base import ProviderAdapter
from .bar_semantics import equity_bar_times, equity_session_bounds, is_final_bar
from .errors import ProviderContractError, ProviderDataUnavailableError, ProviderFallbackEligibleError
from .http_runtime import ProviderHttpRuntime
from ..yahoo_evidence import default_yahoo_evidence_store


YAHOO_INTERVALS = {
    "1m": ("1d", "1m"),
    "5m": ("5d", "5m"),
    "15m": ("5d", "15m"),
    "1h": ("3mo", "1h"),
    "1d": ("10y", "1d"),
    "1w": ("10y", "1wk"),
    "1mo": ("max", "1mo"),
}
MAX_YAHOO_HISTORY_LIMIT = 2_000
STABLE_CURRENCY_CODES = {"BUSD", "USDC", "USDT"}


def fetch_yahoo_chart_result(
    runtime: ProviderHttpRuntime,
    symbol: str,
    *,
    interval: str = "1m",
    range_value: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    include_prepost: bool = False,
    events: str = "",
    cancellation: threading.Event | None = None,
) -> tuple[dict[str, Any], datetime]:
    """Shared Yahoo chart acquisition used by discovery and provider recovery."""

    params: dict[str, Any] = {
        "interval": interval,
        "includePrePost": "true" if include_prepost else "false",
        "events": events,
    }
    if start is not None or end is not None:
        if start is None or end is None or start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Yahoo bounded chart requests require aware start/end")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("Yahoo bounded chart end must be after start")
        params["period1"] = int(start_utc.timestamp())
        params["period2"] = int(end_utc.timestamp())
    else:
        params["range"] = range_value or "1d"

    request_kwargs: dict[str, Any] = {
        "params": params,
        "headers": {"User-Agent": "Mozilla/5.0 Omnix local research"},
        "timeout": 20,
    }
    if cancellation is not None:
        request_kwargs["cancellation"] = cancellation
    request_key = "|".join(
        (
            "yahoo-chart",
            symbol.upper(),
            interval,
            str(range_value or ""),
            str(int(start.astimezone(timezone.utc).timestamp())) if start is not None else "",
            str(int(end.astimezone(timezone.utc).timestamp())) if end is not None else "",
            "prepost" if include_prepost else "regular",
            events,
        )
    )
    coalesced = getattr(runtime, "get_coalesced", None)
    if callable(coalesced):
        response = coalesced(
            request_key,
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            **request_kwargs,
        )
    else:
        response = runtime.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            **request_kwargs,
        )
    received = datetime.now(timezone.utc)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderContractError("Yahoo returned invalid chart JSON") from exc
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ProviderDataUnavailableError(f"Yahoo returned no chart result for {symbol}")
    return result, received


class YahooEquityProvider(ProviderAdapter):
    provider_id = "yahoo"
    policy = POLICIES["yahoo"]

    def __init__(
        self,
        *,
        session: Any | None = None,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
    ) -> None:
        self.runtime = runtime or ProviderHttpRuntime(
            self.provider_id,
            session=session,
            max_concurrency=2,
        )
        self.session = self.runtime.session
        self.cache = cache or TradingMarketDataCache()
        self.evidence_store = default_yahoo_evidence_store()

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next(
            (
                item
                for item in bindings_for_instrument(instrument_id)
                if item.provider == self.provider_id
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"Yahoo does not support instrument: {instrument_id}")
        return binding

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        instrument = instrument_by_id(instrument_id)
        binding = self.get_binding(instrument_id)
        if instrument is None or interval not in YAHOO_INTERVALS:
            raise ValueError(f"unsupported Yahoo request: {instrument_id} {interval}")
        range_value, provider_interval = YAHOO_INTERVALS[interval]
        clean_limit = max(1, min(int(limit), MAX_YAHOO_HISTORY_LIMIT))
        key = self.cache.key(
            binding.binding_id,
            instrument_id,
            interval,
            AdjustmentMode.RAW.value,
            instrument.session_calendar,
            clean_limit,
        )

        def load() -> dict[str, Any]:
            response = self.runtime.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{binding.provider_symbol}",
                params={
                    "range": range_value,
                    "interval": provider_interval,
                    "includePrePost": "false",
                    "events": "div,splits",
                },
                headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
                timeout=20,
                cancellation=cancellation,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Yahoo returned invalid JSON") from exc
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not isinstance(result, dict):
                raise ProviderDataUnavailableError("Yahoo returned no chart result")
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            if not isinstance(quote, dict):
                raise ProviderContractError("Yahoo quote payload is malformed")
            rows = []
            for index, timestamp in enumerate(result.get("timestamp") or []):
                values = [quote.get(name, []) for name in ("open", "high", "low", "close", "volume")]
                if any(not isinstance(items, list) or index >= len(items) for items in values):
                    continue
                open_value, high, low, close, volume = (items[index] for items in values)
                if None in (open_value, high, low, close):
                    continue
                try:
                    open_decimal = Decimal(str(open_value))
                    high_decimal = Decimal(str(high))
                    low_decimal = Decimal(str(low))
                    close_decimal = Decimal(str(close))
                    if (
                        not all(value.is_finite() for value in (open_decimal, high_decimal, low_decimal, close_decimal))
                        or high_decimal < max(open_decimal, close_decimal)
                        or low_decimal > min(open_decimal, close_decimal)
                    ):
                        continue
                except (InvalidOperation, TypeError, ValueError):
                    continue
                rows.append([int(timestamp), open_value, high, low, close, volume or 0])
            if not rows:
                raise ProviderDataUnavailableError(
                    f"Yahoo returned no bars for {instrument_id} {interval}"
                )
            return {"rows": rows[-clean_limit:], "history_complete": len(rows) < clean_limit}

        payload, entry, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=(15 if interval == "1m" else 30 if interval not in {"1d", "1w", "1mo"} else 900),
            source="yahoo_chart",
        )
        valid_rows = []
        for row in payload.get("rows", []):
            try:
                open_decimal = Decimal(str(row[1]))
                high_decimal = Decimal(str(row[2]))
                low_decimal = Decimal(str(row[3]))
                close_decimal = Decimal(str(row[4]))
                if (
                    not all(value.is_finite() for value in (open_decimal, high_decimal, low_decimal, close_decimal))
                    or high_decimal < max(open_decimal, close_decimal)
                    or low_decimal > min(open_decimal, close_decimal)
                ):
                    continue
            except (IndexError, InvalidOperation, TypeError, ValueError):
                continue
            valid_rows.append(row)
        if not valid_rows:
            raise ProviderDataUnavailableError(
                f"Yahoo returned no valid OHLC bars for {instrument_id} {interval}"
            )

        received = datetime.now(timezone.utc)
        bars: list[MarketBar] = []
        for row in valid_rows:
            provider_time = datetime.fromtimestamp(int(row[0]), tz=timezone.utc)
            start, end, session = equity_bar_times(
                provider_time,
                interval,
                instrument.exchange_timezone,
            )
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval=interval,
                    start_time=start,
                    end_time=end,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])),
                    is_final=is_final_bar(end, received),
                    adjustment_mode=AdjustmentMode.RAW,
                    session=session,
                    provider=self.provider_id,
                    provider_event_id=str(row[0]),
                    received_at=received,
                )
            )
        if interval == "1m":
            self.evidence_store.persist_market_bars(tuple(bar for bar in bars if bar.is_final))
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=DatasetProvenance(
                instrument_id=instrument_id,
                requested_binding=binding.binding_id,
                resolved_binding=binding.binding_id,
                dataset_fingerprint=entry.fingerprint,
                freshness_mode="cached" if cached else "polled",
                as_of=bars[-1].end_time if bars else received,
                received_at=received,
                cached=cached,
                history_complete=bool(payload.get("history_complete")),
            ),
            interval=interval,
            bars=bars,
        )


    def get_intraday_bars_range(
        self,
        instrument_id: str,
        *,
        start: datetime,
        end: datetime,
        include_extended_hours: bool = True,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        """Fetch a fresh Yahoo 1m interval bounded to the requested causal window.

        This path deliberately bypasses the disposable response cache.  It is
        used only for exact-gap repair and immediately persists every finalized
        bar into the durable Yahoo evidence store.
        """

        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Yahoo exact-range timestamps must be timezone-aware")
        start_utc = start.astimezone(timezone.utc)
        end_utc = end.astimezone(timezone.utc)
        if end_utc <= start_utc:
            raise ValueError("Yahoo exact-range end must be after start")
        instrument = instrument_by_id(instrument_id)
        binding = self.get_binding(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")

        result, received = fetch_yahoo_chart_result(
            self.runtime,
            binding.provider_symbol,
            interval="1m",
            start=start_utc,
            end=end_utc,
            include_prepost=include_extended_hours,
            events="",
            cancellation=cancellation,
        )
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not isinstance(quote, dict):
            raise ProviderContractError("Yahoo exact-range quote payload is malformed")

        timestamps = result.get("timestamp") or []
        arrays = [quote.get(name) or [] for name in ("open", "high", "low", "close", "volume")]
        bars: list[MarketBar] = []
        for index, raw_timestamp in enumerate(timestamps):
            if any(not isinstance(values, list) or index >= len(values) for values in arrays):
                continue
            open_raw, high_raw, low_raw, close_raw, volume_raw = (
                values[index] for values in arrays
            )
            if None in (open_raw, high_raw, low_raw, close_raw):
                continue
            try:
                open_value = Decimal(str(open_raw))
                high = Decimal(str(high_raw))
                low = Decimal(str(low_raw))
                close = Decimal(str(close_raw))
                volume = Decimal(str(volume_raw or 0))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if (
                not all(value.is_finite() for value in (open_value, high, low, close, volume))
                or high < max(open_value, close)
                or low > min(open_value, close)
                or volume < 0
            ):
                continue
            provider_time = datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc)
            if not start_utc <= provider_time < end_utc:
                continue
            bar_start, bar_end, session = equity_bar_times(
                provider_time,
                "1m",
                instrument.exchange_timezone,
            )
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval="1m",
                    start_time=bar_start,
                    end_time=bar_end,
                    open=open_value,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    is_final=is_final_bar(bar_end, received),
                    adjustment_mode=AdjustmentMode.RAW,
                    session=session,
                    provider=self.provider_id,
                    provider_event_id=str(raw_timestamp),
                    received_at=received,
                )
            )
        bars.sort(key=lambda bar: bar.start_time)
        finalized = tuple(bar for bar in bars if bar.is_final)
        self.evidence_store.persist_market_bars(finalized)
        fingerprint = TradingMarketDataCache.fingerprint(
            {
                "instrument_id": instrument_id,
                "interval": "1m",
                "start": start_utc.isoformat(),
                "end": end_utc.isoformat(),
                "bars": [
                    {
                        "start_time": bar.start_time.isoformat(),
                        "open": str(bar.open),
                        "high": str(bar.high),
                        "low": str(bar.low),
                        "close": str(bar.close),
                        "volume": str(bar.volume),
                    }
                    for bar in finalized
                ],
            }
        )
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=DatasetProvenance(
                instrument_id=instrument_id,
                requested_binding=binding.binding_id,
                resolved_binding=binding.binding_id,
                dataset_fingerprint=fingerprint,
                freshness_mode="polled",
                as_of=finalized[-1].end_time if finalized else received,
                received_at=received,
                cached=False,
                history_complete=False,
            ),
            interval="1m",
            bars=list(finalized),
        )


    def get_quote(
        self,
        instrument_id: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        response = self.get_bars(instrument_id, "1d", 2, cancellation)
        if not response.bars:
            raise ProviderDataUnavailableError("Yahoo returned no quote bars")
        last = response.bars[-1]
        return {
            "instrument_id": instrument_id,
            "binding_id": response.binding.binding_id,
            "provider": self.provider_id,
            "price": str(last.close),
            "received_at": response.provenance.received_at.isoformat(),
            "source_time": last.end_time.isoformat(),
            "freshness_mode": response.provenance.freshness_mode,
        }

    def get_currency_rate(
        self,
        base_currency: str,
        quote_currency: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        base = base_currency.strip().upper()
        quote = quote_currency.strip().upper()
        if base in STABLE_CURRENCY_CODES:
            base = "USD"
        if quote in STABLE_CURRENCY_CODES:
            quote = "USD"
        if not base or not quote or not base.isalpha() or not quote.isalpha():
            raise ValueError("currency codes must contain letters only")
        if base == quote:
            return {
                "base_currency": base,
                "quote_currency": quote,
                "rate": 1.0,
                "provider": self.provider_id,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "freshness_mode": "identity",
            }

        key = self.cache.key("yahoo_fx", base, quote)

        def load() -> dict[str, Any]:
            def read_pair(pair_base: str, pair_quote: str) -> float:
                response = self.runtime.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{pair_base}{pair_quote}=X",
                    params={"range": "5d", "interval": "1d", "events": ""},
                    headers={"User-Agent": "Mozilla/5.0 Omnix local research"},
                    timeout=20,
                    cancellation=cancellation,
                )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ProviderContractError("Yahoo returned invalid FX JSON") from exc
                result = ((payload.get("chart") or {}).get("result") or [None])[0]
                if not isinstance(result, dict):
                    raise ProviderDataUnavailableError("Yahoo returned no FX result")
                quote_payload = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                closes = quote_payload.get("close") if isinstance(quote_payload, dict) else None
                if not isinstance(closes, list):
                    raise ProviderContractError("Yahoo FX payload is malformed")
                for value in reversed(closes):
                    try:
                        rate = float(value)
                    except (TypeError, ValueError):
                        continue
                    if rate > 0:
                        return rate
                raise ProviderDataUnavailableError("Yahoo returned no FX close")

            try:
                return {"rate": read_pair(base, quote)}
            except (ProviderFallbackEligibleError, httpx.HTTPError):
                inverse = read_pair(quote, base)
                if inverse <= 0:
                    raise ProviderDataUnavailableError("Yahoo returned an invalid FX rate")
                return {"rate": 1.0 / inverse}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=300,
            source="yahoo_fx",
        )
        return {
            "base_currency": base,
            "quote_currency": quote,
            "rate": float(payload["rate"]),
            "provider": self.provider_id,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "freshness_mode": "cached" if cached else "polled",
        }


class StooqEquityProvider(ProviderAdapter):
    provider_id = "stooq"
    policy = POLICIES["stooq"]

    def __init__(
        self,
        *,
        session: Any | None = None,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
    ) -> None:
        self.runtime = runtime or ProviderHttpRuntime(
            self.provider_id,
            session=session,
            max_concurrency=2,
        )
        self.session = self.runtime.session
        self.cache = cache or TradingMarketDataCache()

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next(
            (
                item
                for item in bindings_for_instrument(instrument_id)
                if item.provider == self.provider_id
            ),
            None,
        )
        if binding is None:
            raise ValueError(f"Stooq does not support instrument: {instrument_id}")
        return binding

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        if interval != "1d":
            raise ValueError("Stooq supports daily bars only")
        instrument = instrument_by_id(instrument_id)
        binding = self.get_binding(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        clean_limit = max(1, min(int(limit), 5_000))
        key = self.cache.key(
            binding.binding_id,
            instrument_id,
            interval,
            AdjustmentMode.RAW.value,
            instrument.session_calendar,
            clean_limit,
        )

        def load() -> dict[str, Any]:
            response = self.runtime.get(
                "https://stooq.com/q/d/l/",
                params={"s": binding.provider_symbol.lower(), "i": "d"},
                timeout=20,
                cancellation=cancellation,
            )
            rows = list(csv.DictReader(StringIO(response.text)))
            required = {"Date", "Open", "High", "Low", "Close"}
            rows = [row for row in rows if required.issubset(row) and row.get("Date")]
            if not rows:
                raise ProviderDataUnavailableError(
                    f"Stooq returned no bars for {instrument_id}"
                )
            return {"rows": rows[-clean_limit:], "history_complete": len(rows) < clean_limit}

        payload, entry, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=3_600,
            source="stooq_daily_csv",
        )
        received = datetime.now(timezone.utc)
        bars: list[MarketBar] = []
        for row in payload["rows"]:
            session_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            start, end = equity_session_bounds(
                session_date,
                instrument.exchange_timezone,
            )
            bars.append(
                MarketBar(
                    instrument_id=instrument_id,
                    interval="1d",
                    start_time=start,
                    end_time=end,
                    open=Decimal(row["Open"]),
                    high=Decimal(row["High"]),
                    low=Decimal(row["Low"]),
                    close=Decimal(row["Close"]),
                    volume=Decimal(row.get("Volume") or "0"),
                    is_final=is_final_bar(end, received),
                    adjustment_mode=AdjustmentMode.RAW,
                    session="regular",
                    provider=self.provider_id,
                    provider_event_id=row["Date"],
                    received_at=received,
                )
            )
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=DatasetProvenance(
                instrument_id=instrument_id,
                requested_binding=binding.binding_id,
                resolved_binding=binding.binding_id,
                dataset_fingerprint=entry.fingerprint,
                freshness_mode="cached" if cached else "polled",
                as_of=bars[-1].end_time if bars else received,
                received_at=received,
                cached=cached,
                history_complete=bool(payload.get("history_complete")),
            ),
            interval="1d",
            bars=bars,
        )

    def get_quote(
        self,
        instrument_id: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        response = self.get_bars(instrument_id, "1d", 1, cancellation)
        if not response.bars:
            raise ProviderDataUnavailableError("Stooq returned no quote bars")
        last = response.bars[-1]
        return {
            "instrument_id": instrument_id,
            "binding_id": response.binding.binding_id,
            "provider": self.provider_id,
            "price": str(last.close),
            "received_at": response.provenance.received_at.isoformat(),
            "source_time": last.end_time.isoformat(),
            "freshness_mode": response.provenance.freshness_mode,
        }
