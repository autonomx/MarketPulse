"""SEC company profiles against PostgreSQL (TVP-9.3)."""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from app.apps.trading.company_profiles import CompanyProfileRepository
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


def test_profiles_and_shares_are_stored_by_ticker() -> None:
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-company-profile-tests",
    ))
    token = push_tenant(ensure_local_identity(database))
    try:
        repository = CompanyProfileRepository(lambda: unit_of_work(database))
        repository.save_profile("ZZTEST", "0000000001", "Test Corp", "3674", "Semiconductors")
        repository.save_shares({"0000000001": (Decimal(1_000_000), date(2026, 6, 30))}, {"0000000001": [("ZZTEST", "Test Corp"), ("ZZTEST.B", "Test Corp")]})
        profiles = repository.get(["zztest", "ZZTEST.B", "NOPE"])
        assert (profiles["ZZTEST"].sector, profiles["ZZTEST"].industry, profiles["ZZTEST"].shares_outstanding) == ("Technology", "Semiconductors", Decimal(1_000_000))
        # A share class known only from the frames: its shares, no sector yet.
        assert profiles["ZZTEST.B"].sector is None and profiles["ZZTEST.B"].shares_as_of == date(2026, 6, 30)
        assert "NOPE" not in profiles and repository.shares_fetched_at() is not None
    finally:
        with unit_of_work(database) as uow:
            uow.connection.execute("DELETE FROM omnix_trading_company_profiles WHERE ticker LIKE 'ZZTEST%'")
            uow.commit()
        pop_tenant(token)
        database.close()
