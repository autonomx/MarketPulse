from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_v2_management import (
    v2_active_stop_for_prior_high,
    v2_hold_expired,
    v2_initial_stop_from_target,
    v2_management_levels,
)


def config(**updates) -> GapPullbackConfig:
    payload = {
        "strategy_version": "2.0.0",
        "structure_interval": "1m",
        "execution_interval": "1m",
        "reward_multiple": Decimal("1.5"),
        "v2_profit_protection_trigger_r": Decimal("0.75"),
        "v2_protected_stop_r": Decimal("0.25"),
        "v2_max_hold_minutes": 60,
    }
    payload.update(updates)
    return GapPullbackConfig(**payload)


def test_v2_management_levels_are_fill_anchored() -> None:
    levels = v2_management_levels(
        config(),
        entry_price=Decimal("10"),
        initial_stop=Decimal("9"),
    )
    assert levels.initial_risk == Decimal("1")
    assert levels.target_price == Decimal("11.5")
    assert levels.trigger_price == Decimal("10.75")
    assert levels.protected_stop == Decimal("10.25")
    assert levels.max_hold_minutes == 60


def test_v2_protected_stop_uses_only_prior_finalized_high() -> None:
    active = config()
    assert v2_active_stop_for_prior_high(
        active,
        entry_price=Decimal("10"),
        initial_stop=Decimal("9"),
        prior_finalized_high=Decimal("10.7499"),
    ) == Decimal("9")
    assert v2_active_stop_for_prior_high(
        active,
        entry_price=Decimal("10"),
        initial_stop=Decimal("9"),
        prior_finalized_high=Decimal("10.75"),
    ) == Decimal("10.25")


def test_v2_initial_stop_can_be_recovered_after_persisted_stop_moves() -> None:
    assert v2_initial_stop_from_target(
        config(),
        entry_price=Decimal("10"),
        target_price=Decimal("11.5"),
    ) == Decimal("9")


def test_v2_max_hold_is_exact_and_timezone_aware() -> None:
    start = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    assert not v2_hold_expired(config(), activated_at=start, observed_at=start + timedelta(minutes=59, seconds=59))
    assert v2_hold_expired(config(), activated_at=start, observed_at=start + timedelta(minutes=60))


def test_v2_rejects_protected_stop_at_or_above_trigger() -> None:
    with pytest.raises(ValueError, match="protected stop R"):
        config(v2_profit_protection_trigger_r=Decimal("0.75"), v2_protected_stop_r=Decimal("0.75"))
