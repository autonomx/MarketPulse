"""Paper accounts: creation, settings and snapshots (moved out of TradingPaperRepository, which delegates)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import json
from decimal import Decimal
from typing import Any, cast
from app.persistence.errors import RevisionConflict
from .paper import (
    PaperAccount,
    PaperAccountCreate,
    PaperAccountSettings,
    PaperAccountSnapshot,
    PaperBalance,
    PaperFill,
    PaperLedgerEntry,
    PaperMarginPosition,
    PaperPosition,
    paper_margin_status,
)
from .paper_repository import (
    _ORDER_COLUMNS,
    _account,
    _margin_json,
    _order,
)

if TYPE_CHECKING:
    from .paper_repository import TradingPaperRepository


def create_account(repository: TradingPaperRepository, request: PaperAccountCreate) -> PaperAccountSnapshot:
    with repository.uow_factory() as uow:
        account_row = uow.connection.execute(
            """
            INSERT INTO omnix_trading_paper_accounts (
                workspace_id, account_id, owner_user_id, name,
                base_currency, commission_bps, allow_short,
                margin_settings, commission_type, commission_fixed, notify_margin_calls
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            RETURNING account_id, name, base_currency, commission_bps,
                      enabled, revision, created_at, updated_at, allow_short,
               margin_settings, commission_type, commission_fixed, notify_margin_calls
            """,
            (
                repository.context.workspace_id,
                request.account_id,
                repository.context.user_id,
                request.name,
                request.base_currency,
                request.commission_bps,
                request.allow_short,
                _margin_json(request.margin),
                request.commission_type,
                request.commission_fixed,
                request.notify_margin_calls,
            ),
        ).fetchone()
        uow.connection.execute(
            """
            INSERT INTO omnix_trading_paper_balances (
                workspace_id, account_id, currency, available
            ) VALUES (%s, %s, %s, %s)
            """,
            (
                repository.context.workspace_id,
                request.account_id,
                request.base_currency,
                request.initial_cash,
            ),
        )
        ledger_id = f"deposit:{request.account_id}"
        uow.connection.execute(
            """
            INSERT INTO omnix_trading_paper_ledger (
                workspace_id, account_id, ledger_id, entry_type, currency,
                amount, idempotency_key, payload
            ) VALUES (%s, %s, %s, 'deposit', %s, %s, %s, %s::jsonb)
            """,
            (
                repository.context.workspace_id,
                request.account_id,
                ledger_id,
                request.base_currency,
                request.initial_cash,
                ledger_id,
                json.dumps({"source": "account_creation"}),
            ),
        )
        uow.commit()
    return repository.snapshot(request.account_id, account=_account(account_row))


def update_account_settings(
    repository: TradingPaperRepository, account_id: str, settings: PaperAccountSettings, *, expected_revision: int
) -> PaperAccountSnapshot:
    """Change an account's settings (shorting, TVP-7.2a; margin and commission, TVP-7.2b) at the revision the person saw.

    Turning shorting off cancels working short entries (their holds are released and their pending
    stops follow) and leaves open shorts and their exits as they are.
    """
    with repository.uow_factory() as uow:
        if settings.allow_short is False:
            locked, _ = repository._lock_account(uow, account_id)
            for short_entry in repository._working_short_entries(uow, account_id):
                repository._cancel_locked(uow, locked, short_entry)
        row = uow.connection.execute(
            """
            UPDATE omnix_trading_paper_accounts
               SET allow_short = COALESCE(%s, allow_short),
                   margin_settings = COALESCE(%s::jsonb, margin_settings),
                   commission_type = COALESCE(%s, commission_type),
                   commission_bps = COALESCE(%s, commission_bps),
                   commission_fixed = COALESCE(%s, commission_fixed),
                   notify_margin_calls = COALESCE(%s, notify_margin_calls),
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s AND revision = %s
            RETURNING account_id, name, base_currency, commission_bps,
                      enabled, revision, created_at, updated_at, allow_short,
               margin_settings, commission_type, commission_fixed, notify_margin_calls
            """,
            (
                settings.allow_short,
                _margin_json(settings.margin) if settings.margin is not None else None,
                settings.commission_type,
                settings.commission_bps,
                settings.commission_fixed,
                settings.notify_margin_calls,
                repository.context.workspace_id,
                account_id,
                expected_revision,
            ),
        ).fetchone()
        if row is None:
            exists = uow.connection.execute(
                "SELECT 1 FROM omnix_trading_paper_accounts WHERE workspace_id = %s AND account_id = %s",
                (repository.context.workspace_id, account_id),
            ).fetchone()
            if exists is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            raise RevisionConflict(f"Paper account expected revision {expected_revision}: {account_id}")
        uow.commit()
    return repository.snapshot(account_id, account=_account(row))


def account_snapshot(
    repository: TradingPaperRepository,
    account_id: str,
    *,
    account: PaperAccount | None = None,
) -> PaperAccountSnapshot:
    with repository.uow_factory() as uow:
        if account is None:
            row = uow.connection.execute(
                """
                SELECT account_id, name, base_currency, commission_bps,
                       enabled, revision, created_at, updated_at, allow_short,
               margin_settings, commission_type, commission_fixed, notify_margin_calls
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                """,
                (repository.context.workspace_id, account_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            account = _account(row)
        balances = [
            PaperBalance(
                currency=str(row[0]),
                available=Decimal(row[1]),
                reserved=Decimal(row[2]),
            )
            for row in uow.connection.execute(
                "SELECT currency, available, reserved FROM omnix_trading_paper_balances WHERE workspace_id = %s AND account_id = %s ORDER BY currency",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        positions = [
            PaperPosition(
                instrument_id=str(row[0]),
                quantity=Decimal(row[1]),
                reserved_quantity=Decimal(row[2]),
                average_cost=Decimal(row[3]),
                realized_pnl=Decimal(row[4]),
                last_price=Decimal(row[5]) if row[5] is not None else None,
                unrealized_pnl=Decimal(row[6]),
            )
            for row in uow.connection.execute(
                "SELECT instrument_id, quantity, reserved_quantity, average_cost, realized_pnl, last_price, unrealized_pnl FROM omnix_trading_paper_positions WHERE workspace_id = %s AND account_id = %s ORDER BY instrument_id",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        orders = [
            _order(row)
            for row in uow.connection.execute(
                f"SELECT {_ORDER_COLUMNS} FROM omnix_trading_paper_orders WHERE workspace_id = %s AND account_id = %s AND status = 'open' ORDER BY created_at",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        order_history = [
            _order(row)
            for row in uow.connection.execute(
                f"SELECT {_ORDER_COLUMNS} FROM omnix_trading_paper_orders WHERE workspace_id = %s AND account_id = %s ORDER BY created_at DESC, order_id DESC LIMIT 200",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        fills = [
            PaperFill(
                fill_id=str(row[0]),
                order_id=str(row[1]),
                instrument_id=str(row[2]),
                side=cast(Any, str(row[3])),
                quantity=Decimal(row[4]),
                price=Decimal(row[5]),
                commission=Decimal(row[6]),
                source_time=row[7],
                evaluated_at=row[8],
                idempotency_key=str(row[9]),
            )
            for row in uow.connection.execute(
                "SELECT fill_id, order_id, instrument_id, side, quantity, price, commission, source_time, evaluated_at, idempotency_key FROM omnix_trading_paper_fills WHERE workspace_id = %s AND account_id = %s ORDER BY created_at DESC LIMIT 100",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        ledger = [
            PaperLedgerEntry(
                ledger_id=str(row[0]),
                entry_type=cast(Any, str(row[1])),
                currency=str(row[2]),
                amount=Decimal(row[3]),
                order_id=str(row[4]) if row[4] else None,
                fill_id=str(row[5]) if row[5] else None,
                idempotency_key=str(row[6]),
                payload=dict(row[7] or {}),
                created_at=row[8],
            )
            for row in uow.connection.execute(
                "SELECT ledger_id, entry_type, currency, amount, order_id, fill_id, idempotency_key, payload, created_at FROM omnix_trading_paper_ledger WHERE workspace_id = %s AND account_id = %s ORDER BY created_at DESC LIMIT 200",
                (repository.context.workspace_id, account_id),
            ).fetchall()
        ]
        base = next((balance for balance in balances if balance.currency == account.base_currency), None)
        margin_positions = [
            PaperMarginPosition(position.instrument_id, position.quantity, position.average_cost, position.last_price)
            for position in positions
            if position.quantity != 0
        ]
        return PaperAccountSnapshot(
            account=account,
            balances=balances,
            positions=positions,
            open_orders=orders,
            order_history=order_history,
            recent_fills=fills,
            recent_ledger=ledger,
            margin_status=paper_margin_status(
                account,
                base.available if base else Decimal("0"),
                base.reserved if base else Decimal("0"),
                margin_positions,
            ),
        )
