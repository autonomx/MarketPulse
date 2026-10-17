"""Paper order types, time in force and trailing state against PostgreSQL (TVP-7.1)."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.order_gateway import OrderGateway
from app.apps.trading.paper import (
    PaperAccountCreate,
    PaperMarketObservation,
    PaperOrderRequest,
    paper_trailing_protection_update,
)
from app.apps.trading.paper_monitor import TradingPaperMonitor
from app.apps.trading.paper_protection import PaperProtectionUpsert
from app.apps.trading.paper_protection_repository import TradingPaperProtectionRepository
from app.apps.trading.paper_repository import TradingPaperRepository
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

STOP_SLIPPAGE = Decimal("0.9975")


class _Clock:
    def __init__(self) -> None:
        self.now: datetime | None = None

    def __call__(self) -> datetime:
        return self.now or datetime.now(timezone.utc)


@pytest.fixture()
def paper():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-paper-order-types-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        clock = _Clock()

        def repository_factory() -> TradingPaperRepository:
            # A new instance is what a restarted process would build.
            return TradingPaperRepository(context=context, uow_factory=uow, clock=clock)

        protections = TradingPaperProtectionRepository(context=context, uow_factory=uow)
        suffix = uuid.uuid4().hex[:10]
        account_id = f"paper-types-{suffix}"
        repository_factory().create_account(
            PaperAccountCreate(account_id=account_id, name="Order types", initial_cash=Decimal("100000"))
        )
        yield SimpleState(
            repository_factory=repository_factory,
            protections=protections,
            account_id=account_id,
            instrument_id=f"equity:NASDAQ:T{suffix[:4].upper()}",
            clock=clock,
        )
    finally:
        database.close()


class SimpleState:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)


def _observation(instrument_id: str, price: str, *, seconds: int = 1) -> PaperMarketObservation:
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    value = Decimal(price)
    return PaperMarketObservation(
        instrument_id=instrument_id,
        provider="integration",
        price=value,
        bid=value,
        ask=value,
        bid_size=Decimal("100000"),
        ask_size=Decimal("100000"),
        source_time=moment,
        evaluated_at=moment,
        execution_eligible=True,
        freshness_mode="live",
    )


def _request(instrument_id: str, side: str, order_type: str, key: str, **fields) -> PaperOrderRequest:
    return PaperOrderRequest(
        order_id=f"order-{key}",
        instrument_id=instrument_id,
        side=side,
        order_type=order_type,
        quantity=fields.pop("quantity", Decimal("10")),
        idempotency_key=f"idem-{key}",
        **fields,
    )


def _open_long(state, quantity: str = "10", price: str = "10") -> None:
    repository = state.repository_factory()
    OrderGateway(repository).place_manual_entry(
        state.account_id,
        _request(state.instrument_id, "buy", "market", uuid.uuid4().hex, quantity=Decimal(quantity), reference_price=Decimal(price)),
    )
    repository.process_observation(state.account_id, _observation(state.instrument_id, price))


def _cash(state) -> tuple[Decimal, Decimal]:
    balance = state.repository_factory().snapshot(state.account_id).balances[0]
    return balance.available, balance.reserved


def _order(state, order_id: str):
    return next(item for item in state.repository_factory().snapshot(state.account_id).order_history if item.order_id == order_id)


def test_the_migration_keeps_existing_orders_gtc(paper) -> None:
    repository = paper.repository_factory()
    placed = OrderGateway(repository).place_manual_entry(
        paper.account_id, _request(paper.instrument_id, "buy", "limit", "plain", limit_price=Decimal("9"))
    )
    assert (placed.time_in_force, placed.expires_at, placed.trail_amount, placed.trail_water_mark) == ("gtc", None, None, None)
    assert repository.expire_orders(paper.account_id, now=datetime(2100, 1, 1, tzinfo=timezone.utc)) == []
    assert _order(paper, placed.order_id).status == "open"


def test_the_database_refuses_inconsistent_order_rows() -> None:
    with psycopg.connect(os.environ["OMNIX_TEST_DATABASE_URL"]) as connection:
        checks = {
            row[0]
            for row in connection.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'omnix_trading_paper_orders'::regclass"
            ).fetchall()
        }
        assert {
            "omnix_trading_paper_orders_time_in_force_check",
            "omnix_trading_paper_orders_trailing_check",
            "omnix_trading_paper_orders_stop_limit_check",
        } <= checks
        definitions = dict(
            connection.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'omnix_trading_paper_orders'::regclass"
            ).fetchall()
        )
        assert "stop_limit" in definitions["omnix_trading_paper_orders_order_type_check"]
        assert "expired" in definitions["omnix_trading_paper_orders_status_check"]
        column = connection.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'omnix_trading_paper_orders' AND column_name = 'time_in_force'"
        ).fetchone()
        assert column is not None and "gtc" in column[0]


def test_stop_limit_gap_through_rests_then_fills_at_the_limit(paper) -> None:
    repository = paper.repository_factory()
    placed = OrderGateway(repository).place_manual_entry(
        paper.account_id,
        _request(paper.instrument_id, "buy", "stop_limit", "stop-limit", stop_price=Decimal("10"), limit_price=Decimal("10.5")),
    )
    assert placed.reserved_cash == Decimal("105")
    assert repository.process_observation(paper.account_id, _observation(paper.instrument_id, "9.5")) == []
    assert _order(paper, placed.order_id).stop_triggered_at is None

    # The market gaps through both the stop and the limit: triggered, not filled.
    assert repository.process_observation(paper.account_id, _observation(paper.instrument_id, "11", seconds=2)) == []
    triggered = _order(paper, placed.order_id)
    assert triggered.status == "open" and triggered.stop_triggered_at is not None

    # A restarted repository sees a resting limit order, filled back below the stop.
    fills = paper.repository_factory().process_observation(
        paper.account_id, _observation(paper.instrument_id, "9.8", seconds=3)
    )
    assert [(fill.price, fill.quantity) for fill in fills] == [(Decimal("9.8"), Decimal("10"))]
    assert _order(paper, placed.order_id).status == "filled"
    assert _cash(paper) == (Decimal("100000") - Decimal("98"), Decimal("0"))


def test_trailing_stop_state_survives_a_restart(paper) -> None:
    _open_long(paper)
    request = _request(paper.instrument_id, "sell", "trailing_stop", "trail", trail_amount=Decimal("1"))
    placed = OrderGateway(paper.repository_factory()).place_reducing(paper.account_id, request)
    assert placed.stop_price is None and placed.trail_water_mark is None

    paper.repository_factory().process_observation(paper.account_id, _observation(paper.instrument_id, "10"))
    armed = _order(paper, placed.order_id)
    assert (armed.trail_water_mark, armed.stop_price) == (Decimal("10"), Decimal("9"))
    assert armed.trail_moved_at is not None
    paper.repository_factory().process_observation(paper.account_id, _observation(paper.instrument_id, "12", seconds=2))
    paper.repository_factory().process_observation(paper.account_id, _observation(paper.instrument_id, "11.5", seconds=3))

    restarted = paper.repository_factory()
    resumed = _order(paper, placed.order_id)
    assert (resumed.trail_water_mark, resumed.stop_price, resumed.status) == (Decimal("12"), Decimal("11"), "open")
    # An idempotent retry after the trail moved returns the same order.
    assert OrderGateway(restarted).place_reducing(paper.account_id, request).order_id == placed.order_id

    fills = restarted.process_observation(paper.account_id, _observation(paper.instrument_id, "10.9", seconds=4))
    # 10.9 less stop slippage is 10.87275, rounded down to the 0.01 equity tick.
    assert [fill.price for fill in fills] == [Decimal("10.87")]
    assert Decimal("10.9") * STOP_SLIPPAGE == Decimal("10.87275")
    assert _order(paper, placed.order_id).status == "filled"


def test_day_orders_expire_at_the_session_close_and_release_their_holds(paper) -> None:
    _open_long(paper)
    paper.clock.now = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)  # Wednesday 14:00 ET
    close = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
    repository = paper.repository_factory()
    gateway = OrderGateway(repository)
    buy = gateway.place_manual_entry(
        paper.account_id,
        _request(paper.instrument_id, "buy", "limit", "day-buy", limit_price=Decimal("5"), time_in_force="day"),
    )
    sell = gateway.place_reducing(
        paper.account_id,
        _request(paper.instrument_id, "sell", "limit", "day-sell", quantity=Decimal("4"), limit_price=Decimal("50"), time_in_force="day"),
    )
    assert buy.expires_at == close and sell.expires_at == close
    available_before, _ = _cash(paper)

    assert repository.expire_orders(paper.account_id, now=close - timedelta(seconds=1)) == []
    expired = repository.expire_orders(paper.account_id, now=close)
    assert {item.order_id for item in expired} == {buy.order_id, sell.order_id}
    assert {item.status for item in expired} == {"expired"}
    assert {item.rejection_reason for item in expired} == {"time_in_force_expired"}
    available, reserved = _cash(paper)
    assert available == available_before + Decimal("50") and reserved == Decimal("0")
    position = paper.repository_factory().snapshot(paper.account_id).positions[0]
    assert position.reserved_quantity == Decimal("0")


def test_gtd_orders_expire_before_an_observation_can_fill_them(paper) -> None:
    now = datetime.now(timezone.utc)
    repository = paper.repository_factory()
    with pytest.raises(ValueError, match="paper_order_expiry_in_past"):
        OrderGateway(repository).place_manual_entry(
            paper.account_id,
            _request(paper.instrument_id, "buy", "limit", "gtd-past", limit_price=Decimal("10"), time_in_force="gtd", expires_at=now),
        )
    placed = OrderGateway(repository).place_manual_entry(
        paper.account_id,
        _request(
            paper.instrument_id, "buy", "limit", "gtd", limit_price=Decimal("10"),
            time_in_force="gtd", expires_at=now + timedelta(hours=1),
        ),
    )
    assert _cash(paper)[1] == Decimal("100")
    paper.clock.now = now + timedelta(hours=2)
    assert paper.repository_factory().process_observation(paper.account_id, _observation(paper.instrument_id, "9")) == []
    assert _order(paper, placed.order_id).status == "expired"
    assert _cash(paper) == (Decimal("100000"), Decimal("0"))


def test_trailing_stop_loss_leg_is_persisted_and_resumed(paper) -> None:
    _open_long(paper)
    protections = paper.protections
    protection = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9"), trail_amount=Decimal("1")),
    )
    assert protection.status == "active" and protection.trail_amount == Decimal("1")
    assert protection.trail_water_mark is None

    moved = protections.trail_stop(
        paper.account_id, paper.instrument_id,
        water_mark=Decimal("12"), stop_loss=Decimal("11"), expected_revision=protection.revision,
    )
    assert moved is not None and (moved.trail_water_mark, moved.stop_loss, moved.revision) == (
        Decimal("12"), Decimal("11"), protection.revision,
    )
    stale = protections.trail_stop(
        paper.account_id, paper.instrument_id,
        water_mark=Decimal("13"), stop_loss=Decimal("12"), expected_revision=protection.revision + 5,
    )
    assert stale is None

    # Editing the bracket starts a fresh trail from the new initial stop.
    rearmed = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("8"), trail_percent=Decimal("5")),
    )
    assert (rearmed.trail_water_mark, rearmed.stop_loss, rearmed.trail_amount, rearmed.trail_percent) == (
        None, Decimal("8"), None, Decimal("5"),
    )
    protections.trail_stop(
        paper.account_id, paper.instrument_id,
        water_mark=Decimal("12"), stop_loss=Decimal("11.4"), expected_revision=rearmed.revision,
    )
    resumed = protections.get(paper.account_id, paper.instrument_id)
    assert (resumed.trail_water_mark, resumed.stop_loss) == (Decimal("12"), Decimal("11.4"))

    # The monitor exits at the trailed stop, not the initial one.
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    class _Market:
        def execution_observation(self, instrument_id, binding_id=None):
            return ExecutionObservation(
                instrument_id=instrument_id, binding_id="alpaca-paper:test", provider="integration",
                bid=Decimal("10.9"), ask=Decimal("10.9"), last=Decimal("10.9"),
                source_time=now, received_at=now, session="regular", freshness_mode="live",
                execution_eligible=True,
            )

    repository = paper.repository_factory()
    monitor = TradingPaperMonitor(
        repository_factory=lambda: repository,
        protection_repository_factory=lambda: protections,
        market_service_factory=_Market,
        interval_seconds=5,
    )
    asyncio.run(
        monitor._reconcile_protection(
            account_id=paper.account_id,
            instrument_id=paper.instrument_id,
            execution=_Market().execution_observation(paper.instrument_id),
            repository=repository,
            protections=protections,
        )
    )
    exited = protections.get(paper.account_id, paper.instrument_id)
    assert (exited.status, exited.trigger_reason) == ("exit_submitted", "stop_loss")
    exit_order = _order(paper, exited.exit_order_id)
    assert (exit_order.side, exit_order.order_type, exit_order.quantity) == ("sell", "market", Decimal("10"))


def test_a_pending_bracket_is_cancelled_when_its_day_entry_expires(paper) -> None:
    paper.clock.now = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)
    repository = paper.repository_factory()
    protections = paper.protections
    order_id = f"order-day-entry-{uuid.uuid4().hex[:6]}"
    protections.arm_pending_entry(
        paper.account_id,
        PaperProtectionUpsert(
            instrument_id=paper.instrument_id, entry_order_id=order_id,
            stop_loss=Decimal("4"), trail_amount=Decimal("1"),
        ),
    )
    OrderGateway(repository).place_manual_entry(
        paper.account_id,
        PaperOrderRequest(
            order_id=order_id, instrument_id=paper.instrument_id, side="buy", order_type="limit",
            quantity=Decimal("1"), limit_price=Decimal("5"), idempotency_key=order_id, time_in_force="day",
        ),
    )
    # No market data at all: the expiry itself cancels the entry's bracket.
    repository.expire_orders(paper.account_id, now=datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc))
    cancelled = protections.get(paper.account_id, paper.instrument_id)
    assert (cancelled.status, cancelled.trigger_reason) == ("cancelled", "entry_expired")


def test_keeping_the_stop_and_trail_keeps_the_water_mark(paper) -> None:
    _open_long(paper)
    protections = paper.protections
    leg = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9"), trail_amount=Decimal("1")),
    )
    moved_at = datetime.now(timezone.utc)
    protections.trail_stop(
        paper.account_id, paper.instrument_id,
        water_mark=Decimal("12"), stop_loss=Decimal("11"), expected_revision=leg.revision, moved_at=moved_at,
    )
    moved = protections.get(paper.account_id, paper.instrument_id)
    assert moved.trail_moved_at == moved_at

    # A take-profit move that sends the current stop and trail back keeps the trail.
    tp_move = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(
            instrument_id=paper.instrument_id, take_profit=Decimal("15"),
            stop_loss=Decimal("11"), trail_amount=Decimal("1"),
        ),
    )
    assert (tp_move.take_profit, tp_move.trail_water_mark, tp_move.trail_moved_at) == (Decimal("15"), Decimal("12"), moved_at)

    # Moving the stop keeps the trail and records the move; the monitor's next
    # update tightens a looser stop back to the trail.
    stop_move = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(
            instrument_id=paper.instrument_id, take_profit=Decimal("15"),
            stop_loss=Decimal("10"), trail_amount=Decimal("1"),
        ),
    )
    assert (stop_move.stop_loss, stop_move.trail_water_mark) == (Decimal("10"), Decimal("12"))
    assert stop_move.trail_moved_at is not None and stop_move.trail_moved_at > moved_at
    observation = PaperMarketObservation(
        instrument_id=paper.instrument_id, provider="integration", price=Decimal("11.5"),
        source_time=datetime.now(timezone.utc), evaluated_at=datetime.now(timezone.utc),
    )
    assert paper_trailing_protection_update(
        is_long=True, stop_loss=stop_move.stop_loss, trail_amount=stop_move.trail_amount, trail_percent=None,
        water_mark=stop_move.trail_water_mark, observation=observation,
    ) == (Decimal("12"), Decimal("11"))

    # A different trail starts afresh.
    retrailed = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("10"), trail_percent=Decimal("5")),
    )
    # A different trail starts a fresh water mark; the unchanged stop keeps its stamp.
    assert (retrailed.trail_water_mark, retrailed.trail_moved_at) == (None, stop_move.trail_moved_at)


def test_any_stop_edit_on_an_active_leg_is_stamped(paper) -> None:
    _open_long(paper)
    protections = paper.protections
    before = datetime.now(timezone.utc)
    plain = protections.upsert(
        paper.account_id, PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9")),
    )
    assert plain.status == "active" and plain.trail_moved_at is not None and plain.trail_moved_at >= before
    same = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, take_profit=Decimal("12"), stop_loss=Decimal("9")),
    )
    assert same.trail_moved_at == plain.trail_moved_at
    tighter = protections.upsert(
        paper.account_id, PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9.5")),
    )
    assert tighter.trail_moved_at > plain.trail_moved_at

    # Turning trailing on with a new stop starts a fresh trail, stamped too.
    trailing = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9.6"), trail_percent=Decimal("5")),
    )
    assert trailing.trail_water_mark is None and trailing.trail_moved_at > tighter.trail_moved_at
    # Changing only the trail keeps the stop's stamp.
    retrailed = protections.upsert(
        paper.account_id,
        PaperProtectionUpsert(instrument_id=paper.instrument_id, stop_loss=Decimal("9.6"), trail_amount=Decimal("0.5")),
    )
    assert retrailed.trail_water_mark is None and retrailed.trail_moved_at == trailing.trail_moved_at


def _working_entry(state, key: str, *, armed: bool = True):
    repository = state.repository_factory()
    entry = OrderGateway(repository).place_manual_entry(
        state.account_id,
        _request(state.instrument_id, "buy", "limit", key, limit_price=Decimal("9"), time_in_force="day"),
    )
    if armed:
        state.protections.arm_pending_entry(
            state.account_id,
            PaperProtectionUpsert(instrument_id=state.instrument_id, entry_order_id=entry.order_id, stop_loss=Decimal("8"), trail_percent=Decimal("2")),
        )
    return entry


def _moved(state, key: str, price: str = "8.5") -> PaperOrderRequest:
    return _request(state.instrument_id, "buy", "limit", key, limit_price=Decimal(price), quantity=Decimal("12"), time_in_force="day")


def test_moving_an_entry_repoints_its_stop_in_the_same_transaction(paper) -> None:
    entry = _working_entry(paper, "move-a")
    gateway = OrderGateway(paper.repository_factory())
    cancelled, moved = gateway.replace_manual_entry(paper.account_id, entry.order_id, _moved(paper, "move-b"))
    assert (cancelled.status, moved.status, moved.limit_price) == ("cancelled", "open", Decimal("8.5"))
    protection = paper.protections.get(paper.account_id, paper.instrument_id)
    assert (protection.entry_order_id, protection.status, protection.stop_loss, protection.trail_percent) == (
        moved.order_id, "pending_entry", Decimal("8"), Decimal("2"),
    )
    # A second move of the same (now cancelled) order changes nothing: the stop stays on the live entry.
    with pytest.raises(ValueError, match="not_open"):
        gateway.replace_manual_entry(paper.account_id, entry.order_id, _moved(paper, "move-c", "8.6"))
    assert paper.protections.get(paper.account_id, paper.instrument_id).entry_order_id == moved.order_id
    assert _order(paper, moved.order_id).status == "open"


def test_a_moved_entry_cannot_reuse_an_old_order(paper) -> None:
    entry = _working_entry(paper, "reuse-a")
    old = _working_entry(paper, "reuse-old", armed=False)
    OrderGateway(paper.repository_factory()).cancel(paper.account_id, old.order_id)
    with pytest.raises(ValueError, match="paper_order_id_not_new"):
        OrderGateway(paper.repository_factory()).replace_manual_entry(paper.account_id, entry.order_id, _moved(paper, "reuse-old"))
    assert _order(paper, entry.order_id).status == "open"
    assert paper.protections.get(paper.account_id, paper.instrument_id).entry_order_id == entry.order_id


def test_an_entry_without_a_pending_stop_does_not_move(paper) -> None:
    entry = _working_entry(paper, "bare-a", armed=False)
    available, reserved = _cash(paper)
    with pytest.raises(ValueError, match="paper_risk_entry_not_movable"):
        OrderGateway(paper.repository_factory()).replace_manual_entry(paper.account_id, entry.order_id, _moved(paper, "bare-b"))
    # Rolled back: the entry is still working with its reservation, and no replacement exists.
    assert _order(paper, entry.order_id).status == "open"
    assert _cash(paper) == (available, reserved)
    assert all(item.order_id != "order-bare-b" for item in paper.repository_factory().snapshot(paper.account_id).order_history)
