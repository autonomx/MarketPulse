from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.security.tenant_context import RequestTenant, TenantContext
from app.persistence.unit_of_work import unit_of_work

from .binding_authority import infer_binding_purpose
from .paper_protection import PaperPositionProtection, PaperProtectionStatus, PaperProtectionUpsert
from typing import Any, cast


_COLUMNS = """
    account_id, instrument_id, binding_id, binding_purpose, entry_order_id, exit_order_id,
    take_profit, stop_loss, status, trigger_reason, revision, created_at, updated_at,
    trail_amount, trail_percent, trail_water_mark, trail_moved_at
"""


# When an active leg's stop last moved (``trail_moved_at``; for any leg, not
# only a trailing one). Saving an active leg with a new stop, or making a leg
# active, stamps the database's wall clock (``clock_timestamp()``, not the
# transaction start), so a bar that began before the edit cannot trigger the
# new stop by its range. An unchanged stop on an active leg keeps its stamp.
_STOP_MOVED_AT = """
    CASE
        WHEN EXCLUDED.status <> 'active' THEN NULL
        WHEN omnix_trading_paper_protections.status <> 'active'
            OR omnix_trading_paper_protections.stop_loss IS DISTINCT FROM EXCLUDED.stop_loss
            THEN clock_timestamp()
        ELSE omnix_trading_paper_protections.trail_moved_at
    END
"""

# An edit of an active trailing leg that keeps its trail (a take-profit move,
# or the chart sending the levels back) keeps its water mark. A changed stop
# counts as a move of the trailed stop now; a stop looser than the trail
# implies is tightened back to it by the monitor's next update. Changing the
# trail itself, or re-arming, starts a fresh trail.
_KEEP_TRAIL = """
    omnix_trading_paper_protections.status = 'active' AND EXCLUDED.status = 'active'
    AND num_nonnulls(EXCLUDED.trail_amount, EXCLUDED.trail_percent) = 1
    AND omnix_trading_paper_protections.trail_amount IS NOT DISTINCT FROM EXCLUDED.trail_amount
    AND omnix_trading_paper_protections.trail_percent IS NOT DISTINCT FROM EXCLUDED.trail_percent
"""


def _protection(row) -> PaperPositionProtection:
    return PaperPositionProtection(
        account_id=str(row[0]),
        instrument_id=str(row[1]),
        binding_id=str(row[2]) if row[2] is not None else None,
        binding_purpose=cast(Any, str(row[3])),
        entry_order_id=str(row[4]) if row[4] is not None else None,
        exit_order_id=str(row[5]) if row[5] is not None else None,
        take_profit=Decimal(row[6]) if row[6] is not None else None,
        stop_loss=Decimal(row[7]) if row[7] is not None else None,
        status=cast(Any, str(row[8])),
        trigger_reason=str(row[9]) if row[9] is not None else None,
        revision=int(row[10]),
        created_at=row[11],
        updated_at=row[12],
        trail_amount=Decimal(row[13]) if row[13] is not None else None,
        trail_percent=Decimal(row[14]) if row[14] is not None else None,
        trail_water_mark=Decimal(row[15]) if row[15] is not None else None,
        trail_moved_at=row[16],
    )


