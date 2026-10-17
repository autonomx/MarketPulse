from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.api import create_trading_router
from app.apps.trading.catalog import BINDINGS, INSTRUMENTS
from app.apps.trading.models import AdjustmentMode, MarketBar


class FakeRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict] = {}

    def iter(self, record_type: str):

        return iter(self.list(record_type, limit=10_000))


    def list(self, record_type: str, *, limit: int = 100):
        return [value for (kind, _), value in self.records.items() if kind == record_type][:limit]

    def get(self, record_type: str, record_id: str):
        return self.records.get((record_type, record_id))

    def create(self, record_type: str, record_id: str, payload: dict):
        record = {
            "record_type": record_type,
            "record_id": record_id,
            "payload": payload,
            "revision": 1,
            "status": "active",
            "updated_at": "2026-08-05T00:00:00+00:00",
        }
        self.records[(record_type, record_id)] = record
        return record

    def update(self, record_type: str, record_id: str, payload: dict, *, expected_revision: int):
        current = self.records[(record_type, record_id)]
        if current["revision"] != expected_revision:
            from app.persistence.errors import RevisionConflict

            raise RevisionConflict("revision mismatch")
        record = {**current, "payload": payload, "revision": expected_revision + 1}
        self.records[(record_type, record_id)] = record
        return record


def test_canonical_instruments_are_provider_independent() -> None:
    instrument = INSTRUMENTS[0]
    binding = BINDINGS[0]
    assert instrument.instrument_id == "crypto:BINANCE:spot:BTC-USDT"
    assert "binance:rest-ws" not in instrument.instrument_id
    assert binding.instrument_id == instrument.instrument_id
    assert binding.provider_symbol == "BTCUSDT"


def test_bar_contract_requires_timezone_and_distinguishes_ingestion_revision() -> None:
    start = datetime(2026, 8, 5, tzinfo=timezone.utc)
    bar = MarketBar(
        instrument_id=INSTRUMENTS[0].instrument_id,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        provider="binance",
        provider_sequence=None,
        ingestion_revision=2,
        adjustment_mode=AdjustmentMode.RAW,
    )
    assert bar.provider_sequence is None
    assert bar.ingestion_revision == 2
    assert bar.start_time.tzinfo is not None


def test_typed_router_exposes_catalog_and_revisioned_documents() -> None:
    repository = FakeRepository()
    app = FastAPI()
    app.include_router(create_trading_router(lambda: repository))
    client = TestClient(app)

    providers = client.get("/api/trading/providers").json()["providers"]
    assert providers[0]["provider"] == "binance"
    assert providers[0]["policy"]["is_official_api"] is True
    ibkr = next(item for item in providers if item["provider"] == "ibkr")
    assert "enabled" in ibkr["runtime"]
    instruments = client.get("/api/trading/instruments/search", params={"query": "BTC"}).json()["instruments"]
    assert instruments[0]["instrument_id"] == INSTRUMENTS[0].instrument_id

    created = client.post(
        "/api/trading/workspaces",
        json={"record_id": "default", "payload": {"layout": "one"}},
    )
    assert created.status_code == 201
    assert created.json()["revision"] == 1
    updated = client.put(
        "/api/trading/workspaces/default",
        headers={"If-Match": "1"},
        json={"record_id": "default", "payload": {"layout": "four"}},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    conflict = client.put(
        "/api/trading/workspaces/default",
        headers={"If-Match": "1"},
        json={"record_id": "default", "payload": {"layout": "one"}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_revision"] == 2
