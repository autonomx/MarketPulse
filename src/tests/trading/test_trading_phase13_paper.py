from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.persistence.errors import RevisionConflict
from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountCreate,
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperFill,
    PaperLedgerEntry,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    paper_commission,
    paper_fill_decision,
    paper_fill_is_fundable,
    paper_fill_key,
    paper_realized_pnl,
    paper_unrealized_pnl,
)
from app.apps.trading.paper_api import create_trading_paper_router
from app.apps.trading.paper_monitor import TradingPaperMonitor, trading_paper_monitor_enabled


NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
INSTRUMENT = "crypto:BINANCE:spot:BTC-USDT"
BINDING = "binance:BTCUSDT"


def order(order_type="market", side="buy", **overrides) -> PaperOrder:
    payload = {
        "account_id": "paper-1",
        "order_id": f"{side}-{order_type}",
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "side": side,
        "order_type": order_type,
        "quantity": Decimal("2"),
        "limit_price": Decimal("100") if order_type == "limit" else None,
        "stop_price": Decimal("100") if order_type == "stop" else None,
        "idempotency_key": f"key-{side}-{order_type}",
    }
    payload.update(overrides)
    return PaperOrder(**payload)


def observation(price: str, **overrides) -> PaperMarketObservation:
    payload = {
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "provider": "binance",
        "price": Decimal(price),
        "source_time": NOW,
        "evaluated_at": NOW,
    }
    payload.update(overrides)
    return PaperMarketObservation(**payload)


def snapshot(*, revision=1, enabled=True, open_orders=()) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        account=PaperAccount(
            account_id="paper-1",
            name="Paper Account",
            base_currency="USD",
            commission_bps=Decimal("10"),
            enabled=enabled,
            revision=revision,
        ),
        balances=[PaperBalance(currency="USD", available=Decimal("10000"))],
        positions=[],
        open_orders=list(open_orders),
        recent_fills=[],
        recent_ledger=[
            PaperLedgerEntry(
                ledger_id="deposit",
                entry_type="deposit",
                currency="USD",
                amount=Decimal("10000"),
                idempotency_key="deposit",
            )
        ],
    )


def test_order_contract_and_fill_conditions_are_explicit() -> None:
    with pytest.raises(ValidationError):
        PaperOrderRequest(
            order_id="bad-limit",
            instrument_id=INSTRUMENT,
            side="buy",
            order_type="limit",
            quantity="1",
            idempotency_key="bad-limit",
        )
    with pytest.raises(ValidationError):
        PaperOrderRequest(
            order_id="bad-market",
            instrument_id=INSTRUMENT,
            side="buy",
            order_type="market",
            quantity="1",
            limit_price="100",
            idempotency_key="bad-market",
        )

    assert paper_fill_decision(order("market"), observation("101")).should_fill
    assert paper_fill_decision(order("limit", "buy"), observation("99")).should_fill
    assert not paper_fill_decision(order("limit", "buy"), observation("101")).should_fill
    assert paper_fill_decision(order("limit", "sell"), observation("101")).should_fill
    assert paper_fill_decision(order("stop", "buy"), observation("101")).should_fill
    assert paper_fill_decision(order("stop", "sell"), observation("99")).should_fill
    mismatch = observation("100").model_copy(update={"binding_id": "other"})
    assert paper_fill_decision(order("market"), mismatch).reason == "binding_mismatch"


def test_paper_execution_v2_is_partial_latency_halt_and_gap_aware() -> None:
    policy = PaperExecutionPolicy(
        slippage_bps="0",
        stop_slippage_bps="25",
        max_volume_participation_pct="0.10",
        latency_ms=250,
    )
    partial = paper_fill_decision(
        order("market"),
        observation("100", volume=Decimal("5")),
        policy.model_copy(update={"latency_ms": 0}),
    )
    assert partial.should_fill is True
    assert partial.fill_quantity == Decimal("0.5")

    delayed = paper_fill_decision(
        order("market", created_at=NOW),
        observation("100", source_time=NOW + timedelta(milliseconds=100)),
        policy,
    )
    assert delayed.should_fill is False
    assert delayed.reason == "execution_latency_not_elapsed"

    halted = paper_fill_decision(
        order("market"),
        observation("100", halted=True),
        policy.model_copy(update={"latency_ms": 0}),
    )
    assert halted.should_fill is False
    assert halted.reason == "market_halted"

    stop = paper_fill_decision(
        order("stop", "sell"),
        observation(
            "95",
            bid=Decimal("95"),
            ask=Decimal("95.10"),
            high=Decimal("96"),
            low=Decimal("94"),
        ),
        policy.model_copy(update={"latency_ms": 0}),
    )
    assert stop.should_fill is True
    assert stop.fill_price == Decimal("94.7625")
    assert stop.reason == "stop_range_triggered_gap_aware"


