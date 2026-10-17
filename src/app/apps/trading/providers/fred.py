"""FRED economic series as chart symbols (TVP-10.5, decision D-3).

``economic:FRED:<series>`` charts a series' observations as daily bars at their dates (monthly CPI is a bar a month,
daily yields a bar a trading day): each bar opens at the observation before and closes at its own, so a candle shows
the change and a line the level. Weekly and monthly charts aggregate those bars. FRED needs the owner's free API key
(Settings › Trading & Market Data, or ``OMNIX_FRED_API_KEY``). Economic series are research data: their quotes are
the latest observation, and the registry refuses them any execution binding.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import FRED_POLICY, bindings_for_instrument, instrument_by_id
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar, ProviderBinding

from .bar_semantics import is_final_bar
from .base import ProviderAdapter
from .errors import ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
MISSING_KEY = "Add a FRED API key in Settings › Trading & Market Data to chart economic series."
CACHE_SECONDS = 3_600
SEARCH_LIMIT = 15


def fred_configured() -> bool:
    from app.apps.trading.economic_calendar import fred_api_key

    return bool(fred_api_key())


def observation_rows(payload: dict[str, Any]) -> list[tuple[date, Decimal]]:
    """A series' observations in date order; FRED's "." (no value) is skipped."""
    rows = []
    for item in payload.get("observations") or []:
        try:
            rows.append((date.fromisoformat(str(item["date"])), Decimal(str(item["value"]))))
        except (KeyError, ValueError, InvalidOperation):
            continue
    rows.sort(key=lambda row: row[0])
    return rows


class FredSeriesProvider(ProviderAdapter):
    provider_id = "fred"
    policy = FRED_POLICY
    display_name = "FRED"

    def __init__(self, *, cache: TradingMarketDataCache | None = None, runtime: ProviderHttpRuntime | None = None, key: Any = None) -> None:
        self.runtime = runtime or ProviderHttpRuntime("fred_series", max_concurrency=2)
        self.cache = cache or TradingMarketDataCache()
        if key is None:
            from app.apps.trading.economic_calendar import fred_api_key

            key = fred_api_key
        self.key = key

    def configured(self) -> bool:
        return bool(self.key())

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next((item for item in bindings_for_instrument(instrument_id) if item.provider == self.provider_id), None)
        if binding is None:
            raise ValueError(f"FRED does not support instrument: {instrument_id}")
        return binding

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        key = self.key()
        if not key:
            raise ValueError(MISSING_KEY)
        payload = self.runtime.get(f"{FRED_BASE_URL}/{path}", params={**params, "api_key": key, "file_type": "json"}, timeout=30).json()
        return payload if isinstance(payload, dict) else {}

    def search(self, query: str) -> list[tuple[str, str]]:
        """(series id, title) for FRED's most popular series matching ``query``; empty without a key."""
        if not self.key() or len(query.strip()) < 2:
            return []
        payload = self._get("series/search", {"search_text": query.strip(), "limit": SEARCH_LIMIT, "order_by": "popularity", "sort_order": "desc"})
        return [(str(item["id"]).upper(), str(item.get("title") or "")) for item in payload.get("seriess") or [] if item.get("id")]

    def get_bars(self, instrument_id: str, interval: str, limit: int = 500, cancellation: threading.Event | None = None) -> BarsResponse:
        if interval != "1d":
            raise ValueError("FRED series are served as daily observations; longer intervals aggregate them")
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        binding = self.get_binding(instrument_id)
        clean_limit = max(1, min(int(limit), 20_000))

        def load() -> dict[str, Any]:
            rows = observation_rows(self._get("series/observations", {"series_id": binding.provider_symbol}))
            if not rows:
                raise ProviderDataUnavailableError(f"FRED returned no observations for {binding.provider_symbol}")
            return {"rows": [{"date": day.isoformat(), "value": str(value)} for day, value in rows]}

        payload, entry, cached = self.cache.get_or_load(
            self.cache.key(binding.binding_id, instrument_id, "observations"), load, ttl_seconds=CACHE_SECONDS, source="fred_series_observations",
        )
        received_at = datetime.now(timezone.utc)
        rows = payload["rows"]
        start_index = max(0, len(rows) - clean_limit)
        previous = Decimal(rows[start_index - 1]["value"]) if start_index > 0 else None
        bars: list[MarketBar] = []
        for row in rows[start_index:]:
            start = datetime.combine(date.fromisoformat(row["date"]), time.min, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            value = Decimal(row["value"])
            opening = previous if previous is not None else value
            bars.append(MarketBar(
                instrument_id=instrument_id, interval=interval, start_time=start, end_time=end, open=opening,
                high=max(opening, value), low=min(opening, value), close=value, volume=Decimal("0"),
                is_final=is_final_bar(end, received_at), session="24x7", provider=self.provider_id,
                provider_event_id=row["date"], received_at=received_at,
            ))
            previous = value
        provenance = DatasetProvenance(
            instrument_id=instrument_id, requested_binding=binding.binding_id, resolved_binding=binding.binding_id,
            dataset_fingerprint=entry.fingerprint, freshness_mode="cached" if cached else "polled", as_of=bars[-1].end_time,
            received_at=received_at, delay_seconds=self.policy.delay_seconds, cached=cached, history_complete=start_index == 0,
        )
        return BarsResponse(instrument=instrument, binding=binding, provenance=provenance, interval=interval, bars=bars)

    def get_quote(self, instrument_id: str, cancellation: threading.Event | None = None) -> dict[str, Any]:
        result = self.get_bars(instrument_id, "1d", 1, cancellation)
        bar = result.bars[-1]
        return {
            "instrument_id": instrument_id, "binding_id": result.binding.binding_id, "provider": self.provider_id, "price": str(bar.close),
            "received_at": result.provenance.received_at.isoformat(), "freshness_mode": result.provenance.freshness_mode,
        }


__all__ = ["FredSeriesProvider", "fred_configured", "observation_rows"]
