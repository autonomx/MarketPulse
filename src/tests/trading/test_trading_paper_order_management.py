from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperOrder,
    PaperPosition,
)
from app.apps.trading.paper_api import create_trading_paper_router


INSTRUMENT = "equity:NYSE:TEST"
HEADERS = {"X-Omnix-Paper-Order-Management": "v2"}


class Repo:
    def __init__(self) -> None:
        self.account = PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        )
        self.position = PaperPosition(
            instrument_id=INSTRUMENT,
            quantity=Decimal("5"),
            reserved_quantity=Decimal("1"),
            average_cost=Decimal("10"),
            realized_pnl=Decimal("0"),
            last_price=Decimal("10"),
        )
        self.orders: dict[str, PaperOrder] = {
            "old": PaperOrder(
                account_id="paper-1",
                order_id="old",
                instrument_id=INSTRUMENT,
                binding_id=None,
                side="sell",
                order_type="limit",
                quantity=Decimal("1"),
                limit_price=Decimal("11"),
                idempotency_key="old",
            )
        }
        self.fail_replacement = False
        self.authorities = []

    def list_accounts(self, limit=100):
        return [self.account]

    def snapshot(self, account_id):
        history = list(self.orders.values())
        positions = [self.position] if self.position.quantity != 0 else []
        return PaperAccountSnapshot(
            account=self.account,
            balances=[PaperBalance(currency="USD", available=Decimal("1000"))],
            positions=positions,
            open_orders=[order for order in history if order.status == "open"],
            order_history=history,
            recent_fills=[],
            recent_ledger=[],
        )

    def cancel_order(self, account_id, order_id):
        order = self.orders.get(order_id)
        if order is None or order.status != "open":
            raise ValueError("paper_order_not_open")
        cancelled = order.model_copy(update={"status": "cancelled"})
        self.orders[order_id] = cancelled
        return cancelled

    def place_order(self, account_id, request, *, authority):
        self.authorities.append(authority)
        if self.fail_replacement:
            raise ValueError("insufficient_paper_cash")
        order = PaperOrder(account_id=account_id, **request.model_dump())
        self.orders[order.order_id] = order
        return order

    def replace_order(self, account_id, order_id, replacement, *, authority):
        # One transaction in the real repository: a failure changes nothing.
        before = dict(self.orders)
        try:
            cancelled = self.cancel_order(account_id, order_id)
            return cancelled, self.place_order(account_id, replacement, authority=authority)
        except ValueError:
            self.orders = before
            raise


class Lifecycle:
    pass


class Protections:
    def list(self, *args, **kwargs):
        return []


def _client(repo: Repo) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_trading_paper_router(
            repository_factory=lambda: repo,
            lifecycle_factory=lambda: Lifecycle(),
            protection_repository_factory=lambda: Protections(),
        )
    )
    return TestClient(app)


def _replacement(*, side: str = "sell", quantity: str = "2") -> dict[str, object]:
    return {
        "replacement": {
            "order_id": "new",
            "instrument_id": INSTRUMENT,
            "binding_id": None,
            "side": side,
            "order_type": "limit",
            "quantity": quantity,
            "limit_price": "10.5",
            "stop_price": None,
            "reference_price": None,
            "idempotency_key": "new",
        }
    }


def test_cancel_order_is_server_authoritative() -> None:
    repo = Repo()
    response = _client(repo).delete(
        "/api/trading/paper/accounts/paper-1/orders/old",
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert repo.orders["old"].status == "cancelled"


def test_raw_http_order_cannot_create_new_exposure() -> None:
    repo = Repo()
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders",
        json={
            "order_id": "raw-entry",
            "instrument_id": INSTRUMENT,
            "binding_id": None,
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "reference_price": "10",
            "idempotency_key": "raw-entry",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_entry_requires_server_risk_authority"
    assert "raw-entry" not in repo.orders


def test_raw_http_sell_is_limited_to_unreserved_long_quantity() -> None:
    repo = Repo()
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders",
        json={
            "order_id": "exit-too-large",
            "instrument_id": INSTRUMENT,
            "binding_id": None,
            "side": "sell",
            "order_type": "market",
            "quantity": "5",
            "reference_price": "10",
            "idempotency_key": "exit-too-large",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_entry_requires_server_risk_authority"

    allowed = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders",
        json={
            "order_id": "exit-allowed",
            "instrument_id": INSTRUMENT,
            "binding_id": None,
            "side": "sell",
            "order_type": "market",
            "quantity": "4",
            "reference_price": "10",
            "idempotency_key": "exit-allowed",
        },
    )
    assert allowed.status_code == 201
    assert allowed.json()["order_id"] == "exit-allowed"


def test_replace_cancels_old_before_submitting_new_exit_identity() -> None:
    repo = Repo()
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders/old/replace",
        headers=HEADERS,
        json=_replacement(),
    )
    assert response.status_code == 200
    assert response.json()["cancelled"]["order_id"] == "old"
    assert response.json()["replacement"]["order_id"] == "new"
    assert repo.orders["old"].status == "cancelled"
    assert repo.orders["new"].status == "open"


def test_replace_rejects_entry_bypass_before_cancelling_old_order() -> None:
    repo = Repo()
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders/old/replace",
        headers=HEADERS,
        json=_replacement(side="buy", quantity="1"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_order_replacement_requires_server_risk_authority"
    assert repo.orders["old"].status == "open"
    assert "new" not in repo.orders


def test_replace_failure_leaves_the_original_order_open() -> None:
    repo = Repo()
    repo.fail_replacement = True
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders/old/replace",
        headers=HEADERS,
        json=_replacement(),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "insufficient_paper_cash"
    assert repo.orders["old"].status == "open"
    assert "new" not in repo.orders


def test_raw_and_replacement_orders_carry_reduce_only_authority() -> None:
    repo = Repo()
    response = _client(repo).post(
        "/api/trading/paper/accounts/paper-1/orders/old/replace",
        headers=HEADERS,
        json=_replacement(),
    )
    assert response.status_code == 200
    assert [authority.kind for authority in repo.authorities] == ["reduce_only"]
