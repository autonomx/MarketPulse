"""Market breadth rows against PostgreSQL (TVP-6.6)."""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from app.apps.trading.breadth import BreadthDay, BreadthRepository
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

# Far in the past, so these rows never mix with collected ones.
DAYS = [date(1990, 1, 2), date(1990, 1, 3)]


def test_breadth_rows_are_saved_updated_and_read_oldest_first() -> None:
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-breadth-tests",
    ))
    token = push_tenant(ensure_local_identity(database))
    try:
        repository = BreadthRepository(lambda: unit_of_work(database))
        rows = [BreadthDay("NYSE", day, 10 + index, 5, 1, Decimal(1000), Decimal(500)) for index, day in enumerate(DAYS)]
        assert repository.save(rows, "test") == 2
        # A session written again replaces its counts.
        repository.save([BreadthDay("NYSE", DAYS[1], 20, 5, 1, Decimal(2000), Decimal(500))], "test")
        stored = repository.days("NYSE", until=date(1990, 12, 31))
        assert [(day.session_date, day.advances) for day in stored] == [(DAYS[0], 10), (DAYS[1], 20)]
        assert repository.days("NYSE", until=date(1990, 1, 2)) == stored[:1]
    finally:
        with unit_of_work(database) as uow:
            uow.connection.execute("DELETE FROM omnix_trading_market_breadth WHERE session_date < '1991-01-01'")
            uow.commit()
        pop_tenant(token)
        database.close()
