from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.execution import ExecutionObservation
from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperOrder,
    PaperPosition,
)
from app.apps.trading.paper_api import create_trading_paper_router
from app.apps.trading.paper_protection import PaperPositionProtection
from app.apps.trading.paper_risk import PaperRiskPreviewRequest, preview_paper_risk


NOW = datetime.now(timezone.utc)
INSTRUMENT = "equity:NYSE:TEST"
BINDING = "alpaca_iex:TEST"


def snapshot(*, cash: str = "100000", positions=(), open_orders=()) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        account=PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        ),
        balances=[PaperBalance(currency="USD", available=Decimal(cash))],
        positions=list(positions),
        open_orders=list(open_orders),
        order_history=list(open_orders),
        recent_fills=[],
        recent_ledger=[],
    )


def observation(*, bid: str = "9.99", ask: str = "10.01", eligible: bool = True) -> ExecutionObservation:
    return ExecutionObservation(
        instrument_id=INSTRUMENT,
        binding_id=BINDING,
        provider="fixture",
        bid=Decimal(bid),
        ask=Decimal(ask),
        last=(Decimal(bid) + Decimal(ask)) / Decimal("2"),
        source_time=NOW,
        received_at=NOW,
        session="regular",
        freshness_mode="polled",
        execution_eligible=eligible,
        rejection_reasons=() if eligible else ("stale_quote",),
    )


def test_preview_sizes_from_risk_not_browser_quantity() -> None:
    result = preview_paper_risk(
        snapshot=snapshot(),
        protections=[],
        observation=observation(),
        request=PaperRiskPreviewRequest(
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            entry_price=Decimal("10"),
            stop_price=Decimal("9"),
            desired_risk_pct=Decimal("0.35"),
        ),
    )
    assert result.allowed is True
    assert result.recommended_quantity == Decimal("350")
    assert result.actual_risk_dollars == Decimal("350")
    assert result.actual_risk_pct == Decimal("0.3500")
    assert result.aggregate_open_risk_dollars == Decimal("0")
    assert result.buying_power_after == Decimal("96500")


def test_preview_accepts_authoritative_full_day_realized_pnl_override() -> None:
    result = preview_paper_risk(
        snapshot=snapshot(),
        protections=[],
        observation=observation(),
        request=PaperRiskPreviewRequest(
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            entry_price=Decimal("10"),
            stop_price=Decimal("9"),
            desired_risk_pct=Decimal("0.35"),
        ),
        daily_realized_pnl=Decimal("-1500"),
    )
    assert result.allowed is False
    assert result.daily_realized_pnl == Decimal("-1500")
    assert "DAILY_LOSS_LIMIT" in result.reason_codes


def test_preview_fails_closed_for_unprotected_existing_exposure() -> None:
    position = PaperPosition(
        instrument_id="equity:NYSE:OLD",
        quantity=Decimal("100"),
        average_cost=Decimal("20"),
        realized_pnl=Decimal("0"),
        last_price=Decimal("20"),
    )
    result = preview_paper_risk(
        snapshot=snapshot(cash="98000", positions=[position]),
        protections=[],
        observation=observation(),
        request=PaperRiskPreviewRequest(
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            entry_price=Decimal("10"),
            stop_price=Decimal("9"),
            desired_risk_pct=Decimal("0.35"),
        ),
    )
    assert result.allowed is False
    assert result.unprotected_exposure_count == 1
    assert "UNPROTECTED_OPEN_EXPOSURE" in result.reason_codes
    assert result.recommended_quantity == 0


def test_preview_rejects_ineligible_or_wide_execution_data() -> None:
    result = preview_paper_risk(
        snapshot=snapshot(),
        protections=[],
        observation=observation(bid="9", ask="11", eligible=False),
        request=PaperRiskPreviewRequest(
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            entry_price=Decimal("10"),
            stop_price=Decimal("9"),
            desired_risk_pct=Decimal("0.35"),
        ),
    )
    assert result.allowed is False
    assert "EXECUTION_DATA_INELIGIBLE" in result.reason_codes
    assert "SPREAD_TOO_WIDE" in result.reason_codes


class Repo:
    def __init__(self, events: list[str] | None = None) -> None:
        self.current = snapshot()
        self.placed = []
        self.cancelled = []
        self.events = events if events is not None else []
        self.fail_place = False

    def list_accounts(self, limit=100):
        return [self.current.account]

    def snapshot(self, account_id):
        return self.current

    def place_order(self, account_id, request, *, authority):
        self.events.append("order")
        if self.fail_place:
            raise ValueError("insufficient_paper_cash")
        self.placed.append(request)
        order = PaperOrder(account_id=account_id, **request.model_dump())
        self.current = self.current.model_copy(
            update={
                "open_orders": [order],
                "order_history": [order],
            }
        )
        return order

    def cancel_order(self, account_id, order_id):
        self.cancelled.append(order_id)
        order = self.current.open_orders[0].model_copy(update={"status": "cancelled"})
        self.current = self.current.model_copy(update={"open_orders": [], "order_history": [order]})
        return order


