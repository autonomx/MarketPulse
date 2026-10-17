"""Every trading strategy kind Omnix knows, in one reviewed list (WP-8.3).

Adding a strategy is one entry here plus its module; see
docs/trading/STRATEGY_RECIPE.md. The first entry is the default kind.
"""
from __future__ import annotations

from .models import GapPullbackConfig, OvernightShadowConfig, StochRsi5mConfig
from .registry import MonitorOwnedStrategy, StrategyRegistry

STRATEGY_REGISTRY = StrategyRegistry((
    MonitorOwnedStrategy(
        kind="gap_pullback_v1",
        config_model=GapPullbackConfig,
        allowed_modes=("off", "shadow", "auto_paper"),
        owner="strategy_monitor",
    ),
    MonitorOwnedStrategy(
        kind="stoch_rsi_5m_v1",
        config_model=StochRsi5mConfig,
        allowed_modes=("off", "shadow"),
        owner="strategy_monitor",
        mode_rejection="stoch_rsi_5m_is_shadow_only",
    ),
    MonitorOwnedStrategy(
        kind="overnight_shadow_v1",
        config_model=OvernightShadowConfig,
        allowed_modes=("off", "shadow"),
        owner="overnight_shadow_monitor",
        mode_rejection="overnight_shadow_is_shadow_only",
    ),
))
