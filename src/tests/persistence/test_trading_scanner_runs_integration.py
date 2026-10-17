"""Scanner runs against PostgreSQL (TVP-9.2): one at a time per scanner, and only the latest finished runs kept."""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

from app.apps.trading.scanner import TradingScannerDefinition, TradingScannerRule, TradingScannerRun
from app.apps.trading.scanner_repository import RUNS_KEPT_PER_SCANNER, ScannerRunActive, TradingScannerRepository
from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.environ.get("OMNIX_TEST_DATABASE_URL"), reason="OMNIX_TEST_DATABASE_URL is required"),
]


@pytest.fixture()
def scanners():
    database = PostgresDatabase(
        DatabaseSettings(url=os.environ["OMNIX_TEST_DATABASE_URL"], pool_min=1, pool_max=3, connect_timeout_seconds=10,
                         statement_timeout_ms=30_000, application_name="omnix-scanner-run-tests")
    )
    try:
        context = ensure_local_identity(database)
        repository = TradingScannerRepository(context=context, uow_factory=lambda: unit_of_work(database))
        scanner_id = f"scan-{uuid.uuid4().hex[:10]}"
        repository.create_definition(
            TradingScannerDefinition(
                scanner_id=scanner_id, name="Runs", instrument_ids=["equity:NASDAQ:AAPL"],
                rules=[TradingScannerRule(rule_id="r", metric="close", operator="gt", threshold=Decimal("1"))],
            )
        )
        yield repository, scanner_id, database
    finally:
        database.close()


def _run(scanner_id: str) -> TradingScannerRun:
    return TradingScannerRun(run_id=f"run-{uuid.uuid4().hex}", scanner_id=scanner_id, status="queued", universe_count=1, definition_snapshot={})


def test_one_run_at_a_time_per_scanner(scanners) -> None:
    repository, scanner_id, database = scanners
    first = repository.create_run(_run(scanner_id))
    with pytest.raises(ScannerRunActive):
        repository.create_run(_run(scanner_id))
    repository.update_run(first.run_id, status="completed", completed_count=1, matched_count=0)
    repository.create_run(_run(scanner_id))


def test_an_abandoned_run_does_not_block_forever(scanners) -> None:
    repository, scanner_id, database = scanners
    stuck = repository.create_run(_run(scanner_id))
    with unit_of_work(database) as uow:
        uow.connection.execute(
            "UPDATE omnix_trading_scanner_runs SET created_at = CURRENT_TIMESTAMP - INTERVAL '1 hour' WHERE run_id = %s",
            (stuck.run_id,),
        )
        uow.commit()
    repository.create_run(_run(scanner_id))
    # The abandoned run is cleaned up with the old finished ones.
    assert all(item.run_id != stuck.run_id for item in repository.list_runs(scanner_id, limit=500))


def test_only_the_latest_finished_runs_are_kept(scanners) -> None:
    repository, scanner_id, _ = scanners
    for _ in range(RUNS_KEPT_PER_SCANNER + 3):
        run = repository.create_run(_run(scanner_id))
        repository.update_run(run.run_id, status="completed", completed_count=1, matched_count=0)
    assert len(repository.list_runs(scanner_id, limit=500)) == RUNS_KEPT_PER_SCANNER
