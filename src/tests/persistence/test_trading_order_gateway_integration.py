"""The order gateway's guarantees against PostgreSQL (WP-8.3)."""
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
from app.apps.trading.kill_switches import TradingKillSwitchRepository
from app.apps.trading.order_gateway import OrderGateway
from app.apps.trading.paper import PaperAccountCreate, PaperMarketObservation, PaperOrderRequest
from app.apps.trading.paper_repository import TradingPaperRepository

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("OMNIX_TEST_DATABASE_URL"),
        reason="OMNIX_TEST_DATABASE_URL is required for PostgreSQL integration tests",
    ),
]


class _ApprovingAuthorizer:
    def __init__(self) -> None:
        self.attempts: list[str] = []

    def authorize(self, account_id, request, *, trade_attempt_id):
        self.attempts.append(trade_attempt_id)


@pytest.fixture()
def paper():
    database = PostgresDatabase(
        DatabaseSettings(
            url=os.environ["OMNIX_TEST_DATABASE_URL"],
            pool_min=1,
            pool_max=3,
            connect_timeout_seconds=10,
            statement_timeout_ms=30_000,
            application_name="omnix-order-gateway-tests",
        )
    )
    try:
        context = ensure_local_identity(database)

        def uow():
            return unit_of_work(database)

        repository = TradingPaperRepository(context=context, uow_factory=uow)
        switches = TradingKillSwitchRepository(context=context, uow_factory=uow)
        suffix = uuid.uuid4().hex[:10]
        account_id = f"paper-gateway-{suffix}"
        repository.create_account(
            PaperAccountCreate(account_id=account_id, name="Order gateway", initial_cash=Decimal("100000"))
        )
        yield repository, switches, account_id, f"equity:NASDAQ:G{suffix[:4].upper()}", database
        for scope, scope_id in (("global", ""), ("account", account_id), ("strategy", f"strategy-{suffix}")):
            switches.set(scope, scope_id, engaged=False)
    finally:
        database.close()


def _order(instrument_id: str, side: str, quantity: str, key: str, **prices) -> PaperOrderRequest:
    if not prices:
        prices = {"reference_price": Decimal("10")}
    return PaperOrderRequest(
        order_id=f"order-{key}",
        instrument_id=instrument_id,
        side=side,
        order_type="market" if "reference_price" in prices else "limit",
        quantity=Decimal(quantity),
        idempotency_key=f"idem-{key}",
        **prices,
    )


def _fill(repository, account_id: str, instrument_id: str) -> None:
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    repository.process_observation(
        account_id,
        PaperMarketObservation(
            instrument_id=instrument_id,
            provider="integration",
            price=Decimal("10"),
            bid=Decimal("9.99"),
            ask=Decimal("10.01"),
            bid_size=Decimal("100000"),
            ask_size=Decimal("100000"),
            source_time=now,
            evaluated_at=now,
            execution_eligible=True,
            freshness_mode="live",
        ),
    )


def _open_long(repository, account_id: str, instrument_id: str, quantity: str = "10") -> None:
    OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "buy", quantity, uuid.uuid4().hex))
    _fill(repository, account_id, instrument_id)


def test_an_entry_needs_entry_authority(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper

    with pytest.raises(ValueError, match="paper_order_requires_entry_authority"):
        OrderGateway(repository).place_reducing(account_id, _order(instrument_id, "buy", "1", "reduce-buy"))

    placed = OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "buy", "1", "manual-buy"))
    assert placed.status == "open"


@pytest.mark.parametrize("scope", ["global", "account", "strategy"])
def test_an_engaged_kill_switch_stops_entries_but_not_exits(paper, scope) -> None:
    repository, switches, account_id, instrument_id, _ = paper
    strategy_id = f"strategy-{account_id.rsplit('-', 1)[1]}"
    _open_long(repository, account_id, instrument_id)
    scope_id = {"global": "", "account": account_id, "strategy": strategy_id}[scope]
    switches.set(scope, scope_id, engaged=True, reason="integration test")
    gateway = OrderGateway(repository, entry_authorizer=_ApprovingAuthorizer())

    with pytest.raises(ValueError, match=f"trading_kill_switch_engaged:{scope}"):
        gateway.place_strategy_entry(
            account_id,
            _order(instrument_id, "buy", "1", f"entry-{scope}"),
            strategy_id=strategy_id,
            trade_attempt_id="attempt-1",
        )
    exit_order = gateway.place_reducing(account_id, _order(instrument_id, "sell", "10", f"exit-{scope}"))
    assert exit_order.status == "open"

    switches.set(scope, scope_id, engaged=False)
    entry = gateway.place_strategy_entry(
        account_id,
        _order(instrument_id, "buy", "1", f"entry-after-{scope}"),
        strategy_id=strategy_id,
        trade_attempt_id="attempt-2",
    )
    assert entry.status == "open"


