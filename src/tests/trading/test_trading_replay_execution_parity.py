from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.apps.trading.paper import (
    PaperAccount,
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    PaperPosition,
    paper_fill_decision,
)
from app.apps.trading.replay_api import create_trading_replay_router
from app.apps.trading.replay_execution import (
    ReplayExecutionBar,
    advance_replay_snapshot,
    detached_replay_snapshot,
    place_replay_order,
)


def _snapshot() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        account=PaperAccount(
            account_id="paper-1",
            name="Paper",
            base_currency="USD",
            commission_bps=Decimal("0"),
        ),
        balances=[PaperBalance(currency="USD", available=Decimal("1000"), reserved=Decimal("25"))],
        positions=[],
        open_orders=[],
        order_history=[],
        recent_fills=[],
        recent_ledger=[],
    )


def _bar(
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    start_hour: int = 10,
) -> ReplayExecutionBar:
    value = Decimal(close)
    start = datetime(2024, 1, 2, start_hour, 0, tzinfo=timezone.utc)
    return ReplayExecutionBar(
        instrument_id="equity:NYSE:TEST",
        binding_id=None,
        start_time=start,
        end_time=start + timedelta(hours=1),
        open=value,
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=value,
        volume=Decimal("100"),
    )


def test_replay_market_waits_for_post_activation_bar_volume() -> None:
    funded = _snapshot().model_copy(
        update={"balances": [PaperBalance(currency="USD", available=Decimal("10000"), reserved=Decimal("0"))]}
    )
    detached = detached_replay_snapshot(funded)
    placed = place_replay_order(
        detached,
        PaperOrderRequest(
            order_id="market-1",
            instrument_id="equity:NYSE:TEST",
            binding_id=None,
            side="buy",
            order_type="market",
            quantity=Decimal("20"),
            reference_price=Decimal("101"),
            idempotency_key="market-1",
        ),
        _bar("101"),
    )

    # The placement bar's volume predates activation and cannot be consumed.
    assert placed.order.status == "open"
    assert placed.order.filled_quantity == Decimal("0")
    assert placed.snapshot.positions == []

    advanced = advance_replay_snapshot(placed.snapshot, _bar("101", start_hour=11))
    filled = next(order for order in advanced.order_history if order.order_id == "market-1")
    # 10% of the first fully post-activation 100-share bar is executable;
    # paper-execution-v2 then applies the common 10 bps market slippage.
    assert filled.status == "open"
    assert filled.filled_quantity == Decimal("10")
    assert filled.average_fill_price == Decimal("101.101")
    assert advanced.positions[0].quantity == Decimal("10")


def test_same_bar_range_before_activation_cannot_trigger_new_order() -> None:
    policy = PaperExecutionPolicy(latency_ms=250)
    created = datetime(2024, 1, 2, 10, 59, 59, 500000, tzinfo=timezone.utc)
    order = PaperOrder(
        account_id="paper-1",
        order_id="limit-causal",
        instrument_id="equity:NYSE:TEST",
        side="buy",
        order_type="limit",
        quantity=Decimal("1"),
        limit_price=Decimal("90"),
        idempotency_key="limit-causal",
        created_at=created,
    )
    observation = PaperMarketObservation(
        instrument_id="equity:NYSE:TEST",
        provider="fixture",
        price=Decimal("100"),
        ask=Decimal("100"),
        ask_size=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("89"),
        bar_start_time=datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc),
        source_time=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
        evaluated_at=datetime(2024, 1, 2, 11, 0, tzinfo=timezone.utc),
    )

    decision = paper_fill_decision(order, observation, policy)
    assert decision.should_fill is False
    assert decision.reason == "limit_range_not_reached"


def test_replay_limit_waits_for_next_causal_range() -> None:
    detached = detached_replay_snapshot(_snapshot())
    placed = place_replay_order(
        detached,
        PaperOrderRequest(
            order_id="limit-1",
            instrument_id="equity:NYSE:TEST",
            binding_id=None,
            side="buy",
            order_type="limit",
            quantity=Decimal("2"),
            limit_price=Decimal("90"),
            idempotency_key="limit-1",
        ),
        _bar("100", high="105", low="89"),
    )
    assert placed.order.status == "open"
    assert placed.order.filled_quantity == Decimal("0")

    advanced = advance_replay_snapshot(
        placed.snapshot,
        _bar("92", high="95", low="89", start_hour=11),
    )
    filled = next(order for order in advanced.order_history if order.order_id == "limit-1")
    assert filled.status == "filled"
    assert filled.average_fill_price == Decimal("90")


def test_detached_replay_state_does_not_inherit_live_reservations() -> None:
    detached = detached_replay_snapshot(_snapshot())
    assert detached.balances[0].available == Decimal("1025")
    assert detached.balances[0].reserved == Decimal("0")
    assert detached.open_orders == []
    assert detached.order_history == []


def _market_request(order_id: str, quantity: str) -> PaperOrderRequest:
    return PaperOrderRequest(
        order_id=order_id,
        instrument_id="equity:NYSE:TEST",
        binding_id=None,
        side="buy",
        order_type="market",
        quantity=Decimal(quantity),
        reference_price=Decimal("101"),
        idempotency_key=order_id,
    )


def _funded_replay_snapshot() -> PaperAccountSnapshot:
    funded = _snapshot().model_copy(
        update={"balances": [PaperBalance(currency="USD", available=Decimal("10000"), reserved=Decimal("0"))]}
    )
    return detached_replay_snapshot(funded)


