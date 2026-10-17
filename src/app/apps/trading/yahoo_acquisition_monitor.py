"""Proactive Yahoo 1m acquisition for active trading universes.

Strategies should normally read a locally accumulated factual tape. This monitor
captures Yahoo bars independently of strategy evaluation so transient provider
failures later in the session do not erase already-observed evidence.
"""

from __future__ import annotations

from app.config.env import environment

import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .service import TradingMarketDataService, default_market_data_service
from .strategy_dynamic_discovery import CandidateLifecycleState
from .strategy_dynamic_discovery_repository import DynamicDiscoveryEventRepository
from .strategy_repository import TradingStrategyRepository, default_strategy_repository
from app.apps.trading.us_equity_calendar import EASTERN as _ET


_STATE_KEY = "_omnix_trading_yahoo_acquisition_monitor"
_SESSION_OPEN = time(4, 0)
_SESSION_CLOSE = time(16, 5)


def _flag(name: str, default: str = "1") -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def yahoo_acquisition_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_YAHOO_ACQUISITION_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_YAHOO_ACQUISITION", "1")


def _interval_seconds() -> float:
    try:
        value = float(environment().get("OMNIX_TRADING_YAHOO_ACQUISITION_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(15.0, value)


class TradingYahooAcquisitionMonitor(ScheduledTradingMonitor):
    capture_error_count: int
    last_run_at: datetime | None

    def record_cycle_error(self, exc: Exception) -> None:
        self.capture_error_count += 1
        self.last_run_at = self.now_factory()
        super().record_cycle_error(exc)

    def __init__(
        self,
        *,
        repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.repository_factory = repository_factory
        self.market_service_factory = market_service_factory
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self._last_capture_end: dict[str, datetime] = {}
        self.last_run_at = None
        self.last_error: str | None = None
        self.capture_count = 0
        self.capture_error_count = 0
        self.active_symbol_count = 0

    @staticmethod
    def _session_bounds(now: datetime) -> tuple[datetime, datetime]:
        local = now.astimezone(_ET)
        start = datetime.combine(local.date(), _SESSION_OPEN, tzinfo=_ET)
        end = datetime.combine(local.date(), _SESSION_CLOSE, tzinfo=_ET)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    def _active_instruments(
        self,
        repository: TradingStrategyRepository,
        now: datetime,
    ) -> dict[str, str | None]:
        session_date = now.astimezone(_ET).date()
        result: dict[str, str | None] = {}
        for universe in repository.list_universes(
            start_date=session_date,
            end_date=session_date,
        ):
            if universe.evaluation_time > now:
                continue
            for candidate in universe.candidates:
                result[candidate.instrument_id] = candidate.binding_id

        try:
            dynamic = DynamicDiscoveryEventRepository(repository).latest_candidates(
                session_date
            )
        except Exception:
            dynamic = {}
        for instrument_id, dynamic_candidate in dynamic.items():
            lifecycle = getattr(dynamic_candidate, "lifecycle", None)
            lifecycle_value = getattr(lifecycle, "value", lifecycle)
            if lifecycle_value == CandidateLifecycleState.EXPIRED.value:
                continue
            result.setdefault(instrument_id, None)
        return result

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("Yahoo acquisition monitor clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        session_start, session_end = self._session_bounds(now)
        if now < session_start or now > session_end:
            self.last_run_at = now
            self.active_symbol_count = 0
            return 0

        repository = self.repository_factory()
        service = self.market_service_factory()
        instruments = await asyncio.to_thread(
            self._active_instruments,
            repository,
            now,
        )
        self.active_symbol_count = len(instruments)
        if not instruments:
            self.last_run_at = now
            return 0

        yahoo = service.registry.provider("yahoo")
        fetch = getattr(yahoo, "get_intraday_bars_range", None)
        if not callable(fetch):
            raise RuntimeError("Yahoo provider does not expose exact-range acquisition")

        succeeded = 0
        failed = 0
        for instrument_id in sorted(instruments):
            prior = self._last_capture_end.get(instrument_id)
            start = session_start if prior is None else max(
                session_start,
                prior - timedelta(minutes=2),
            )
            end = min(now, session_end)
            if end <= start + timedelta(seconds=30):
                continue
            try:
                response = await asyncio.to_thread(
                    fetch,
                    instrument_id,
                    start=start,
                    end=end,
                    include_extended_hours=True,
                )
                bars = list(response.bars)
                if bars:
                    self._last_capture_end[instrument_id] = max(
                        bar.end_time for bar in bars
                    )
                succeeded += 1
                self.capture_count += 1
            except Exception as exc:
                failed += 1
                self.capture_error_count += 1
                self.last_error = f"{type(exc).__name__}: {exc}"

        service.yahoo_evidence_store.record_acquisition(
            attempted=succeeded + failed,
            succeeded=succeeded,
            failed=failed,
            symbols=len(instruments),
        )
        self.last_run_at = now
        if failed == 0:
            self.last_error = None
        return succeeded

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": yahoo_acquisition_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "capture_count": self.capture_count,
            "capture_error_count": self.capture_error_count,
            "active_symbol_count": self.active_symbol_count,
            "authority": "acquisition_only",
            "execution_authority": False,
        }


def create_trading_yahoo_acquisition_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingYahooAcquisitionMonitor):
        return None
    monitor = TradingYahooAcquisitionMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=yahoo_acquisition_monitor_enabled)


__all__ = [
    "TradingYahooAcquisitionMonitor",
    "create_trading_yahoo_acquisition_monitor_task",
    "yahoo_acquisition_monitor_enabled",
]
