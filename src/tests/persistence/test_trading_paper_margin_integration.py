"""Paper leverage, margin calls and fixed commission against PostgreSQL (TVP-7.2b)."""
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import ROUND_CEILING
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.order_gateway import OrderGateway
from app.apps.trading.paper import (
    PaperAccountCreate,
    PaperAccountSettings,
    PaperMargin,
    PaperMarketObservation,
    PaperOrderRequest,
)
from app.apps.trading.paper_monitor import MARGIN_CALL_ORDER_PREFIX, TradingPaperMonitor
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


class SimpleState:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)


@pytest.fixture()
def paper():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-paper-margin-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def repository_factory() -> TradingPaperRepository:
            # A new instance is what a restarted process would build.
            return TradingPaperRepository(context=context, uow_factory=lambda: unit_of_work(database))

        suffix = uuid.uuid4().hex[:10]

        def account(name: str, **fields) -> str:
            account_id = f"paper-margin-{name}-{suffix}"
            repository_factory().create_account(
                PaperAccountCreate(account_id=account_id, name=name, initial_cash=Decimal("10000"), **fields)
            )
            return account_id

        yield SimpleState(
            repository_factory=repository_factory, account=account, instrument_id=f"equity:NASDAQ:M{suffix[:4].upper()}",
            context=context, uow=lambda: unit_of_work(database),
        )
    finally:
        database.close()


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


def _market(state, account_id: str, instrument_id: str, side: str, quantity: str, price: str) -> None:
    key = uuid.uuid4().hex
    OrderGateway(state.repository_factory()).place_manual_entry(
        account_id,
        PaperOrderRequest(
            order_id=f"order-{key}", instrument_id=instrument_id, side=side, order_type="market",
            quantity=Decimal(quantity), reference_price=Decimal(price), idempotency_key=f"idem-{key}",
        ),
    )
    state.repository_factory().process_observation(account_id, _observation(instrument_id, price))


def _margin_call(state, account_id: str, price: str, notifier=None) -> TradingPaperMonitor:
    repository = state.repository_factory()
    extra = {"margin_call_notifier_factory": notifier} if notifier is not None else {}
    monitor = TradingPaperMonitor(repository_factory=lambda: repository, interval_seconds=5, **extra)
    asyncio.run(monitor._margin_call(account_id, {state.instrument_id: Decimal(price)}, repository))
    return monitor


def _margin_orders(state, account_id: str):
    return [order for order in state.repository_factory().snapshot(account_id).order_history if order.order_id.startswith(MARGIN_CALL_ORDER_PREFIX)]


def test_leverage_raises_buying_power_and_borrows_the_rest(paper) -> None:
    cash_only = paper.account("cash")
    leveraged = paper.account("leveraged", margin={"equity": PaperMargin(long_pct=Decimal("50"))})
    with pytest.raises(ValueError, match="insufficient_paper_cash"):
        _market(paper, cash_only, paper.instrument_id, "buy", "150", "100")
    _market(paper, leveraged, paper.instrument_id, "buy", "150", "100")
    snapshot = paper.repository_factory().snapshot(leveraged)
    position = snapshot.positions[0]
    cost = position.quantity * position.average_cost  # market fills pay the slippage
    assert position.quantity == Decimal("150")
    assert snapshot.balances[0].available == Decimal("10000") - cost
    status = snapshot.margin_status
    assert status is not None
    # Half the value is held, half borrowed: buying power is the cash plus the long's free half, at its last price.
    assert status.buying_power == Decimal("10000") - cost + position.quantity * position.last_price / 2
    assert status.maintenance == position.quantity * position.last_price / 2
    assert status.margin_call is False
    # An account made without margin settings (as strategies make them) holds 100%, as before.
    assert paper.repository_factory().snapshot(cash_only).account.margin == {}


def test_a_margin_call_closes_only_what_restores_the_margin_once(paper) -> None:
    account_id = paper.account("call", margin={"equity": PaperMargin(long_pct=Decimal("50"))})
    _market(paper, account_id, paper.instrument_id, "buy", "150", "100")
    _margin_call(paper, account_id, "70")
    assert _margin_orders(paper, account_id) == []

    # At 60: equity below the 4,500 maintenance; each unit closed frees 30 of it, so the shortfall / 30, rounded up.
    cash = paper.repository_factory().snapshot(account_id).balances[0].available
    shortfall = 150 * Decimal("60") / 2 - (cash + 150 * Decimal("60"))
    expected = (shortfall / 30).to_integral_value(rounding=ROUND_CEILING)
    _margin_call(paper, account_id, "60")
    orders = _margin_orders(paper, account_id)
    assert [(order.side, order.order_type, order.quantity, order.status) for order in orders] == [("sell", "market", expected, "open")]
    # A second tick, or a restarted monitor, places nothing more while it works.
    _margin_call(paper, account_id, "60")
    assert len(_margin_orders(paper, account_id)) == 1

    paper.repository_factory().process_observation(account_id, _observation(paper.instrument_id, "60", seconds=2))
    snapshot = paper.repository_factory().snapshot(account_id)
    assert snapshot.positions[0].quantity == 150 - expected
    _margin_call(paper, account_id, "60")
    assert [order.status for order in _margin_orders(paper, account_id)] == ["filled"]


