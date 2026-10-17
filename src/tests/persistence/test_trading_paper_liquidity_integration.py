from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.persistence.config import DatabaseSettings
from app.persistence.database import PostgresDatabase
from app.persistence.identity_service import ensure_local_identity
from app.persistence.unit_of_work import unit_of_work
from app.apps.trading.paper import (
    PaperAccountCreate,
    PaperMarketObservation,
    PaperOrderRequest,
)
from app.apps.trading.order_gateway import OrderGateway
from app.apps.trading.paper_repository import TradingPaperRepository


pytestmark = pytest.mark.skipif(
    not os.environ.get("OMNIX_TEST_DATABASE_URL"),
    reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


def _database() -> PostgresDatabase:
    return PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-paper-liquidity-tests",
        )
    )


def test_paper_observation_liquidity_is_aggregate_and_replay_safe() -> None:
    database = _database()
    try:
        context = ensure_local_identity(database)
        repository = TradingPaperRepository(
            context=context,
            uow_factory=lambda: unit_of_work(database),
        )
        suffix = uuid.uuid4().hex[:10]
        account_id = f"paper-liquidity-{suffix}"
        instrument_id = f"equity:NASDAQ:T{suffix[:4].upper()}"

        repository.create_account(
            PaperAccountCreate(
                account_id=account_id,
                name="Paper liquidity integration",
                initial_cash=Decimal("100000"),
            )
        )
        for index in range(2):
            OrderGateway(repository).place_manual_entry(
                account_id,
                PaperOrderRequest(
                    order_id=f"order-{suffix}-{index}",
                    instrument_id=instrument_id,
                    side="buy",
                    order_type="market",
                    quantity=Decimal("100"),
                    reference_price=Decimal("10"),
                    idempotency_key=f"idem-{suffix}-{index}",
                ),
            )

        event_time = datetime.now(timezone.utc) + timedelta(seconds=1)
        observation = PaperMarketObservation(
            instrument_id=instrument_id,
            provider="integration",
            price=Decimal("10"),
            bid=Decimal("9.99"),
            ask=Decimal("10.01"),
            bid_size=Decimal("300"),
            ask_size=Decimal("300"),
            source_time=event_time,
            evaluated_at=event_time,
            execution_eligible=True,
            freshness_mode="live",
        )

        first = repository.process_observation(account_id, observation)
        assert sum((fill.quantity for fill in first), Decimal("0")) == Decimal("30")

        replay = repository.process_observation(account_id, observation)
        assert replay == []

        changed_same_time = observation.model_copy(
            update={
                "price": Decimal("10.02"),
                "bid": Decimal("10.01"),
                "ask": Decimal("10.03"),
                "bid_size": Decimal("400"),
                "ask_size": Decimal("400"),
            }
        )
        changed_fills = repository.process_observation(account_id, changed_same_time)
        assert sum(
            (fill.quantity for fill in changed_fills),
            Decimal("0"),
        ) == Decimal("40")

        snapshot = repository.snapshot(account_id)
        assert sum(
            (order.filled_quantity for order in snapshot.order_history),
            Decimal("0"),
        ) == Decimal("70")
    finally:
        database.close()