def test_accounts_are_long_only_unless_they_allow_shorting(paper) -> None:
    repository, _, account_id, instrument_id, database = paper

    with pytest.raises(ValueError, match="paper_short_not_allowed"):
        OrderGateway(repository).place_reducing(account_id, _order(instrument_id, "sell", "1", "naked-sell"))

    with unit_of_work(database) as uow:
        uow.connection.execute(
            "UPDATE omnix_trading_paper_accounts SET allow_short = TRUE WHERE workspace_id = %s AND account_id = %s",
            (repository.context.workspace_id, account_id),
        )
        uow.commit()
    # A short opens exposure: it needs entry authority, as a buy does.
    with pytest.raises(ValueError, match="paper_order_requires_entry_authority"):
        OrderGateway(repository).place_reducing(account_id, _order(instrument_id, "sell", "1", "short-reduce"))
    short = OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "sell", "1", "short-entry"))
    assert short.status == "open"


def test_a_sell_beyond_the_unreserved_position_is_rejected(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper
    _open_long(repository, account_id, instrument_id, quantity="5")

    with pytest.raises(ValueError, match="insufficient_paper_position"):
        OrderGateway(repository).place_reducing(account_id, _order(instrument_id, "sell", "6", "oversell"))


def test_a_rejected_replacement_leaves_the_original_order_open(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper
    _open_long(repository, account_id, instrument_id, quantity="5")
    gateway = OrderGateway(repository)
    original = gateway.place_reducing(
        account_id, _order(instrument_id, "sell", "5", "original", limit_price=Decimal("12"))
    )

    with pytest.raises(ValueError, match="insufficient_paper_position"):
        gateway.replace_reducing(
            account_id,
            original.order_id,
            _order(instrument_id, "sell", "6", "too-large", limit_price=Decimal("12")),
        )
    snapshot = repository.snapshot(account_id)
    assert [order.order_id for order in snapshot.open_orders] == [original.order_id]
    position = next(item for item in snapshot.positions if item.instrument_id == instrument_id)
    assert position.reserved_quantity == Decimal("5")

    cancelled, replacement = gateway.replace_reducing(
        account_id,
        original.order_id,
        _order(instrument_id, "sell", "5", "replacement", limit_price=Decimal("11")),
    )
    assert cancelled.status == "cancelled"
    assert replacement.status == "open"


def test_an_idempotent_retry_returns_the_placed_order_even_after_a_kill_switch(paper) -> None:
    repository, switches, account_id, instrument_id, _ = paper
    gateway = OrderGateway(repository)
    request = _order(instrument_id, "buy", "1", "retry")
    first = gateway.place_manual_entry(account_id, request)
    switches.set("account", account_id, engaged=True)

    assert gateway.place_manual_entry(account_id, request) == first


def test_the_kill_switch_http_control_takes_effect_without_a_restart(paper) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.apps.trading.kill_switches import create_trading_kill_switch_router

    repository, switches, account_id, instrument_id, _ = paper
    app = FastAPI()
    app.include_router(create_trading_kill_switch_router(lambda: switches))
    client = TestClient(app)

    engaged = client.put(
        "/api/trading/kill-switches",
        json={"scope": "account", "scope_id": account_id, "engaged": True, "reason": "operator stop"},
    )
    assert engaged.status_code == 200
    assert engaged.json()["engaged"] is True
    with pytest.raises(ValueError, match="kill_switch"):
        OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "buy", "1", uuid.uuid4().hex))

    listed = client.get("/api/trading/kill-switches").json()
    assert {"scope": "account", "scope_id": account_id, "engaged": True}.items() <= next(
        item for item in listed if item["scope_id"] == account_id
    ).items()

    released = client.put(
        "/api/trading/kill-switches", json={"scope": "account", "scope_id": account_id, "engaged": False}
    )
    assert released.json()["revision"] == engaged.json()["revision"] + 1
    assert OrderGateway(repository).place_manual_entry(
        account_id, _order(instrument_id, "buy", "1", uuid.uuid4().hex)
    ).status == "open"

    # A global switch has no scope id; account and strategy switches need one.
    assert client.put("/api/trading/kill-switches", json={"scope": "global", "scope_id": "x", "engaged": True}).status_code == 422
    assert client.put("/api/trading/kill-switches", json={"scope": "strategy", "engaged": True}).status_code == 422


