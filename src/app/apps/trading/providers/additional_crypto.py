from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import POLICIES, bindings_for_instrument, instrument_by_id
from app.apps.trading.models import BarsResponse, DatasetProvenance, MarketBar, ProviderBinding

from .base import ProviderAdapter
from .bar_semantics import continuous_bar_end, is_final_bar
from .errors import ProviderContractError, ProviderDataUnavailableError
from .http_runtime import ProviderHttpRuntime


INTERVALS: dict[str, dict[str, Any]] = {
    "coinbase": {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400},
    "kraken": {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440},
    "hyperliquid": {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"},
}


class AdditionalCryptoProvider(ProviderAdapter):
    def __init__(
        self,
        provider_id: str,
        *,
        session: Any | None = None,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
    ) -> None:
        if provider_id not in INTERVALS:
            raise ValueError(f"unsupported crypto provider: {provider_id}")
        self.provider_id = provider_id
        self.policy = POLICIES[provider_id]
        self.runtime = runtime or ProviderHttpRuntime(
            provider_id,
            session=session,
            max_concurrency=3,
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
            raise ValueError(f"{self.provider_id} does not support instrument: {instrument_id}")
        return binding

    def _rows(
        self,
        binding: ProviderBinding,
        interval: str,
        limit: int,
        cancellation: threading.Event | None,
    ) -> list[Any]:
        provider_interval = INTERVALS[self.provider_id].get(interval)
        if provider_interval is None:
            raise ValueError(f"unsupported {self.provider_id} interval: {interval}")
        if self.provider_id == "coinbase":
            response = self.runtime.get(
                f"https://api.exchange.coinbase.com/products/{binding.provider_symbol}/candles",
                params={"granularity": provider_interval},
                timeout=20,
                cancellation=cancellation,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Coinbase returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise ProviderContractError("Coinbase candles payload must be a list")
            return sorted(payload, key=lambda row: row[0])[-limit:]
        if self.provider_id == "kraken":
            response = self.runtime.get(
                "https://api.kraken.com/0/public/OHLC",
                params={
                    "pair": binding.provider_symbol.replace("-", ""),
                    "interval": provider_interval,
                },
                timeout=20,
                cancellation=cancellation,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Kraken returned invalid JSON") from exc
            if payload.get("error"):
                raise ProviderDataUnavailableError(str(payload["error"]))
            result = payload.get("result") or {}
            if not isinstance(result, dict):
                raise ProviderContractError("Kraken OHLC result is malformed")
            rows = result.get(next((key for key in result if key != "last"), ""), [])
            if not isinstance(rows, list):
                raise ProviderContractError("Kraken OHLC rows must be a list")
            return rows[-limit:]
        response = self.runtime.post(
            "https://api.hyperliquid.xyz/info",
            json={
                "type": "candleSnapshot",
                "req": {
                    "coin": binding.provider_symbol,
                    "interval": provider_interval,
                    "startTime": 0,
                    "endTime": int(datetime.now(timezone.utc).timestamp() * 1000),
                },
            },
            timeout=20,
            cancellation=cancellation,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderContractError("Hyperliquid returned invalid JSON") from exc
        if not isinstance(payload, list):
            raise ProviderContractError("Hyperliquid candle payload must be a list")
        return payload[-limit:]

    def get_bars(
        self,
        instrument_id: str,
        interval: str,
        limit: int = 500,
        cancellation: threading.Event | None = None,
    ) -> BarsResponse:
        instrument = instrument_by_id(instrument_id)
        binding = self.get_binding(instrument_id)
        if instrument is None:
            raise ValueError(f"unknown instrument: {instrument_id}")
        clean_limit = max(1, min(int(limit), 1_000))
        key = self.cache.key(
            binding.binding_id,
            instrument_id,
            interval,
            "raw",
            instrument.session_calendar,
            clean_limit,
        )

        def load() -> dict[str, Any]:
            rows = self._rows(binding, interval, clean_limit, cancellation)
            if not rows:
                raise ProviderDataUnavailableError(
                    f"{self.provider_id} returned no bars for {instrument_id} {interval}"
                )
            return {"rows": rows, "history_complete": len(rows) < clean_limit}

        payload, entry, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=30,
            source=f"{self.provider_id}_candles",
        )
        received = datetime.now(timezone.utc)
        deduplicated: dict[int, MarketBar] = {}
        for row in payload["rows"]:
            if self.provider_id == "coinbase":
                if not isinstance(row, list) or len(row) < 6:
                    raise ProviderContractError("Coinbase returned a malformed candle")
                timestamp, low, high, open_value, close, volume = row[:6]
                provider_end = None
            elif self.provider_id == "kraken":
                if not isinstance(row, list) or len(row) < 7:
                    raise ProviderContractError("Kraken returned a malformed candle")
                timestamp, open_value, high, low, close, _vwap, volume = row[:7]
                provider_end = None
            else:
                if not isinstance(row, dict) or not {"t", "o", "h", "l", "c", "v"}.issubset(row):
                    raise ProviderContractError("Hyperliquid returned a malformed candle")
                timestamp = int(row["t"]) / 1000
                provider_end = (
                    datetime.fromtimestamp(int(row["T"]) / 1000, tz=timezone.utc)
                    if row.get("T") is not None
                    else None
                )
                open_value, high, low, close, volume = (
                    row["o"],
                    row["h"],
                    row["l"],
                    row["c"],
                    row["v"],
                )
            start = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            end = provider_end or continuous_bar_end(start, interval)
            deduplicated[int(start.timestamp())] = MarketBar(
                instrument_id=instrument_id,
                interval=interval,
                start_time=start,
                end_time=end,
                open=Decimal(str(open_value)),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                close=Decimal(str(close)),
                volume=Decimal(str(volume)),
                is_final=is_final_bar(end, received),
                session="24x7",
                provider=self.provider_id,
                provider_event_id=str(timestamp),
                received_at=received,
            )
        bars = [deduplicated[key] for key in sorted(deduplicated)]
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

    def get_quote(
        self,
        instrument_id: str,
        cancellation: threading.Event | None = None,
    ) -> dict[str, Any]:
        response = self.get_bars(instrument_id, "1m", 1, cancellation)
        if not response.bars:
            raise ProviderDataUnavailableError(
                f"{self.provider_id} returned no quote bars"
            )
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
