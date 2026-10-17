"""Cached company financial statements against PostgreSQL (TVP-10.2)."""
from __future__ import annotations

import os

import pytest

from app.apps.trading.fundamentals import FinancialsRepository
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


def test_statements_are_cached_per_company() -> None:
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-financials-tests",
    ))
    token = push_tenant(ensure_local_identity(database))
    try:
        repository = FinancialsRepository(lambda: unit_of_work(database))
        assert repository.get("9999999999") is None
        repository.save("9999999999", {"annual": {"income": [{"end": "2025-12-31", "values": {"revenue": 1.0}}]}})
        repository.save("9999999999", {"annual": {"income": [{"end": "2025-12-31", "values": {"revenue": 2.0}}]}})
        statements, fetched_at = repository.get("9999999999")
        assert statements["annual"]["income"][0]["values"]["revenue"] == 2.0 and fetched_at is not None
    finally:
        with unit_of_work(database) as uow:
            uow.connection.execute("DELETE FROM omnix_trading_company_financials WHERE cik = '9999999999'")
            uow.commit()
        pop_tenant(token)
        database.close()
