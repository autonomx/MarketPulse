from __future__ import annotations

from app.config.env import env_str, environment

import asyncio
import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.apps.trading.cache import TradingMarketDataCache
from app.apps.trading.catalog import bindings_for_instrument, instrument_by_id
from app.apps.trading.providers.errors import ProviderContractError, ProviderDataUnavailableError
from app.apps.trading.providers.http_runtime import ProviderHttpRuntime


class MarketMetricPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    time: datetime
    value: Decimal

    @field_validator("time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("metric timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class MarketMetricSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    unit: str | None = None
    kind: Literal["line", "histogram"] = "line"
    points: list[MarketMetricPoint] = Field(default_factory=list)


class MarketMetricResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    metric: str
    provider: str
    interval: str
    series: list[MarketMetricSeries]
    received_at: datetime
    freshness_mode: Literal["live", "polled", "cached", "runtime"]
    history_complete: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


def _utc_from_milliseconds(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _utc_from_seconds(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderContractError(f"invalid numeric {field}") from exc
    if not result.is_finite():
        raise ProviderContractError(f"non-finite numeric {field}")
    return result


def _metric_response(
    *,
    instrument_id: str,
    metric: str,
    provider: str,
    interval: str,
    series: list[MarketMetricSeries],
    freshness_mode: Literal["live", "polled", "cached", "runtime"],
    history_complete: bool = False,
    metadata: dict[str, Any] | None = None,
) -> MarketMetricResponse:
    return MarketMetricResponse(
        instrument_id=instrument_id,
        metric=metric,
        provider=provider,
        interval=interval,
        series=series,
        received_at=datetime.now(timezone.utc),
        freshness_mode=freshness_mode,
        history_complete=history_complete,
        metadata=metadata or {},
    )


BINANCE_DERIVATIVE_PERIODS: tuple[tuple[str, int], ...] = (
    ("5m", 300),
    ("15m", 900),
    ("30m", 1_800),
    ("1h", 3_600),
    ("2h", 7_200),
    ("4h", 14_400),
    ("6h", 21_600),
    ("12h", 43_200),
    ("1d", 86_400),
)

INTERVAL_SECONDS: dict[str, int] = {
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


def _binance_period(interval: str) -> str:
    target = INTERVAL_SECONDS.get(interval, 3_600)
    return min(BINANCE_DERIVATIVE_PERIODS, key=lambda item: abs(item[1] - target))[0]


def _binance_kline_interval(interval: str) -> str:
    if interval == "1mo":
        return "1M"
    return interval if interval in INTERVAL_SECONDS else "1h"


def _binance_symbol(instrument_id: str) -> str:
    instrument = instrument_by_id(instrument_id)
    if instrument is None or str(instrument.asset_class) != "crypto":
        raise ValueError("Binance derivatives metrics require a crypto instrument")
    if instrument.venue != "BINANCE":
        raise ValueError("Binance derivatives metrics require a Binance instrument")
    binding = next(
        (item for item in bindings_for_instrument(instrument_id) if item.provider == "binance"),
        None,
    )
    if binding is None:
        raise ValueError(f"Binance binding unavailable for {instrument_id}")
    return binding.provider_symbol.upper()


class BinanceDerivativesMetricAdapter:
    provider_id = "binance-futures"

    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 15.0,
        liquidation_buffer: "BinanceLiquidationBuffer | None" = None,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=128)
        self.runtime = runtime or ProviderHttpRuntime(self.provider_id, max_concurrency=4)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.liquidation_buffer = liquidation_buffer or BinanceLiquidationBuffer()

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
        )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderContractError("Binance Futures returned invalid JSON") from exc

    def _cached_json(
        self,
        *,
        key: str,
        path: str,
        params: dict[str, Any],
        ttl_seconds: float,
        cancellation: threading.Event | None,
    ) -> tuple[Any, bool]:
        def load() -> dict[str, Any]:
            return {"payload": self._get_json(path, params, cancellation)}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=ttl_seconds,
            source=f"binance_futures:{path}",
        )
        return payload.get("payload"), cached

    @staticmethod
    def _bounded_limit(limit: int, maximum: int = 500) -> int:
        return max(1, min(int(limit), maximum))

    @staticmethod
    def _end_time_params(end_time: datetime | None) -> dict[str, int]:
        if end_time is None:
            return {}
        return {"endTime": int(end_time.timestamp() * 1000)}

    def open_interest(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        symbol = _binance_symbol(instrument_id)
        period = _binance_period(interval)
        params = {
            "symbol": symbol,
            "period": period,
            "limit": self._bounded_limit(limit),
            **self._end_time_params(end_time),
        }
        payload, cached = self._cached_json(
            key=self.cache.key("metric", "binance", "open-interest", symbol, period, params.get("endTime"), params["limit"]),
            path="/futures/data/openInterestHist",
            params=params,
            ttl_seconds=30,
            cancellation=cancellation,
        )
        if not isinstance(payload, list):
            raise ProviderContractError("Binance open interest payload must be a list")
        points: list[MarketMetricPoint] = []
        for row in payload:
            if not isinstance(row, dict) or "timestamp" not in row or "sumOpenInterest" not in row:
                continue
            points.append(
                MarketMetricPoint(
                    time=_utc_from_milliseconds(row["timestamp"]),
                    value=_decimal(row["sumOpenInterest"], field="sumOpenInterest"),
                )
            )
        if not points:
            raise ProviderDataUnavailableError("Binance returned no open-interest history")
        return _metric_response(
            instrument_id=instrument_id,
            metric="binance.open_interest",
            provider=self.provider_id,
            interval=period,
            series=[MarketMetricSeries(key="open-interest", title="Open Interest", unit="contracts", points=points)],
            freshness_mode="cached" if cached else "polled",
            metadata={"symbol": symbol, "history_window": "Binance latest-month futures statistics"},
        )

    def funding_rate(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        symbol = _binance_symbol(instrument_id)
        params = {
            "symbol": symbol,
            "limit": self._bounded_limit(limit, 1_000),
            **self._end_time_params(end_time),
        }
        payload, cached = self._cached_json(
            key=self.cache.key("metric", "binance", "funding-rate", symbol, params.get("endTime"), params["limit"]),
            path="/fapi/v1/fundingRate",
            params=params,
            ttl_seconds=60,
            cancellation=cancellation,
        )
        if not isinstance(payload, list):
            raise ProviderContractError("Binance funding-rate payload must be a list")
        points = [
            MarketMetricPoint(
                time=_utc_from_milliseconds(row["fundingTime"]),
                value=_decimal(row["fundingRate"], field="fundingRate") * Decimal("100"),
            )
            for row in payload
            if isinstance(row, dict) and row.get("fundingTime") is not None and row.get("fundingRate") is not None
        ]
        if not points:
            raise ProviderDataUnavailableError("Binance returned no funding-rate history")
        return _metric_response(
            instrument_id=instrument_id,
            metric="binance.funding_rate",
            provider=self.provider_id,
            interval=interval,
            series=[MarketMetricSeries(key="funding-rate", title="Funding Rate", unit="%", points=points)],
            freshness_mode="cached" if cached else "polled",
            metadata={"symbol": symbol},
        )

    def long_short_ratio(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        scope: Literal["global_accounts", "top_accounts", "top_positions"],
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        symbol = _binance_symbol(instrument_id)
        period = _binance_period(interval)
        paths = {
            "global_accounts": "/futures/data/globalLongShortAccountRatio",
            "top_accounts": "/futures/data/topLongShortAccountRatio",
            "top_positions": "/futures/data/topLongShortPositionRatio",
        }
        path = paths[scope]
        params = {
            "symbol": symbol,
            "period": period,
            "limit": self._bounded_limit(limit),
            **self._end_time_params(end_time),
        }
        payload, cached = self._cached_json(
            key=self.cache.key("metric", "binance", scope, symbol, period, params.get("endTime"), params["limit"]),
            path=path,
            params=params,
            ttl_seconds=60,
            cancellation=cancellation,
        )
        if not isinstance(payload, list):
            raise ProviderContractError("Binance long/short payload must be a list")
        ratio_points: list[MarketMetricPoint] = []
        long_points: list[MarketMetricPoint] = []
        short_points: list[MarketMetricPoint] = []
        for row in payload:
            if not isinstance(row, dict) or row.get("timestamp") is None:
                continue
            moment = _utc_from_milliseconds(row["timestamp"])
            if row.get("longShortRatio") is not None:
                ratio_points.append(MarketMetricPoint(time=moment, value=_decimal(row["longShortRatio"], field="longShortRatio")))
            if row.get("longAccount") is not None:
                long_points.append(MarketMetricPoint(time=moment, value=_decimal(row["longAccount"], field="longAccount") * Decimal("100")))
            if row.get("shortAccount") is not None:
                short_points.append(MarketMetricPoint(time=moment, value=_decimal(row["shortAccount"], field="shortAccount") * Decimal("100")))
        if not ratio_points and not long_points:
            raise ProviderDataUnavailableError("Binance returned no long/short history")
        series = []
        if ratio_points:
            series.append(MarketMetricSeries(key="ratio", title="Long / Short Ratio", unit="ratio", points=ratio_points))
        if long_points:
            series.append(MarketMetricSeries(key="long-percent", title="Long %", unit="%", points=long_points))
        if short_points:
            series.append(MarketMetricSeries(key="short-percent", title="Short %", unit="%", points=short_points))
        return _metric_response(
            instrument_id=instrument_id,
            metric=f"binance.{scope}",
            provider=self.provider_id,
            interval=period,
            series=series,
            freshness_mode="cached" if cached else "polled",
            metadata={"symbol": symbol, "scope": scope},
        )

    def basis(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        symbol = _binance_symbol(instrument_id)
        period = _binance_period(interval)
        params = {
            "pair": symbol,
            "contractType": "PERPETUAL",
            "period": period,
            "limit": self._bounded_limit(limit),
            **self._end_time_params(end_time),
        }
        payload, cached = self._cached_json(
            key=self.cache.key("metric", "binance", "basis", symbol, period, params.get("endTime"), params["limit"]),
            path="/futures/data/basis",
            params=params,
            ttl_seconds=60,
            cancellation=cancellation,
        )
        if not isinstance(payload, list):
            raise ProviderContractError("Binance basis payload must be a list")
        points = [
            MarketMetricPoint(
                time=_utc_from_milliseconds(row["timestamp"]),
                value=_decimal(row.get("basisRate", row.get("basis")), field="basisRate") * Decimal("100"),
            )
            for row in payload
            if isinstance(row, dict) and row.get("timestamp") is not None and (row.get("basisRate") is not None or row.get("basis") is not None)
        ]
        if not points:
            raise ProviderDataUnavailableError("Binance returned no basis history")
        return _metric_response(
            instrument_id=instrument_id,
            metric="binance.basis",
            provider=self.provider_id,
            interval=period,
            series=[MarketMetricSeries(key="basis", title="Perpetual Basis", unit="%", points=points)],
            freshness_mode="cached" if cached else "polled",
            metadata={"symbol": symbol, "contract_type": "PERPETUAL"},
        )

    def price_index(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        source: Literal["mark", "index", "premium"],
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        symbol = _binance_symbol(instrument_id)
        paths = {
            "mark": ("/fapi/v1/markPriceKlines", "symbol"),
            "index": ("/fapi/v1/indexPriceKlines", "pair"),
            "premium": ("/fapi/v1/premiumIndexKlines", "symbol"),
        }
        path, symbol_param = paths[source]
        provider_interval = _binance_kline_interval(interval)
        params = {
            symbol_param: symbol,
            "interval": provider_interval,
            "limit": self._bounded_limit(limit, 1_000),
            **self._end_time_params(end_time),
        }
        payload, cached = self._cached_json(
            key=self.cache.key("metric", "binance", source, symbol, provider_interval, params.get("endTime"), params["limit"]),
            path=path,
            params=params,
            ttl_seconds=30,
            cancellation=cancellation,
        )
        if not isinstance(payload, list):
            raise ProviderContractError(f"Binance {source} price payload must be a list")
        points = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 5:
                continue
            points.append(MarketMetricPoint(time=_utc_from_milliseconds(row[0]), value=_decimal(row[4], field=f"{source} close")))
        if not points:
            raise ProviderDataUnavailableError(f"Binance returned no {source} price history")
        title = {"mark": "Mark Price", "index": "Index Price", "premium": "Premium Index"}[source]
        unit = "%" if source == "premium" else None
        return _metric_response(
            instrument_id=instrument_id,
            metric=f"binance.{source}_price" if source != "premium" else "binance.premium",
            provider=self.provider_id,
            interval=provider_interval,
            series=[MarketMetricSeries(key=source, title=title, unit=unit, points=points)],
            freshness_mode="cached" if cached else "polled",
            metadata={"symbol": symbol},
        )

    def liquidations(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        del cancellation
        symbol = _binance_symbol(instrument_id)
        events = self.liquidation_buffer.snapshot(symbol)
        cutoff = end_time.astimezone(timezone.utc) if end_time is not None else None
        if cutoff is not None:
            events = [event for event in events if event.time <= cutoff]
        bucket_seconds = max(60, INTERVAL_SECONDS.get(interval, 3_600))
        buckets: dict[datetime, dict[str, Decimal]] = defaultdict(
            lambda: {"long": Decimal("0"), "short": Decimal("0")}
        )
        for event in events:
            timestamp = int(event.time.timestamp())
            bucket_time = datetime.fromtimestamp(timestamp - timestamp % bucket_seconds, tz=timezone.utc)
            buckets[bucket_time][event.liquidation_side] += event.notional
        ordered = sorted(buckets)[-max(1, min(int(limit), 1_500)) :]
        long_points = [MarketMetricPoint(time=moment, value=buckets[moment]["long"]) for moment in ordered]
        short_points = [MarketMetricPoint(time=moment, value=buckets[moment]["short"]) for moment in ordered]
        return _metric_response(
            instrument_id=instrument_id,
            metric="binance.liquidations",
            provider=self.provider_id,
            interval=interval,
            series=[
                MarketMetricSeries(key="long-liquidations", title="Long Liquidations", unit="quote notional", kind="histogram", points=long_points),
                MarketMetricSeries(key="short-liquidations", title="Short Liquidations", unit="quote notional", kind="histogram", points=short_points),
            ],
            freshness_mode="runtime",
            history_complete=False,
            metadata={
                "symbol": symbol,
                "history_scope": "runtime-only official Binance force-order stream",
                "collecting": self.liquidation_buffer.is_collecting(symbol),
                "event_count": len(events),
            },
        )


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    time: datetime
    liquidation_side: Literal["long", "short"]
    notional: Decimal


class BinanceLiquidationBuffer:
    """Bounded runtime collector for Binance USD-M force-order events.

    Binance exposes liquidation orders through the official force-order WebSocket
    stream. This collector intentionally does not fabricate pre-start history.
    """

    def __init__(
        self,
        *,
        base_url: str = "wss://fstream.binance.com/ws",
        retention: timedelta = timedelta(days=7),
        max_events_per_symbol: int = 50_000,
        connect_factory: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retention = retention
        self.max_events_per_symbol = max(100, int(max_events_per_symbol))
        self.connect_factory = connect_factory
        self._events: dict[str, deque[LiquidationEvent]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._runs: dict[str, Any] = {}
        self._stopping = threading.Event()
        self._lock = threading.RLock()

    @staticmethod
    def parse(payload: dict[str, Any]) -> LiquidationEvent:
        event = raw_data if isinstance(raw_data := payload.get("data"), dict) else payload
        order = event.get("o") if isinstance(event, dict) else None
        if not isinstance(order, dict):
            raise ProviderContractError("Binance force-order payload is malformed")
        side = str(order.get("S") or "").upper()
        if side not in {"BUY", "SELL"}:
            raise ProviderContractError("Binance force-order side is missing")
        price = _decimal(order.get("ap") or order.get("p"), field="liquidation price")
        quantity = _decimal(order.get("z") or order.get("l") or order.get("q"), field="liquidation quantity")
        timestamp = order.get("T") or event.get("E")
        if timestamp is None:
            raise ProviderContractError("Binance force-order timestamp is missing")
        liquidation_side: Literal["long", "short"] = "long" if side == "SELL" else "short"
        return LiquidationEvent(
            time=_utc_from_milliseconds(timestamp),
            liquidation_side=liquidation_side,
            notional=abs(price * quantity),
        )

    def _append(self, symbol: str, event: LiquidationEvent) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - self.retention
        with self._lock:
            queue = self._events.setdefault(symbol, deque(maxlen=self.max_events_per_symbol))
            queue.append(event)
            while queue and queue[0].time < cutoff:
                queue.popleft()

    async def _run_async(self, symbol: str) -> None:
        connect = self.connect_factory
        kwargs: dict[str, Any] = {"ping_interval": 20, "ping_timeout": 20}
        if connect is None:
            from websockets.asyncio.client import connect as websocket_connect

            connect = websocket_connect
            kwargs["proxy"] = env_str("OMNIX_BINANCE_FUTURES_WS_PROXY") or None
        url = f"{self.base_url}/{symbol.lower()}@forceOrder"
        while True:
            try:
                async with connect(url, **kwargs) as socket:
                    async for raw in socket:
                        try:
                            payload = json.loads(raw if isinstance(raw, str) else raw.decode())
                            self._append(symbol, self.parse(payload))
                        except (ValueError, TypeError, ProviderContractError):
                            continue
            except Exception:
                await asyncio.sleep(2)

    def _run(self, symbol: str) -> None:
        async def run():
            with self._lock:
                if self._stopping.is_set():
                    return
                self._runs[symbol] = (asyncio.get_running_loop(), asyncio.current_task())
            try:
                await self._run_async(symbol)
            except asyncio.CancelledError:
                pass
            finally:
                with self._lock:
                    self._runs.pop(symbol, None)
        asyncio.run(run())

    def close(self, timeout: float = 5) -> None:
        self._stopping.set()
        with self._lock:
            runs, threads = list(self._runs.values()), list(self._threads.values())
        for loop, task in runs:
            if not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError('Liquidation collectors did not stop')

    def ensure_started(self, symbol: str) -> None:
        from app.runtime.config import get_runtime_config
        if self._stopping.is_set() or not get_runtime_config().owns_background_runtime:
            return
        guard = getattr(self, 'start_guard', None)
        if guard is not None:
            guard()
        symbol = symbol.upper()
        with self._lock:
            thread = self._threads.get(symbol)
            if thread is not None and thread.is_alive():
                return
            thread = threading.Thread(
                target=self._run,
                args=(symbol,),
                name=f"omnix-binance-liquidations-{symbol}",
                daemon=True,
            )
            self._threads[symbol] = thread
            thread.start()

    def snapshot(self, symbol: str) -> list[LiquidationEvent]:
        symbol = symbol.upper()
        self.ensure_started(symbol)
        cutoff = datetime.now(timezone.utc) - self.retention
        with self._lock:
            queue = self._events.setdefault(symbol, deque(maxlen=self.max_events_per_symbol))
            while queue and queue[0].time < cutoff:
                queue.popleft()
            return list(queue)

    def is_collecting(self, symbol: str) -> bool:
        with self._lock:
            thread = self._threads.get(symbol.upper())
            return bool(thread and thread.is_alive())


class YahooFundamentalMetricAdapter:
    provider_id = "yahoo"

    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=64)
        self.runtime = runtime or ProviderHttpRuntime("yahoo-metrics", max_concurrency=2)
        self.timeout_seconds = timeout_seconds
        self.headers = {"User-Agent": "Mozilla/5.0 Omnix local research"}

    @staticmethod
    def _symbol(instrument_id: str) -> str:
        instrument = instrument_by_id(instrument_id)
        if instrument is None or str(instrument.asset_class) != "equity":
            raise ValueError("Yahoo fundamental metrics require an equity instrument")
        binding = next(
            (item for item in bindings_for_instrument(instrument_id) if item.provider == "yahoo"),
            None,
        )
        if binding is None:
            raise ValueError(f"Yahoo binding unavailable for {instrument_id}")
        return binding.provider_symbol

    def _json(self, url: str, params: dict[str, Any], cancellation: threading.Event | None) -> Any:
        response = self.runtime.get(
            url,
            params=params,
            headers=self.headers,
            timeout=self.timeout_seconds,
            cancellation=cancellation,
        )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderContractError("Yahoo metric endpoint returned invalid JSON") from exc

    @staticmethod
    def _raw(value: Any) -> Any:
        if isinstance(value, dict) and "raw" in value:
            return value["raw"]
        return value

    def analyst_targets(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        del limit
        now = datetime.now(timezone.utc)
        if end_time is not None and end_time < now - timedelta(hours=1):
            raise ProviderDataUnavailableError(
                "historical analyst-target snapshots are not available; current snapshot only"
            )
        symbol = self._symbol(instrument_id)
        key = self.cache.key("metric", "yahoo", "analyst-targets", symbol)

        def load() -> dict[str, Any]:
            payload = self._json(
                f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
                {"modules": "financialData"},
                cancellation,
            )
            result = (((payload or {}).get("quoteSummary") or {}).get("result") or [None])[0]
            data = result.get("financialData") if isinstance(result, dict) else None
            if not isinstance(data, dict):
                fallback = self._json(
                    "https://query1.finance.yahoo.com/v7/finance/quote",
                    {"symbols": symbol},
                    cancellation,
                )
                quote = (((fallback or {}).get("quoteResponse") or {}).get("result") or [None])[0]
                if not isinstance(quote, dict):
                    raise ProviderDataUnavailableError("Yahoo returned no analyst target data")
                data = quote
            return {"data": data}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=900,
            source="yahoo_analyst_targets",
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProviderDataUnavailableError("Yahoo returned no analyst target data")
        fields = [
            ("target-low", "Analyst Target Low", "targetLowPrice"),
            ("target-mean", "Analyst Target Mean", "targetMeanPrice"),
            ("target-high", "Analyst Target High", "targetHighPrice"),
        ]
        series: list[MarketMetricSeries] = []
        for key_name, title, field in fields:
            raw = self._raw(data.get(field))
            if raw is None:
                continue
            series.append(
                MarketMetricSeries(
                    key=key_name,
                    title=title,
                    unit="price",
                    points=[MarketMetricPoint(time=now, value=_decimal(raw, field=field))],
                )
            )
        if not series:
            raise ProviderDataUnavailableError("Yahoo analyst target fields are unavailable")
        opinions = self._raw(data.get("numberOfAnalystOpinions"))
        return _metric_response(
            instrument_id=instrument_id,
            metric="yahoo.analyst_price_forecast",
            provider=self.provider_id,
            interval=interval,
            series=series,
            freshness_mode="cached" if cached else "polled",
            metadata={
                "symbol": symbol,
                "snapshot_only": True,
                "analyst_opinions": opinions,
            },
        )

    def dividend_yield(
        self,
        instrument_id: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        del limit
        now = datetime.now(timezone.utc)
        if end_time is not None and end_time < now - timedelta(hours=1):
            raise ProviderDataUnavailableError(
                "historical dividend-yield snapshots are not available; current TTM snapshot only"
            )
        symbol = self._symbol(instrument_id)
        key = self.cache.key("metric", "yahoo", "dividend-yield", symbol)

        def load() -> dict[str, Any]:
            payload = self._json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                {
                    "range": "1y",
                    "interval": "1d",
                    "includePrePost": "false",
                    "events": "div",
                },
                cancellation,
            )
            result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
            if not isinstance(result, dict):
                raise ProviderDataUnavailableError("Yahoo returned no dividend chart")
            meta = raw_meta if isinstance(raw_meta := result.get("meta"), dict) else {}
            price = meta.get("regularMarketPrice")
            if price is None:
                quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                closes = quote.get("close") if isinstance(quote, dict) else []
                price = next((value for value in reversed(closes or []) if value is not None), None)
            events = (result.get("events") or {}).get("dividends") or {}
            if price is None:
                raise ProviderDataUnavailableError("Yahoo returned no current price for dividend yield")
            dividends = [
                item.get("amount")
                for item in events.values()
                if isinstance(item, dict) and item.get("amount") is not None
            ]
            return {"price": price, "dividends": dividends}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=3_600,
            source="yahoo_dividend_events",
        )
        price = _decimal(payload.get("price"), field="regularMarketPrice")
        if price <= 0:
            raise ProviderDataUnavailableError("Yahoo returned an invalid current price")
        total = sum((_decimal(value, field="dividend") for value in payload.get("dividends") or []), Decimal("0"))
        yield_percent = total / price * Decimal("100")
        return _metric_response(
            instrument_id=instrument_id,
            metric="yahoo.dividend_yield",
            provider=self.provider_id,
            interval=interval,
            series=[
                MarketMetricSeries(
                    key="dividend-yield",
                    title="Trailing 12M Dividend Yield",
                    unit="%",
                    points=[MarketMetricPoint(time=now, value=yield_percent)],
                )
            ],
            freshness_mode="cached" if cached else "polled",
            metadata={
                "symbol": symbol,
                "snapshot_only": True,
                "method": "trailing_12_month_dividends/current_price",
            },
        )


BLOCKCHAIN_METRICS: dict[str, tuple[str, str, str | None, Decimal]] = {
    "blockchain.hash_rate": ("hash-rate", "Hash Rate", "TH/s", Decimal("1")),
    "blockchain.difficulty": ("difficulty", "Difficulty", None, Decimal("1")),
    "blockchain.transaction_fees": ("transaction-fees", "Transaction Fees", "BTC", Decimal("1")),
    "blockchain.transaction_rate": ("transactions-per-second", "Transaction Rate", "tx/s", Decimal("1")),
    "blockchain.total_utxos": ("utxo-count", "Total UTXOs", "UTXOs", Decimal("1")),
    "blockchain.blocks_mined": ("n-blocks-mined", "Blocks Mined", "blocks/day", Decimal("1")),
    "blockchain.mean_block_size_bytes": ("avg-block-size", "Mean Block Size", "bytes", Decimal("1000000")),
    "blockchain.total_block_size_bytes": ("blocks-size", "Blockchain Size", "bytes", Decimal("1000000")),
}


class BlockchainMetricAdapter:
    provider_id = "blockchain.com"

    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        runtime: ProviderHttpRuntime | None = None,
        base_url: str = "https://api.blockchain.info",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=64)
        self.runtime = runtime or ProviderHttpRuntime("blockchain-metrics", max_concurrency=2)
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _require_bitcoin(instrument_id: str) -> None:
        instrument = instrument_by_id(instrument_id)
        if instrument is None or str(instrument.asset_class) != "crypto" or instrument.base_currency != "BTC":
            raise ValueError("Blockchain.com on-chain metrics currently require a BTC instrument")

    def metric(
        self,
        instrument_id: str,
        metric: str,
        interval: str,
        limit: int,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        self._require_bitcoin(instrument_id)
        config = BLOCKCHAIN_METRICS.get(metric)
        if config is None:
            raise ValueError(f"unsupported Blockchain.com metric: {metric}")
        slug, title, unit, multiplier = config
        clean_limit = max(1, min(int(limit), 1_500))
        seconds = INTERVAL_SECONDS.get(interval, 86_400)
        requested_days = max(30, math.ceil(clean_limit * seconds / 86_400))
        timespan = f"{min(requested_days, 3650)}days"
        params: dict[str, Any] = {
            "timespan": timespan,
            "format": "json",
            "sampled": "true",
        }
        if end_time is not None:
            start = end_time - timedelta(days=min(requested_days, 3650))
            params["start"] = int(start.timestamp())
        key = self.cache.key("metric", "blockchain", slug, timespan, params.get("start"))

        def load() -> dict[str, Any]:
            response = self.runtime.get(
                f"{self.base_url}/charts/{slug}",
                params=params,
                timeout=self.timeout_seconds,
                cancellation=cancellation,
            )
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderContractError("Blockchain.com returned invalid chart JSON") from exc
            if not isinstance(payload, dict):
                raise ProviderContractError("Blockchain.com chart payload must be an object")
            return {"payload": payload}

        payload, _, cached = self.cache.get_or_load(
            key,
            load,
            ttl_seconds=900,
            source=f"blockchain_chart:{slug}",
        )
        raw_values = (payload.get("payload") or {}).get("values")
        if not isinstance(raw_values, list):
            raise ProviderContractError("Blockchain.com chart payload has no values")
        cutoff = end_time.astimezone(timezone.utc) if end_time is not None else None
        points = []
        for row in raw_values:
            if not isinstance(row, dict) or row.get("x") is None or row.get("y") is None:
                continue
            moment = _utc_from_seconds(row["x"])
            if cutoff is not None and moment > cutoff:
                continue
            points.append(
                MarketMetricPoint(
                    time=moment,
                    value=_decimal(row["y"], field=slug) * multiplier,
                )
            )
        points = points[-clean_limit:]
        if not points:
            raise ProviderDataUnavailableError(f"Blockchain.com returned no {title} history")
        return _metric_response(
            instrument_id=instrument_id,
            metric=metric,
            provider=self.provider_id,
            interval=interval,
            series=[MarketMetricSeries(key=slug, title=title, unit=unit, points=points)],
            freshness_mode="cached" if cached else "polled",
            history_complete=len(points) < clean_limit,
            metadata={"chart": slug, "timespan": timespan, "bitcoin_only": True},
        )


class TradingMetricDataService:
    """Routes external TradingView-style metrics to truthful provider adapters."""

    def __init__(
        self,
        *,
        cache: TradingMarketDataCache | None = None,
        binance: BinanceDerivativesMetricAdapter | None = None,
        yahoo: YahooFundamentalMetricAdapter | None = None,
        blockchain: BlockchainMetricAdapter | None = None,
    ) -> None:
        self.cache = cache or TradingMarketDataCache(max_entries=256)
        self.binance = binance or BinanceDerivativesMetricAdapter(cache=self.cache)
        self.yahoo = yahoo or YahooFundamentalMetricAdapter(cache=self.cache)
        self.blockchain = blockchain or BlockchainMetricAdapter(cache=self.cache)

    def metric(
        self,
        instrument_id: str,
        metric: str,
        interval: str,
        limit: int = 500,
        *,
        end_time: datetime | None = None,
        cancellation: threading.Event | None = None,
    ) -> MarketMetricResponse:
        if end_time is not None and end_time.tzinfo is None:
            raise ValueError("end_time must include a timezone")
        if end_time is not None:
            end_time = end_time.astimezone(timezone.utc)

        if metric == "binance.open_interest":
            return self.binance.open_interest(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
        if metric == "binance.funding_rate":
            return self.binance.funding_rate(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
        if metric == "binance.liquidations":
            return self.binance.liquidations(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
        if metric == "binance.global_long_short_accounts":
            return self.binance.long_short_ratio(instrument_id, interval, limit, scope="global_accounts", end_time=end_time, cancellation=cancellation)
        if metric == "binance.top_long_short_accounts":
            return self.binance.long_short_ratio(instrument_id, interval, limit, scope="top_accounts", end_time=end_time, cancellation=cancellation)
        if metric == "binance.top_long_short_positions":
            return self.binance.long_short_ratio(instrument_id, interval, limit, scope="top_positions", end_time=end_time, cancellation=cancellation)
        if metric == "binance.basis":
            return self.binance.basis(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
        if metric == "binance.mark_price":
            return self.binance.price_index(instrument_id, interval, limit, source="mark", end_time=end_time, cancellation=cancellation)
        if metric == "binance.index_price":
            return self.binance.price_index(instrument_id, interval, limit, source="index", end_time=end_time, cancellation=cancellation)
        if metric == "binance.premium":
            return self.binance.price_index(instrument_id, interval, limit, source="premium", end_time=end_time, cancellation=cancellation)
        if metric in {"yahoo.analyst_price_forecast", "yahoo.price_target"}:
            result = self.yahoo.analyst_targets(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
            if metric == "yahoo.price_target":
                result.metric = metric
            return result
        if metric == "yahoo.dividend_yield":
            return self.yahoo.dividend_yield(instrument_id, interval, limit, end_time=end_time, cancellation=cancellation)
        if metric in BLOCKCHAIN_METRICS:
            return self.blockchain.metric(instrument_id, metric, interval, limit, end_time=end_time, cancellation=cancellation)
        raise ValueError(f"unsupported Trading metric: {metric}")


_default_metric_service: TradingMetricDataService | None = None
_default_metric_lock = threading.Lock()


def default_metric_data_service() -> TradingMetricDataService:
    global _default_metric_service
    if _default_metric_service is None:
        with _default_metric_lock:
            if _default_metric_service is None:
                _default_metric_service = TradingMetricDataService()
    return _default_metric_service