def test_paper_accounting_math_and_fill_idempotency_are_reproducible() -> None:
    assert paper_commission(Decimal("1000"), Decimal("10")) == Decimal("1")
    assert paper_unrealized_pnl(Decimal("2"), Decimal("100"), Decimal("110")) == Decimal("20")
    assert paper_realized_pnl(Decimal("2"), Decimal("100"), Decimal("90")) == Decimal("-20")
    market_order = order("market", reference_price=Decimal("100"), reserved_cash=Decimal("200"))
    assert paper_fill_is_fundable(
        market_order,
        total_cost=Decimal("202"),
        available_cash=Decimal("2"),
    )
    limit_order = order("limit", reserved_cash=Decimal("200"))
    assert not paper_fill_is_fundable(
        limit_order,
        total_cost=Decimal("202"),
        available_cash=Decimal("2"),
    )
    key = paper_fill_key("paper-1", "order-1", observation("101"))
    assert key == paper_fill_key("paper-1", "order-1", observation("101"))
    assert key != paper_fill_key("paper-1", "order-1", observation("102"))


class FakePaperRepository:
    def __init__(self) -> None:
        self.current = snapshot(open_orders=[order("market")])
        self.observations: list[tuple[str, PaperMarketObservation]] = []

    def list_accounts(self, limit=100):
        return [self.current.account]

    def create_account(self, request: PaperAccountCreate):
        self.current = PaperAccountSnapshot(
            account=PaperAccount(
                account_id=request.account_id,
                name=request.name,
                base_currency=request.base_currency,
                commission_bps=request.commission_bps,
                revision=1,
            ),
            balances=[PaperBalance(currency=request.base_currency, available=request.initial_cash)],
            positions=[],
            open_orders=[],
            recent_fills=[],
            recent_ledger=[],
        )
        return self.current

    def snapshot(self, account_id):
        if account_id != self.current.account.account_id:
            raise ValueError("paper_account_not_found")
        return self.current

    def place_order(self, account_id, request, *, authority):
        value = PaperOrder(account_id=account_id, **request.model_dump())
        self.current = self.current.model_copy(
            update={"open_orders": [*self.current.open_orders, value]}
        )
        return value

    def cancel_order(self, account_id, order_id):
        current = next(item for item in self.current.open_orders if item.order_id == order_id)
        cancelled = current.model_copy(update={"status": "cancelled"})
        self.current = self.current.model_copy(
            update={"open_orders": [item for item in self.current.open_orders if item.order_id != order_id]}
        )
        return cancelled

    def process_observation(self, account_id, value):
        self.observations.append((account_id, value))
        fill = PaperFill(
            fill_id="fill-1",
            order_id=self.current.open_orders[0].order_id,
            instrument_id=value.instrument_id,
            side="buy",
            quantity=Decimal("2"),
            price=value.price,
            commission=Decimal("0"),
            source_time=value.source_time,
            evaluated_at=value.evaluated_at,
            idempotency_key="fill-key",
        )
        return [fill]


class FakeProtectionRepository:
    def list(self, account_id, *, active_only=True):
        return []

    def get(self, *args, **kwargs):
        raise ValueError("paper_protection_not_found")


class FakeLifecycle:
    def __init__(self, repository: FakePaperRepository) -> None:
        self.repository = repository

    def reset_account(self, account_id, *, initial_cash, expected_revision):
        if expected_revision != self.repository.current.account.revision:
            raise RevisionConflict("stale paper account")
        self.repository.current = snapshot(revision=expected_revision + 1)
        self.repository.current.balances[0].available = initial_cash
        return self.repository.current

    def archive_account(self, account_id, *, expected_revision):
        if expected_revision != self.repository.current.account.revision:
            raise RevisionConflict("stale paper account")
        self.repository.current = snapshot(revision=expected_revision + 1, enabled=False)
        return self.repository.current


class FakeMarketService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def execution_observation(self, instrument_id, binding_id=None):
        self.calls.append((instrument_id, binding_id))
        return ExecutionObservation(
            instrument_id=instrument_id,
            binding_id=binding_id or BINDING,
            provider="fixture",
            bid=Decimal("100.99"),
            ask=Decimal("101.01"),
            last=Decimal("101"),
            cumulative_volume=None,
            source_time=NOW,
            received_at=NOW,
            session="regular",
            freshness_mode="polled",
            execution_eligible=True,
        )