class Protections:
    def __init__(self, events: list[str] | None = None) -> None:
        self.values: list[PaperPositionProtection] = []
        self.events = events if events is not None else []

    def list(self, account_id, *, active_only=True):
        return self.values

    def arm_pending_entry(self, account_id, request):
        self.events.append("arm")
        value = PaperPositionProtection(
            account_id=account_id,
            instrument_id=request.instrument_id,
            binding_id=request.binding_id,
            entry_order_id=request.entry_order_id,
            take_profit=request.take_profit,
            stop_loss=request.stop_loss,
            status="pending_entry",
            trigger_reason="entry_armed",
        )
        self.values = [value]
        return value

    def upsert(self, account_id, request):
        value = PaperPositionProtection(
            account_id=account_id,
            instrument_id=request.instrument_id,
            binding_id=request.binding_id,
            entry_order_id=request.entry_order_id,
            take_profit=request.take_profit,
            stop_loss=request.stop_loss,
            status="pending_entry",
        )
        self.values = [value]
        return value

    def transition(
        self,
        account_id,
        instrument_id,
        *,
        status,
        exit_order_id,
        trigger_reason,
        expected_revision=None,
    ):
        del exit_order_id, expected_revision
        self.events.append("cancel-protection")
        current = self.values[0]
        value = current.model_copy(
            update={"status": status, "trigger_reason": trigger_reason}
        )
        self.values = [value]
        return value


class Market:
    def execution_observation(self, instrument_id, binding_id=None):
        return observation()


class DailyPnl:
    def __init__(self, value: str = "0") -> None:
        self.value = Decimal(value)
        self.calls = []

    def daily_paper_pnl(self, account_id, *, start_time, end_time):
        self.calls.append((account_id, start_time, end_time))
        return self.value


class Lifecycle:
    pass


def _client(
    repo: Repo,
    protections: Protections,
    daily_pnl: DailyPnl | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_trading_paper_router(
            repository_factory=lambda: repo,
            lifecycle_factory=lambda: Lifecycle(),
            protection_repository_factory=lambda: protections,
            market_service_factory=lambda: Market(),
            strategy_repository_factory=lambda: daily_pnl or DailyPnl(),
        )
    )
    return TestClient(app)


def _risk_order_payload() -> dict[str, object]:
    return {
        "order_id": "risk-1",
        "instrument_id": INSTRUMENT,
        "binding_id": BINDING,
        "order_type": "market",
        "trigger_price": None,
        "stop_loss": "9.01",
        "take_profit": "12",
        "desired_risk_pct": "0.35",
        "idempotency_key": "risk-1",
    }


def test_risk_order_endpoint_owns_quantity_and_arms_protection_before_order() -> None:
    events: list[str] = []
    repo = Repo(events)
    protections = Protections(events)
    daily_pnl = DailyPnl()
    response = _client(repo, protections, daily_pnl).post(
        "/api/trading/paper/accounts/paper-1/risk-orders",
        json=_risk_order_payload(),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["preview"]["allowed"] is True
    assert payload["order"]["quantity"] == "350"
    assert payload["order"]["reference_price"] == "10.01"
    assert payload["protection"]["entry_order_id"] == "risk-1"
    assert payload["protection"]["stop_loss"] == "9.01"
    assert repo.placed[0].quantity == Decimal("350")
    assert events == ["arm", "order"]
    assert len(daily_pnl.calls) == 1
    assert daily_pnl.calls[0][1].tzinfo is not None
    assert daily_pnl.calls[0][2] > daily_pnl.calls[0][1]


def test_risk_order_uses_full_day_relational_loss_not_snapshot_window() -> None:
    events: list[str] = []
    repo = Repo(events)
    protections = Protections(events)
    response = _client(repo, protections, DailyPnl("-1500")).post(
        "/api/trading/paper/accounts/paper-1/risk-orders",
        json=_risk_order_payload(),
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "paper_risk_rejected"
    assert "DAILY_LOSS_LIMIT" in detail["reason_codes"]
    assert events == []
    assert repo.placed == []


def test_failed_entry_submission_cancels_prearmed_protection() -> None:
    events: list[str] = []
    repo = Repo(events)
    repo.fail_place = True
    protections = Protections(events)
    response = _client(repo, protections).post(
        "/api/trading/paper/accounts/paper-1/risk-orders",
        json=_risk_order_payload(),
    )
    assert response.status_code == 409, response.text
    assert events == ["arm", "order", "cancel-protection"]
    assert protections.values[0].status == "cancelled"
    assert protections.values[0].trigger_reason == "entry_submit_failed"
