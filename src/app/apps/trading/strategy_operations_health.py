from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .execution import ExecutionObservation
from .paper import PaperAccountSnapshot
from .paper_protection import PaperPositionProtection
from .paper_risk import PaperRiskPolicy
from .strategy_repository import StrategyProtection, TradingStrategyConfigDocument
from .strategy_risk import paper_account_equity
from app.apps.trading.us_equity_calendar import EASTERN as _ET


HealthState = Literal["healthy", "degraded", "blocked", "unknown"]


class AccountRiskHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: HealthState
    reason_codes: tuple[str, ...] = ()
    account_id: str
    policy_source: Literal["active_strategy", "paper_default"]
    equity: Decimal
    buying_power: Decimal
    open_risk_dollars: Decimal
    open_risk_pct: Decimal
    max_open_risk_pct: Decimal
    daily_realized_pnl: Decimal
    daily_loss_limit_dollars: Decimal
    daily_loss_remaining: Decimal
    max_daily_loss_pct: Decimal
    unprotected_exposure_count: int
    position_count: int
    open_order_count: int
    active_protection_count: int


class ExecutionHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: HealthState
    reason_codes: tuple[str, ...] = ()
    instrument_id: str | None = None
    requested_binding_id: str | None = None
    resolved_binding_id: str | None = None
    provider: str | None = None
    policy_version: str | None = None
    execution_eligible: bool = False
    source_time: datetime | None = None
    observation_age_ms: Decimal | None = None
    spread_bps: Decimal | None = None
    freshness_mode: str | None = None
    session: str | None = None
    halted: bool | None = None


class TradingOperationalHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    state: HealthState
    reason_codes: tuple[str, ...] = ()
    risk: AccountRiskHealth
    execution: ExecutionHealth
    paper_only: Literal[True] = True
    live_broker_enabled: Literal[False] = False
    ai_order_placement_enabled: Literal[False] = False


def _strictest_limits(
    configs: list[TradingStrategyConfigDocument],
) -> tuple[Decimal, Decimal, Literal["active_strategy", "paper_default"]]:
    active = [config for config in configs if config.mode == "auto_paper" and config.enabled]
    if active:
        return (
            min(config.risk.max_open_risk_pct for config in active),
            min(config.risk.max_daily_loss_pct for config in active),
            "active_strategy",
        )
    policy = PaperRiskPolicy()
    return policy.max_open_risk_pct, policy.max_daily_loss_pct, "paper_default"


def _protection_stops(
    manual: list[PaperPositionProtection],
    strategy: list[StrategyProtection],
) -> dict[str, Decimal]:
    stops: dict[str, Decimal] = {}
    for item in manual:
        if item.status not in {"pending_entry", "active", "exit_submitted"} or item.stop_loss is None:
            continue
        previous = stops.get(item.instrument_id)
        stops[item.instrument_id] = item.stop_loss if previous is None else max(previous, item.stop_loss)
    for strategy_protection in strategy:
        if strategy_protection.status not in {"pending_entry", "active", "exit_submitted"}:
            continue
        previous = stops.get(strategy_protection.instrument_id)
        stops[strategy_protection.instrument_id] = strategy_protection.stop_price if previous is None else max(previous, strategy_protection.stop_price)
    return stops


