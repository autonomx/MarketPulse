from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

from pydantic import BaseModel, ConfigDict

from .paper import PaperAccountSnapshot
from .strategies.models import StrategyRiskProfile, StrategySignal
from app.apps.trading.us_equity_calendar import EASTERN as _ET


class StrategyRiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason_code: str
    quantity: Decimal = Decimal("0")
    account_equity: Decimal = Decimal("0")
    estimated_risk: Decimal = Decimal("0")
    estimated_notional: Decimal = Decimal("0")


def paper_account_equity(snapshot: PaperAccountSnapshot) -> Decimal:
    cash = sum((item.available + item.reserved for item in snapshot.balances), Decimal("0"))
    mark_value = sum(
        (
            item.quantity * (item.last_price if item.last_price is not None else item.average_cost)
            for item in snapshot.positions
        ),
        Decimal("0"),
    )
    return cash + mark_value


def paper_daily_realized_pnl(
    snapshot: PaperAccountSnapshot,
    *,
    observed_at: datetime | None = None,
) -> Decimal:
    """Net today's realized PnL and commissions from the snapshot ledger window.

    Live automation passes a complete relational daily aggregate when available;
    this helper keeps pure tests and detached callers deterministic.
    """
    now = (observed_at or datetime.now(timezone.utc)).astimezone(_ET)
    total = Decimal("0")
    for entry in snapshot.recent_ledger:
        if entry.entry_type not in {"realized_pnl", "commission"}:
            continue
        if entry.created_at is None:
            continue
        if entry.created_at.astimezone(_ET).date() != now.date():
            continue
        total += entry.amount
    return total


def size_strategy_entry(
    snapshot: PaperAccountSnapshot,
    signal: StrategySignal,
    risk: StrategyRiskProfile,
    *,
    spread_bps: Decimal | None,
    trades_today: int = 0,
    traded_symbols_today: set[str] | None = None,
    reserved_instruments: set[str] | None = None,
    daily_realized_pnl: Decimal | None = None,
    open_strategy_risk: Decimal = Decimal("0"),
    observed_at: datetime | None = None,
) -> StrategyRiskDecision:
    equity = paper_account_equity(snapshot)
    now_et = (observed_at or datetime.now(timezone.utc)).astimezone(_ET)
    if risk.kill_switch:
        return StrategyRiskDecision(allowed=False, reason_code="KILL_SWITCH", account_equity=equity)
    if now_et.time() < risk.entry_start_et:
        return StrategyRiskDecision(allowed=False, reason_code="ENTRY_WINDOW_NOT_OPEN", account_equity=equity)
    if now_et.time() > risk.last_entry_et or now_et.time() >= risk.force_flat_et:
        return StrategyRiskDecision(allowed=False, reason_code="ENTRY_WINDOW_CLOSED", account_equity=equity)
    if equity <= 0:
        return StrategyRiskDecision(allowed=False, reason_code="NO_ACCOUNT_EQUITY", account_equity=equity)
    if spread_bps is None:
        return StrategyRiskDecision(allowed=False, reason_code="SPREAD_MISSING", account_equity=equity)
    if spread_bps > risk.max_spread_bps:
        return StrategyRiskDecision(allowed=False, reason_code="SPREAD_TOO_WIDE", account_equity=equity)

    active_instruments = {
        position.instrument_id for position in snapshot.positions if position.quantity != 0
    }
    active_instruments.update(
        order.instrument_id
        for order in snapshot.open_orders
        if order.status == "open" and order.side == "buy"
    )
    if reserved_instruments:
        active_instruments.update(reserved_instruments)
    if signal.instrument_id not in active_instruments and len(active_instruments) >= risk.max_positions:
        return StrategyRiskDecision(allowed=False, reason_code="MAX_POSITIONS", account_equity=equity)
    if trades_today >= risk.max_trades_per_day:
        return StrategyRiskDecision(allowed=False, reason_code="MAX_TRADES_PER_DAY", account_equity=equity)
    if (
        risk.one_trade_per_symbol_per_day
        and traded_symbols_today is not None
        and signal.instrument_id in traded_symbols_today
    ):
        return StrategyRiskDecision(allowed=False, reason_code="SYMBOL_ALREADY_TRADED", account_equity=equity)

    realized_today = (
        paper_daily_realized_pnl(snapshot, observed_at=now_et)
        if daily_realized_pnl is None
        else daily_realized_pnl
    )
    max_daily_loss = equity * risk.max_daily_loss_pct / Decimal("100")
    if realized_today <= -max_daily_loss:
        return StrategyRiskDecision(allowed=False, reason_code="MAX_DAILY_LOSS", account_equity=equity)
    max_open_risk = equity * risk.max_open_risk_pct / Decimal("100")
    if open_strategy_risk >= max_open_risk:
        return StrategyRiskDecision(allowed=False, reason_code="MAX_OPEN_RISK", account_equity=equity)

    risk_budget = equity * risk.risk_per_trade_pct / Decimal("100")
    if signal.risk_per_share <= 0:
        return StrategyRiskDecision(allowed=False, reason_code="INVALID_RISK_DISTANCE", account_equity=equity)
    by_risk = risk_budget / signal.risk_per_share
    by_notional = risk.max_trade_value / signal.entry_price
    quantity = min(by_risk, by_notional).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if quantity <= 0:
        return StrategyRiskDecision(allowed=False, reason_code="ZERO_SIZED_TRADE", account_equity=equity)
    estimated_risk = quantity * signal.risk_per_share
    estimated_notional = quantity * signal.entry_price
    if open_strategy_risk + estimated_risk > max_open_risk:
        remaining_risk = max(Decimal("0"), max_open_risk - open_strategy_risk)
        quantity = min(quantity, remaining_risk / signal.risk_per_share).quantize(
            Decimal("0.000001"), rounding=ROUND_DOWN
        )
        estimated_risk = quantity * signal.risk_per_share
        estimated_notional = quantity * signal.entry_price
    if quantity <= 0:
        return StrategyRiskDecision(allowed=False, reason_code="OPEN_RISK_EXHAUSTED", account_equity=equity)
    return StrategyRiskDecision(
        allowed=True,
        reason_code="RISK_ACCEPTED",
        quantity=quantity,
        account_equity=equity,
        estimated_risk=estimated_risk,
        estimated_notional=estimated_notional,
    )
