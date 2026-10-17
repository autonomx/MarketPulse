"""Background quote capture for causal shadow execution simulation."""

from __future__ import annotations

from app.config.env import environment

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.runtime.model_executor import ModelExecutor
from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .binding_authority import binding_can_execute
from .execution_observation_plane import (
    ExecutionObservationPlane,
    default_execution_observation_plane,
)
from .service import TradingMarketDataService, default_market_data_service
from .strategy_managed_finviz_shadow import MANAGED_FINVIZ_SHADOW_STRATEGY_ID
from .strategy_repository import (
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import resolve_v2_shadow_archive
from .trade_logging import trade_log
from .us_equity_calendar import regular_session_settled


_STATE_KEY = "_omnix_trading_execution_observation_monitor"


def _flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def execution_observation_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_EXECUTION_OBSERVATION_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_EXECUTION_OBSERVATION_MONITOR", "1")


def _observation_workers() -> int:
    try:
        value = int(environment().get("OMNIX_TRADING_EXECUTION_OBSERVATION_WORKERS", "8"))
    except ValueError:
        value = 8
    return max(1, min(64, value))


def _interval_seconds() -> float:
    try:
        value = float(environment().get("OMNIX_TRADING_EXECUTION_OBSERVATION_INTERVAL_SECONDS", "3"))
    except ValueError:
        value = 3.0
    return max(0.25, value)


class TradingExecutionObservationMonitor(ScheduledTradingMonitor):
    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        plane: ExecutionObservationPlane | None = None,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.plane = plane or default_execution_observation_plane()
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.capture_count = 0
        self.capture_error_count = 0
        self.skipped_non_execution_binding_count = 0
        self.backoff_skip_count = 0
        self._consecutive_failures: dict[str, int] = {}
        self._next_capture_at: dict[str, datetime] = {}
        # Quote captures run on their own bounded pool (WP-7.5): a large
        # universe no longer floods the event loop's shared default executor.
        self._capture_pool = ModelExecutor("trading-observation", _observation_workers())

    async def _capture_one(self, market_service, candidate, *, now: datetime):
        if not binding_can_execute(candidate.binding_id):
            self.skipped_non_execution_binding_count += 1
            return None
        instrument_id = candidate.instrument_id
        next_capture = self._next_capture_at.get(instrument_id)
        if next_capture is not None and now < next_capture:
            self.backoff_skip_count += 1
            return None
        try:
            observation = await self._capture_pool.run(
                market_service.execution_observation,
                instrument_id,
                candidate.binding_id,
            )
        except Exception as exc:
            self.capture_error_count += 1
            failures = self._consecutive_failures.get(instrument_id, 0) + 1
            self._consecutive_failures[instrument_id] = failures
            cooldown_seconds = min(60, 3 * (2 ** min(failures - 1, 4)))
            self._next_capture_at[instrument_id] = now + timedelta(
                seconds=cooldown_seconds
            )
            self.last_error = f"{instrument_id}: {type(exc).__name__}: {exc}"
            trade_log(
                "auto_trading",
                "execution_observation_capture_error",
                instrument_id=instrument_id,
                binding_id=candidate.binding_id,
                error_type=type(exc).__name__,
                detail=str(exc),
                retry_after_seconds=cooldown_seconds,
                execution_authority=False,
            )
            return None
        self._consecutive_failures.pop(instrument_id, None)
        self._next_capture_at.pop(instrument_id, None)
        recorded_at = self.now_factory()
        if recorded_at.tzinfo is None:
            raise ValueError("execution observation receipt clock must be timezone-aware")
        recorded_at = recorded_at.astimezone(timezone.utc)
        if self.plane.record(observation, recorded_at=recorded_at):
            self.capture_count += 1
        return observation

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("execution observation monitor clock must be timezone-aware")
        if regular_session_settled(now):
            # No shadow entry can become actionable after the session settles.
            self.last_run_at = now
            return 0
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        config = next(
            (
                item
                for item in configs
                if item.strategy_id == MANAGED_FINVIZ_SHADOW_STRATEGY_ID
                and item.mode == "shadow"
            ),
            None,
        )
        if config is None:
            self.last_run_at = now
            return 0
        try:
            universe = await asyncio.to_thread(
                resolve_v2_shadow_archive,
                config,
                repository,
                now=now,
            )
        except Exception as exc:
            self.last_error = f"universe: {type(exc).__name__}: {exc}"
            self.last_run_at = now
            return 0
        if universe is None:
            self.last_run_at = now
            return 0

        self._forget_instruments_outside({candidate.instrument_id for candidate in universe.candidates})
        before = self.capture_count
        await asyncio.gather(
            *[
                self._capture_one(market_service, candidate, now=now)
                for candidate in universe.candidates
            ]
        )
        self.last_run_at = now
        return self.capture_count - before

    def _forget_instruments_outside(self, instrument_ids: set[str]) -> None:
        """Backoff state is kept only for the current universe (WP-7.5, TR9)."""
        for state in (self._consecutive_failures, self._next_capture_at):
            for instrument_id in [key for key in state if key not in instrument_ids]:
                del state[instrument_id]

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": execution_observation_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "capture_count": self.capture_count,
            "capture_error_count": self.capture_error_count,
            "backoff_skip_count": self.backoff_skip_count,
            "active_backoff_count": len(self._next_capture_at),
            "skipped_non_execution_binding_count": self.skipped_non_execution_binding_count,
            "causal_fill_policy": "first_source_and_recorded_quote_after_actionable_at",
        }


def create_trading_execution_observation_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingExecutionObservationMonitor):
        return None
    monitor = TradingExecutionObservationMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=execution_observation_monitor_enabled)


__all__ = [
    "TradingExecutionObservationMonitor",
    "execution_observation_monitor_enabled",
    "create_trading_execution_observation_monitor_task",
]
