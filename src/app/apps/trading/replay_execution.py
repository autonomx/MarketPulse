from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .paper import (
    PaperAccountSnapshot,
    PaperBalance,
    PaperExecutionPolicy,
    PaperFill,
    PaperLedgerEntry,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    PaperPosition,
    paper_buy_reservation,
    paper_order_commission,
    paper_fill_decision,
    paper_realized_pnl,
    paper_unrealized_pnl,
)


class ReplayExecutionBar(BaseModel):
    """Historical candle normalized into execution evidence for chart replay.

    Replay deliberately has no synthetic quote book. The common paper policy
    therefore uses the bar's traded volume for the participation cap and the
    candle range for limit/stop reachability.
    """

    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(min_length=3, max_length=200)
    binding_id: str | None = Field(default=None, max_length=240)
    start_time: datetime
    end_time: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(default=Decimal("0"), ge=0)


class ReplayAdvanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: PaperAccountSnapshot
    bar: ReplayExecutionBar


class ReplayOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: PaperAccountSnapshot
    bar: ReplayExecutionBar
    order: PaperOrderRequest
    # False when the snapshot has already been advanced through ``bar``: the
    # bar is then not applied to working orders a second time.
    advance_bar: bool = True


class ReplayOrderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: PaperAccountSnapshot
    order: PaperOrder


def detached_replay_snapshot(source: PaperAccountSnapshot) -> PaperAccountSnapshot:
    """Return replay-only state with no production reservations or open orders."""

    balances = [
        balance.model_copy(
            update={
                "available": balance.available + balance.reserved,
                "reserved": Decimal("0"),
            }
        )
        for balance in source.balances
    ]
    positions = [position.model_copy(update={"reserved_quantity": Decimal("0")}) for position in source.positions]
    return source.model_copy(
        update={
            "balances": balances,
            "positions": positions,
            "open_orders": [],
            "order_history": [],
            "recent_fills": [],
            "recent_ledger": [],
        }
    )


def _observation(bar: ReplayExecutionBar) -> PaperMarketObservation:
    # Historical bars are immutable replay evidence. evaluated_at is pinned to
    # source_time so wall-clock age cannot make a historical replay stale.
    return PaperMarketObservation(
        instrument_id=bar.instrument_id,
        binding_id=bar.binding_id,
        provider="historical-replay",
        price=bar.close,
        high=bar.high,
        low=bar.low,
        volume=bar.volume,
        bar_start_time=bar.start_time,
        source_time=bar.end_time,
        evaluated_at=bar.end_time,
        execution_eligible=True,
        freshness_mode="historical_replay",
        rejection_reasons=(),
        halted=False,
    )


def _balance(snapshot: PaperAccountSnapshot) -> PaperBalance | None:
    return next(
        (value for value in snapshot.balances if value.currency == snapshot.account.base_currency),
        snapshot.balances[0] if snapshot.balances else None,
    )


def _position(snapshot: PaperAccountSnapshot, instrument_id: str) -> PaperPosition | None:
    return next((value for value in snapshot.positions if value.instrument_id == instrument_id), None)


def _replace_order(snapshot: PaperAccountSnapshot, order: PaperOrder) -> PaperAccountSnapshot:
    open_orders = [value for value in snapshot.open_orders if value.order_id != order.order_id]
    if order.status == "open":
        open_orders.append(order)
    history = [order if value.order_id == order.order_id else value for value in snapshot.order_history]
    if not any(value.order_id == order.order_id for value in history):
        history.append(order)
    return snapshot.model_copy(update={"open_orders": open_orders, "order_history": history})


def _mark(snapshot: PaperAccountSnapshot, bar: ReplayExecutionBar) -> PaperAccountSnapshot:
    """Mark positions in the bar's instrument at its close.

    Positions in other instruments keep their last mark: a bar never prices
    another instrument.
    """
    positions = [
        position.model_copy(
            update={
                "last_price": bar.close,
                "unrealized_pnl": paper_unrealized_pnl(position.quantity, position.average_cost, bar.close),
            }
        )
        if position.instrument_id == bar.instrument_id
        else position
        for position in snapshot.positions
    ]
    return snapshot.model_copy(update={"positions": positions})


def _reject(snapshot: PaperAccountSnapshot, order: PaperOrder, reason: str) -> ReplayOrderResult:
    rejected = order.model_copy(
        update={
            "status": "rejected",
            "rejection_reason": reason,
            "updated_at": order.created_at,
        }
    )
    return ReplayOrderResult(snapshot=_replace_order(snapshot, rejected), order=rejected)


