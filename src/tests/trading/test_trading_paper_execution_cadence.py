from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.paper import PaperAccount, PaperAccountSnapshot, PaperBalance, PaperOrder
from app.apps.trading.paper_monitor import TradingPaperMonitor


NOW = datetime.now(timezone.utc)
INSTRUMENT = "equity:NYSE:TEST"
BINDING = "fixture:test"


class Repo:
    def __init__(self, *, active: bool) -> None:
        self.account = PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        )
        order = PaperOrder(
            account_id="paper-1",
            order_id="order-1",
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            side="buy",
            order_type="market",
            quantity=Decimal("1"),
            reference_price=Decimal("10"),
            idempotency_key="order-1",
        )
        self.snapshot_value = PaperAccountSnapshot(
            account=self.account,
            balances=[PaperBalance(currency="USD", available=Decimal("1000"))],
            positions=[],
            open_orders=[order] if active else [],
            order_history=[order] if active else [],
            recent_fills=[],
            recent_ledger=[],
        )

    def list_accounts(self, limit=100):
        return [self.account]

    def snapshot(self, account_id):
        return self.snapshot_value

    def process_observation(self, account_id, observation):
        return []


class Protections:
    def list(self, account_id, *, active_only=True):
        return []

    def get(self, *args, **kwargs):
        raise ValueError("paper_protection_not_found")


class Market:
    def execution_observation(self, instrument_id, binding_id=None):
        return ExecutionObservation(
            instrument_id=instrument_id,
            binding_id=binding_id or BINDING,
            provider="fixture",
            bid=Decimal("9.99"),
            ask=Decimal("10.01"),
            last=Decimal("10"),
            source_time=NOW,
            received_at=NOW,
            session="regular",
            freshness_mode="polled",
            execution_eligible=True,
        )


def test_active_orders_switch_monitor_to_fast_execution_cadence() -> None:
    repo = Repo(active=True)
    monitor = TradingPaperMonitor(
        repository_factory=lambda: repo,
        protection_repository_factory=lambda: Protections(),
        market_service_factory=lambda: Market(),
        interval_seconds=15,
        active_interval_seconds=1,
    )
    asyncio.run(monitor.run_once())
    diagnostics = monitor.diagnostics()
    assert diagnostics["active_order_count"] == 1
    assert diagnostics["active_target_count"] == 1
    assert diagnostics["current_interval_seconds"] == 1
    assert diagnostics["adaptive_execution_cadence"] is True
    assert diagnostics["last_execution_observation_at"] == NOW.isoformat()
    assert diagnostics["last_observation_age_ms"] is not None


def test_idle_monitor_keeps_slow_account_scan_cadence() -> None:
    repo = Repo(active=False)
    monitor = TradingPaperMonitor(
        repository_factory=lambda: repo,
        protection_repository_factory=lambda: Protections(),
        market_service_factory=lambda: Market(),
        interval_seconds=15,
        active_interval_seconds=1,
    )
    asyncio.run(monitor.run_once())
    diagnostics = monitor.diagnostics()
    assert diagnostics["active_target_count"] == 0
    assert diagnostics["current_interval_seconds"] == 15


def test_monitor_wake_interrupts_active_timeout() -> None:
    monitor = TradingPaperMonitor(
        repository_factory=lambda: Repo(active=False),
        protection_repository_factory=lambda: Protections(),
        market_service_factory=lambda: Market(),
        interval_seconds=30,
        active_interval_seconds=1,
    )

    # The scheduler ticks at the active cadence; an idle monitor waits for its
    # slow interval unless something wakes it.
    assert monitor.tick_seconds() == 1
    monitor.begin_cycle(100.0)
    assert monitor.cycle_due(101.0) is False
    assert monitor.cycle_due(130.0) is True
    monitor.wake()
    assert monitor.cycle_due(101.0) is True
    monitor.begin_cycle(101.0)
    assert monitor.cycle_due(102.0) is False
