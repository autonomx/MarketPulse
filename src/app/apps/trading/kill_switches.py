"""Trading kill switches (WP-8.3).

A kill switch stops paper orders that open or add exposure; the order gateway
checks the engaged switches for the workspace, the order's account and its
strategy in the same transaction that writes the order. Orders that only
reduce a position are never blocked, so protective exits keep working.

Switches change at runtime through ``PUT /api/trading/kill-switches``, which
needs the ``trading:control`` permission and is audited as
``trading.control.update``.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.persistence.unit_of_work import PostgresUnitOfWork, unit_of_work
from app.security.tenant_context import RequestTenant, TenantContext

KillSwitchScope = Literal["global", "account", "strategy"]


class KillSwitchChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: KillSwitchScope
    scope_id: str = Field(default="", max_length=200)
    engaged: bool
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def scope_id_matches_scope(self):
        if (self.scope == "global") != (self.scope_id == ""):
            raise ValueError("a global kill switch has no scope_id; account and strategy switches need one")
        return self


class TradingKillSwitch(KillSwitchChange):
    updated_by: str | None = None
    revision: int = 1
    updated_at: datetime | None = None


class TradingKillSwitchRepository:
    context = RequestTenant()

    def __init__(
        self,
        *,
        context: TenantContext | None = None,
        uow_factory: Callable[[], AbstractContextManager[PostgresUnitOfWork]] = unit_of_work,
    ) -> None:
        self.context = context
        self.uow_factory = uow_factory

    def list(self) -> list[TradingKillSwitch]:
        with self.uow_factory() as uow:
            rows = uow.connection.execute(
                """
                SELECT scope, scope_id, engaged, reason, updated_by, revision, updated_at
                  FROM omnix_trading_kill_switches
                 WHERE workspace_id = %s
                 ORDER BY scope, scope_id
                """,
                (self.context.workspace_id,),
            ).fetchall()
        return [_switch(row) for row in rows]

    def set(self, scope: KillSwitchScope, scope_id: str, *, engaged: bool, reason: str = "") -> TradingKillSwitch:
        """Engage or release one switch; the revision counts every change."""
        switch = TradingKillSwitch(scope=scope, scope_id=scope_id, engaged=engaged, reason=reason)
        with self.uow_factory() as uow:
            row = uow.connection.execute(
                """
                INSERT INTO omnix_trading_kill_switches (
                    workspace_id, scope, scope_id, engaged, reason, updated_by
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, scope, scope_id) DO UPDATE
                   SET engaged = EXCLUDED.engaged,
                       reason = EXCLUDED.reason,
                       updated_by = EXCLUDED.updated_by,
                       revision = omnix_trading_kill_switches.revision + 1,
                       updated_at = CURRENT_TIMESTAMP
                RETURNING scope, scope_id, engaged, reason, updated_by, revision, updated_at
                """,
                (
                    self.context.workspace_id,
                    switch.scope,
                    switch.scope_id,
                    switch.engaged,
                    switch.reason,
                    self.context.user_id,
                ),
            ).fetchone()
            uow.commit()
        return _switch(row)


def _switch(row) -> TradingKillSwitch:
    return TradingKillSwitch(
        scope=cast(Any, str(row[0])),
        scope_id=str(row[1]),
        engaged=bool(row[2]),
        reason=str(row[3]),
        updated_by=str(row[4]) if row[4] is not None else None,
        revision=int(row[5]),
        updated_at=row[6],
    )


def create_trading_kill_switch_router(
    repository_factory: Callable[[], TradingKillSwitchRepository] = TradingKillSwitchRepository,
):
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/trading/kill-switches", tags=["trading-kill-switches"])

    @router.get("", response_model=list[TradingKillSwitch])
    def list_kill_switches() -> list[TradingKillSwitch]:
        return repository_factory().list()

    @router.put("", response_model=TradingKillSwitch)
    def set_kill_switch(change: KillSwitchChange) -> TradingKillSwitch:
        """Engage or release a switch; takes effect for the next order, without a restart."""
        return repository_factory().set(change.scope, change.scope_id, engaged=change.engaged, reason=change.reason)

    return router
