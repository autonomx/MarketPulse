from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .strategies.models import GapPullbackConfig


@dataclass(frozen=True)
class V2ManagementLevels:
    entry_price: Decimal
    initial_stop: Decimal
    initial_risk: Decimal
    target_price: Decimal
    trigger_price: Decimal | None
    protected_stop: Decimal
    max_hold_minutes: int


def v2_management_levels(
    config: GapPullbackConfig,
    *,
    entry_price: Decimal,
    initial_stop: Decimal,
) -> V2ManagementLevels:
    """Return fill-anchored V2 management levels.

    The historical V11 harness anchored R to the actual pessimistic paper fill,
    not to the breakout close. Production and standard backtests share this
    exact calculation.
    """
    risk = entry_price - initial_stop
    if risk <= 0:
        raise ValueError("v2 management requires positive initial risk")
    trigger_r = config.v2_profit_protection_trigger_r
    trigger_price = None if trigger_r is None else entry_price + risk * trigger_r
    protected_stop = max(initial_stop, entry_price + risk * config.v2_protected_stop_r)
    return V2ManagementLevels(
        entry_price=entry_price,
        initial_stop=initial_stop,
        initial_risk=risk,
        target_price=entry_price + risk * config.reward_multiple,
        trigger_price=trigger_price,
        protected_stop=protected_stop,
        max_hold_minutes=config.v2_max_hold_minutes,
    )


def v2_initial_stop_from_target(
    config: GapPullbackConfig,
    *,
    entry_price: Decimal,
    target_price: Decimal,
) -> Decimal:
    """Recover the immutable initial structural stop after the persisted stop moves."""
    if config.reward_multiple <= 0:
        raise ValueError("reward multiple must be positive")
    initial_risk = (target_price - entry_price) / config.reward_multiple
    if initial_risk <= 0:
        raise ValueError("v2 persisted target must be above entry")
    return entry_price - initial_risk


def v2_active_stop_for_prior_high(
    config: GapPullbackConfig,
    *,
    entry_price: Decimal,
    initial_stop: Decimal,
    prior_finalized_high: Decimal,
) -> Decimal:
    """Select the stop for the current bar from information finalized beforehand.

    A bar that first reaches +trigger R cannot tighten the stop for itself. Its
    high becomes eligible only after that bar is finalized, so the returned stop
    applies to a later bar/observation. This is the conservative V4/V11 rule.
    """
    levels = v2_management_levels(
        config,
        entry_price=entry_price,
        initial_stop=initial_stop,
    )
    if levels.trigger_price is None or prior_finalized_high < levels.trigger_price:
        return initial_stop
    return levels.protected_stop


def v2_hold_expired(
    config: GapPullbackConfig,
    *,
    activated_at: datetime,
    observed_at: datetime,
) -> bool:
    if activated_at.tzinfo is None or observed_at.tzinfo is None:
        raise ValueError("v2 hold timestamps must be timezone-aware")
    return observed_at >= activated_at + timedelta(minutes=config.v2_max_hold_minutes)
