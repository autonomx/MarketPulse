"""OrderGateway: the only path to paper orders (WP-8.3)."""
from __future__ import annotations

import ast
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.apps.trading.order_gateway import OrderGateway, StrategyEntryAuthorizer, StrategyPaperAccess
from app.apps.trading.paper import PaperOrderRequest

APP = Path(__file__).parents[2] / "app"
ORDER_METHODS = {"place_order", "cancel_order", "replace_order", "replace_entry"}
INSTRUMENT = "equity:NASDAQ:TEST"


def test_only_the_gateway_calls_the_repositorys_order_methods() -> None:
    callers = set()
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_METHODS and not (
                isinstance(node.ctx, ast.Store)
            ):
                callers.add(path.relative_to(APP).as_posix())
    # paper_repository defines them; the guarded strategy view names them to refuse them.
    assert callers <= {"apps/trading/order_gateway.py", "apps/trading/paper_repository.py"}


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def place_order(self, account_id, request, *, authority):
        self.calls.append(("place", authority))
        return request

    def snapshot(self, account_id):
        return "snapshot"


def _request(order_id: str = "order-1", side: str = "buy") -> PaperOrderRequest:
    return PaperOrderRequest(
        order_id=order_id,
        instrument_id=INSTRUMENT,
        side=side,
        order_type="market",
        quantity=Decimal("1"),
        reference_price=Decimal("10"),
        idempotency_key=order_id,
    )


def test_each_entry_point_names_its_authority() -> None:
    repository = _Repository()
    gateway = OrderGateway(repository, entry_authorizer=SimpleNamespace(authorize=lambda *a, **k: None))

    gateway.place_reducing("paper", _request(side="sell"))
    gateway.place_manual_entry("paper", _request())
    gateway.place_strategy_entry("paper", _request(), strategy_id="s-1", trade_attempt_id="attempt-1")

    kinds = [(authority.kind, authority.strategy_id, authority.trade_attempt_id) for _, authority in repository.calls]
    assert kinds == [
        ("reduce_only", None, None),
        ("manual_risk", None, None),
        ("strategy_entry", "s-1", "attempt-1"),
    ]


def test_a_strategy_entry_without_an_authorizer_is_denied() -> None:
    repository = _Repository()
    with pytest.raises(ValueError, match="trade_authorization_denied:AUTHORIZER_MISSING"):
        OrderGateway(repository).place_strategy_entry(
            "paper", _request(), strategy_id="s-1", trade_attempt_id="attempt-1"
        )
    assert repository.calls == []


def test_a_strategy_reads_its_account_but_orders_only_through_the_gateway() -> None:
    repository = _Repository()
    access = StrategyPaperAccess(repository, OrderGateway(repository), strategy_id="s-1")

    assert access.snapshot("paper") == "snapshot"
    with pytest.raises(AttributeError):
        access.place_order  # noqa: B018
    access.place_exit("paper", _request(side="sell"))
    assert repository.calls[0][1].kind == "reduce_only"


class _StrategyRepository:
    def __init__(self, events) -> None:
        self.events = events
        self.appended = []

    def recent_events(self, strategy_id, limit):
        return list(self.events)

    def get_universe(self, universe_id):
        raise KeyError(universe_id)

    def append_event(self, event):
        self.appended.append(event)
        return True


def _risk_event(attempt: str, allowed: bool, observed_at: datetime):
    return SimpleNamespace(
        event_type="risk_decision",
        event_id=f"risk-{attempt}",
        instrument_id=INSTRUMENT,
        state="risk_checked",
        observed_at=observed_at,
        payload={
            "trade_attempt_id": attempt,
            "universe_id": f"universe-{attempt}",
            "risk_decision": {"allowed": allowed, "attempt": attempt},
            "profile_fingerprint": "1.0.0",
        },
    )


def test_authorization_is_bound_to_the_orders_own_trade_attempt() -> None:
    earlier = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
    # The instrument's latest risk decision belongs to another attempt.
    strategy_repository = _StrategyRepository([
        _risk_event("attempt-a", False, earlier),
        _risk_event("attempt-b", True, earlier + timedelta(minutes=5)),
    ])
    config = SimpleNamespace(
        strategy_id="s-1",
        config=SimpleNamespace(strategy_version="1.0.0", entry_start_et=time(0, 0), last_entry_et=time(23, 59)),
        risk=SimpleNamespace(max_spread_bps=100, kill_switch=False),
    )
    authorizer = StrategyEntryAuthorizer(
        monitor=SimpleNamespace(current_run_id="run-1"),
        config=config,
        strategy_repository=strategy_repository,
        market_service=object(),
        monitor_module=object(),
        qualification_module=object(),
    )

    with pytest.raises(ValueError, match="trade_authorization_denied"):
        authorizer.authorize("paper", _request(), trade_attempt_id="attempt-a")

    [event] = strategy_repository.appended
    assert event.payload["assessment"]["trade_attempt_id"] == "attempt-a"
    assert event.payload["assessment"]["universe_id"] == "universe-attempt-a"
    assert event.payload["risk_decision"] == {"allowed": False, "attempt": "attempt-a"}
