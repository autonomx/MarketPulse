from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.persistence.errors import RevisionConflict
from app.apps.trading.api import create_trading_router


class RevisionedRepository:
    def __init__(self) -> None:
        self.records = {
            ("watchlist", "default"): {
                "record_type": "watchlist",
                "record_id": "default",
                "payload": {"name": "Default", "instrumentIds": ["btc"]},
                "revision": 2,
                "status": "active",
                "updated_at": "2026-08-05T00:00:00+00:00",
            }
        }

    def iter(self, record_type: str):

        return iter(self.list(record_type, limit=10_000))


    def list(self, record_type: str, *, limit: int = 100):
        return [record for (kind, _), record in self.records.items() if kind == record_type and record["status"] == "active"][:limit]

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
            raise RevisionConflict("revision mismatch")
        current = {**current, "payload": payload, "revision": expected_revision + 1}
        self.records[(record_type, record_id)] = current
        return current

    def archive(self, record_type: str, record_id: str, *, expected_revision: int):
        current = self.records[(record_type, record_id)]
        if current["revision"] != expected_revision or current["status"] != "active":
            raise RevisionConflict("revision mismatch")
        current = {**current, "status": "archived", "revision": expected_revision + 1}
        self.records[(record_type, record_id)] = current
        return current


class EmptyMarketService:
    def diagnostics(self):
        return {}


def test_watchlist_archive_requires_current_revision() -> None:
    repository = RevisionedRepository()
    app = FastAPI()
    app.include_router(create_trading_router(lambda: repository, lambda: EmptyMarketService()))
    client = TestClient(app)

    conflict = client.delete("/api/trading/watchlists/default", headers={"If-Match": "1"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_revision"] == 2

    archived = client.delete("/api/trading/watchlists/default", headers={"If-Match": "2"})
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["revision"] == 3
    assert client.get("/api/trading/watchlists").json()["records"] == []


def test_watchlist_flags_are_a_separate_revisioned_document() -> None:
    repository = RevisionedRepository()
    app = FastAPI()
    app.include_router(create_trading_router(lambda: repository, lambda: EmptyMarketService()))
    client = TestClient(app)
    payload = {"schemaVersion": 1, "flags": [{"instrumentId": "equity:NASDAQ:AAPL", "color": "red"}]}

    created = client.post("/api/trading/watchlist-flags", json={"record_id": "default", "payload": payload})
    assert created.status_code == 201
    assert created.json()["record_type"] == "watchlist_flag_set"

    stale = client.put(
        "/api/trading/watchlist-flags/default",
        json={"record_id": "default", "payload": {"schemaVersion": 1, "flags": []}},
        headers={"If-Match": "2"},
    )
    assert stale.status_code == 409

    updated = client.put(
        "/api/trading/watchlist-flags/default",
        json={"record_id": "default", "payload": {"schemaVersion": 1, "flags": []}},
        headers={"If-Match": "1"},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    # Flags never show up as a watchlist.
    assert [record["record_id"] for record in client.get("/api/trading/watchlists").json()["records"]] == ["default"]
    assert client.get("/api/trading/watchlist-flags").json()["records"][0]["payload"]["flags"] == []


def test_watchlist_flag_set_is_a_supported_document_type() -> None:
    from app.apps.trading.repositories import SUPPORTED_DOCUMENT_TYPES
    from app.persistence.document_schemas import registered_document_kinds

    assert "watchlist_flag_set" in SUPPORTED_DOCUMENT_TYPES
    assert ("trading", "watchlist_flag_set") in registered_document_kinds()


def test_rehydration_finds_symbols_inside_v2_watchlist_items_and_flags(monkeypatch) -> None:
    from app.apps.trading import api as trading_api

    repository = RevisionedRepository()
    repository.records[("watchlist", "default")]["payload"] = {
        "schemaVersion": 2,
        "name": "Sectioned",
        # No instrumentIds mirror: the items alone must be enough.
        "items": [
            {"type": "symbol", "instrumentId": "equity:NASDAQ:AAPL"},
            {"type": "section", "id": "s1", "name": "Crypto", "collapsed": True},
            {"type": "symbol", "instrumentId": "crypto:BINANCE:spot:BTC-USDT"},
        ],
    }
    repository.create(
        "watchlist_flag_set",
        "default",
        {"schemaVersion": 1, "flags": [{"instrumentId": "equity:NYSE:GME", "color": "red"}]},
    )
    rehydrated: list[str] = []
    monkeypatch.setattr(trading_api, "bindings_for_instrument", rehydrated.append)

    trading_api._rehydrate_persisted_bindings(lambda: repository)

    assert sorted(rehydrated) == ["crypto:BINANCE:spot:BTC-USDT", "equity:NASDAQ:AAPL", "equity:NYSE:GME"]
