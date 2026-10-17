from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from decimal import Decimal
from typing import Protocol

from app.persistence.errors import RevisionConflict
from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work

from .paper import PaperAccountSnapshot
from .paper_repository import TradingPaperRepository


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AbstractContextManager[PostgresUnitOfWork]: ...


class TradingPaperLifecycle:
    context = RequestTenant()
    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: UnitOfWorkFactory = unit_of_work,
        repository_factory: Callable[[], TradingPaperRepository] | None = None,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory
        self.repository_factory = repository_factory or (
            lambda: TradingPaperRepository(
                context=self.context,
                uow_factory=self.uow_factory,
            )
        )

    def _archive_current_epoch(
        self,
        uow: PostgresUnitOfWork,
        account_id: str,
        reason: str,
    ) -> str | None:
        """Freeze the current simulator state before a lifecycle boundary.

        The operational paper tables remain current-simulation tables. Epoch
        archives preserve the complete pre-reset/pre-archive evidence so a UI
        reset can never erase strategy-validation history.
        """

        row = uow.connection.execute(
            """
            SELECT epoch_id
              FROM omnix_trading_paper_simulation_epochs
             WHERE workspace_id = %s AND account_id = %s AND is_current = TRUE
             ORDER BY ordinal DESC
             LIMIT 1
             FOR UPDATE
            """,
            (self.context.workspace_id, account_id),
        ).fetchone()
        if row is None:
            return None
        epoch_id = str(row[0])
        workspace_id = self.context.workspace_id
        uow.connection.execute(
            """
            INSERT INTO omnix_trading_paper_epoch_archives (
                workspace_id, account_id, epoch_id, reason, snapshot
            )
            SELECT %s, %s, %s, %s,
                   jsonb_build_object(
                       'account', COALESCE((
                           SELECT to_jsonb(item)
                             FROM omnix_trading_paper_accounts AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '{}'::jsonb),
                       'balances', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.currency)
                             FROM omnix_trading_paper_balances AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'positions', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.instrument_id)
                             FROM omnix_trading_paper_positions AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'orders', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.created_at, item.order_id)
                             FROM omnix_trading_paper_orders AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'fills', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.source_time, item.fill_id)
                             FROM omnix_trading_paper_fills AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'ledger', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.created_at, item.ledger_id)
                             FROM omnix_trading_paper_ledger AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'manual_protections', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.created_at, item.instrument_id)
                             FROM omnix_trading_paper_protections AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb),
                       'strategy_protections', COALESCE((
                           SELECT jsonb_agg(to_jsonb(item) ORDER BY item.created_at, item.protection_id)
                             FROM omnix_trading_strategy_protections AS item
                            WHERE item.workspace_id = %s AND item.account_id = %s
                       ), '[]'::jsonb)
                   )
            ON CONFLICT (workspace_id, account_id, epoch_id) DO UPDATE SET
                archived_at = CURRENT_TIMESTAMP,
                reason = EXCLUDED.reason,
                snapshot = EXCLUDED.snapshot
            """,
            (
                workspace_id,
                account_id,
                epoch_id,
                reason,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
                workspace_id,
                account_id,
            ),
        )
        uow.connection.execute(
            """
            UPDATE omnix_trading_paper_simulation_epochs
               SET is_current = FALSE,
                   ended_at = COALESCE(ended_at, CURRENT_TIMESTAMP),
                   end_reason = %s
             WHERE workspace_id = %s AND account_id = %s AND epoch_id = %s
            """,
            (reason, workspace_id, account_id, epoch_id),
        )
        return epoch_id

    def _disable_account_automation(self, uow: PostgresUnitOfWork, account_id: str, reason: str) -> None:
        """Fail safe: lifecycle changes cannot leave autonomous entries armed."""
        uow.connection.execute(
            """
            UPDATE omnix_trading_strategy_configs
               SET mode = 'off', revision = revision + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s
               AND (mode <> 'off' OR enabled = TRUE)
            """,
            (self.context.workspace_id, account_id),
        )
        uow.connection.execute(
            """
            UPDATE omnix_trading_strategy_protections
               SET status = 'cancelled', trigger_reason = %s,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s
               AND status IN ('pending_entry', 'active', 'exit_submitted')
            """,
            (reason, self.context.workspace_id, account_id),
        )
        uow.connection.execute(
            """
            UPDATE omnix_trading_paper_protections
               SET status = 'cancelled', trigger_reason = %s,
                   revision = revision + 1, updated_at = CURRENT_TIMESTAMP
             WHERE workspace_id = %s AND account_id = %s
               AND status IN ('pending_entry', 'active', 'exit_submitted')
            """,
            (reason, self.context.workspace_id, account_id),
        )

    def reset_account(
        self,
        account_id: str,
        *,
        initial_cash: Decimal,
        expected_revision: int,
    ) -> PaperAccountSnapshot:
        if initial_cash < 0:
            raise ValueError("initial_cash_must_be_non_negative")
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                SELECT base_currency, revision
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            if int(row[1]) != expected_revision:
                raise RevisionConflict(
                    f"Paper account expected revision {expected_revision}: {account_id}"
                )
            currency = str(row[0])
            prior_epoch_id = self._archive_current_epoch(uow, account_id, "account_reset")
            self._disable_account_automation(uow, account_id, "account_reset")
            # The old simulator state is now immutable in its epoch archive.
            # Clear only the operational/current-simulation tables; analytics,
            # completed trade records and prior equity curves remain durable.
            uow.connection.execute(
                "DELETE FROM omnix_trading_paper_protections WHERE workspace_id = %s AND account_id = %s",
                (self.context.workspace_id, account_id),
            )
            for table in (
                "omnix_trading_paper_ledger",
                "omnix_trading_paper_fills",
                "omnix_trading_paper_orders",
                "omnix_trading_paper_positions",
                "omnix_trading_paper_balances",
            ):
                uow.connection.execute(
                    f"DELETE FROM {table} WHERE workspace_id = %s AND account_id = %s",
                    (self.context.workspace_id, account_id),
                )
            # The balance-insert epoch trigger creates the next current epoch.
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_paper_balances (
                    workspace_id, account_id, currency, available
                ) VALUES (%s, %s, %s, %s)
                """,
                (self.context.workspace_id, account_id, currency, initial_cash),
            )
            ledger_id = f"reset:{account_id}:{expected_revision + 1}"
            uow.connection.execute(
                """
                INSERT INTO omnix_trading_paper_ledger (
                    workspace_id, account_id, ledger_id, entry_type,
                    currency, amount, idempotency_key, payload
                ) VALUES (%s, %s, %s, 'deposit', %s, %s, %s, %s::jsonb)
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    ledger_id,
                    currency,
                    initial_cash,
                    ledger_id,
                    json.dumps(
                        {
                            "source": "explicit_account_reset",
                            "automation_mode_after_reset": "off",
                            "prior_epoch_id": prior_epoch_id,
                        }
                    ),
                ),
            )
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_accounts
                   SET enabled = TRUE, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s
                """,
                (self.context.workspace_id, account_id),
            )
            uow.commit()
        return self.repository_factory().snapshot(account_id)

    def archive_account(
        self,
        account_id: str,
        *,
        expected_revision: int,
    ) -> PaperAccountSnapshot:
        with self.uow_factory() as uow:
            account_row = uow.connection.execute(
                """
                SELECT base_currency, revision
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            if account_row is None or int(account_row[1]) != expected_revision:
                raise RevisionConflict(
                    f"Paper account expected revision {expected_revision}: {account_id}"
                )
            currency = str(account_row[0])
            self._archive_current_epoch(uow, account_id, "account_archived")
            self._disable_account_automation(uow, account_id, "account_archived")

            # Release reservations before cancelling the open orders so the
            # archived snapshot remains internally balanced and auditable.
            buy_reserved = uow.connection.execute(
                """
                SELECT COALESCE(SUM(reserved_cash), 0)
                  FROM omnix_trading_paper_orders
                 WHERE workspace_id = %s AND account_id = %s
                   AND status = 'open' AND side = 'buy'
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            reserved_cash = Decimal(buy_reserved[0]) if buy_reserved else Decimal("0")
            if reserved_cash > 0:
                uow.connection.execute(
                    """
                    UPDATE omnix_trading_paper_balances
                       SET available = available + %s,
                           reserved = GREATEST(0, reserved - %s),
                           updated_at = CURRENT_TIMESTAMP
                     WHERE workspace_id = %s AND account_id = %s AND currency = %s
                    """,
                    (
                        reserved_cash,
                        reserved_cash,
                        self.context.workspace_id,
                        account_id,
                        currency,
                    ),
                )
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_positions AS position
                   SET reserved_quantity = GREATEST(
                           0,
                           position.reserved_quantity - COALESCE(open_sell.remaining, 0)
                       ),
                       updated_at = CURRENT_TIMESTAMP
                  FROM (
                        SELECT instrument_id,
                               SUM(GREATEST(quantity - filled_quantity, 0)) AS remaining
                          FROM omnix_trading_paper_orders
                         WHERE workspace_id = %s AND account_id = %s
                           AND status = 'open' AND side = 'sell'
                         GROUP BY instrument_id
                       ) AS open_sell
                 WHERE position.workspace_id = %s
                   AND position.account_id = %s
                   AND position.instrument_id = open_sell.instrument_id
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    self.context.workspace_id,
                    account_id,
                ),
            )
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_orders
                   SET status = 'cancelled', reserved_cash = 0,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND status = 'open'
                """,
                (self.context.workspace_id, account_id),
            )
            uow.connection.execute(
                """
                UPDATE omnix_trading_paper_accounts
                   SET enabled = FALSE, revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s
                """,
                (self.context.workspace_id, account_id),
            )
            uow.commit()
        return self.repository_factory().snapshot(account_id)


PaperLifecycleFactory = Callable[[], TradingPaperLifecycle]


def default_paper_lifecycle() -> TradingPaperLifecycle:
    return TradingPaperLifecycle()
