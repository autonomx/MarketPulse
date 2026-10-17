from __future__ import annotations

from app.config.env import env_str, environment

import json
import os
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .manager import StreamingBarUpdate


BINANCE_STREAM_BASE_URL = "wss://stream.binance.com:9443/ws"
BINANCE_INTERVALS = {"1mo": "1M"}


def parse_binance_kline(
    payload: dict[str, Any],
    *,
    binding_id: str,
    instrument_id: str,
    interval: str,
    ingestion_revision: int = 1,
) -> StreamingBarUpdate:
    event = raw_data if isinstance(raw_data := payload.get("data"), dict) else payload
    kline = event.get("k") if isinstance(event, dict) else None
    if not isinstance(kline, dict):
        raise ValueError("Binance stream payload does not contain a kline")
    if str(kline.get("i")) != BINANCE_INTERVALS.get(interval, interval):
        raise ValueError(f"unexpected Binance interval: {kline.get('i')}")
    return StreamingBarUpdate(
        binding_id=binding_id,
        instrument_id=instrument_id,
        interval=interval,
        start_time=datetime.fromtimestamp(int(kline["t"]) / 1000, tz=timezone.utc),
        end_time=datetime.fromtimestamp(int(kline["T"]) / 1000, tz=timezone.utc),
        open=Decimal(str(kline["o"])),
        high=Decimal(str(kline["h"])),
        low=Decimal(str(kline["l"])),
        close=Decimal(str(kline["c"])),
        volume=Decimal(str(kline["v"])),
        is_final=bool(kline["x"]),
        provider_event_id=str(event.get("E") or kline["t"]),
        provider_sequence=None,
        ingestion_revision=ingestion_revision,
    )


class BinanceWebSocketStream:
    def __init__(
        self,
        *,
        connect_factory: Callable[..., Any] | None = None,
        base_url: str = BINANCE_STREAM_BASE_URL,
    ) -> None:
        self.connect_factory = connect_factory
        self.base_url = base_url.rstrip("/")

    async def messages(
        self,
        *,
        provider_symbol: str,
        binding_id: str,
        instrument_id: str,
        interval: str,
    ) -> AsyncIterator[StreamingBarUpdate]:
        connect = self.connect_factory
        connect_kwargs: dict[str, Any] = {
            "ping_interval": 20,
            "ping_timeout": 20,
        }
        if connect is None:
            from websockets.asyncio.client import connect as websocket_connect

            connect = websocket_connect
            # websockets 16 automatically discovers proxy variables by default.
            # A partially speaking local proxy can make the TLS handshake fail
            # with ASN1/NOT_ENOUGH_DATA before Binance receives the request. Keep
            # direct connections as the default and allow an explicit proxy when
            # the runtime requires one.
            connect_kwargs["proxy"] = env_str("OMNIX_BINANCE_WS_PROXY") or None
        stream_name = f"{provider_symbol.lower()}@kline_{BINANCE_INTERVALS.get(interval, interval)}"
        async with connect(f"{self.base_url}/{stream_name}", **connect_kwargs) as socket:
            revision = 0
            async for raw_message in socket:
                revision += 1
                payload = json.loads(raw_message) if isinstance(raw_message, str) else json.loads(raw_message.decode())
                yield parse_binance_kline(
                    payload,
                    binding_id=binding_id,
                    instrument_id=instrument_id,
                    interval=interval,
                    ingestion_revision=revision,
                )
