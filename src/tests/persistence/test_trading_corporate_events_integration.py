"""Cached earnings, dividends and splits against PostgreSQL (TVP-10.1)."""
from __future__ import annotations

import os

import pytest

from app.apps.trading.corporate_events import CorporateEventRepository
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


def test_events_are_cached_per_ticker() -> None:
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-corporate-events-tests",
    ))
    token = push_tenant(ensure_local_identity(database))
    try:
        repository = CorporateEventRepository(lambda: unit_of_work(database))
        assert repository.get(["ZZTEST1"]) == {}
        repository.save("ZZTEST1", {"events": [{"kind": "dividend", "date": "2026-08-01", "amount": 0.1}]})
        repository.save("ZZTEST1", {"events": [{"kind": "dividend", "date": "2026-08-01", "amount": 0.2}]})
        repository.save("ZZTEST2", {"events": []})
        stored = repository.get(["ZZTEST1", "ZZTEST2", "ZZTEST3"])
        assert set(stored) == {"ZZTEST1", "ZZTEST2"}
        assert stored["ZZTEST1"][0]["events"][0]["amount"] == 0.2 and stored["ZZTEST1"][1] is not None
    finally:
        with unit_of_work(database) as uow:
            uow.connection.execute("DELETE FROM omnix_trading_corporate_events WHERE ticker LIKE 'ZZTEST%%'")
            uow.commit()
        pop_tenant(token)
        database.close()
