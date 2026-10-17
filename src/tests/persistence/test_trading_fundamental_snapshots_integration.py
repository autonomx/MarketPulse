"""Screener fundamentals against PostgreSQL (TVP-9.1): snapshots joined to tickers through company profiles."""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from app.apps.trading.company_profiles import CompanyProfileRepository
from app.apps.trading.fundamental_snapshots import FundamentalSnapshotRepository
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


def test_a_tickers_snapshot_carries_its_shares() -> None:
    database = PostgresDatabase(DatabaseSettings(
        url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=2, connect_timeout_seconds=10,
        statement_timeout_ms=30_000, application_name="omnix-fundamental-snapshot-tests",
    ))
    token = push_tenant(ensure_local_identity(database))
    try:
        profiles = CompanyProfileRepository(lambda: unit_of_work(database))
        snapshots = FundamentalSnapshotRepository(lambda: unit_of_work(database))
        profiles.save_shares({"0000000077": (Decimal(500), date(2026, 6, 30))}, {"0000000077": [("ZZFUND", "Fund Test")]})
        snapshots.save({"0000000077": {"revenue_ttm": Decimal(10), "revenue_prev_ttm": None, "net_income_ttm": Decimal(1), "eps_ttm": Decimal("0.5"), "equity": Decimal(20)}})
        found = snapshots.for_tickers(["zzfund", "NOPE"])
        assert found == {"ZZFUND": {"shares": Decimal(500), "revenue_ttm": Decimal(10), "revenue_prev_ttm": None, "net_income_ttm": Decimal(1), "eps_ttm": Decimal("0.5"), "equity": Decimal(20)}}
        assert snapshots.refreshed_at() is not None
    finally:
        with unit_of_work(database) as uow:
            uow.connection.execute("DELETE FROM omnix_trading_fundamental_snapshots WHERE cik = '0000000077'")
            uow.connection.execute("DELETE FROM omnix_trading_company_profiles WHERE ticker = 'ZZFUND'")
            uow.commit()
        pop_tenant(token)
        database.close()
