"""Scheduler monitor and provisioning for ``overnight-trading-strategy-shadow``.

SHADOW research only. At the configured decision time it scans the market and
records each arm's picks plus a baseline population as strategy events; after
``label_after_et`` on the next session it labels entries/exits from one-minute
SIP bars and records a per-session scorecard. Events are idempotent, so restarts
and overlapping workers converge. Nothing here places, sizes or routes an order.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from app.config.env import environment
from app.runtime.features import FeatureContext

from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .paper import PaperAccountCreate
from .paper_repository import TradingPaperRepository
from .strategies.models import OvernightShadowConfig, StrategyRiskProfile
from .strategy_overnight_scan import OvernightMarketData
from .strategy_overnight_shadow import (
    OVERNIGHT_TRADING_STRATEGY_ID,
    OvernightCandidate,
    OvernightOutcome,
    OvernightPick,
    label_outcome,
    population,
    score,
    select_arm_picks,
)
from .strategy_repository import StrategyEvent, TradingStrategyConfigDocument, TradingStrategyRepository
from .trade_logging import trade_log
from .us_equity_calendar import REGULAR_CLOSE, regular_close_time, regular_holidays
from app.apps.trading.us_equity_calendar import EASTERN as _ET

logger = logging.getLogger(__name__)

_STATE_KEY = "_omnix_overnight_shadow_monitor"
OVERNIGHT_SHADOW_ACCOUNT_ID = "omnix-overnight-shadow"
_ACCOUNT_NAME = "Omnix Overnight SHADOW"
EVENT_PICK = "overnight_pick"
EVENT_POPULATION = "overnight_population"
EVENT_SCAN = "overnight_scan"
EVENT_OUTCOME = "overnight_outcome"
EVENT_SCORECARD = "overnight_scorecard"
_SCAN_INSTRUMENT = "__scan__"
_POPULATION_INSTRUMENT = "__population__"
_SCORECARD_INSTRUMENT = "__scorecard__"
_LABEL_LOOKBACK_SESSIONS = 5


def _flag(name: str, default: str) -> bool:
    return environment().get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def overnight_shadow_monitor_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_OVERNIGHT_SHADOW_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_OVERNIGHT_SHADOW_MONITOR", "1")


def overnight_shadow_autoprovision_enabled() -> bool:
    if environment().get("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_OVERNIGHT_SHADOW_AUTOPROVISION_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_OVERNIGHT_SHADOW_AUTOPROVISION", "1")


# ---------------------------------------------------------------- calendar
def is_session(day: date) -> bool:
    return day.weekday() < 5 and day not in regular_holidays(day.year)


def previous_session(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not is_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def next_session(day: date) -> date:
    cursor = day + timedelta(days=1)
    while not is_session(cursor):
        cursor += timedelta(days=1)
    return cursor


def _close(day: date) -> time:
    return regular_close_time(day)


# ---------------------------------------------------------------- provisioning
def overnight_shadow_document(account_id: str) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id=OVERNIGHT_TRADING_STRATEGY_ID,
        account_id=account_id,
        strategy_kind="overnight_shadow_v1",
        strategy_version="1.0.0",
        mode="shadow",
        active_universe_id=None,
        config=OvernightShadowConfig(),
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def _resolve_account(paper_repository: TradingPaperRepository) -> str:
    accounts = {account.account_id for account in paper_repository.list_accounts(limit=500)}
    if OVERNIGHT_SHADOW_ACCOUNT_ID in accounts:
        return OVERNIGHT_SHADOW_ACCOUNT_ID
    try:
        # SHADOW never places orders; the account only owns the evidence (FK).
        paper_repository.create_account(PaperAccountCreate(
            account_id=OVERNIGHT_SHADOW_ACCOUNT_ID, name=_ACCOUNT_NAME,
            initial_cash=Decimal("10000"), commission_bps=Decimal("0"),
        ))
    except Exception:
        # Another worker may have created it first; converge on the durable row.
        if OVERNIGHT_SHADOW_ACCOUNT_ID not in {a.account_id for a in paper_repository.list_accounts(limit=500)}:
            raise
    return OVERNIGHT_SHADOW_ACCOUNT_ID


def provision_overnight_shadow_strategy(
    *,
    strategy_repository: TradingStrategyRepository | None = None,
    paper_repository: TradingPaperRepository | None = None,
) -> str:
    """Create the SHADOW group once. Existing configurations (and archives) are never overwritten."""

    if not overnight_shadow_autoprovision_enabled():
        return "disabled"
    strategy_repo = strategy_repository or TradingStrategyRepository()
    try:
        strategy_repo.get_config(OVERNIGHT_TRADING_STRATEGY_ID)
        return "unchanged"
    except ValueError as exc:
        if str(exc) != "strategy_config_not_found":
            raise
    account_id = _resolve_account(paper_repository or TradingPaperRepository())
    try:
        strategy_repo.create_config(overnight_shadow_document(account_id))
    except Exception:
        try:
            strategy_repo.get_config(OVERNIGHT_TRADING_STRATEGY_ID)
            return "unchanged"
        except ValueError:
            raise
    trade_log("auto_trading", "overnight_shadow_provisioned", strategy_id=OVERNIGHT_TRADING_STRATEGY_ID,
              account_id=account_id, execution_authority=False)
    return "created"


# ---------------------------------------------------------------- events
def _event_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]


def _event(
    config: TradingStrategyConfigDocument,
    *,
    event_type: str,
    instrument_id: str,
    session_date: date,
    observed_at: datetime,
    payload: dict[str, Any],
    key_suffix: str = "",
    state: str = "recorded",
) -> StrategyEvent:
    key = f"{event_type}:{session_date.isoformat()}:{instrument_id}{key_suffix}"
    return StrategyEvent(
        strategy_id=config.strategy_id,
        event_id=_event_id(config.strategy_id, key),
        run_id=f"overnight:{session_date.isoformat()}",
        instrument_id=instrument_id,
        event_type=event_type,
        state=state,
        observed_at=observed_at,
        idempotency_key=key,
        strategy_revision=config.revision,
        payload=payload,
    )


def _session_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0), tzinfo=_ET)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _events(
    repository: TradingStrategyRepository,
    event_types: tuple[str, ...],
    day: date,
    *,
    through: date | None = None,
) -> list[StrategyEvent]:
    """Events of entry session ``day`` (matched by run id) observed from ``day`` through ``through``.

    Picks are observed on the entry session; outcomes and scorecards the next morning or later,
    so labelling lookups pass ``through`` to include those days.
    """

    start, _ = _session_bounds(day)
    _, end = _session_bounds(through or day)
    rows = repository.events_by_types_between(
        OVERNIGHT_TRADING_STRATEGY_ID, event_types=event_types, start_time=start, end_time=end, limit=10_000,
    )
    run_id = f"overnight:{day.isoformat()}"
    return [row for row in rows if row.run_id == run_id]


# ---------------------------------------------------------------- monitor
class OvernightShadowMonitor(ScheduledTradingMonitor):
    error_event = "overnight_shadow_monitor_error"
    # The full-market decision scan takes about a minute when idle and must
    # finish inside the 15:50-16:00 window.
    timeout_seconds = 600.0

    def __init__(
        self,
        *,
        interval_seconds: float = 60.0,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = TradingStrategyRepository,
        market_data_factory: Callable[[], OvernightMarketData] = OvernightMarketData,
        now_factory: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.interval_seconds = max(15.0, float(interval_seconds))
        self.strategy_repository_factory = strategy_repository_factory
        self.market_data_factory = market_data_factory
        self.now_factory = now_factory
        self.last_run_at: datetime | None = None
        self.scan_count = 0
        self.label_count = 0
        self.provision_result: str | None = None
        self.provision_error: str | None = None
        # Scheduler cancellation cannot stop a worker thread: a timed-out cycle's
        # scan keeps running, and the next cycle must not start a second one.
        self._work_lock = threading.Lock()

    def error_log_fields(self) -> dict[str, Any]:
        return {"execution_authority": False, "strategy_id": OVERNIGHT_TRADING_STRATEGY_ID}

    def provision(self) -> None:
        try:
            self.provision_result = provision_overnight_shadow_strategy(
                strategy_repository=self.strategy_repository_factory()
            )
            self.provision_error = None
        except Exception as exc:  # startup must not fail the trading feature
            self.provision_error = f"{type(exc).__name__}: {exc}"
            logger.warning("overnight shadow provisioning failed: %s", self.provision_error)

    def _config(self, repository: TradingStrategyRepository) -> TradingStrategyConfigDocument | None:
        try:
            config = repository.get_config(OVERNIGHT_TRADING_STRATEGY_ID)
        except ValueError:
            return None
        if (
            config.strategy_kind != "overnight_shadow_v1"
            or not config.enabled
            or config.mode != "shadow"
            or config.archived_at is not None
        ):
            return None
        return config

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("overnight shadow monitor clock must be timezone-aware")
        self.last_run_at = now
        local = now.astimezone(_ET)
        repository = self.strategy_repository_factory()
        config = await asyncio.to_thread(self._config, repository)
        if config is None or not is_session(local.date()):
            return 0
        abandoned = threading.Event()
        try:
            return await asyncio.to_thread(self._run_exclusive, repository, config, now, abandoned)
        except asyncio.CancelledError:
            abandoned.set()
            raise

    def _run_exclusive(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        now: datetime,
        abandoned: threading.Event,
    ) -> int:
        if not self._work_lock.acquire(blocking=False):
            return 0  # a cancelled cycle's work is still running in its thread
        try:
            return self._label_due(repository, config, now) + self._scan_due(repository, config, now)
        except Exception as exc:
            if abandoned.is_set():
                # Nothing awaits this thread any more; record the failure here.
                self.record_cycle_error(exc)
            raise
        finally:
            self._work_lock.release()

    # -- 15:50 decision
    def _scan_due(self, repository: TradingStrategyRepository, config: TradingStrategyConfigDocument, now: datetime) -> int:
        settings: OvernightShadowConfig = config.config
        local = now.astimezone(_ET)
        day = local.date()
        if _close(day) != REGULAR_CLOSE:
            return 0  # early close: no entry minute, no picks (matches the backtest)
        if not (settings.decision_et <= local.time() < _close(day)):
            return 0
        if _events(repository, (EVENT_SCAN,), day):
            return 0
        result = self.market_data_factory().scan(day, previous_session(day), settings)
        picks = select_arm_picks(result.candidates, settings)
        pop = population(result.candidates, settings)
        late = local.time() >= settings.entry_et
        for arm_id, arm_picks in picks.items():
            for pick in arm_picks:
                repository.append_event(_event(
                    config, event_type=EVENT_PICK, instrument_id=pick.candidate.instrument_id,
                    session_date=day, observed_at=now, key_suffix=f":{arm_id}",
                    payload=pick.model_dump(mode="json") | {"decided_late": late},
                ))
        repository.append_event(_event(
            config, event_type=EVENT_POPULATION, instrument_id=_POPULATION_INSTRUMENT,
            session_date=day, observed_at=now,
            payload={"candidates": [c.model_dump(mode="json") for c in pop]},
        ))
        # The scan marker is written last: a crash before it lets the next cycle retry;
        # picks and population are idempotent, so a retry cannot duplicate them.
        repository.append_event(_event(
            config, event_type=EVENT_SCAN, instrument_id=_SCAN_INSTRUMENT,
            session_date=day, observed_at=now,
            payload={
                "eligible": result.eligible, "scanned": result.scanned, "candidates": len(result.candidates),
                "population": len(pop), "picks": {arm: len(p) for arm, p in picks.items()},
                "decided_late": late, "data_cutoff_et": settings.data_cutoff_et.isoformat(),
                "execution_authority": False,
            },
        ))
        self.scan_count += 1
        trade_log("auto_trading", "overnight_shadow_scan", strategy_id=config.strategy_id, session_date=day,
                  picks={arm: [p.candidate.symbol for p in ps] for arm, ps in picks.items()},
                  population=len(pop), execution_authority=False)
        return 1

    # -- next-morning labelling
    def _label_due(self, repository: TradingStrategyRepository, config: TradingStrategyConfigDocument, now: datetime) -> int:
        settings: OvernightShadowConfig = config.config
        local = now.astimezone(_ET)
        today = local.date()
        if local.time() < settings.label_after_et:
            return 0
        labelled = 0
        entry_day = previous_session(today)
        for _ in range(_LABEL_LOOKBACK_SESSIONS):
            if _events(repository, (EVENT_SCAN,), entry_day) and not _events(
                repository, (EVENT_SCORECARD,), entry_day, through=today
            ):
                self._label_session(repository, config, entry_day, now)
                labelled += 1
            entry_day = previous_session(entry_day)
        return labelled

    def _label_session(
        self, repository: TradingStrategyRepository, config: TradingStrategyConfigDocument, entry_day: date, now: datetime,
    ) -> None:
        settings: OvernightShadowConfig = config.config
        exit_day = next_session(entry_day)
        rows = _events(repository, (EVENT_PICK, EVENT_POPULATION), entry_day)
        picks = [OvernightPick.model_validate({k: v for k, v in r.payload.items() if k != "decided_late"})
                 for r in rows if r.event_type == EVENT_PICK]
        pop = [OvernightCandidate.model_validate(c) for r in rows if r.event_type == EVENT_POPULATION
               for c in r.payload.get("candidates", [])]
        symbols = sorted({p.candidate.symbol for p in picks} | {c.symbol for c in pop})
        data = self.market_data_factory()
        late = data.minute_bars(symbols, datetime.combine(entry_day, time(15, 25), tzinfo=_ET),
                                datetime.combine(entry_day, _close(entry_day), tzinfo=_ET))
        morning = data.minute_bars(symbols, datetime.combine(exit_day, time(4, 0), tzinfo=_ET),
                                   datetime.combine(exit_day, time(9, 46), tzinfo=_ET))

        def outcome(arm_id: str, candidate: OvernightCandidate) -> OvernightOutcome:
            return label_outcome(
                arm_id=arm_id, candidate=candidate, session_date=entry_day, next_session_date=exit_day,
                session_bars=late.get(candidate.symbol, []), next_bars=morning.get(candidate.symbol, []),
                config=settings, session_close=_close(entry_day), next_close=_close(exit_day),
            )

        by_arm: dict[str, list[OvernightOutcome]] = {arm.arm_id: [] for arm in settings.arms if arm.enabled}
        for pick in picks:
            result = outcome(pick.arm_id, pick.candidate)
            by_arm.setdefault(pick.arm_id, []).append(result)
            repository.append_event(_event(
                config, event_type=EVENT_OUTCOME, instrument_id=pick.candidate.instrument_id,
                session_date=entry_day, observed_at=now, key_suffix=f":{pick.arm_id}", state=result.status,
                payload=result.model_dump(mode="json"),
            ))
        pop_outcomes = [outcome("population-10pct", c) for c in pop]
        repository.append_event(_event(
            config, event_type=EVENT_SCORECARD, instrument_id=_SCORECARD_INSTRUMENT,
            session_date=entry_day, observed_at=now, state="labelled",
            payload={
                "entry_session": entry_day.isoformat(), "exit_session": exit_day.isoformat(),
                "arms": [score(arm_id, outs).model_dump(mode="json") for arm_id, outs in sorted(by_arm.items())],
                "population_10pct": score("population-10pct", pop_outcomes).model_dump(mode="json"),
                "execution_authority": False,
            },
        ))
        self.label_count += 1

    @property
    def running(self) -> bool:
        return self.scheduled


def create_overnight_shadow_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    if isinstance(getattr(state, _STATE_KEY, None), OvernightShadowMonitor):
        return None
    monitor = OvernightShadowMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(
        name=__name__, monitor=monitor, enabled=overnight_shadow_monitor_enabled, startup=(monitor.provision,),
    )


__all__ = [
    "EVENT_OUTCOME",
    "EVENT_PICK",
    "EVENT_POPULATION",
    "EVENT_SCAN",
    "EVENT_SCORECARD",
    "OVERNIGHT_SHADOW_ACCOUNT_ID",
    "OvernightShadowMonitor",
    "create_overnight_shadow_monitor_task",
    "overnight_shadow_autoprovision_enabled",
    "overnight_shadow_document",
    "overnight_shadow_monitor_enabled",
    "provision_overnight_shadow_strategy",
]
