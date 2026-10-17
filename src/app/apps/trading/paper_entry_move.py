"""Moving a working risk entry to a new price (TVP-7.3, chart trading).

A manual entry is sized by the server's risk rules and carries a pending
stop/target protection. Dragging it on the chart re-prices it the way the
ticket would place it: the server sizes the moved entry again, aiming at the
same dollar risk as the original (capped by the policy), and replaces the
order in one transaction. The moved order is a new order id; its pending
protection is pointed at it in the same transaction, unchanged otherwise.

This module decides what may move and what the replacement is; the API route
runs it and the order gateway enforces the entry authority.
"""
from __future__ import annotations

from typing import Any, cast

from decimal import ROUND_DOWN, Decimal

from pydantic import BaseModel, ConfigDict, Field

from .paper import PaperAccountSnapshot, PaperOrder
from .paper_protection import PaperPositionProtection
from .paper_risk import PaperRiskOrderRequest, PaperRiskPolicy, PaperRiskPreview, _entry_price

MOVABLE_ORDER_TYPES = frozenset({"limit", "stop", "stop_limit"})


class PaperRiskEntryMoveRequest(BaseModel):
    """The moved entry's price and its new order id; everything else comes from the working order."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=240)
    # The limit or stop price; for a stop-limit entry, its stop price.
    trigger_price: Decimal = Field(gt=0)
    # Only for a stop-limit entry: its limit price (kept when omitted).
    limit_price: Decimal | None = Field(default=None, gt=0)


class PaperRiskEntryMoveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: PaperRiskPreview
    cancelled: PaperOrder
    order: PaperOrder
    protection: PaperPositionProtection


def movable_entry(
    snapshot: PaperAccountSnapshot,
    protections: list[PaperPositionProtection],
    order_id: str,
) -> tuple[PaperOrder, PaperPositionProtection]:
    """The working risk entry and its pending protection, or a ValueError saying why it can't move."""
    order = next((item for item in snapshot.open_orders if item.order_id == order_id), None)
    if order is None or order.status != "open":
        raise ValueError("paper_order_not_open")
    if order.order_type not in MOVABLE_ORDER_TYPES:
        raise ValueError("paper_risk_entry_not_movable")
    # A long or (TVP-7.2a) short entry; a sell against a long position is an exit, moved by replace instead.
    if order.side == "sell" and any(item.instrument_id == order.instrument_id and item.quantity > 0 for item in snapshot.positions):
        raise ValueError("paper_risk_entry_not_movable")
    if order.filled_quantity > 0:
        raise ValueError("paper_risk_entry_partially_filled")
    if order.order_type == "stop_limit" and order.stop_triggered_at is not None:
        # Its stop was reached: it now works as a limit order and stays as it is.
        raise ValueError("paper_risk_entry_not_movable")
    protection = next(
        (
            item
            for item in protections
            if item.instrument_id == order.instrument_id
            and item.entry_order_id == order.order_id
            and item.status == "pending_entry"
        ),
        None,
    )
    if protection is None or protection.stop_loss is None:
        # Only an entry placed with server risk (and so a pending stop) moves this way.
        raise ValueError("paper_risk_entry_not_movable")
    return order, protection


def moved_entry_intent(
    order: PaperOrder,
    protection: PaperPositionProtection,
    move: PaperRiskEntryMoveRequest,
    *,
    equity: Decimal,
    policy: PaperRiskPolicy | None = None,
) -> PaperRiskOrderRequest:
    """The risk order the moved entry is: same type, stop, target and time in force, at the new price.

    Its risk percent is the original order's dollar risk over today's equity, so
    the server sizes the moved entry to about the same risk; the policy caps it.
    """
    active = policy or PaperRiskPolicy()
    entry = _entry_price(order)
    stop = protection.stop_loss
    if entry is None or stop is None or equity <= 0:
        raise ValueError("paper_risk_entry_move_risk_unavailable")
    distance = stop - entry if order.side == "sell" else entry - stop
    risk = max(Decimal("0"), distance) * (order.quantity - order.filled_quantity)
    percent = min(risk / equity * Decimal("100"), active.max_risk_per_trade_pct)
    percent = percent.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if percent <= 0:
        raise ValueError("paper_risk_entry_move_risk_unavailable")
    return PaperRiskOrderRequest(
        order_id=move.order_id,
        side=order.side,
        instrument_id=order.instrument_id,
        binding_id=order.binding_id,
        # movable_entry admits only MOVABLE_ORDER_TYPES (limit, stop, stop_limit).
        order_type=cast(Any, order.order_type),
        trigger_price=move.trigger_price,
        limit_price=(move.limit_price or order.limit_price) if order.order_type == "stop_limit" else None,
        stop_loss=stop,
        take_profit=protection.take_profit,
        # The protection is re-pointed as it is (trail included), not re-armed from this request.
        trailing_stop_loss=False,
        desired_risk_pct=percent,
        idempotency_key=move.idempotency_key,
        time_in_force=order.time_in_force,
        # A DAY order's stored expiry is derived by the server; only a GTD order sends one.
        expires_at=order.expires_at if order.time_in_force == "gtd" else None,
    )


def snapshot_without_order(snapshot: PaperAccountSnapshot, order: PaperOrder) -> PaperAccountSnapshot:
    """The account as if ``order`` were cancelled: its cash reservation is free again, so sizing doesn't count it twice."""
    currency = snapshot.account.base_currency
    balances = [
        item.model_copy(
            update={
                "available": item.available + order.reserved_cash,
                "reserved": max(Decimal("0"), item.reserved - order.reserved_cash),
            }
        )
        if item.currency == currency
        else item
        for item in snapshot.balances
    ]
    return snapshot.model_copy(
        update={
            "open_orders": [item for item in snapshot.open_orders if item.order_id != order.order_id],
            "balances": balances,
        }
    )