def test_a_short_is_margin_called_when_it_runs_against_the_account(paper) -> None:
    account_id = paper.account("short", allow_short=True)
    _market(paper, account_id, paper.instrument_id, "sell", "100", "100")
    # 100% short margin: at 160 equity (cash with the proceeds, less the buy-back) is far below 16,000.
    cash = paper.repository_factory().snapshot(account_id).balances[0].available
    shortfall = 100 * Decimal("160") - (cash - 100 * Decimal("160"))
    expected = (shortfall / 160).to_integral_value(rounding=ROUND_CEILING)
    _margin_call(paper, account_id, "160")
    assert [(order.side, order.quantity) for order in _margin_orders(paper, account_id)] == [("buy", expected)]


def test_a_fixed_commission_is_charged_once_per_order(paper) -> None:
    account_id = paper.account("fixed", commission_type="fixed_per_order", commission_fixed=Decimal("5"))
    _market(paper, account_id, paper.instrument_id, "buy", "10", "10")
    _market(paper, account_id, paper.instrument_id, "buy", "10", "10")
    snapshot = paper.repository_factory().snapshot(account_id)
    assert [fill.commission for fill in snapshot.recent_fills] == [Decimal("5"), Decimal("5")]
    cost = sum(fill.quantity * fill.price for fill in snapshot.recent_fills)
    assert snapshot.balances[0].available == Decimal("10000") - cost - 10


def test_settings_change_margin_and_commission_and_keep_the_rest(paper) -> None:
    account_id = paper.account("settings")
    repository = paper.repository_factory()
    revision = repository.snapshot(account_id).account.revision
    updated = repository.update_account_settings(
        account_id,
        PaperAccountSettings(allow_short=True, margin={"crypto": PaperMargin(long_pct=Decimal("10"), short_pct=Decimal("20"))}, commission_type="fixed_per_order", commission_fixed=Decimal("1.5")),
        expected_revision=revision,
    ).account
    assert updated.margin == {"crypto": PaperMargin(long_pct=Decimal("10"), short_pct=Decimal("20"))}
    assert (updated.commission_type, updated.commission_fixed, updated.allow_short) == ("fixed_per_order", Decimal("1.5"), True)
    kept = repository.update_account_settings(account_id, PaperAccountSettings(allow_short=False), expected_revision=updated.revision).account
    assert kept.margin == updated.margin and kept.commission_fixed == Decimal("1.5") and kept.allow_short is False


def test_partial_fills_of_a_leveraged_limit_buy_each_draw_their_margin_share(paper) -> None:
    account_id = paper.account("partial", margin={"equity": PaperMargin(long_pct=Decimal("50"))})
    key = uuid.uuid4().hex
    OrderGateway(paper.repository_factory()).place_manual_entry(
        account_id,
        PaperOrderRequest(
            order_id=f"order-{key}", instrument_id=paper.instrument_id, side="buy", order_type="limit",
            quantity=Decimal("100"), limit_price=Decimal("100"), idempotency_key=f"idem-{key}",
        ),
    )
    # Thin books: each observation fills part of the order.
    for second in range(1, 40):
        observation = _observation(paper.instrument_id, "100", seconds=second).model_copy(update={"ask_size": Decimal("100")})
        paper.repository_factory().process_observation(account_id, observation)
        snapshot = paper.repository_factory().snapshot(account_id)
        if not snapshot.open_orders:
            break
    order = next(item for item in snapshot.order_history if item.order_id == f"order-{key}")
    assert (order.status, order.filled_quantity) == ("filled", Decimal("100"))
    assert len([fill for fill in snapshot.recent_fills if fill.order_id == order.order_id]) > 1
    assert snapshot.balances[0].reserved == 0
    assert snapshot.balances[0].available == Decimal("10000") - Decimal("10000")


