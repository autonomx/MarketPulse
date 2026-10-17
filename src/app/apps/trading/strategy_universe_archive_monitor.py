from __future__ import annotations

from app.config.env import environment

import asyncio
from datetime import datetime, timezone

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .strategy_repository import TradingStrategyRepository, default_strategy_repository
from .strategy_universe_archiver import archive_daily_universe_if_due
from .trade_logging import trade_log


_STATE_KEY = "_omnix_trading_strategy_universe_archive_monitor"


def _flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def strategy_universe_archive_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_UNIVERSE_ARCHIVER_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_UNIVERSE_ARCHIVER", "1")


def _interval_seconds() -> float:
    try:
        value = float(environment().get("OMNIX_TRADING_UNIVERSE_ARCHIVER_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(5.0, value)


class TradingStrategyUniverseArchiveMonitor(ScheduledTradingMonitor):
    """Evidence-only morning scanner archive; never changes execution authority."""

    error_event = "daily_universe_archive_monitor_error"

    def __init__(self, *, interval_seconds: float | None = None) -> None:
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.archive_count = 0

    async def run_once(self, *, allow_late_recovery: bool = False) -> int:
        repository: TradingStrategyRepository = default_strategy_repository()
        # Archive research evidence even while a saved strategy is Off. Execution
        # mode is irrelevant here; the per-strategy archiver still requires the
        # strategy itself to be enabled and auto-archive configured.
        configs = await asyncio.to_thread(repository.list_configs, active_only=False)
        archived = 0
        now = datetime.now(timezone.utc)
        for config in configs:
            try:
                snapshot = await asyncio.to_thread(
                    archive_daily_universe_if_due,
                    config,
                    repository,
                    now=now,
                    allow_late_recovery=allow_late_recovery,
                )
                if snapshot is not None:
                    archived += 1
            except Exception as exc:
                self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "daily_universe_archive_error",
                    strategy_id=config.strategy_id,
                    observed_at=now,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
        self.archive_count += archived
        self.last_run_at = datetime.now(timezone.utc)
        return archived

    async def prepare_for_scheduled_execution(self) -> None:
        """Run startup-only late recovery before periodic archive passes."""
        if not strategy_universe_archive_monitor_enabled():
            return
        try:
            recovered = await self.run_once(allow_late_recovery=True)
            trade_log(
                "auto_trading",
                "daily_universe_archive_startup_reconciliation",
                observed_at=datetime.now(timezone.utc),
                archive_count=recovered,
                execution_authority=False,
            )
        except Exception as exc:
            # Reconciliation is best-effort. A provider/database problem at boot
            # must not prevent the normal periodic monitor from starting.
            self.last_error = f"startup_reconciliation: {type(exc).__name__}: {exc}"
            trade_log(
                "auto_trading",
                "daily_universe_archive_startup_reconciliation_error",
                observed_at=datetime.now(timezone.utc),
                error_type=type(exc).__name__,
                detail=str(exc),
                execution_authority=False,
            )


def create_trading_strategy_universe_archive_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingStrategyUniverseArchiveMonitor):
        return None
    monitor = TradingStrategyUniverseArchiveMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=strategy_universe_archive_monitor_enabled)


__all__ = [
    "TradingStrategyUniverseArchiveMonitor",
    "create_trading_strategy_universe_archive_monitor_task",
    "strategy_universe_archive_monitor_enabled",
]
