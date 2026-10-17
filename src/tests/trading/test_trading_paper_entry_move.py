"""Moving a working risk entry from the chart (TVP-7.3)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.apps.trading.paper import PaperBalance, PaperOrder
from app.apps.trading.paper_entry_move import snapshot_without_order
from app.apps.trading.paper_protection import PaperPositionProtection

from .test_trading_paper_risk_authority import (
    BINDING,
    INSTRUMENT,
    DailyPnl,
    Protections,
    Repo,
    _client,
    snapshot,
)

HEADERS = {"X-Omnix-Paper-Order-Management": "v2"}
URL = "/api/trading/paper/accounts/paper-1/risk-orders/entry-1/move"


class MoveRepo(Repo):
    def __init__(self, events: list[str]) -> None:
        super().__init__(events)
        self.fail_replace: str | None = None
        self.protections: Protections | None = None
        self.replaced = []
        entry = PaperOrder(
            account_id="paper-1",
            order_id="entry-1",
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            side="buy",
            order_type="limit",
            quantity=Decimal("100"),
            limit_price=Decimal("10"),
            idempotency_key="entry-1",
            reserved_cash=Decimal("1000"),
            time_in_force="day",
            # The repository stores a DAY order's derived expiry.
            expires_at=datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc),
        )
        self.current = snapshot(open_orders=[entry]).model_copy(
            update={"balances": [PaperBalance(currency="USD", available=Decimal("99000"), reserved=Decimal("1000"))]}
        )

    def replace_entry(self, account_id, order_id, replacement, *, authority):
        self.events.append(f"replace-entry:{authority.kind}")
        if self.fail_replace:
            raise ValueError(self.fail_replace)
        self.replaced.append((order_id, replacement))
        # As the repository does in the replacement's transaction: the pending stop follows the entry.
        if self.protections is not None:
            self.protections.values = [item.model_copy(update={"entry_order_id": replacement.order_id, "revision": item.revision + 1}) for item in self.protections.values]
        cancelled = self.current.open_orders[0].model_copy(update={"status": "cancelled"})
        return cancelled, PaperOrder(account_id=account_id, **replacement.model_dump())


class MoveProtections(Protections):
    def get(self, account_id, instrument_id, *, include_inactive=True):
        return self.values[0]


def _protections(events: list[str], *, entry_order_id: str = "entry-1", repo: MoveRepo | None = None) -> Protections:
    protections = MoveProtections(events)
    if repo is not None:
        repo.protections = protections
    protections.values = [
        PaperPositionProtection(
            account_id="paper-1",
            instrument_id=INSTRUMENT,
            binding_id=BINDING,
            entry_order_id=entry_order_id,
            take_profit=Decimal("12"),
            stop_loss=Decimal("9"),
            status="pending_entry",
            trigger_reason="entry_armed",
        )
    ]
    return protections


def _move(**overrides) -> dict[str, object]:
    return {"order_id": "entry-2", "idempotency_key": "entry-2", "trigger_price": "9.5", **overrides}


def test_a_moved_entry_is_resized_to_the_same_risk_and_replaced_atomically() -> None:
    events: list[str] = []
    repo = MoveRepo(events)
    protections = _protections(events, repo=repo)
    response = _client(repo, protections, DailyPnl()).post(URL, json=_move(), headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    # The original risks (10 - 9) x 100 = 100 USD; at 9.5 the same risk is 200 shares.
    assert body["order"]["quantity"] == "200"
    assert body["order"]["limit_price"] == "9.5"
    assert body["order"]["time_in_force"] == "day"
    # A DAY order's expiry is the server's to derive again; only GTD sends one.
    assert repo.replaced[0][1].expires_at is None
    assert body["cancelled"]["order_id"] == "entry-1"
    assert body["protection"]["entry_order_id"] == "entry-2"
    assert body["protection"]["stop_loss"] == "9"
    assert body["protection"]["take_profit"] == "12"
    # The stop is re-pointed inside the replacement's transaction, never armed separately.
    assert events == ["replace-entry:manual_risk"]
    assert repo.replaced[0][0] == "entry-1"


def test_a_failed_replacement_leaves_the_entry_and_its_stop_alone() -> None:
    events: list[str] = []
    repo = MoveRepo(events)
    repo.fail_replace = "paper_order_not_open: entry-1"
    protections = _protections(events, repo=repo)
    response = _client(repo, protections).post(URL, json=_move(), headers=HEADERS)
    assert response.status_code == 409, response.text
    assert events == ["replace-entry:manual_risk"]
    assert protections.values[0].entry_order_id == "entry-1"


def test_a_trailing_stop_moves_with_its_trail_and_a_triggered_stop_limit_stays() -> None:
    events: list[str] = []
    repo = MoveRepo(events)
    protections = _protections(events, repo=repo)
    protections.values = [protections.values[0].model_copy(update={"trail_percent": Decimal("2")})]
    response = _client(repo, protections).post(URL, json=_move(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["protection"]["trail_percent"] == "2"
    triggered = repo.current.open_orders[0].model_copy(update={
        "order_type": "stop_limit", "stop_price": Decimal("10"), "limit_price": Decimal("10.2"), "stop_triggered_at": datetime(2026, 10, 8, 15, tzinfo=timezone.utc),
    })
    repo.current = repo.current.model_copy(update={"open_orders": [triggered]})
    refused = _client(repo, _protections([])).post(URL, json=_move(), headers=HEADERS)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "paper_risk_entry_not_movable"


def test_a_moved_order_needs_a_new_order_id() -> None:
    events: list[str] = []
    response = _client(MoveRepo(events), _protections(events)).post(URL, json=_move(order_id="entry-1"), headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_order_id_not_new"
    assert events == []


def test_only_risk_entries_with_a_pending_stop_move() -> None:
    events: list[str] = []
    response = _client(MoveRepo(events), _protections(events, entry_order_id="other")).post(URL, json=_move(), headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_risk_entry_not_movable"
    assert events == []


def test_an_entry_moved_below_its_stop_is_rejected_by_the_risk_rules() -> None:
    events: list[str] = []
    response = _client(MoveRepo(events), _protections(events)).post(URL, json=_move(trigger_price="8.9"), headers=HEADERS)
    assert response.status_code == 409
    assert "STOP_NOT_BELOW_ENTRY" in response.json()["detail"]["reason_codes"]
    assert events == []


def test_moving_needs_the_order_management_header() -> None:
    events: list[str] = []
    response = _client(MoveRepo(events), _protections(events)).post(URL, json=_move())
    assert response.status_code == 409
    assert response.json()["detail"] == "paper_order_cancellation_disabled"


def test_sizing_treats_the_working_entry_as_cancelled() -> None:
    repo = MoveRepo([])
    freed = snapshot_without_order(repo.current, repo.current.open_orders[0])
    assert freed.open_orders == []
    assert freed.balances[0].available == Decimal("100000")
    assert freed.balances[0].reserved == Decimal("0")