def _reserve(snapshot: PaperAccountSnapshot, request: PaperOrderRequest, order: PaperOrder) -> ReplayOrderResult:
    balance = _balance(snapshot)
    position = _position(snapshot, request.instrument_id)
    balances = list(snapshot.balances)
    positions = list(snapshot.positions)
    reserved_cash = Decimal("0")

    if request.side == "buy":
        if balance is None:
            return _reject(snapshot, order, "paper_balance_not_found")
        reserved_cash = paper_buy_reservation(
            request,
            available_cash=balance.available,
            commission_bps=snapshot.account.commission_bps,
            # Replay trades on cash alone, but charges the account's fixed commission where it has one (TVP-7.2b).
            fixed_commission=snapshot.account.commission_fixed if snapshot.account.commission_type == "fixed_per_order" else None,
        )
        if reserved_cash <= 0 or reserved_cash > balance.available:
            return _reject(snapshot, order, "insufficient_paper_cash")
        balances = [
            value.model_copy(
                update={
                    "available": value.available - reserved_cash,
                    "reserved": value.reserved + reserved_cash,
                }
            )
            if value.currency == balance.currency
            else value
            for value in balances
        ]
    else:
        quantity = position.quantity if position is not None else Decimal("0")
        reserved = position.reserved_quantity if position is not None else Decimal("0")
        if quantity - reserved < request.quantity:
            return _reject(snapshot, order, "insufficient_paper_position")
        positions = [
            value.model_copy(update={"reserved_quantity": value.reserved_quantity + request.quantity})
            if value.instrument_id == request.instrument_id
            else value
            for value in positions
        ]

    opened = order.model_copy(update={"reserved_cash": reserved_cash})
    return ReplayOrderResult(
        snapshot=_replace_order(snapshot.model_copy(update={"balances": balances, "positions": positions}), opened),
        order=opened,
    )


def _apply_fill(
    snapshot: PaperAccountSnapshot,
    order: PaperOrder,
    observation: PaperMarketObservation,
    policy: PaperExecutionPolicy,
) -> ReplayOrderResult:
    decision = paper_fill_decision(order, observation, policy)
    if not decision.should_fill or decision.fill_price is None or decision.fill_quantity is None:
        return ReplayOrderResult(snapshot=snapshot, order=order)

    remaining_before = max(Decimal("0"), order.quantity - order.filled_quantity)
    fill_quantity = min(remaining_before, decision.fill_quantity)
    if fill_quantity <= 0:
        return ReplayOrderResult(snapshot=snapshot, order=order)

    fill_price = decision.fill_price
    notional = fill_quantity * fill_price
    # The account's commission: bps, or its fixed amount once per order (TVP-7.2b).
    commission = paper_order_commission(snapshot.account, notional, first_fill=order.filled_quantity == 0)
    balance = _balance(snapshot)
    position = _position(snapshot, order.instrument_id)
    if balance is None:
        return _reject(snapshot, order, "paper_balance_not_found")

    balances = list(snapshot.balances)
    positions = [value for value in snapshot.positions if value.instrument_id != order.instrument_id]
    realized = Decimal("0")

    if order.side == "buy":
        reservation_release = (
            order.reserved_cash * fill_quantity / remaining_before
            if order.reserved_cash > 0 and remaining_before > 0
            else Decimal("0")
        )
        usable_cash = balance.available + reservation_release
        total_cost = notional + commission
        if usable_cash < total_cost:
            return _reject(snapshot, order, "insufficient_paper_cash")
        balances = [
            value.model_copy(
                update={
                    "available": value.available + reservation_release - total_cost,
                    "reserved": max(Decimal("0"), value.reserved - reservation_release),
                }
            )
            if value.currency == balance.currency
            else value
            for value in balances
        ]
        prior_quantity = position.quantity if position is not None else Decimal("0")
        prior_cost = position.average_cost if position is not None else Decimal("0")
        next_quantity = prior_quantity + fill_quantity
        next_cost = ((prior_quantity * prior_cost) + notional) / next_quantity
        next_position = PaperPosition(
            instrument_id=order.instrument_id,
            quantity=next_quantity,
            reserved_quantity=position.reserved_quantity if position is not None else Decimal("0"),
            average_cost=next_cost,
            realized_pnl=position.realized_pnl if position is not None else Decimal("0"),
            last_price=fill_price,
            unrealized_pnl=Decimal("0"),
        )
        positions.append(next_position)
    else:
        if position is None or position.quantity < fill_quantity or position.reserved_quantity < fill_quantity:
            return _reject(snapshot, order, "insufficient_paper_position")
        realized = paper_realized_pnl(fill_quantity, position.average_cost, fill_price)
        balances = [
            value.model_copy(update={"available": value.available + notional - commission})
            if value.currency == balance.currency
            else value
            for value in balances
        ]
        next_quantity = position.quantity - fill_quantity
        if next_quantity > 0:
            positions.append(
                position.model_copy(
                    update={
                        "quantity": next_quantity,
                        "reserved_quantity": max(Decimal("0"), position.reserved_quantity - fill_quantity),
                        "realized_pnl": position.realized_pnl + realized,
                        "last_price": fill_price,
                        "unrealized_pnl": Decimal("0"),
                    }
                )
            )

    next_filled = order.filled_quantity + fill_quantity
    previous_notional = (order.average_fill_price or Decimal("0")) * order.filled_quantity
    average_fill = (previous_notional + notional) / next_filled
    complete = next_filled >= order.quantity
    remaining_reserved = max(
        Decimal("0"),
        order.reserved_cash
        - (order.reserved_cash * fill_quantity / remaining_before if order.reserved_cash > 0 and remaining_before > 0 else Decimal("0")),
    )
    next_order = order.model_copy(
        update={
            "status": "filled" if complete else "open",
            "filled_quantity": next_filled,
            "average_fill_price": average_fill,
            "reserved_cash": Decimal("0") if complete else remaining_reserved,
            "updated_at": observation.source_time,
        }
    )

    fill = PaperFill(
        fill_id=f"replay-fill:{order.order_id}:{len(snapshot.recent_fills) + 1}",
        order_id=order.order_id,
        instrument_id=order.instrument_id,
        side=order.side,
        quantity=fill_quantity,
        price=fill_price,
        commission=commission,
        source_time=observation.source_time,
        evaluated_at=observation.evaluated_at,
        idempotency_key=f"{order.idempotency_key}:fill:{next_filled}",
    )
    ledger = [
        PaperLedgerEntry(
            ledger_id=f"replay-ledger:{fill.fill_id}:trade",
            entry_type="trade_cash",
            currency=balance.currency,
            amount=-notional if order.side == "buy" else notional,
            order_id=order.order_id,
            fill_id=fill.fill_id,
            idempotency_key=f"{fill.idempotency_key}:trade",
            payload={"replay": True, "execution_policy": policy.policy_version},
            created_at=observation.source_time,
        ),
        PaperLedgerEntry(
            ledger_id=f"replay-ledger:{fill.fill_id}:commission",
            entry_type="commission",
            currency=balance.currency,
            amount=-commission,
            order_id=order.order_id,
            fill_id=fill.fill_id,
            idempotency_key=f"{fill.idempotency_key}:commission",
            payload={"replay": True},
            created_at=observation.source_time,
        ),
    ]
    if realized != 0:
        ledger.append(
            PaperLedgerEntry(
                ledger_id=f"replay-ledger:{fill.fill_id}:pnl",
                entry_type="realized_pnl",
                currency=balance.currency,
                amount=realized,
                order_id=order.order_id,
                fill_id=fill.fill_id,
                idempotency_key=f"{fill.idempotency_key}:pnl",
                payload={"replay": True},
                created_at=observation.source_time,
            )
        )

    updated = snapshot.model_copy(
        update={
            "balances": balances,
            "positions": positions,
            "recent_fills": [*snapshot.recent_fills, fill],
            "recent_ledger": [*snapshot.recent_ledger, *ledger],
        }
    )
    updated = _replace_order(updated, next_order)
    return ReplayOrderResult(snapshot=updated, order=next_order)


