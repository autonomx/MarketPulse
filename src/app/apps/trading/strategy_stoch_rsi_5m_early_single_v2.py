"""Research-only trade management and sizing arms for STOCH_RSI_5M_EARLY_SINGLE.

Every arm keeps the canonical early-single (cap150) entry. Unlike the earlier
loss-control research, nothing here vetoes a trade:

- ``profit_lock`` (arm A): once a finalized five-minute high has reached the
  lock trigger, a standing stop protects the entry, optionally trailing the
  highest high by a multiple of the causal five-minute ATR.
- ``partial_take`` (arm B): arm A plus a resting sell of part of the position
  at a fixed profit target.
- ``context_sizing`` (arm C): the canonical trade sized by entry context.
- ``lock_and_sizing`` (arm D): arms A and C together.

Trade management can only exit at or before the canonical exit; it never holds
a position longer. The research universe is labelled with end-of-day gains, so
a rule that held longer would inherit that lookahead. Stops and targets set
from a finalized bar act only on later bars. A bar that touches both the stop
and the target is assumed to hit the stop first. The module has no broker or
order side effects.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .indicators.engine import average_true_range
from .models import MarketBar
from .strategies.models import StochRsi5mConfig
from .strategy_stoch_rsi_5m import StochRsi5mTrade
from .strategy_stoch_rsi_5m_early_single import evaluate_stoch_rsi_5m_early_single
from app.apps.trading.us_equity_calendar import EASTERN as _ET


EarlySingleV2Arm = Literal[
    "baseline",
    "profit_lock",
    "partial_take",
    "context_sizing",
    "lock_and_sizing",
]

_HUNDRED = Decimal("100")
_ONE = Decimal("1")
CONTEXT_REDUCED_WEIGHT = Decimal("0.5")
CONTEXT_FALLING_FROM_OPEN_PCT = Decimal("-10")
CONTEXT_REDUCED_WINDOW = (time(10, 30), time(11, 0))


class ExitPolicy(BaseModel):
    """Profit-lock and partial-take parameters (percent values are 0-100)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lock_trigger_pct: Decimal = Field(default=Decimal("3"), gt=0)
    # None keeps a breakeven stop at entry without trailing.
    trail_atr_multiple: Decimal | None = Field(default=None, gt=0)
    atr_period: int = Field(default=14, ge=2, le=100)
    partial_target_pct: Decimal | None = Field(default=None, gt=0)
    partial_fraction: Decimal = Field(default=Decimal("1") / Decimal("3"), gt=0, lt=1)


class EarlySingleV2Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: EarlySingleV2Arm
    reason_code: str
    trade: StochRsi5mTrade | None = None
    # Position size as a fraction of one normal slot.
    size_weight: Decimal = _ONE
    size_reason: str | None = None
    partial_exit_time: datetime | None = None
    partial_exit_price: Decimal | None = None
    canonical_return_pct: Decimal | None = None
    execution_authority: Literal[False] = False


def _session_bars(bars_5m: list[MarketBar] | tuple[MarketBar, ...], trade: StochRsi5mTrade) -> list[MarketBar]:
    session_date = trade.entry_time.astimezone(_ET).date()
    return sorted(
        (
            bar
            for bar in bars_5m
            if bar.is_final
            and bar.session == "regular"
            and bar.interval == "5m"
            and bar.start_time.astimezone(_ET).date() == session_date
        ),
        key=lambda bar: bar.start_time,
    )


def _atr_by_end_time(bars: list[MarketBar], period: int) -> dict[datetime, Decimal]:
    values = average_true_range(
        [bar.high for bar in bars],
        [bar.low for bar in bars],
        [bar.close for bar in bars],
        period,
    )
    offset = len(bars) - len(values)
    return {bars[offset + index].end_time: value for index, value in enumerate(values)}