def test_replay_order_on_an_advanced_bar_does_not_apply_the_bar_again() -> None:
    placed = place_replay_order(_funded_replay_snapshot(), _market_request("market-1", "20"), _bar("101"))
    advanced = advance_replay_snapshot(placed.snapshot, _bar("101", start_hour=11))
    first = next(order for order in advanced.order_history if order.order_id == "market-1")
    assert first.filled_quantity == Decimal("10")

    second = place_replay_order(
        advanced,
        _market_request("market-2", "1"),
        _bar("101", start_hour=11),
        advance_bar=False,
    )

    working = next(order for order in second.snapshot.order_history if order.order_id == "market-1")
    assert working.filled_quantity == Decimal("10")
    assert second.order.order_id == "market-2"
    assert second.snapshot.positions[0].last_price == Decimal("101")


def test_replay_order_advances_the_bar_by_default() -> None:
    placed = place_replay_order(_funded_replay_snapshot(), _market_request("market-1", "20"), _bar("101"))
    advanced = advance_replay_snapshot(placed.snapshot, _bar("101", start_hour=11))

    # The default keeps the existing contract: the bar is applied before placing.
    second = place_replay_order(advanced, _market_request("market-2", "1"), _bar("101", start_hour=11))

    working = next(order for order in second.snapshot.order_history if order.order_id == "market-1")
    assert working.filled_quantity == Decimal("20")


def test_replay_order_endpoint_honours_advance_bar() -> None:
    placed = place_replay_order(_funded_replay_snapshot(), _market_request("market-1", "20"), _bar("101"))
    advanced = advance_replay_snapshot(placed.snapshot, _bar("101", start_hour=11))
    app = FastAPI()
    app.include_router(
        create_trading_replay_router(repository_factory=lambda: None, market_service_factory=lambda: None)
    )
    client = TestClient(app)
    body = {
        "snapshot": advanced.model_dump(mode="json"),
        "order": _market_request("market-2", "1").model_dump(mode="json"),
        "bar": _bar("101", start_hour=11).model_dump(mode="json"),
    }

    once = client.post("/api/trading/replay/execution/orders", json={**body, "advance_bar": False})
    again = client.post("/api/trading/replay/execution/orders", json=body)

    assert once.status_code == 200, once.text
    assert again.status_code == 200, again.text

    def filled(response) -> str:
        orders = response.json()["snapshot"]["order_history"]
        return next(order for order in orders if order["order_id"] == "market-1")["filled_quantity"]

    assert Decimal(filled(once)) == Decimal("10")
    assert Decimal(filled(again)) == Decimal("20")


OTHER = "equity:NYSE:OTHER"


def _with_other_position() -> PaperAccountSnapshot:
    other = PaperPosition(
        instrument_id=OTHER,
        quantity=Decimal("5"),
        average_cost=Decimal("40"),
        realized_pnl=Decimal("0"),
        last_price=Decimal("50"),
        unrealized_pnl=Decimal("50"),
    )
    return _funded_replay_snapshot().model_copy(update={"positions": [other]})


def _equity(snapshot: PaperAccountSnapshot) -> Decimal:
    cash = sum((balance.available + balance.reserved for balance in snapshot.balances), Decimal("0"))
    return cash + sum(
        (position.quantity * (position.last_price or position.average_cost) for position in snapshot.positions),
        Decimal("0"),
    )


def test_replay_bars_mark_only_positions_in_their_own_instrument() -> None:
    seeded = _with_other_position()
    placed = place_replay_order(seeded, _market_request("market-1", "20"), _bar("101"))
    advanced = advance_replay_snapshot(placed.snapshot, _bar("120", start_hour=11))

    other = next(position for position in advanced.positions if position.instrument_id == OTHER)
    own = next(position for position in advanced.positions if position.instrument_id == "equity:NYSE:TEST")
    assert other.last_price == Decimal("50")
    assert other.unrealized_pnl == Decimal("50")
    assert own.last_price == Decimal("120")
    # The other instrument still contributes 5 x 50 at its own last mark.
    cash = sum((balance.available + balance.reserved for balance in advanced.balances), Decimal("0"))
    assert _equity(advanced) == cash + Decimal("250") + own.quantity * Decimal("120")


def test_a_bar_that_cannot_touch_the_account_leaves_it_unchanged() -> None:
    # The browser queue skips such bars without asking the server; that is only
    # correct if advancing through them changes nothing, equity included.
    seeded = _with_other_position()
    for hour in (10, 11, 12):
        advanced = advance_replay_snapshot(seeded, _bar("150", high="160", low="90", start_hour=hour))
        assert advanced == seeded
        assert _equity(advanced) == _equity(seeded)


def test_replay_orders_on_a_feed_fill_only_from_that_feeds_bars() -> None:
    # Replay follows live paper: an order bound to a feed fills only from that
    # feed's observations, so replay bars must carry the session's binding.
    request = _market_request("market-bound", "5").model_copy(update={"binding_id": "bind-1"})
    placed = place_replay_order(_funded_replay_snapshot(), request, _bar("101"))
    bound_bar = _bar("101", start_hour=11).model_copy(update={"binding_id": "bind-1"})
    unbound_bar = _bar("101", start_hour=11)

    filled = advance_replay_snapshot(placed.snapshot, bound_bar)
    unfilled = advance_replay_snapshot(placed.snapshot, unbound_bar)

    assert next(o for o in filled.order_history if o.order_id == "market-bound").filled_quantity == Decimal("5")
    assert next(o for o in unfilled.order_history if o.order_id == "market-bound").filled_quantity == Decimal("0")
