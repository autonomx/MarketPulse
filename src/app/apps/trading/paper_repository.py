from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, cast

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work
from app.apps.trading.us_equity_calendar import EASTERN

from .paper import (
    PaperAccount,
    PaperAccountCreate,
    PaperAccountSettings,
    OrderAuthority,
    PaperAccountSnapshot,
    PaperFill,
    PaperMargin,
    PaperMarginPosition,
    PaperMarketObservation,
    PaperOrder,
    PaperOrderRequest,
    PaperOrderStateUpdate,
    TIME_IN_FORCE_EXPIRED,
    paper_buy_reservation,
    paper_buying_power,
    paper_fill_is_fundable,
    paper_fill_decision,
    paper_fill_key,
    paper_liquidity_allocation,
    paper_margin_fraction,
    paper_observation_key,
    paper_order_commission,
    paper_order_expiry,
    paper_order_request_matches,
    paper_order_state_update,
    paper_price_tick,
    paper_realized_pnl,
    paper_unrealized_pnl,
)


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[PostgresUnitOfWork]: ...


def _account(row) -> PaperAccount:
    return PaperAccount(
        account_id=str(row[0]),
        name=str(row[1]),
        base_currency=str(row[2]),
        commission_bps=Decimal(row[3]),
        enabled=bool(row[4]),
        revision=int(row[5]),
        created_at=row[6],
        updated_at=row[7],
        allow_short=bool(row[8]) if len(row) > 8 else False,
        margin=_margin_settings(row[9]) if len(row) > 9 else {},
        commission_type=cast(Any, str(row[10])) if len(row) > 10 and row[10] else "percent",
        commission_fixed=Decimal(row[11]) if len(row) > 11 and row[11] is not None else Decimal("0"),
        notify_margin_calls=bool(row[12]) if len(row) > 12 else False,
    )


def _margin_settings(raw: Any) -> dict[str, PaperMargin]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return {str(key): PaperMargin.model_validate(value) for key, value in dict(raw or {}).items()}


def _margin_json(margin: dict[str, PaperMargin]) -> str:
    return json.dumps({key: {"long_pct": str(value.long_pct), "short_pct": str(value.short_pct)} for key, value in sorted(margin.items())})


def _order(row) -> PaperOrder:
    return PaperOrder(
        account_id=str(row[0]),
        order_id=str(row[1]),
        instrument_id=str(row[2]),
        binding_id=str(row[3]) if row[3] is not None else None,
        side=cast(Any, str(row[4])),
        order_type=cast(Any, str(row[5])),
        quantity=Decimal(row[6]),
        limit_price=Decimal(row[7]) if row[7] is not None else None,
        stop_price=Decimal(row[8]) if row[8] is not None else None,
        reference_price=Decimal(row[9]) if row[9] is not None else None,
        status=cast(Any, str(row[10])),
        filled_quantity=Decimal(row[11]),
        average_fill_price=Decimal(row[12]) if row[12] is not None else None,
        idempotency_key=str(row[13]),
        rejection_reason=str(row[14]) if row[14] is not None else None,
        reserved_cash=Decimal(row[15]),
        created_at=row[16],
        updated_at=row[17],
        time_in_force=cast(Any, str(row[18])),
        expires_at=row[19],
        trail_amount=Decimal(row[20]) if row[20] is not None else None,
        trail_percent=Decimal(row[21]) if row[21] is not None else None,
        trail_water_mark=Decimal(row[22]) if row[22] is not None else None,
        stop_triggered_at=row[23],
        trail_moved_at=row[24],
    )


_ORDER_COLUMNS = """
    account_id, order_id, instrument_id, binding_id, side, order_type,
    quantity, limit_price, stop_price, reference_price, status,
    filled_quantity, average_fill_price, idempotency_key, rejection_reason,
    reserved_cash, created_at, updated_at, time_in_force, expires_at,
    trail_amount, trail_percent, trail_water_mark, stop_triggered_at,
    trail_moved_at
"""