def test_entries_stop_once_the_daily_loss_limit_is_reached_but_exits_continue(paper) -> None:
    repository, _, account_id, instrument_id, database = paper
    _open_long(repository, account_id, instrument_id, "10")
    # 1.5% of the 100,000 account is the manual policy's daily limit.
    with unit_of_work(database) as uow:
        uow.connection.execute(
            """
            INSERT INTO omnix_trading_paper_ledger (
                workspace_id, account_id, ledger_id, entry_type, currency, amount, idempotency_key, payload
            ) VALUES (%s, %s, %s, 'realized_pnl', 'USD', %s, %s, '{}'::jsonb)
            """,
            (repository.context.workspace_id, account_id, f"loss-{account_id}", Decimal("-1600"), f"loss-{account_id}"),
        )
        uow.commit()

    with pytest.raises(ValueError, match="paper_daily_loss_limit_reached"):
        OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "buy", "1", uuid.uuid4().hex))
    # A strategy with a wider limit in its risk profile may still enter.
    authorizer = _ApprovingAuthorizer()
    placed = OrderGateway(repository, entry_authorizer=authorizer).place_strategy_entry(
        account_id, _order(instrument_id, "buy", "1", uuid.uuid4().hex),
        strategy_id="strategy-wide", trade_attempt_id="attempt-1", max_daily_loss_pct=Decimal("5"),
    )
    assert placed.status == "open"
    # Exits are never stopped by the loss limit.
    exit_order = OrderGateway(repository).place_reducing(account_id, _order(instrument_id, "sell", "5", uuid.uuid4().hex))
    assert exit_order.side == "sell"


def test_shorting_is_an_account_setting_changed_at_its_revision(paper) -> None:
    from app.apps.trading.paper import PaperAccountSettings
    from app.persistence.errors import RevisionConflict

    repository, _, account_id, instrument_id, _ = paper
    account = repository.snapshot(account_id).account
    assert account.allow_short is False
    updated = repository.update_account_settings(account_id, PaperAccountSettings(allow_short=True), expected_revision=account.revision)
    assert (updated.account.allow_short, updated.account.revision) == (True, account.revision + 1)
    with pytest.raises(RevisionConflict):
        repository.update_account_settings(account_id, PaperAccountSettings(allow_short=False), expected_revision=account.revision)
    assert any(item.allow_short for item in repository.list_accounts() if item.account_id == account_id)
    created = repository.create_account(
        PaperAccountCreate(account_id=f"{account_id}-short", name="Shorts", initial_cash=Decimal("1000"), allow_short=True)
    )
    assert created.account.allow_short is True


def test_a_short_opens_adds_reduces_and_covers(paper) -> None:
    from app.apps.trading.paper import PaperAccountSettings

    repository, _, account_id, instrument_id, _ = paper
    revision = repository.snapshot(account_id).account.revision
    repository.update_account_settings(account_id, PaperAccountSettings(allow_short=True), expected_revision=revision)
    gateway = OrderGateway(repository)

    def position() -> Decimal:
        return next((item.quantity for item in repository.snapshot(account_id).positions if item.instrument_id == instrument_id), Decimal("0"))

    gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "3", "short-open"))
    _fill(repository, account_id, instrument_id)
    assert position() == Decimal("-3")
    gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "2", "short-add"))
    _fill(repository, account_id, instrument_id)
    assert position() == Decimal("-5")
    # A buy within the short only reduces it: no entry authority needed.
    gateway.place_reducing(account_id, _order(instrument_id, "buy", "2", "short-reduce"))
    _fill(repository, account_id, instrument_id)
    assert position() == Decimal("-3")
    gateway.place_reducing(account_id, _order(instrument_id, "buy", "3", "short-cover"))
    _fill(repository, account_id, instrument_id)
    assert position() == Decimal("0")
    # Turning shorting off stops new shorts.
    revision = repository.snapshot(account_id).account.revision
    repository.update_account_settings(account_id, PaperAccountSettings(allow_short=False), expected_revision=revision)
    with pytest.raises(ValueError, match="paper_short_not_allowed"):
        gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "1", "short-again"))


