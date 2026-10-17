"""Background orchestration for the prospective-gap runtime.

Once the premarket authority is frozen, Omnix—not the reporting agent—owns
post-open confirmation and deterministic post-close finalization.
"""

from __future__ import annotations

from app.config.env import environment

import asyncio
from datetime import date, datetime, time, timedelta, timezone

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .prospective_gap_runtime import ProspectiveGapRuntime, default_prospective_gap_runtime
from .us_equity_calendar import regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_close_time


_STATE_KEY = "_omnix_prospective_gap_monitor"
_PREMARKET_HANDOFF_INGEST_START = time(9, 24)
_PREMARKET_HANDOFF_INGEST_END = time(9, 27, 59)


def _flag(name: str, default: str) -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def prospective_gap_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_PROSPECTIVE_GAP_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_PROSPECTIVE_GAP_MONITOR", "1")


class ProspectiveGapMonitor(ScheduledTradingMonitor):
    def __init__(
        self,
        *,
        interval_seconds: float = 60.0,
        runtime_factory=default_prospective_gap_runtime,
    ) -> None:
        self.interval_seconds = max(15.0, float(interval_seconds))
        self.runtime_factory = runtime_factory
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.confirmation_run_count = 0
        self.postclose_finalize_count = 0
        self.no_session_count = 0
        self.scheduler_handoff_ingest_count = 0
        self.scheduler_handoff_error_count = 0
        self._no_session_reported_dates: set[date] = set()

    async def run_once(self, *, now: datetime | None = None) -> int:
        completed = await self._run_cycle(now=now)
        self.last_error = None
        return completed

    async def _run_cycle(self, *, now: datetime | None = None) -> int:
        observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        local = observed.astimezone(_ET)
        self.last_run_at = observed
        if local.weekday() >= 5 or local.date() in regular_holidays(local.year):
            return 0

        runtime: ProspectiveGapRuntime = self.runtime_factory()
        ledger = runtime.session_ledger(local.date())
        clock = local.timetz().replace(tzinfo=None)
        if ledger.latest(kind="session_manifest", instrument_id="__session__") is None:
            if _PREMARKET_HANDOFF_INGEST_START <= clock <= _PREMARKET_HANDOFF_INGEST_END:
                try:
                    ingested = await asyncio.to_thread(
                        runtime.try_freeze_scheduler_inbox,
                        local.date(),
                        observed_at=observed,
                    )
                    if ingested is not None:
                        self.scheduler_handoff_ingest_count += 1
                        ledger = runtime.session_ledger(local.date())
                except Exception:
                    self.scheduler_handoff_error_count += 1
                    raise
            if ledger.latest(kind="session_manifest", instrument_id="__session__") is None:
                if (
                    clock > _PREMARKET_HANDOFF_INGEST_END
                    and local.date() not in self._no_session_reported_dates
                ):
                    self.no_session_count += 1
                    self._no_session_reported_dates.add(local.date())
                return 0

        if time(9, 30) <= clock <= time(11, 35):
            await asyncio.to_thread(
                runtime.run_confirmation,
                session_date=local.date(),
                evaluated_at=observed,
            )
            self.confirmation_run_count += 1
            return 1

        regular_close = regular_close_time(local.date())
        finalize_after = (
            datetime.combine(local.date(), regular_close, tzinfo=_ET)
            + timedelta(minutes=20)
        ).time()
        if clock >= finalize_after:
            if ledger.latest(kind="daily_scorecard", instrument_id="__scorecard__") is None:
                await asyncio.to_thread(
                    runtime.finalize_postclose,
                    session_date=local.date(),
                    evaluated_at=observed,
                )
                self.postclose_finalize_count += 1
                return 1
        return 0

    @property
    def running(self) -> bool:
        return self.scheduled


def create_prospective_gap_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, ProspectiveGapMonitor):
        return None
    monitor = ProspectiveGapMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=prospective_gap_monitor_enabled)


__all__ = [
    "ProspectiveGapMonitor",
    "prospective_gap_monitor_enabled",
    "create_prospective_gap_monitor_task",
]
