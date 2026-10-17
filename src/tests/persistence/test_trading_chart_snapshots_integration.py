"""Chart snapshot links against PostgreSQL (TVP-2.1/2.5)."""
from __future__ import annotations

import base64
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import snapshots_api
from app.apps.trading.snapshots_api import SnapshotRepository, create_trading_snapshots_router, decode_png
from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.runtime.tenant_context import pop_tenant, push_tenant

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_only_pngs_up_to_3_mb() -> None:
    assert decode_png("data:image/png;base64," + base64.b64encode(PNG).decode()) == PNG
    for bad in ("data:image/jpeg;base64,AAAA", base64.b64encode(b"GIF89a....").decode(), "not base64!",
                base64.b64encode(PNG + b"\x00" * (3 * 1024 * 1024)).decode()):
        with pytest.raises(ValueError):
            decode_png(bad)


@pytest.fixture()
def client():
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-snapshot-tests",
    ))
    try:
        context = ensure_local_identity(database)
        token = push_tenant(context)
        repository = SnapshotRepository(context=context, uow_factory=lambda: unit_of_work(database))
        app = FastAPI()
        app.include_router(create_trading_snapshots_router(lambda: repository))
        yield TestClient(app), repository, database
        pop_tenant(token)
    finally:
        database.close()


def test_a_snapshot_link_serves_the_image_and_old_ones_are_pruned(client, monkeypatch) -> None:
    http, repository, database = client
    created = http.post("/api/trading/snapshots", json={"image": base64.b64encode(PNG).decode(), "instrument_id": "equity:X:Y", "interval": "1h"})
    assert created.status_code == 201
    body = created.json()
    assert body["url"] == f"/api/trading/snapshots/{body['snapshot_id']}.png" and len(body["snapshot_id"]) >= 20
    image = http.get(body["url"])
    assert image.status_code == 200 and image.content == PNG and image.headers["content-type"] == "image/png"
    assert "private" in image.headers["cache-control"]
    assert http.get("/api/trading/snapshots").json()["snapshots"][0]["snapshot_id"] == body["snapshot_id"]
    assert http.post("/api/trading/snapshots", json={"image": "data:image/gif;base64,AAAA"}).status_code == 422
    assert http.get("/api/trading/snapshots/nope.png").status_code == 404
    # Only the newest are kept.
    monkeypatch.setattr(snapshots_api, "SNAPSHOTS_KEPT", 1)
    newer = repository.create(PNG, created_by="u", instrument_id="", interval="")
    assert http.get(body["url"]).status_code == 404 and repository.image(newer.snapshot_id) == PNG
    assert http.delete(f"/api/trading/snapshots/{newer.snapshot_id}").status_code == 204
    with unit_of_work(database) as uow:
        flags = uow.connection.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'omnix_trading_chart_snapshots'"
        ).fetchone()
    assert tuple(flags) == (True, True)