def test_paper_monitor_groups_quotes_and_runs_without_browser() -> None:
    repository = FakePaperRepository()
    duplicate = order("limit", order_id="second-order", idempotency_key="second-key")
    repository.current = snapshot(open_orders=[order("market"), duplicate])
    market = FakeMarketService()
    monitor = TradingPaperMonitor(
        repository_factory=lambda: repository,
        protection_repository_factory=lambda: FakeProtectionRepository(),
        market_service_factory=lambda: market,
        interval_seconds=5,
    )
    assert asyncio.run(monitor.run_once()) == 1
    assert market.calls == [(INSTRUMENT, BINDING)]
    assert len(repository.observations) == 1
    assert monitor.diagnostics()["quote_count"] == 1
    assert monitor.diagnostics()["reference_price_fallback"] is False


def test_paper_monitor_is_disabled_in_legacy_tests_by_default(monkeypatch) -> None:
    monkeypatch.setenv("OMNIX_PERSISTENCE_MODE", "legacy_test")
    monkeypatch.delenv("OMNIX_TRADING_PAPER_MONITOR_IN_TESTS", raising=False)
    assert trading_paper_monitor_enabled() is False


def test_paper_routes_support_orders_reset_archive_and_revision_conflicts() -> None:
    repository = FakePaperRepository()
    lifecycle = FakeLifecycle(repository)
    app = FastAPI()
    app.include_router(
        create_trading_paper_router(
            repository_factory=lambda: repository,
            lifecycle_factory=lambda: lifecycle,
        )
    )
    client = TestClient(app)

    assert client.get("/api/trading/paper/accounts").status_code == 200
    placed = client.post(
        "/api/trading/paper/accounts/paper-1/orders",
        json={
            "order_id": "api-order",
            "instrument_id": INSTRUMENT,
            "binding_id": BINDING,
            "side": "buy",
            "order_type": "limit",
            "quantity": "1",
            "limit_price": "100",
            "idempotency_key": "api-order",
        },
    )
    assert placed.status_code == 409
    assert placed.json()["detail"] == "paper_entry_requires_server_risk_authority"

    cancel = client.delete(
        "/api/trading/paper/accounts/paper-1/orders/buy-market",
    )
    assert cancel.status_code == 409
    assert cancel.json()["detail"] == "paper_order_cancellation_disabled"
    assert repository.current.open_orders[0].status == "open"

    browser_observation = client.post(
        "/api/trading/paper/accounts/paper-1/observations",
        json={
            "instrument_id": INSTRUMENT,
            "binding_id": BINDING,
            "provider": "paper-reference",
            "price": "999",
            "source_time": NOW.isoformat(),
            "evaluated_at": NOW.isoformat(),
        },
    )
    assert browser_observation.status_code == 200
    assert browser_observation.json() == {"fills": []}

    reset = client.post(
        "/api/trading/paper/accounts/paper-1/reset",
        headers={"If-Match": "1"},
        json={"initial_cash": "25000"},
    )
    assert reset.status_code == 200
    assert reset.json()["balances"][0]["available"] == "25000"
    assert reset.json()["account"]["revision"] == 2

    stale = client.delete(
        "/api/trading/paper/accounts/paper-1",
        headers={"If-Match": "1"},
    )
    assert stale.status_code == 409
    archived = client.delete(
        "/api/trading/paper/accounts/paper-1",
        headers={"If-Match": "2"},
    )
    assert archived.status_code == 200
    assert archived.json()["account"]["enabled"] is False


def test_paper_authority_is_relational_and_no_live_execution_path_exists() -> None:
    migration = Path("src/app/apps/trading/migrations/0023_trading_paper.sql").read_text()
    for table in (
        "omnix_trading_paper_accounts",
        "omnix_trading_paper_balances",
        "omnix_trading_paper_positions",
        "omnix_trading_paper_orders",
        "omnix_trading_paper_fills",
        "omnix_trading_paper_ledger",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
    assert "idempotency_key" in migration

    protection_migration = Path(
        "src/app/apps/trading/migrations/0039_trading_paper_protections.sql"
    ).read_text()
    assert "CREATE TABLE IF NOT EXISTS omnix_trading_paper_protections" in protection_migration

    backend = "\n".join(
        path.read_text()
        for path in Path("src/app/apps/trading").glob("paper*.py")
    ).lower()
    for forbidden in (
        "alpaca",
        "interactive_brokers",
        "submit_live_order",
        "broker_credentials",
    ):
        assert forbidden not in backend

    monitor = Path("src/app/apps/trading/paper_monitor.py").read_text()
    assert "paper-reference" not in monitor
    assert "server_authoritative_protection" in monitor

    gateway = Path("src/app/apps/trading/route_registration.py").read_text()
    assert "create_trading_paper_router" in gateway
    assert "create_trading_paper_monitor_task" in gateway