def manage_trade(
    bars_5m: list[MarketBar] | tuple[MarketBar, ...],
    trade: StochRsi5mTrade,
    policy: ExitPolicy,
) -> tuple[StochRsi5mTrade, datetime | None, Decimal | None]:
    """Apply the profit lock and optional partial take to one canonical trade.

    Returns the managed trade (``return_pct`` is the blended position return)
    plus the partial fill time and price when the target filled.
    """

    session = _session_bars(bars_5m, trade)
    atr = _atr_by_end_time(session, policy.atr_period) if policy.trail_atr_multiple else {}
    entry = trade.entry_price
    lock_price = entry * (_ONE + policy.lock_trigger_pct / _HUNDRED)
    target = (
        entry * (_ONE + policy.partial_target_pct / _HUNDRED)
        if policy.partial_target_pct is not None
        else None
    )

    stop: Decimal | None = None
    highest = entry
    partial_time: datetime | None = None
    partial_price: Decimal | None = None
    exit_time, exit_price = trade.exit_time, trade.exit_price
    exit_signal_time, reason = trade.exit_signal_time, trade.exit_reason_code

    for bar in session:
        if bar.start_time < trade.entry_time:
            continue
        if bar.start_time >= trade.exit_time:
            break
        # Orders resting from earlier finalized bars act inside this bar.
        if stop is not None and bar.low <= stop:
            exit_time = bar.start_time
            exit_price = min(bar.open, stop)
            exit_signal_time = bar.start_time
            reason = (
                "STOCH_RSI_5M_EARLY_SINGLE_V2_TRAILING_STOP"
                if stop > entry
                else "STOCH_RSI_5M_EARLY_SINGLE_V2_BREAKEVEN_STOP"
            )
            break
        if target is not None and partial_time is None and bar.high >= target:
            partial_time = bar.start_time
            partial_price = max(bar.open, target)
        # The finalized bar then updates the lock and trail for later bars.
        highest = max(highest, bar.high)
        if highest >= lock_price:
            candidate = entry
            if policy.trail_atr_multiple is not None and bar.end_time in atr:
                candidate = max(candidate, highest - atr[bar.end_time] * policy.trail_atr_multiple)
            stop = candidate if stop is None else max(stop, candidate)

    remainder = (exit_price - entry) / entry * _HUNDRED
    if partial_price is not None:
        fraction = policy.partial_fraction
        partial_return = (partial_price - entry) / entry * _HUNDRED
        blended = partial_return * fraction + remainder * (_ONE - fraction)
    else:
        blended = remainder
    managed = trade.model_copy(
        update={
            "exit_signal_time": exit_signal_time,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "exit_reason_code": reason,
            "return_pct": blended,
        }
    )
    return managed, partial_time, partial_price


def context_size_weight(
    bars_5m: list[MarketBar] | tuple[MarketBar, ...],
    trade: StochRsi5mTrade,
) -> tuple[Decimal, str]:
    """Size from facts known at entry: time of day and move from the session open."""

    session = _session_bars(bars_5m, trade)
    if not session:
        return _ONE, "CONTEXT_FULL_NO_SESSION_OPEN"
    session_open = session[0].open
    move_pct = (trade.entry_price / session_open - _ONE) * _HUNDRED
    if move_pct <= CONTEXT_FALLING_FROM_OPEN_PCT:
        return CONTEXT_REDUCED_WEIGHT, "CONTEXT_HALF_ENTRY_10PCT_BELOW_OPEN"
    start, end = CONTEXT_REDUCED_WINDOW
    if start <= trade.entry_time.astimezone(_ET).time() < end:
        return CONTEXT_REDUCED_WEIGHT, "CONTEXT_HALF_ENTRY_1030_1100"
    return _ONE, "CONTEXT_FULL"


def evaluate_stoch_rsi_5m_early_single_v2(
    bars_5m: list[MarketBar] | tuple[MarketBar, ...],
    arm: EarlySingleV2Arm,
    *,
    policy: ExitPolicy | None = None,
    config: StochRsi5mConfig | None = None,
) -> EarlySingleV2Result:
    snapshot = evaluate_stoch_rsi_5m_early_single(bars_5m, config)
    if not snapshot.trades:
        return EarlySingleV2Result(arm=arm, reason_code=snapshot.reason_code)
    canonical = snapshot.trades[0]
    trade, partial_time, partial_price = canonical, None, None
    if arm in {"profit_lock", "partial_take", "lock_and_sizing"}:
        active = policy or ExitPolicy()
        if arm != "partial_take":
            active = active.model_copy(update={"partial_target_pct": None})
        elif active.partial_target_pct is None:
            raise ValueError("partial_take requires partial_target_pct")
        trade, partial_time, partial_price = manage_trade(bars_5m, canonical, active)
    weight, size_reason = _ONE, None
    if arm in {"context_sizing", "lock_and_sizing"}:
        weight, size_reason = context_size_weight(bars_5m, canonical)
    return EarlySingleV2Result(
        arm=arm,
        reason_code=trade.exit_reason_code,
        trade=trade,
        size_weight=weight,
        size_reason=size_reason,
        partial_exit_time=partial_time,
        partial_exit_price=partial_price,
        canonical_return_pct=canonical.return_pct,
    )


__all__ = [
    "CONTEXT_REDUCED_WEIGHT",
    "EarlySingleV2Arm",
    "EarlySingleV2Result",
    "ExitPolicy",
    "context_size_weight",
    "evaluate_stoch_rsi_5m_early_single_v2",
    "manage_trade",
]