class TradingPaperProtectionRepository:
    context = RequestTenant()
    def __init__(self, *, context: TenantContext | None = None, uow_factory=unit_of_work) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def list(
        self,
        account_id: str,
        *,
        active_only: bool = True,
    ) -> list[PaperPositionProtection]:
        where = "AND status IN ('pending_entry', 'active', 'exit_submitted')" if active_only else ""
        with self.uow_factory() as uow:
            account = uow.connection.execute(
                "SELECT 1 FROM omnix_trading_paper_accounts WHERE workspace_id = %s AND account_id = %s",
                (self.context.workspace_id, account_id),
            ).fetchone()
            if account is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            rows = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_paper_protections
                 WHERE workspace_id = %s AND account_id = %s {where}
                 ORDER BY updated_at DESC, instrument_id
                """,
                (self.context.workspace_id, account_id),
            ).fetchall()
        return [_protection(row) for row in rows]

    def get(
        self,
        account_id: str,
        instrument_id: str,
        *,
        include_inactive: bool = True,
    ) -> PaperPositionProtection:
        suffix = "" if include_inactive else "AND status IN ('pending_entry', 'active', 'exit_submitted')"
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_paper_protections
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s {suffix}
                """,
                (self.context.workspace_id, account_id, instrument_id),
            ).fetchone()
        if row is None:
            raise ValueError("paper_protection_not_found")
        return _protection(row)

    def arm_pending_entry(
        self,
        account_id: str,
        request: PaperProtectionUpsert,
    ) -> PaperPositionProtection:
        """Persist protection intent before the corresponding entry can execute.

        The entry order is deliberately allowed to be absent at this point. The
        paper monitor treats such a pending row as inert until the server order
        exists, which removes the fill-before-protection race while remaining
        fail-closed if the process stops between the two writes.
        """
        if not request.entry_order_id:
            raise ValueError("paper_protection_pending_entry_requires_order_id")
        with self.uow_factory() as uow:
            account = uow.connection.execute(
                """
                SELECT enabled
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            if account is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            if not bool(account[0]):
                raise ValueError(f"paper_account_disabled: {account_id}")

            existing = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_paper_protections
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, request.instrument_id),
            ).fetchone()
            if existing is not None and str(existing[8]) == "exit_submitted":
                raise ValueError("paper_protection_exit_already_submitted")

            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_paper_protections (
                    workspace_id, account_id, instrument_id, binding_id, binding_purpose,
                    entry_order_id, take_profit, stop_loss, status,
                    exit_order_id, trigger_reason, trail_amount, trail_percent,
                    trail_water_mark
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending_entry', NULL, 'entry_armed', %s, %s, NULL)
                ON CONFLICT (workspace_id, account_id, instrument_id) DO UPDATE
                   SET binding_id = EXCLUDED.binding_id,
                       binding_purpose = EXCLUDED.binding_purpose,
                       entry_order_id = EXCLUDED.entry_order_id,
                       take_profit = EXCLUDED.take_profit,
                       stop_loss = EXCLUDED.stop_loss,
                       trail_amount = EXCLUDED.trail_amount,
                       trail_percent = EXCLUDED.trail_percent,
                       trail_water_mark = NULL,
                       trail_moved_at = NULL,
                       status = 'pending_entry',
                       exit_order_id = NULL,
                       trigger_reason = 'entry_armed',
                       revision = omnix_trading_paper_protections.revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                RETURNING {_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    request.instrument_id,
                    request.binding_id,
                    infer_binding_purpose(request.binding_id),
                    request.entry_order_id,
                    request.take_profit,
                    request.stop_loss,
                    request.trail_amount,
                    request.trail_percent,
                ),
            ).fetchone()
            uow.commit()
        return _protection(row)

    def upsert(
        self,
        account_id: str,
        request: PaperProtectionUpsert,
    ) -> PaperPositionProtection:
        with self.uow_factory() as uow:
            account = uow.connection.execute(
                """
                SELECT enabled
                  FROM omnix_trading_paper_accounts
                 WHERE workspace_id = %s AND account_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id),
            ).fetchone()
            if account is None:
                raise ValueError(f"paper_account_not_found: {account_id}")
            if not bool(account[0]):
                raise ValueError(f"paper_account_disabled: {account_id}")

            existing = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_paper_protections
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, request.instrument_id),
            ).fetchone()
            if existing is not None and str(existing[8]) == "exit_submitted":
                raise ValueError("paper_protection_exit_already_submitted")

            position = uow.connection.execute(
                """
                SELECT quantity
                  FROM omnix_trading_paper_positions
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                """,
                (self.context.workspace_id, account_id, request.instrument_id),
            ).fetchone()
            has_position = position is not None and Decimal(position[0]) != 0

            entry_order_id = request.entry_order_id
            binding_id = request.binding_id
            entry = None
            if entry_order_id:
                entry = uow.connection.execute(
                    """
                    SELECT status, instrument_id, binding_id
                      FROM omnix_trading_paper_orders
                     WHERE workspace_id = %s AND account_id = %s AND order_id = %s
                    """,
                    (self.context.workspace_id, account_id, entry_order_id),
                ).fetchone()
                if entry is None:
                    raise ValueError("paper_protection_entry_order_not_found")
                if str(entry[1]) != request.instrument_id:
                    raise ValueError("paper_protection_entry_order_instrument_mismatch")
            elif not has_position:
                # The current UI can attach protection immediately after placing
                # an order. Infer that newest still-open entry so browser state is
                # never required for authority or recovery after a reload.
                entry = uow.connection.execute(
                    """
                    SELECT status, instrument_id, binding_id, order_id
                      FROM omnix_trading_paper_orders
                     WHERE workspace_id = %s AND account_id = %s
                       AND instrument_id = %s AND status = 'open'
                     ORDER BY created_at DESC, order_id DESC
                     LIMIT 1
                    """,
                    (self.context.workspace_id, account_id, request.instrument_id),
                ).fetchone()
                if entry is not None:
                    entry_order_id = str(entry[3])
            has_entry_order = entry is not None and str(entry[0]) in {"open", "filled"}
            if binding_id is None and entry is not None and entry[2] is not None:
                historical_binding = str(entry[2])
                if infer_binding_purpose(historical_binding) == "EXECUTION":
                    binding_id = historical_binding
                else:
                    # Historical data provenance does not become protection
                    # execution authority. A null binding lets the paper monitor
                    # resolve the current execution provider independently.
                    binding_id = None
            if not has_position and not has_entry_order:
                raise ValueError("paper_protection_requires_position_or_entry_order")

            status = "active" if has_position else "pending_entry"
            row = uow.connection.execute(
                f"""
                INSERT INTO omnix_trading_paper_protections (
                    workspace_id, account_id, instrument_id, binding_id, binding_purpose,
                    entry_order_id, take_profit, stop_loss, status,
                    exit_order_id, trigger_reason, trail_amount, trail_percent,
                    trail_water_mark, trail_moved_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, NULL,
                    CASE WHEN %s::text = 'active' THEN clock_timestamp() END
                )
                ON CONFLICT (workspace_id, account_id, instrument_id) DO UPDATE
                   SET binding_id = EXCLUDED.binding_id,
                       binding_purpose = EXCLUDED.binding_purpose,
                       entry_order_id = EXCLUDED.entry_order_id,
                       take_profit = EXCLUDED.take_profit,
                       stop_loss = EXCLUDED.stop_loss,
                       trail_amount = EXCLUDED.trail_amount,
                       trail_percent = EXCLUDED.trail_percent,
                       trail_water_mark = CASE WHEN {_KEEP_TRAIL}
                           THEN omnix_trading_paper_protections.trail_water_mark END,
                       trail_moved_at = {_STOP_MOVED_AT},
                       status = EXCLUDED.status,
                       exit_order_id = NULL,
                       trigger_reason = NULL,
                       revision = omnix_trading_paper_protections.revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                RETURNING {_COLUMNS}
                """,
                (
                    self.context.workspace_id,
                    account_id,
                    request.instrument_id,
                    binding_id,
                    infer_binding_purpose(binding_id),
                    entry_order_id,
                    request.take_profit,
                    request.stop_loss,
                    status,
                    request.trail_amount,
                    request.trail_percent,
                    status,
                ),
            ).fetchone()
            uow.commit()
        return _protection(row)

    def trail_stop(
        self,
        account_id: str,
        instrument_id: str,
        *,
        water_mark: Decimal,
        stop_loss: Decimal,
        expected_revision: int,
        moved_at: datetime | None = None,
    ) -> PaperPositionProtection | None:
        """Persist a trailing stop-loss leg's new water mark and stop.

        Only an active trailing leg at ``expected_revision`` moves, so a user's
        edit or a trigger in between wins. The water mark is monitor state: the
        revision and ``updated_at`` (the leg's activation evidence) are left as
        they are. ``moved_at`` records when the stop moved, so a bar that began
        earlier cannot trigger it by its range. Returns None when the leg
        changed underneath.
        """
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                UPDATE omnix_trading_paper_protections
                   SET trail_water_mark = %s, stop_loss = %s,
                       trail_moved_at = COALESCE(%s, trail_moved_at)
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                   AND status = 'active' AND revision = %s
                   AND (trail_amount IS NOT NULL OR trail_percent IS NOT NULL)
                RETURNING {_COLUMNS}
                """,
                (
                    water_mark,
                    stop_loss,
                    moved_at,
                    self.context.workspace_id,
                    account_id,
                    instrument_id,
                    expected_revision,
                ),
            ).fetchone()
            uow.commit()
        return _protection(row) if row is not None else None

    def clear(self, account_id: str, instrument_id: str) -> PaperPositionProtection:
        return self.transition(
            account_id,
            instrument_id,
            status="cancelled",
            exit_order_id=None,
            trigger_reason="user_cleared",
        )

    def transition(
        self,
        account_id: str,
        instrument_id: str,
        *,
        status: PaperProtectionStatus,
        exit_order_id: str | None,
        trigger_reason: str | None,
        expected_revision: int | None = None,
    ) -> PaperPositionProtection:
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                f"""
                SELECT {_COLUMNS}
                  FROM omnix_trading_paper_protections
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                 FOR UPDATE
                """,
                (self.context.workspace_id, account_id, instrument_id),
            ).fetchone()
            if row is None:
                raise ValueError("paper_protection_not_found")
            current = _protection(row)
            if expected_revision is not None and current.revision != expected_revision:
                raise ValueError("paper_protection_revision_conflict")
            updated = uow.connection.execute(
                f"""
                UPDATE omnix_trading_paper_protections
                   SET status = %s,
                       exit_order_id = %s,
                       trigger_reason = %s,
                       revision = revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE workspace_id = %s AND account_id = %s AND instrument_id = %s
                RETURNING {_COLUMNS}
                """,
                (
                    status,
                    exit_order_id,
                    trigger_reason,
                    self.context.workspace_id,
                    account_id,
                    instrument_id,
                ),
            ).fetchone()
            uow.commit()
        return _protection(updated)


def default_paper_protection_repository() -> TradingPaperProtectionRepository:
    return TradingPaperProtectionRepository()