class TradingPaperRepository:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(timezone.utc)

    def create_account(self, request: PaperAccountCreate) -> PaperAccountSnapshot:
        from .paper_repository_accounts import create_account

        return create_account(self, request)

    def update_account_settings(
        self, account_id: str, settings: PaperAccountSettings, *, expected_revision: int
    ) -> PaperAccountSnapshot:
        """Change an account's settings (shorting, TVP-7.2a; margin and commission, TVP-7.2b) at the revision the person saw.

        Turning shorting off cancels working short entries (their holds are released and their pending
        stops follow) and leaves open shorts and their exits as they are.
        """
        from .paper_repository_accounts import update_account_settings

        return update_account_settings(self, account_id, settings, expected_revision=expected_revision)

    def _working_short_entries(self, uow: PostgresUnitOfWork, account_id: str) -> list[str]:
        """Open sells on instruments with no long position: entries that open or add to a short."""
        rows = uow.connection.execute(
            """
            SELECT orders.order_id
              FROM omnix_trading_paper_orders AS orders
              LEFT JOIN omnix_trading_paper_positions AS positions
                ON positions.workspace_id = orders.workspace_id
               AND positions.account_id = orders.account_id
               AND positions.instrument_id = orders.instrument_id
             WHERE orders.workspace_id = %s AND orders.account_id = %s
               AND orders.side = 'sell' AND orders.status = 'open'
               AND COALESCE(positions.quantity, 0) <= 0
            """,
            (self.context.workspace_id, account_id),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def list_accounts(self, limit: int = 100) -> list[PaperAccount]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT account_id, name, base_currency, commission_bps,
                       enabled, revision, created_at, updated_at, allow_short,
                   margin_settings, commission_type, commission_fixed, notify_margin_calls
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s
                 ORDER BY created_at DESC LIMIT %s
                """,
                (self.context.workspace_id, limit),
            ).fetchall()
            return [_account(row) for row in rows]

    def place_order(
        self,
        account_id: str,
        request: PaperOrderRequest,
        *,
        authority: OrderAuthority,
    ) -> PaperOrder:
        """Insert an order under the account lock (called by the order gateway only)."""
        with self.uow_factory() as uow:
            account, allow_short = self._lock_account(uow, account_id)
            order = self._place_locked(uow, account, allow_short, request, authority)
            uow.commit()
            return order

    def cancel_order(self, account_id: str, order_id: str) -> PaperOrder:
        """Cancel an open order and release its reservation (order gateway only)."""
        with self.uow_factory() as uow:
            account, _ = self._lock_account(uow, account_id)
            order = self._cancel_locked(uow, account, order_id)
            uow.commit()
            return order

    def replace_order(
        self,
        account_id: str,
        order_id: str,
        replacement: PaperOrderRequest,
        *,
        authority: OrderAuthority,
    ) -> tuple[PaperOrder, PaperOrder]:
        """Cancel ``order_id`` and place ``replacement`` in one transaction.

        Either both happen or neither: a rejected replacement leaves the
        original order open with its reservation.
        """
        with self.uow_factory() as uow:
            account, allow_short = self._lock_account(uow, account_id)
            cancelled = self._cancel_locked(uow, account, order_id)
            placed = self._place_locked(uow, account, allow_short, replacement, authority)
            uow.commit()
            return cancelled, placed

    def replace_entry(
        self,
        account_id: str,
        order_id: str,
        replacement: PaperOrderRequest,
        *,
        authority: OrderAuthority,
    ) -> tuple[PaperOrder, PaperOrder]:
        """Move a working entry (TVP-7.3): cancel it, place ``replacement`` and point its pending protection at
        the new order, in one transaction. Without a pending protection for ``order_id`` nothing changes, so an
        entry never ends up without its stop and a stop never points at a cancelled entry.
        """
        with self.uow_factory() as uow:
            account, allow_short = self._lock_account(uow, account_id)
            used = uow.connection.execute(
                """
                SELECT 1 FROM omnix_trading_paper_orders
                 WHERE workspace_id = %s AND account_id = %s AND (order_id = %s OR idempotency_key = %s)
                 LIMIT 1
                """,
                (self.context.workspace_id, account.account_id, replacement.order_id, replacement.idempotency_key),
            ).fetchone()
            if used is not None:
                # A moved entry is always a new order: an old one returned by its key would carry the stop to a dead order.
                raise ValueError("paper_order_id_not_new")
            cancelled = self._cancel_locked(uow, account, order_id)
            placed = self._place_locked(uow, account, allow_short, replacement, authority)
            moved = uow.connection.execute(
                """
                UPDATE omnix_trading_paper_protections
                   SET entry_order_id = %s, revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s
                   AND entry_order_id = %s AND status = 'pending_entry'
                RETURNING instrument_id
                """,
                (placed.order_id, self.context.workspace_id, account.account_id, order_id),
            ).fetchone()
            if moved is None or placed.order_id == order_id:
                raise ValueError("paper_risk_entry_not_movable")
            uow.commit()
            return cancelled, placed

    def _lock_account(self, uow: PostgresUnitOfWork, account_id: str) -> tuple[PaperAccount, bool]:
        row = uow.connection.execute(
            """
            SELECT account_id, name, base_currency, commission_bps,
                   enabled, revision, created_at, updated_at, allow_short,
                   margin_settings, commission_type, commission_fixed, notify_margin_calls
              FROM omnix_trading_paper_accounts
             WHERE workspace_id = %s AND account_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, account_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"paper_account_not_found: {account_id}")
        return _account(row), bool(row[8])

    def _engaged_kill_switch(
        self,
        uow: PostgresUnitOfWork,
        account_id: str,
        strategy_id: str | None,
    ) -> str | None:
        row = uow.connection.execute(
            """
            SELECT scope
              FROM omnix_trading_kill_switches
             WHERE workspace_id = %s AND engaged
               AND (scope = 'global'
                    OR (scope = 'account' AND scope_id = %s)
                    OR (scope = 'strategy' AND scope_id = %s))
             ORDER BY CASE scope WHEN 'global' THEN 0 WHEN 'account' THEN 1 ELSE 2 END
             LIMIT 1
             FOR SHARE
            """,
            (self.context.workspace_id, account_id, strategy_id or ""),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def _covers_short(self, uow: PostgresUnitOfWork, account_id: str, request: PaperOrderRequest) -> bool:
        """A buy no larger than an open short, less the buys already working on it, only reduces exposure."""
        row = uow.connection.execute(
            """
            SELECT quantity
              FROM omnix_trading_paper_positions
             WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, account_id, request.instrument_id),
        ).fetchone()
        quantity = Decimal(row[0]) if row else Decimal("0")
        if quantity >= 0:
            return False
        working = uow.connection.execute(
            """
            SELECT COALESCE(SUM(quantity - filled_quantity), 0)
              FROM omnix_trading_paper_orders
             WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
               AND side = 'buy' AND status = 'open'
            """,
            (self.context.workspace_id, account_id, request.instrument_id),
        ).fetchone()
        return request.quantity <= -quantity - Decimal(working[0] if working else 0)

    def _reserve_short_entry(self, uow: PostgresUnitOfWork, account: PaperAccount, request: PaperOrderRequest) -> Decimal:
        """Hold cash for a working short entry, as a buy holds it, within the buying power (TVP-7.2a).

        The hold is released when the short fills (its proceeds and margin then count through the short
        liability) or when the order is cancelled or expires.
        """
        balance_row = uow.connection.execute(
            """
            SELECT available
              FROM omnix_trading_paper_balances
             WHERE workspace_id = %s AND account_id = %s AND currency = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, account.account_id, account.base_currency),
        ).fetchone()
        available = Decimal(balance_row[0]) if balance_row else Decimal("0")
        hold = paper_buy_reservation(
            request.model_copy(update={"side": "buy"}),
            available_cash=available,
            commission_bps=account.commission_bps,
            margin=paper_margin_fraction(account, request.instrument_id, short=True),
            fixed_commission=account.commission_fixed if account.commission_type == "fixed_per_order" else None,
        )
        if hold <= 0 or paper_buying_power(account, available, self._margin_positions(uow, account.account_id)) < hold:
            raise ValueError("insufficient_paper_cash")
        uow.connection.execute(
            """
            UPDATE omnix_trading_paper_balances
               SET available = available - %s, reserved = reserved + %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s AND currency = %s
            """,
            (hold, hold, self.context.workspace_id, account.account_id, account.base_currency),
        )
        return hold

    def _margin_positions(self, uow: PostgresUnitOfWork, account_id: str) -> list[PaperMarginPosition]:
        """The open positions, for buying power and margin (TVP-7.2b): a short holds its proceeds and its margin,
        a leveraged long only its margin share."""
        rows = uow.connection.execute(
            """
            SELECT instrument_id, quantity, average_cost, last_price
              FROM omnix_trading_paper_positions
             WHERE workspace_id = %s AND account_id = %s AND quantity <> 0
             ORDER BY instrument_id
            """,
            (self.context.workspace_id, account_id),
        ).fetchall()
        return [
            PaperMarginPosition(str(row[0]), Decimal(row[1]), Decimal(row[2]), Decimal(row[3]) if row[3] is not None else None)
            for row in rows
        ]

    def _require_entry_authority(
        self,
        uow: PostgresUnitOfWork,
        account: PaperAccount,
        authority: OrderAuthority,
    ) -> None:
        """An order that opens or adds exposure needs entry authority and no engaged kill switch."""
        if not authority.may_add_exposure:
            raise ValueError("paper_order_requires_entry_authority")
        scope = self._engaged_kill_switch(uow, account.account_id, authority.strategy_id)
        if scope is not None:
            raise ValueError(f"trading_kill_switch_engaged:{scope}")
        if authority.max_daily_loss_pct is not None and self._daily_loss_reached(
            uow, account.account_id, authority.max_daily_loss_pct
        ):
            raise ValueError("paper_daily_loss_limit_reached")

    def _daily_loss_reached(self, uow: PostgresUnitOfWork, account_id: str, max_loss_pct: Decimal) -> bool:
        """Today's realized loss (Eastern day, commissions included) against account equity."""
        start = datetime.now(timezone.utc).astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
        realized, equity = uow.connection.execute(
            """
            SELECT
                (SELECT COALESCE(SUM(amount), 0) FROM omnix_trading_paper_ledger
                  WHERE workspace_id = %(workspace)s AND account_id = %(account)s
                    AND entry_type IN ('realized_pnl', 'commission') AND created_at >= %(start)s),
                (SELECT COALESCE(SUM(available + reserved), 0) FROM omnix_trading_paper_balances
                  WHERE workspace_id = %(workspace)s AND account_id = %(account)s)
                + (SELECT COALESCE(SUM(quantity * COALESCE(last_price, average_cost)), 0)
                     FROM omnix_trading_paper_positions
                    WHERE workspace_id = %(workspace)s AND account_id = %(account)s)
            """,
            {"workspace": self.context.workspace_id, "account": account_id, "start": start},
        ).fetchone()
        limit = Decimal(equity) * max_loss_pct / Decimal("100")
        return limit > 0 and Decimal(realized) <= -limit

    def _place_locked(
        self,
        uow: PostgresUnitOfWork,
        account: PaperAccount,
        allow_short: bool,
        request: PaperOrderRequest,
        authority: OrderAuthority,
    ) -> PaperOrder:
        account_id = account.account_id
        if not account.enabled:
            raise ValueError(f"paper_account_disabled: {account_id}")

        existing_row = uow.connection.execute(
            f"""
            SELECT {_ORDER_COLUMNS}
              FROM omnix_trading_paper_orders
             WHERE workspace_id = %s AND account_id = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (self.context.workspace_id, account_id, request.idempotency_key),
        ).fetchone()
        if existing_row is not None:
            existing = _order(existing_row)
            if not paper_order_request_matches(existing, request):
                raise ValueError("paper_idempotency_payload_mismatch")
            return existing

        placed_at = self._now()
        expires_at = paper_order_expiry(request, placed_at)
        if expires_at is not None and expires_at <= placed_at:
            raise ValueError("paper_order_expiry_in_past")

        reserved_cash = Decimal("0")
        if request.side == "buy":
            covers = self._covers_short(uow, account_id, request)
            if not covers:
                self._require_entry_authority(uow, account, authority)
            balance_row = uow.connection.execute(
                """
                SELECT available, reserved
                  FROM omnix_trading_paper_balances
                 WHERE workspace_id = %s AND account_id = %s AND currency = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, account.base_currency),
            ).fetchone()
            available = Decimal(balance_row[0]) if balance_row else Decimal("0")
            reserved_cash = paper_buy_reservation(
                request,
                available_cash=available,
                commission_bps=account.commission_bps,
                # A long holds its margin share; buying back a short holds its whole cost, as before.
                margin=Decimal("1") if covers else paper_margin_fraction(account, request.instrument_id, short=False),
                fixed_commission=account.commission_fixed if account.commission_type == "fixed_per_order" else None,
            )
            if covers:
                # Buying back a short is never refused for cash: it holds what there is, and a loss can
                # leave the balance below zero (margin calls close shorts before that, TVP-7.2b).
                reserved_cash = max(Decimal("0"), min(reserved_cash, available))
            elif reserved_cash <= 0 or paper_buying_power(account, available, self._margin_positions(uow, account_id)) < reserved_cash:
                raise ValueError("insufficient_paper_cash")
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_balances
                   SET available = available - %s,
                       reserved = reserved + %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND currency = %s
                """,
                (
                    reserved_cash,
                    reserved_cash,
                    self.context.workspace_id,
                    account_id,
                    account.base_currency,
                ),
            )
        else:
            position_row = uow.connection.execute(
                """
                SELECT quantity, reserved_quantity
                  FROM omnix_trading_paper_positions
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, request.instrument_id),
            ).fetchone()
            quantity = Decimal(position_row[0]) if position_row else Decimal("0")
            already_reserved = Decimal(position_row[1]) if position_row else Decimal("0")
            if quantity > 0 and quantity - already_reserved < request.quantity:
                raise ValueError("insufficient_paper_position")
            if quantity > 0:
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_positions
                       SET reserved_quantity = reserved_quantity + %s,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                    """,
                    (
                        request.quantity,
                        self.context.workspace_id,
                        account_id,
                        request.instrument_id,
                    ),
                )
            else:
                # Long-only unless the account opts in; a short opens exposure.
                if not allow_short:
                    raise ValueError("paper_short_not_allowed")
                self._require_entry_authority(uow, account, authority)
                reserved_cash = self._reserve_short_entry(uow, account, request)

        row = uow.connection.execute(
            f"""
            INSERT INTO omnix_trading_paper_orders (
                workspace_id, account_id, order_id, instrument_id, binding_id,
                side, order_type, quantity, limit_price, stop_price,
                reference_price, status, idempotency_key, reserved_cash,
                time_in_force, expires_at, trail_amount, trail_percent
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s, %s)
            RETURNING {_ORDER_COLUMNS}
            """,
            (
                self.context.workspace_id,
                account_id,
                request.order_id,
                request.instrument_id,
                request.binding_id,
                request.side,
                request.order_type,
                request.quantity,
                request.limit_price,
                request.stop_price,
                request.reference_price,
                request.idempotency_key,
                reserved_cash,
                request.time_in_force,
                expires_at,
                request.trail_amount,
                request.trail_percent,
            ),
        ).fetchone()
        return _order(row)

    def _cancel_locked(self, uow: PostgresUnitOfWork, account: PaperAccount, order_id: str) -> PaperOrder:
        order_row = uow.connection.execute(
            f"""
            SELECT {_ORDER_COLUMNS}
              FROM omnix_trading_paper_orders
             WHERE workspace_id = %s AND account_id = %s AND order_id = %s
               AND status = 'open'
             FOR UPDATE
            """,
            (self.context.workspace_id, account.account_id, order_id),
        ).fetchone()
        if order_row is None:
            raise ValueError("paper_order_not_open")
        order = _order(order_row)
        self._release_order_reservation(uow, account, order)
        row = uow.connection.execute(
            f"""
            UPDATE omnix_trading_paper_orders
               SET status = 'cancelled', reserved_cash = 0,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s AND order_id = %s
            RETURNING {_ORDER_COLUMNS}
            """,
            (self.context.workspace_id, account.account_id, order_id),
        ).fetchone()
        return _order(row)

    def expire_orders(self, account_id: str, *, now: datetime | None = None) -> list[PaperOrder]:
        """Expire the account's due DAY/GTD orders and release their holds.

        Expiry runs on the server clock, so an order expires even when its
        instrument has no market data after the close.
        """
        with self.uow_factory() as uow:
            account, _ = self._lock_account(uow, account_id)
            expired = self._expire_locked(uow, account, now or self._now())
            uow.commit()
            return expired

    def _expire_locked(self, uow: PostgresUnitOfWork, account: PaperAccount, now: datetime) -> list[PaperOrder]:
        rows = uow.connection.execute(
            f"""
            SELECT {_ORDER_COLUMNS}
              FROM omnix_trading_paper_orders
             WHERE workspace_id = %s AND account_id = %s
               AND status = 'open' AND expires_at IS NOT NULL AND expires_at <= %s
             ORDER BY expires_at, order_id
             FOR UPDATE
            """,
            (self.context.workspace_id, account.account_id, now),
        ).fetchall()
        expired: list[PaperOrder] = []
        for row in rows:
            order = _order(row)
            self._release_order_reservation(uow, account, order)
            updated = uow.connection.execute(
                f"""
                UPDATE omnix_trading_paper_orders
                   SET status = 'expired', rejection_reason = %s, reserved_cash = 0,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND order_id = %s
                RETURNING {_ORDER_COLUMNS}
                """,
                (TIME_IN_FORCE_EXPIRED, self.context.workspace_id, account.account_id, order.order_id),
            ).fetchone()
            if order.filled_quantity == 0:
                # Nothing filled, so no position will ever activate the entry's
                # bracket; cancel it here rather than waiting for market data.
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_protections
                       SET status = 'cancelled', trigger_reason = 'entry_expired',
                           revision = revision + 1, updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s
                       AND entry_order_id = %s AND status = 'pending_entry'
                    """,
                    (self.context.workspace_id, account.account_id, order.order_id),
                )
            expired.append(_order(updated))
        return expired

    def _save_order_state(
        self,
        uow: PostgresUnitOfWork,
        account_id: str,
        order_id: str,
        state: PaperOrderStateUpdate,
    ) -> None:
        uow.connection.execute(
            """
            UPDATE omnix_trading_paper_orders
               SET stop_price = %s, trail_water_mark = %s, stop_triggered_at = %s,
                   trail_moved_at = %s, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s AND order_id = %s
            """,
            (
                state.stop_price,
                state.trail_water_mark,
                state.stop_triggered_at,
                state.trail_moved_at,
                self.context.workspace_id,
                account_id,
                order_id,
            ),
        )

    def _release_order_reservation(
        self,
        uow: PostgresUnitOfWork,
        account: PaperAccount,
        order: PaperOrder,
    ) -> None:
        # A buy's hold, or a working short entry's (TVP-7.2a).
        if order.reserved_cash > 0:
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_balances
                   SET available = available + %s,
                       reserved = reserved - %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND currency = %s
                """,
                (
                    order.reserved_cash,
                    order.reserved_cash,
                    self.context.workspace_id,
                    account.account_id,
                    account.base_currency,
                ),
            )
        if order.side == "sell":
            remaining = max(Decimal("0"), order.quantity - order.filled_quantity)
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_positions
                   SET reserved_quantity = reserved_quantity - %s,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                   AND reserved_quantity >= %s
                """,
                (
                    remaining,
                    self.context.workspace_id,
                    account.account_id,
                    order.instrument_id,
                    remaining,
                ),
            )

    def process_observation(
        self,
        account_id: str,
        observation: PaperMarketObservation,
    ) -> list[PaperFill]:
        fills: list[PaperFill] = []
        with self.uow_factory() as uow:
            account_row = uow.connection.execute(
                """
                SELECT account_id, name, base_currency, commission_bps,
                       enabled, revision, created_at, updated_at, allow_short,
                   margin_settings, commission_type, commission_fixed, notify_margin_calls
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            if account_row is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            account = _account(account_row)
            # Due DAY/GTD orders expire, releasing their holds, before the
            # balance and position this observation works on are read.
            self._expire_locked(uow, account, self._now())
            balance_row = uow.connection.execute(
                """
                SELECT available, reserved
                  FROM omnix_trading_paper_balances
                 WHERE workspace_id = %s AND account_id = %s AND currency = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, account.base_currency),
            ).fetchone()
            cash_available = Decimal(balance_row[0]) if balance_row else Decimal("0")
            cash_reserved = Decimal(balance_row[1]) if balance_row else Decimal("0")
            position_row = uow.connection.execute(
                """
                SELECT quantity, reserved_quantity, average_cost, realized_pnl
                  FROM omnix_trading_paper_positions
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, observation.instrument_id),
            ).fetchone()
            position_quantity = Decimal(position_row[0]) if position_row else Decimal("0")
            reserved_quantity = Decimal(position_row[1]) if position_row else Decimal("0")
            average_cost = Decimal(position_row[2]) if position_row else Decimal("0")
            realized_pnl = Decimal(position_row[3]) if position_row else Decimal("0")
            order_rows = uow.connection.execute(
                f"""
                SELECT {_ORDER_COLUMNS}
                  FROM omnix_trading_paper_orders
                 WHERE workspace_id = %s AND account_id = %s
                   AND instrument_id = %s AND status = 'open'
                 ORDER BY created_at, order_id
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, observation.instrument_id),
            ).fetchall()
            observation_key = paper_observation_key(observation)
            tick_size = paper_price_tick(observation.instrument_id)
            existing_liquidity_rows = uow.connection.execute(
                """
                SELECT side, COALESCE(SUM(quantity), 0)
                  FROM omnix_trading_paper_fills
                 WHERE workspace_id = %s AND account_id = %s
                   AND instrument_id = %s AND observation_key = %s
                 GROUP BY side
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    observation.instrument_id,
                    observation_key,
                ),
            ).fetchall()
            existing_by_side = {
                str(row[0]): Decimal(row[1]) for row in existing_liquidity_rows
            }
            liquidity_consumed = {
                "book:buy": existing_by_side.get("buy", Decimal("0")),
                "book:sell": existing_by_side.get("sell", Decimal("0")),
                "bar": sum(existing_by_side.values(), Decimal("0")),
            }

            for row in order_rows:
                order = _order(row)
                # The decision uses the order's state from before this
                # observation; the observation then moves that state.
                decision = paper_fill_decision(order, observation, tick_size=tick_size)
                state = paper_order_state_update(order, observation, tick_size=tick_size)
                if state is not None:
                    self._save_order_state(uow, account_id, order.order_id, state)
                if (
                    not decision.should_fill
                    or decision.fill_price is None
                    or decision.fill_quantity is None
                    or decision.fill_quantity <= 0
                ):
                    continue
                requested_fill_quantity = min(
                    decision.fill_quantity,
                    max(Decimal("0"), order.quantity - order.filled_quantity),
                )
                fill_quantity, liquidity_scope = paper_liquidity_allocation(
                    order,
                    observation,
                    requested_fill_quantity,
                    liquidity_consumed,
                )
                if fill_quantity <= 0:
                    continue
                notional = fill_quantity * decision.fill_price
                # A fixed commission is charged once per order, on its first fill (TVP-7.2b).
                commission = paper_order_commission(account, notional, first_fill=order.filled_quantity == 0)
                total_cost = notional + commission
                rejection = None
                # Buying back a short is never refused for cash (TVP-7.2a): a stop must be able to close it.
                covers_short = order.side == "buy" and position_quantity < 0 and fill_quantity <= -position_quantity
                # A long needs its margin share in cash; the rest is borrowed and leaves the cash below zero.
                margin_cost = notional * paper_margin_fraction(account, order.instrument_id, short=False) + commission
                if not covers_short and not paper_fill_is_fundable(
                    order,
                    total_cost=margin_cost,
                    available_cash=cash_available,
                ):
                    rejection = "insufficient_paper_cash"
                if order.side == "sell" and position_quantity > 0 and (
                    position_quantity < fill_quantity or reserved_quantity < fill_quantity
                ):
                    rejection = "insufficient_paper_position"
                if rejection:
                    # A buy's or a short entry's cash hold goes back; an exit gives back its share of the long.
                    cash_available += order.reserved_cash
                    cash_reserved -= order.reserved_cash
                    if order.side == "sell" and order.reserved_cash == 0:
                        remaining = max(Decimal("0"), order.quantity - order.filled_quantity)
                        release_quantity = min(reserved_quantity, remaining)
                        reserved_quantity -= release_quantity
                    uow.connection.execute(
                        """
                        UPDATE omnix_trading_paper_orders
                           SET status = 'rejected', rejection_reason = %s,
                               reserved_cash = 0, updated_at = CURRENT_TIMESTAMP
                         WHERE workspace_id = %s AND account_id = %s AND order_id = %s
                        """,
                        (rejection, self.context.workspace_id, account_id, order.order_id),
                    )
                    continue

                key = paper_fill_key(account_id, order.order_id, observation)
                fill_id = key[:32]
                inserted = uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_paper_fills (
                        workspace_id, account_id, fill_id, order_id, instrument_id,
                        side, quantity, price, commission, source_time,
                        evaluated_at, idempotency_key, observation_key
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, account_id, idempotency_key) DO NOTHING
                    RETURNING fill_id
                    """,
                    (
                        self.context.workspace_id,
                        account_id,
                        fill_id,
                        order.order_id,
                        order.instrument_id,
                        order.side,
                        fill_quantity,
                        decision.fill_price,
                        commission,
                        observation.source_time,
                        observation.evaluated_at,
                        key,
                        observation_key,
                    ),
                ).fetchone()
                if inserted is None:
                    continue
                if liquidity_scope is not None:
                    liquidity_consumed[liquidity_scope] = (
                        liquidity_consumed.get(liquidity_scope, Decimal("0"))
                        + fill_quantity
                    )

                remaining_before = max(Decimal("0"), order.quantity - order.filled_quantity)
                if order.side == "buy":
                    # The hold covers the margin share and the commission; the rest of a leveraged buy is borrowed,
                    # taken from the cash (TVP-7.2b). At 100% margin the share is the whole cost, as before.
                    reservation_spend = min(order.reserved_cash, total_cost if covers_short else margin_cost)
                    cash_reserved -= reservation_spend
                    cash_available -= total_cost - reservation_spend
                    next_reserved_cash = max(
                        Decimal("0"),
                        order.reserved_cash - reservation_spend,
                    )
                    prior_cost = position_quantity * average_cost
                    if position_quantity >= 0:
                        position_quantity += fill_quantity
                        average_cost = (prior_cost + notional) / position_quantity
                        realized_delta = Decimal("0")
                    elif fill_quantity <= abs(position_quantity):
                        realized_delta = (average_cost - decision.fill_price) * fill_quantity
                        position_quantity += fill_quantity
                    else:
                        covered = abs(position_quantity)
                        realized_delta = (average_cost - decision.fill_price) * covered
                        position_quantity = fill_quantity - covered
                        average_cost = decision.fill_price
                    realized_pnl += realized_delta
                    if position_quantity == 0:
                        average_cost = Decimal("0")
                else:
                    # A short entry's hold is released as it fills: the short liability holds its proceeds and margin.
                    release = (
                        order.reserved_cash
                        if fill_quantity >= remaining_before
                        else order.reserved_cash * fill_quantity / remaining_before
                    )
                    cash_reserved -= release
                    cash_available += release
                    next_reserved_cash = order.reserved_cash - release
                    if position_quantity > 0:
                        reserved_quantity -= fill_quantity
                        close_quantity = min(position_quantity, fill_quantity)
                        realized_delta = paper_realized_pnl(
                            close_quantity,
                            average_cost,
                            decision.fill_price,
                        )
                        position_quantity -= close_quantity
                        if fill_quantity > close_quantity:
                            position_quantity = -(fill_quantity - close_quantity)
                            average_cost = decision.fill_price
                    else:
                        prior_short_quantity = abs(position_quantity)
                        position_quantity -= fill_quantity
                        average_cost = (
                            (prior_short_quantity * average_cost) + notional
                        ) / abs(position_quantity)
                        realized_delta = Decimal("0")
                    cash_available += notional - commission
                    realized_pnl += realized_delta
                    if position_quantity == 0:
                        average_cost = Decimal("0")

                new_filled_quantity = order.filled_quantity + fill_quantity
                prior_fill_notional = (
                    order.average_fill_price * order.filled_quantity
                    if order.average_fill_price is not None
                    else Decimal("0")
                )
                new_average_fill = (
                    prior_fill_notional + decision.fill_price * fill_quantity
                ) / new_filled_quantity
                new_status = "filled" if new_filled_quantity >= order.quantity else "open"
                if order.side == "buy" and new_status == "filled" and next_reserved_cash > 0:
                    cash_reserved -= next_reserved_cash
                    cash_available += next_reserved_cash
                    next_reserved_cash = Decimal("0")

                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_balances
                       SET available = %s, reserved = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND currency = %s
                    """,
                    (
                        cash_available,
                        cash_reserved,
                        self.context.workspace_id,
                        account_id,
                        account.base_currency,
                    ),
                )
                unrealized = paper_unrealized_pnl(
                    position_quantity,
                    average_cost,
                    observation.price,
                )
                uow.connection.execute(
                    """
                    INSERT INTO omnix_trading_paper_positions (
                        workspace_id, account_id, instrument_id, quantity,
                        reserved_quantity, average_cost, realized_pnl,
                        last_price, unrealized_pnl
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (workspace_id, account_id, instrument_id) DO UPDATE
                       SET quantity = EXCLUDED.quantity,
                           reserved_quantity = EXCLUDED.reserved_quantity,
                           average_cost = EXCLUDED.average_cost,
                           realized_pnl = EXCLUDED.realized_pnl,
                           last_price = EXCLUDED.last_price,
                           unrealized_pnl = EXCLUDED.unrealized_pnl,
                           updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        self.context.workspace_id,
                        account_id,
                        order.instrument_id,
                        position_quantity,
                        reserved_quantity,
                        average_cost,
                        realized_pnl,
                        observation.price,
                        unrealized,
                    ),
                )
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_orders
                       SET status = %s,
                           filled_quantity = %s,
                           average_fill_price = %s,
                           reserved_cash = %s,
                           updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND order_id = %s
                    """,
                    (
                        new_status,
                        new_filled_quantity,
                        new_average_fill,
                        next_reserved_cash,
                        self.context.workspace_id,
                        account_id,
                        order.order_id,
                    ),
                )

                ledger_rows = [
                    ("trade_cash", notional if order.side == "sell" else -notional),
                    ("commission", -commission),
                ]
                if realized_delta != 0:
                    ledger_rows.append(("realized_pnl", realized_delta))
                for entry_type, amount in ledger_rows:
                    ledger_key = f"{key}:{entry_type}"
                    uow.connection.execute(
                        """
                        INSERT INTO omnix_trading_paper_ledger (
                            workspace_id, account_id, ledger_id, entry_type,
                            currency, amount, order_id, fill_id,
                            idempotency_key, payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            self.context.workspace_id,
                            account_id,
                            ledger_key,
                            entry_type,
                            account.base_currency,
                            amount,
                            order.order_id,
                            fill_id,
                            ledger_key,
                            json.dumps(
                                {
                                    "provider": observation.provider,
                                    "binding_id": observation.binding_id,
                                    "source_time": observation.source_time.isoformat(),
                                    "high": str(observation.high) if observation.high is not None else None,
                                    "low": str(observation.low) if observation.low is not None else None,
                                    "fill_quantity": str(fill_quantity),
                                    "order_filled_quantity": str(new_filled_quantity),
                                }
                            ),
                        ),
                    )
                fills.append(
                    PaperFill(
                        fill_id=fill_id,
                        order_id=order.order_id,
                        instrument_id=order.instrument_id,
                        side=order.side,
                        quantity=fill_quantity,
                        price=decision.fill_price,
                        commission=commission,
                        source_time=observation.source_time,
                        evaluated_at=observation.evaluated_at,
                        idempotency_key=key,
                    )
                )

            if position_row is not None and not fills:
                unrealized = paper_unrealized_pnl(
                    position_quantity,
                    average_cost,
                    observation.price,
                )
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_positions
                       SET last_price = %s, unrealized_pnl = %s,
                           reserved_quantity = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                    """,
                    (
                        observation.price,
                        unrealized,
                        reserved_quantity,
                        self.context.workspace_id,
                        account_id,
                        observation.instrument_id,
                    ),
                )
            if balance_row is not None and not fills:
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_balances
                       SET available = %s, reserved = %s, updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND currency = %s
                    """,
                    (
                        cash_available,
                        cash_reserved,
                        self.context.workspace_id,
                        account_id,
                        account.base_currency,
                    ),
                )
            # The instrument's open position is marked at every observation (TVP-7.2b): buying power and margin read it.
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_positions
                   SET last_price = %s, unrealized_pnl = (%s - average_cost) * quantity
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s AND quantity <> 0
                """,
                (observation.price, observation.price, self.context.workspace_id, account_id, observation.instrument_id),
            )
            uow.commit()
        return fills

    def snapshot(
        self,
        account_id: str,
        *,
        account: PaperAccount | None = None,
    ) -> PaperAccountSnapshot:
        from .paper_repository_accounts import account_snapshot

        return account_snapshot(self, account_id, account=account)


PaperRepositoryFactory = Callable[[], TradingPaperRepository]


def default_paper_repository() -> TradingPaperRepository:
    return TradingPaperRepository()
