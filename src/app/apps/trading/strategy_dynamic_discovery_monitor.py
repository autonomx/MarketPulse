from __future__ import annotations

from app.config.env import env_str as _env_str

from datetime import datetime, time, timezone

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .strategy_discovery_acquisition import (
    CausalMarketObservation,
    install_default_discovery_sources,
)
from .strategy_dynamic_discovery import (
    DynamicCandidate,
    INTERDAY_TRADING_STRATEGY_ID,
)
from .strategy_repository import TradingStrategyRepository
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from typing import Any

_STATE_KEY = "_omnix_interday_dynamic_discovery_monitor"


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def dynamic_discovery_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_DYNAMIC_DISCOVERY_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_DYNAMIC_DISCOVERY", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_DYNAMIC_DISCOVERY_INTERVAL_SECONDS", "300"))
    except ValueError:
        value = 300.0
    return max(60.0, value)


def _inside_discovery_window(now: datetime) -> bool:
    local = now.astimezone(_ET)
    if local.weekday() >= 5:
        return False
    clock = local.time().replace(tzinfo=None)
    return time(4, 0) <= clock <= time(16, 5)


async def run_dynamic_discovery_once(
    *,
    now: datetime | None = None,
    repository: TradingStrategyRepository | None = None,
    observations: tuple[CausalMarketObservation, ...] | None = None,
) -> tuple[DynamicCandidate, ...]:
    """Run the composed causal discovery scan and persist its evidence."""

    from .strategy_dynamic_discovery_quality import _run_dynamic_discovery_once_refined

    return await _run_dynamic_discovery_once_refined(
        now=now,
        repository=repository,
        observations=observations,
    )


class InterdayDynamicDiscoveryMonitor(ScheduledTradingMonitor):
    """Continuously rediscover market leadership without changing order authority."""

    error_event = "interday_dynamic_discovery_error"

    def error_log_fields(self) -> dict[str, Any]:
        return {
            "strategy_id": INTERDAY_TRADING_STRATEGY_ID,
            "observed_at": datetime.now(timezone.utc),
            "research_only": True,
            "execution_authority": False,
        }

    def __init__(self, *, interval_seconds: float | None = None) -> None:
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.candidate_count = 0

    async def run_once(self) -> int:
        candidates = await run_dynamic_discovery_once()
        self.candidate_count = len(candidates)
        self.last_run_at = datetime.now(timezone.utc)
        self.last_error = None
        return len(candidates)


def create_interday_dynamic_discovery_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, InterdayDynamicDiscoveryMonitor):
        return None
    install_default_discovery_sources()
    monitor = InterdayDynamicDiscoveryMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=dynamic_discovery_monitor_enabled)


__all__ = [
    "InterdayDynamicDiscoveryMonitor",
    "dynamic_discovery_monitor_enabled",
    "create_interday_dynamic_discovery_monitor_task",
    "run_dynamic_discovery_once",
]
