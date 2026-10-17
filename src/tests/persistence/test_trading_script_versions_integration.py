"""Omnix Scripts' versions against PostgreSQL (TVP-11.3): each save with a new source is a version."""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading import repositories
from app.apps.trading.repositories import TradingDocumentRepository
from app.apps.trading.scripts_api import create_trading_scripts_router
from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]


@pytest.fixture()
def documents():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=2,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-script-version-tests",
        )
    )
    try:
        context = ensure_local_identity(database)
        yield TradingDocumentRepository(context=context, uow_factory=lambda: unit_of_work(database))
    finally:
        database.close()


def test_each_new_source_is_a_version_and_old_ones_can_be_read(documents, monkeypatch) -> None:
    script_id = f"script-{uuid.uuid4().hex[:10]}"
    first = documents.create("script", script_id, {"name": "Mine", "source": "//@version=5\nindicator('a')\nplot(close)\n"})
    second = documents.update("script", script_id, {"name": "Mine", "source": "//@version=5\nindicator('a')\nplot(open)\n"}, expected_revision=first["revision"])
    # The same source and name again (another field changed): no new version.
    third = documents.update("script", script_id, {"name": "Mine", "source": second["payload"]["source"], "folder": "x"}, expected_revision=second["revision"])
    renamed = documents.update("script", script_id, {"name": "Renamed", "source": second["payload"]["source"]}, expected_revision=third["revision"])
    versions = documents.script_versions(script_id)
    assert [item["revision"] for item in versions] == [renamed["revision"], second["revision"], first["revision"]]
    assert versions[-1]["lines"] == 4 and versions[0]["name"] == "Renamed"
    assert documents.script_version(script_id, first["revision"])["source"].endswith("plot(close)\n")
    assert documents.script_version(script_id, third["revision"]) is None

    app = FastAPI()
    app.include_router(create_trading_scripts_router(repository_factory=lambda: documents))
    client = TestClient(app)
    listed = client.get(f"/api/trading/scripts/{script_id}/versions").json()["versions"]
    assert [item["revision"] for item in listed] == [item["revision"] for item in versions]
    assert client.get(f"/api/trading/scripts/{script_id}/versions/{second['revision']}").json()["source"].endswith("plot(open)\n")
    assert client.get(f"/api/trading/scripts/{script_id}/versions/9999").status_code == 404

    # Only the newest versions are kept.
    monkeypatch.setattr(repositories, "SCRIPT_VERSIONS_KEPT", 2)
    documents.update("script", script_id, {"name": "Renamed", "source": "plot(high)\n"}, expected_revision=renamed["revision"])
    assert len(documents.script_versions(script_id)) == 2


def test_other_documents_have_no_versions(documents) -> None:
    record_id = f"watchlist-{uuid.uuid4().hex[:10]}"
    documents.create("watchlist", record_id, {"name": "x", "source": "not a script"})
    assert documents.script_versions(record_id) == []