def _shorting_account(repository, account_id: str) -> None:
    from app.apps.trading.paper import PaperAccountSettings

    revision = repository.snapshot(account_id).account.revision
    repository.update_account_settings(account_id, PaperAccountSettings(allow_short=True), expected_revision=revision)


def test_shorts_hold_buying_power_and_cannot_compound(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper
    _shorting_account(repository, account_id)
    gateway = OrderGateway(repository)
    # 100,000 cash: a 4,000-share short at 10 holds 40,000 while working, then 80,000 (proceeds and margin) once filled.
    gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "4000", "short-big"))
    assert repository.snapshot(account_id).balances[0].reserved == Decimal("40000")
    _fill(repository, account_id, instrument_id)
    balance = repository.snapshot(account_id).balances[0]
    assert balance.reserved == Decimal("0")
    # Its proceeds don't buy more: 140,000 available, 80,000 held for the short, so about 60,000 is left (the short filled at the bid).
    other = f"{instrument_id}X"
    with pytest.raises(ValueError, match="insufficient_paper_cash"):
        gateway.place_manual_entry(account_id, _order(other, "buy", "6100", "long-too-big"))
    with pytest.raises(ValueError, match="insufficient_paper_cash"):
        gateway.place_manual_entry(account_id, _order(other, "sell", "6100", "short-too-big"))
    gateway.place_manual_entry(account_id, _order(other, "buy", "5000", "long-fits"))


def test_a_short_is_bought_back_by_hand_without_flipping_long(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper
    _shorting_account(repository, account_id)
    gateway = OrderGateway(repository)
    gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "5", "short-5"))
    _fill(repository, account_id, instrument_id)
    gateway.place_reducing(account_id, _order(instrument_id, "buy", "3", "cover-3", limit_price=Decimal("9")))
    # Only 2 are left to buy back: a 3-share reducing buy would flip the account long.
    with pytest.raises(ValueError, match="paper_order_requires_entry_authority"):
        gateway.place_reducing(account_id, _order(instrument_id, "buy", "3", "cover-3-again", limit_price=Decimal("9")))
    gateway.place_reducing(account_id, _order(instrument_id, "buy", "2", "cover-2", limit_price=Decimal("9")))


def test_turning_shorting_off_cancels_working_short_entries(paper) -> None:
    from app.apps.trading.paper import PaperAccountSettings

    repository, _, account_id, instrument_id, _ = paper
    _shorting_account(repository, account_id)
    available_before = repository.snapshot(account_id).balances[0].available
    entry = OrderGateway(repository).place_manual_entry(account_id, _order(instrument_id, "sell", "10", "short-working", limit_price=Decimal("12")))
    revision = repository.snapshot(account_id).account.revision
    repository.update_account_settings(account_id, PaperAccountSettings(allow_short=False), expected_revision=revision)
    snapshot = repository.snapshot(account_id)
    assert next(item for item in snapshot.order_history if item.order_id == entry.order_id).status == "cancelled"
    assert (snapshot.balances[0].available, snapshot.balances[0].reserved) == (available_before, Decimal("0"))


def test_a_short_is_bought_back_even_when_the_cash_does_not_cover_it(paper) -> None:
    repository, _, account_id, instrument_id, _ = paper
    _shorting_account(repository, account_id)
    gateway = OrderGateway(repository)
    gateway.place_manual_entry(account_id, _order(instrument_id, "sell", "6000", "short-gap"))
    _fill(repository, account_id, instrument_id)
    # The price gaps from 10 to 40: buying back 6,000 costs 240,000, more than the 160,000 the account holds.
    gateway.place_reducing(account_id, _order(instrument_id, "buy", "6000", "cover-gap", reference_price=Decimal("40")))
    now = datetime.now(timezone.utc) + timedelta(seconds=2)
    repository.process_observation(
        account_id,
        PaperMarketObservation(
            instrument_id=instrument_id, provider="integration", price=Decimal("40"), bid=Decimal("39.99"), ask=Decimal("40.01"),
            bid_size=Decimal("100000"), ask_size=Decimal("100000"), source_time=now, evaluated_at=now,
            execution_eligible=True, freshness_mode="live",
        ),
    )
    snapshot = repository.snapshot(account_id)
    assert next(item for item in snapshot.order_history if item.order_id == "order-cover-gap").status == "filled"
    assert all(item.quantity == 0 for item in snapshot.positions if item.instrument_id == instrument_id)
    assert snapshot.balances[0].available < 0