def test_a_short_with_a_working_cover_is_still_margin_called(paper) -> None:
    account_id = paper.account("covered", allow_short=True)
    _market(paper, account_id, paper.instrument_id, "sell", "100", "100")
    key = uuid.uuid4().hex
    OrderGateway(paper.repository_factory()).place_reducing(
        account_id,
        PaperOrderRequest(
            order_id=f"tp-{key}", instrument_id=paper.instrument_id, side="buy", order_type="limit",
            quantity=Decimal("100"), limit_price=Decimal("50"), idempotency_key=f"tp-{key}",
        ),
    )
    _margin_call(paper, account_id, "160")
    snapshot = paper.repository_factory().snapshot(account_id)
    assert [order.side for order in _margin_orders(paper, account_id)] == ["buy"]
    # The take-profit made room for it.
    assert next(order for order in snapshot.order_history if order.order_id == f"tp-{key}").status == "cancelled"


def test_a_cancelled_margin_call_is_followed_by_a_new_one(paper) -> None:
    account_id = paper.account("retry", margin={"equity": PaperMargin(long_pct=Decimal("50"))})
    _market(paper, account_id, paper.instrument_id, "buy", "150", "100")
    _margin_call(paper, account_id, "60")
    [first] = _margin_orders(paper, account_id)
    paper.repository_factory().cancel_order(account_id, first.order_id)
    _margin_call(paper, account_id, "60")
    orders = _margin_orders(paper, account_id)
    assert sorted(order.status for order in orders) == ["cancelled", "open"]


def test_a_margin_call_is_sent_by_email_once_when_the_account_asks(paper) -> None:
    """TVP-7.2b: margin calls go through the outbox on the workspace's email and push channels."""
    from app.apps.trading.alerts_delivery import NotificationDeliveryRepository
    from app.apps.trading.alerts_notify import EmailSettings, NotificationSettingsRepository
    from app.apps.trading.paper_margin_notifications import PaperMarginCallNotifier

    def settings() -> NotificationSettingsRepository:
        return NotificationSettingsRepository(context=paper.context, uow_factory=paper.uow)

    def notifier() -> PaperMarginCallNotifier:
        return PaperMarginCallNotifier(context=paper.context, uow_factory=paper.uow, settings_factory=settings)

    symbol = paper.instrument_id.split(":")[-1]
    previous = settings().email()
    settings().save_email(EmailSettings(host="smtp.example.com", from_address="omnix@example.com", to_addresses=["me@example.com"]))
    try:
        quiet = paper.account("quiet", margin={"equity": PaperMargin(long_pct=Decimal("50"))})
        _market(paper, quiet, paper.instrument_id, "buy", "150", "100")
        _margin_call(paper, quiet, "60", notifier)
        account_id = paper.account("notified", margin={"equity": PaperMargin(long_pct=Decimal("50"))}, notify_margin_calls=True)
        assert paper.repository_factory().snapshot(account_id).account.notify_margin_calls is True
        _market(paper, account_id, paper.instrument_id, "buy", "150", "100")
        monitor = _margin_call(paper, account_id, "60", notifier)
        assert monitor.last_error is None
        _margin_call(paper, account_id, "60", notifier)  # the next pass queues nothing new

        deliveries = NotificationDeliveryRepository(context=paper.context, uow_factory=paper.uow).list_deliveries(limit=500)
        ours = [item for item in deliveries if item.event_kind == "margin_call" and item.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5)]
        mine = [item for item in ours if item.alert_id is None and item.trigger_id is None]
        assert [(item.channel, item.status) for item in mine] == [("email", "pending")]
        claimed = NotificationDeliveryRepository(all_workspaces=True, uow_factory=paper.uow).claim_due(datetime.now(timezone.utc) + timedelta(seconds=1), limit=500)
        delivery = next(item for item in claimed if item.delivery_id == mine[0].delivery_id)
        assert (delivery.event_kind, delivery.alert_id, delivery.trigger_id) == ("margin_call", "", "")
        assert "Sold" in delivery.message and symbol in delivery.message and "notified" in delivery.message
    finally:
        settings().save_email(previous)


def test_margin_call_notifications_are_a_setting_that_keeps_the_rest(paper) -> None:
    account_id = paper.account("setting", allow_short=True)
    repository = paper.repository_factory()
    current = repository.snapshot(account_id).account
    updated = repository.update_account_settings(account_id, PaperAccountSettings(notify_margin_calls=True), expected_revision=current.revision)
    assert updated.account.notify_margin_calls is True and updated.account.allow_short is True
