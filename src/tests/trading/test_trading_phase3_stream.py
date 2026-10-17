from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.api import create_trading_router
from app.apps.trading.catalog import BINDINGS, INSTRUMENTS
from app.apps.trading.streaming.binance_stream import BinanceWebSocketStream, parse_binance_kline
from app.apps.trading.streaming.manager import StreamingBarUpdate


class EmptyRepository:
    def iter(self, record_type: str):
        return iter(self.list(record_type, limit=10_000))

    def list(self, record_type: str, *, limit: int = 100):
        return []

    def get(self, record_type: str, record_id: str):
        return None


class FakeMarketService:
    async def stream_updates(self, instrument_id: str, interval: str):
        moment = datetime(2026, 8, 5, tzinfo=timezone.utc)
        yield StreamingBarUpdate(
            binding_id=BINDINGS[0].binding_id,
            instrument_id=instrument_id,
            interval=interval,
            start_time=moment,
            end_time=moment + timedelta(minutes=1),
            open=Decimal("100"),
            high=Decimal("102"),
            low=Decimal("99"),
            close=Decimal("101"),
            volume=Decimal("5"),
            is_final=False,
            provider_event_id="event-1",
            ingestion_revision=3,
        )

    def diagnostics(self):
        return {"provider": "binance"}


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_binance_kline_parser_marks_partial_and_final_candles() -> None:
    base = {
        "E": 12345,
        "k": {
            "t": 1_700_000_000_000,
            "T": 1_700_000_059_999,
            "i": "1m",
            "o": "100",
            "h": "102",
            "l": "99",
            "c": "101",
            "v": "8",
            "x": False,
        },
    }
    partial = parse_binance_kline(
        base,
        binding_id=BINDINGS[0].binding_id,
        instrument_id=INSTRUMENTS[0].instrument_id,
        interval="1m",
        ingestion_revision=4,
    )
    assert partial.is_final is False
    assert partial.ingestion_revision == 4
    final = parse_binance_kline(
        {**base, "k": {**base["k"], "x": True}},
        binding_id=BINDINGS[0].binding_id,
        instrument_id=INSTRUMENTS[0].instrument_id,
        interval="1m",
        ingestion_revision=5,
    )
    assert final.is_final is True
    assert final.start_time == partial.start_time


def test_binance_stream_yields_provider_neutral_updates() -> None:
    payload = json.dumps(
        {
            "E": 12345,
            "k": {
                "t": 1_700_000_000_000,
                "T": 1_700_000_059_999,
                "i": "1m",
                "o": "100",
                "h": "102",
                "l": "99",
                "c": "101",
                "v": "8",
                "x": True,
            },
        }
    )

    def connect_factory(url, **kwargs):
        assert url.endswith("/btcusdt@kline_1m")
        assert kwargs["ping_interval"] == 20
        return FakeSocket([payload])

    async def collect():
        stream = BinanceWebSocketStream(connect_factory=connect_factory)
        return [
            update
            async for update in stream.messages(
                provider_symbol="BTCUSDT",
                binding_id=BINDINGS[0].binding_id,
                instrument_id=INSTRUMENTS[0].instrument_id,
                interval="1m",
            )
        ]

    import asyncio

    updates = asyncio.run(collect())
    assert len(updates) == 1
    assert updates[0].instrument_id == INSTRUMENTS[0].instrument_id
    assert updates[0].provider_event_id == "12345"


def test_gateway_websocket_sends_normalized_decimal_and_time_values() -> None:
    app = FastAPI()
    service = FakeMarketService()
    app.include_router(create_trading_router(lambda: EmptyRepository(), lambda: service))
    client = TestClient(app)
    with client.websocket_connect(
        f"/api/trading/stream?instrument_id={INSTRUMENTS[0].instrument_id}&interval=1m"
    ) as websocket:
        message = websocket.receive_json()
        assert message["type"] == "bar"
        assert message["bar"]["close"] == "101"
        assert message["bar"]["is_final"] is False
        assert message["bar"]["ingestion_revision"] == 3
        assert message["bar"]["start_time"].endswith("+00:00")