def account_risk_health(
    *,
    snapshot: PaperAccountSnapshot,
    manual_protections: list[PaperPositionProtection],
    strategy_protections: list[StrategyProtection],
    strategy_configs: list[TradingStrategyConfigDocument],
    daily_realized_pnl: Decimal,
) -> AccountRiskHealth:
    equity = paper_account_equity(snapshot)
    balance = next(
        (item for item in snapshot.balances if item.currency == snapshot.account.base_currency),
        None,
    )
    buying_power = balance.available if balance is not None else Decimal("0")
    max_open_pct, max_daily_pct, policy_source = _strictest_limits(strategy_configs)
    stops = _protection_stops(manual_protections, strategy_protections)
    open_risk = Decimal("0")
    unprotected = 0

    for position in snapshot.positions:
        if position.quantity <= 0:
            continue
        stop = stops.get(position.instrument_id)
        if stop is None:
            unprotected += 1
            continue
        open_risk += max(Decimal("0"), position.average_cost - stop) * position.quantity

    for order in snapshot.open_orders:
        if order.side != "buy" or order.status != "open":
            continue
        remaining = max(Decimal("0"), order.quantity - order.filled_quantity)
        if remaining <= 0:
            continue
        entry_price = order.limit_price or order.stop_price or order.reference_price or order.average_fill_price
        stop = stops.get(order.instrument_id)
        if stop is None or entry_price is None:
            unprotected += 1
            continue
        open_risk += max(Decimal("0"), entry_price - stop) * remaining

    open_risk_pct = open_risk / equity * Decimal("100") if equity > 0 else Decimal("0")
    daily_limit = equity * max_daily_pct / Decimal("100") if equity > 0 else Decimal("0")
    daily_remaining = max(Decimal("0"), daily_limit + daily_realized_pnl)
    reasons: list[str] = []
    state: HealthState = "healthy"

    if equity <= 0:
        reasons.append("NON_POSITIVE_EQUITY")
        state = "blocked"
    if unprotected > 0:
        reasons.append("UNPROTECTED_OPEN_EXPOSURE")
        state = "blocked"
    if equity > 0 and open_risk_pct >= max_open_pct:
        reasons.append("OPEN_RISK_LIMIT")
        state = "blocked"
    if daily_limit > 0 and daily_realized_pnl <= -daily_limit:
        reasons.append("DAILY_LOSS_LIMIT")
        state = "blocked"

    if state != "blocked" and equity > 0:
        if open_risk_pct >= max_open_pct * Decimal("0.8"):
            reasons.append("OPEN_RISK_NEAR_LIMIT")
            state = "degraded"
        if daily_limit > 0 and daily_remaining <= daily_limit * Decimal("0.2"):
            reasons.append("DAILY_LOSS_NEAR_LIMIT")
            state = "degraded"

    return AccountRiskHealth(
        state=state,
        reason_codes=tuple(dict.fromkeys(reasons)),
        account_id=snapshot.account.account_id,
        policy_source=policy_source,
        equity=equity,
        buying_power=buying_power,
        open_risk_dollars=open_risk,
        open_risk_pct=open_risk_pct,
        max_open_risk_pct=max_open_pct,
        daily_realized_pnl=daily_realized_pnl,
        daily_loss_limit_dollars=daily_limit,
        daily_loss_remaining=daily_remaining,
        max_daily_loss_pct=max_daily_pct,
        unprotected_exposure_count=unprotected,
        position_count=sum(1 for item in snapshot.positions if item.quantity != 0),
        open_order_count=len(snapshot.open_orders),
        active_protection_count=len(manual_protections) + len(strategy_protections),
    )


def execution_health(
    observation: ExecutionObservation | None,
    *,
    instrument_id: str | None,
    requested_binding_id: str | None,
    error: str | None = None,
    observed_at: datetime | None = None,
) -> ExecutionHealth:
    now = observed_at or datetime.now(timezone.utc)
    if observation is None:
        if instrument_id is None:
            return ExecutionHealth(
                state="unknown",
                reason_codes=("INSTRUMENT_NOT_SELECTED",),
                instrument_id=None,
                requested_binding_id=requested_binding_id,
            )
        return ExecutionHealth(
            state="blocked",
            reason_codes=("EXECUTION_DATA_UNAVAILABLE",),
            instrument_id=instrument_id,
            requested_binding_id=requested_binding_id,
            freshness_mode=error,
        )

    age_ms = max(
        Decimal("0"),
        Decimal(str((now - observation.source_time.astimezone(timezone.utc)).total_seconds()))
        * Decimal("1000"),
    )
    reasons = list(observation.rejection_reasons)
    state: HealthState = "healthy" if observation.execution_eligible else "blocked"
    if observation.execution_eligible and observation.age_seconds >= Decimal("4"):
        reasons.append("EXECUTION_DATA_NEAR_STALE")
        state = "degraded"

    return ExecutionHealth(
        state=state,
        reason_codes=tuple(dict.fromkeys(reasons)),
        instrument_id=observation.instrument_id,
        requested_binding_id=requested_binding_id,
        resolved_binding_id=observation.binding_id,
        provider=observation.provider,
        policy_version=observation.policy_version,
        execution_eligible=observation.execution_eligible,
        source_time=observation.source_time,
        observation_age_ms=age_ms,
        spread_bps=observation.spread_bps,
        freshness_mode=observation.freshness_mode,
        session=observation.session,
        halted=observation.halted,
    )


def day_bounds_et(observed_at: datetime | None = None) -> tuple[datetime, datetime]:
    now_et = (observed_at or datetime.now(timezone.utc)).astimezone(_ET)
    start = datetime(now_et.year, now_et.month, now_et.day, tzinfo=_ET)
    return start, start + timedelta(days=1)


def operational_health(
    *,
    observed_at: datetime,
    risk: AccountRiskHealth,
    execution: ExecutionHealth,
) -> TradingOperationalHealth:
    rank = {"unknown": 0, "healthy": 1, "degraded": 2, "blocked": 3}
    states = [risk.state]
    if execution.instrument_id is not None:
        states.append(execution.state)
    state = max(states, key=lambda item: rank[item])
    reasons = tuple(dict.fromkeys([*risk.reason_codes, *execution.reason_codes]))
    return TradingOperationalHealth(
        observed_at=observed_at,
        state=state,
        reason_codes=reasons,
        risk=risk,
        execution=execution,
    )


__all__ = [
    "AccountRiskHealth",
    "ExecutionHealth",
    "TradingOperationalHealth",
    "account_risk_health",
    "day_bounds_et",
    "execution_health",
    "operational_health",
]
