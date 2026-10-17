from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import BINANCE_POLICY, bindings_for_instrument, instrument_by_id, search_instruments
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar, ProviderBinding

from .base import ProviderAdapter
from .bar_semantics import is_final_bar
from .errors import ProviderContractError, ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime


INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
    "3d": 259_200,
    "1w": 604_800,
    "1mo": 2_592_000,
}

BINANCE_INTERVALS = {"1mo": "1M"}



def _request_weight(path: str, params: dict[str, Any]) -> int:
    """Binance's request weight for the spot endpoints this provider calls (WP-8.3)."""
    if path == "/api/v3/ticker/24hr" and not (params.get("symbol") or params.get("symbols")):
        return 80
    return 2

class BinanceMarketDataProvider(ProviderAdapter):
    provider_id = "binance"
    policy = BINANCE_POLICY

    def __init__(
        self,
        *,
        session: Any | None = None,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        base_url: str = "https://api.binance.com",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.runtime = runtime or ProviderHttpRuntime(
            self.provider_id,
            session=session,
            max_concurrency=4,
        )
        self.session = self.runtime.session
        self.cache = cache or TradingMarketDataCache()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def search_instruments(self, query: str):
        return search_instruments(query)

    def get_binding(self, instrument_id: str) -> ProviderBinding:
        binding = next(
            (item for item in bindings_for_instrument(instrument_id) if item.provider == self.provider_id),
            None,
        )
        if binding is None:
            raise ValueError(f"unsupported Binance instrument: {instrument_id}")
        return binding

    def _get_json(
        self,
        path: str,
        params: dict[str, Any],
        cancellation: threading.Event | None = None,
    ) -> Any:
        response = self.runtime.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout_seconds,
            cancellation=cancellation,
            weight=_request_weight(path, params),
        )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderContractError("Binance returned invalid JSON") from exc

    def _history_rows(
        self,
        symbol: str,
        interval: str,
        limit: int,
        cancellation: threading.Event | None = None,
    ) -> list[list[Any]]:
        remaining = max(1, min(int(limit), 5_000))
        end_time: int | None = None
        rows: list[list[Any]] = []
        while remaining > 0:
            page_limit = min(1_000, remaining)
            params: dict[str, Any] = {
                "symbol": symbol,
                "interval": BINANCE_INTERVALS.get(interval, interval),
                "limit": page_limit,
            }
            if end_time is not None:
                params["endTime"] = end_time
            page = self._get_json("/api/v3/klines", params, cancellation)
            if not isinstance(page, list):
                raise ProviderContractError("Binance klines payload must be a list")
            if not page:
                break
            if any(not isinstance(row, list) or len(row) < 7 for row in page):
                raise ProviderContractError("Binance returned a malformed kline")
            rows = page + rows
            remaining -= len(page)
            if len(page) < page_limit:
                break
            next_end = int(page[0][0]) - 1
            if next_end == end_time:
                break
            end_time = next_end
        deduplicated = {int(row[0]): row for row in rows}
        return [deduplicated[key] for key in sorted(deduplicated)][-limit:]

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"unsupported Binance interval: {interval}")
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        binding = self.get_binding(instrument_id)
        clean_limit = max(1, min(int(limit), 5_000))
        cache_key = self.cache.key(
            binding.binding_id,
            instrument_id,
            interval,
            "raw",
            instrument.session_calendar,
            clean_limit,
        )

        def load() -> dict[str, Any]:
            rows = self._history_rows(
                binding.provider_symbol,
                interval,
                clean_limit,
                cancellation,
            )
            if not rows:
                raise ProviderDataUnavailableError(
                    f"Binance returned no bars for {instrument_id} {interval}"
                )
            return {"rows": rows, "history_complete": len(rows) < clean_limit}

        payload, entry, cached = self.cache.get_or_load(
            cache_key,
            load,
            ttl_seconds=max(5, min(INTERVAL_SECONDS[interval] / 2, 300)),
            source="binance_klines",
        )
        received_at = datetime.now(timezone.utc)
        bars: list[MarketBar] = []
        for row in payload["rows"]:
            start = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            end = datetime.fromtimestamp(int(row[6]) / 1000, tz=timezone.utc)
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
                    is_final=is_final_bar(end, received_at),
                    session="24x7",
                    provider=self.provider_id,
                    provider_event_id=str(row[0]),
                    ingestion_revision=1,
                    received_at=received_at,
                )
            )
        as_of = bars[-1].end_time if bars else received_at
        provenance = DatasetProvenance(
            instrument_id=instrument_id,
            requested_binding=binding.binding_id,
            resolved_binding=binding.binding_id,
            dataset_fingerprint=entry.fingerprint,
            freshness_mode="cached" if cached else "polled",
            as_of=as_of,
            received_at=received_at,
            cached=cached,
            history_complete=bool(payload.get("history_complete")),
        )
        return BarsResponse(
            instrument=instrument,
            binding=binding,
            provenance=provenance,
            interval=interval,
            bars=bars,
        )

    def get_quote(
        self,
        instrument_id: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        instrument = instrument_by_id(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        binding = self.get_binding(instrument_id)
        payload = self._get_json(
            "/api/v3/ticker/24hr",
            {"symbol": binding.provider_symbol},
            cancellation,
        )
        if not isinstance(payload, dict) or "lastPrice" not in payload:
            raise ProviderContractError("Binance returned a malformed quote")
        return {
            "instrument_id": instrument_id,
            "binding_id": binding.binding_id,
            "provider": self.provider_id,
            "price": str(payload["lastPrice"]),
            "bid": str(payload.get("bidPrice") or "0"),
            "ask": str(payload.get("askPrice") or "0"),
            "change_percent_24h": str(payload.get("priceChangePercent") or "0"),
            "volume_24h": str(payload.get("volume") or "0"),
            "received_at": datetime.now(timezone.utc).isoformat(),
            "freshness_mode": "polled",
        }