def advance_replay_snapshot(
    source: PaperAccountSnapshot,
    bar: ReplayExecutionBar,
    *,
    policy: PaperExecutionPolicy | None = None,
) -> PaperAccountSnapshot:
    active = policy or PaperExecutionPolicy()
    observation = _observation(bar)
    current = _mark(source, bar)
    for order in list(current.open_orders):
        current = _apply_fill(current, order, observation, active).snapshot
    return _mark(current, bar)


def place_replay_order(
    source: PaperAccountSnapshot,
    request: PaperOrderRequest,
    bar: ReplayExecutionBar,
    *,
    policy: PaperExecutionPolicy | None = None,
    advance_bar: bool = True,
) -> ReplayOrderResult:
    """Place a replay order at ``bar``.

    By default the snapshot is first advanced through ``bar``. Pass
    ``advance_bar=False`` when the caller has already advanced it, so working
    orders see each bar exactly once.
    """
    active = policy or PaperExecutionPolicy()
    prepared = advance_replay_snapshot(source, bar, policy=active) if advance_bar else _mark(source, bar)
    # Place the order just before the immutable bar close so the same execution
    # policy latency check remains meaningful without using wall-clock time.
    created_at = bar.end_time - timedelta(milliseconds=active.latency_ms)
    order = PaperOrder(
        account_id=prepared.account.account_id,
        **request.model_dump(),
        status="open",
        filled_quantity=Decimal("0"),
        reserved_cash=Decimal("0"),
        created_at=created_at,
        updated_at=created_at,
    )
    # Replay snapshots are detached and do not carry trigger/trailing state or
    # a session clock, so the TVP-7.1 order types and time in force are live
    # paper only for now.
    if request.order_type in {"stop_limit", "trailing_stop"}:
        return _reject(prepared, order, "replay_order_type_unsupported")
    if request.time_in_force != "gtc":
        return _reject(prepared, order, "replay_time_in_force_unsupported")
    reserved = _reserve(prepared, request, order)
    if reserved.order.status == "rejected":
        return reserved
    observation = _observation(bar)
    return _apply_fill(reserved.snapshot, reserved.order, observation, active)
