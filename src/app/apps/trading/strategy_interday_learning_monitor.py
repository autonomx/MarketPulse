from __future__ import annotations

import logging
from app.config.env import env_str as _env_str

import asyncio
from datetime import datetime, time, timezone

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .service import TradingMarketDataService, default_market_data_service
from .strategy_dynamic_discovery import AttributionEvent, INTERDAY_TRADING_STRATEGY_ID
from .strategy_dynamic_discovery_learning import build_daily_discovery_report
from .strategy_dynamic_discovery_repository import (
    DynamicDiscoveryEventRepository,
    EVENT_ATTRIBUTION,
)
from .strategy_interday_attribution import bridge_strategy_events
from .strategy_interday_postclose import (
    label_candidate_outcome,
    persist_candidate_outcome,
    qualification_from_persisted_evidence,
    session_outcomes,
)
from .strategy_repository import TradingStrategyRepository, default_strategy_repository
from .trade_logging import trade_log
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from typing import Any
from .strategy_repository import StrategyEvent

logger = logging.getLogger(__name__)

_STATE_KEY = "_omnix_interday_learning_monitor"


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def interday_learning_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_INTERDAY_LEARNING_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_INTERDAY_LEARNING", "1")


def _interval_seconds() -> float:
    try:
        value = float(
            _env_str("OMNIX_TRADING_INTERDAY_LEARNING_INTERVAL_SECONDS", "300")
        )
    except ValueError:
        value = 300.0
    return max(60.0, value)


def _same_session(event_time: datetime, session_date) -> bool:
    return event_time.astimezone(_ET).date() == session_date


async def _run_interday_learning_once_core(
    *,
    now: datetime | None = None,
    repository: TradingStrategyRepository | None = None,
    market_service: TradingMarketDataService | None = None,
) -> dict[str, int | bool]:
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    session_date = observed_at.astimezone(_ET).date()
    repo = repository or default_strategy_repository()
    try:
        parent = await asyncio.to_thread(repo.get_config, INTERDAY_TRADING_STRATEGY_ID)
    except ValueError:
        return {
            "bridged": 0,
            "reported": False,
            "labeled": 0,
            "qualified": False,
        }
    if not parent.enabled or parent.archived_at is not None:
        return {
            "bridged": 0,
            "reported": False,
            "labeled": 0,
            "qualified": False,
        }

    configs = await asyncio.to_thread(repo.list_configs, active_only=False)
    strategy_ids = {
        item.strategy_id
        for item in configs
        if item.strategy_id == INTERDAY_TRADING_STRATEGY_ID
        or item.parent_strategy_id == INTERDAY_TRADING_STRATEGY_ID
    }
    events_by_strategy: dict[str, list[StrategyEvent]] = {}
    for strategy_id in sorted(strategy_ids):
        rows = await asyncio.to_thread(repo.recent_events, strategy_id, 10_000)
        events_by_strategy[strategy_id] = [
            row for row in rows if _same_session(row.observed_at, session_date)
        ]
    bridged = await asyncio.to_thread(
        bridge_strategy_events,
        repo,
        session_date=session_date,
        events_by_strategy=events_by_strategy,
    )

    local_time = observed_at.astimezone(_ET).time().replace(tzinfo=None)
    if local_time < time(16, 10):
        return {
            "bridged": bridged,
            "reported": False,
            "labeled": 0,
            "qualified": False,
        }

    event_repo = DynamicDiscoveryEventRepository(repo)
    candidates = tuple(
        (await asyncio.to_thread(event_repo.latest_candidates, session_date)).values()
    )

    # Persist one immutable post-close durability outcome per discovered symbol.
    # Missing provider history remains simply unlabeled; it never becomes inferred
    # success/failure evidence.
    existing_outcomes = await asyncio.to_thread(
        session_outcomes,
        repo,
        session_date=session_date,
    )
    already_labeled = {row.instrument_id for row in existing_outcomes}
    active_market_service = market_service or default_market_data_service()
    labeled = 0
    for candidate in candidates:
        if candidate.instrument_id in already_labeled:
            continue
        try:
            outcome = await asyncio.to_thread(
                label_candidate_outcome,
                active_market_service,
                candidate,
                observed_at=observed_at,
            )
        except Exception as exc:
            trade_log(
                "auto_trading",
                "interday_discovery_outcome_error",
                strategy_id=INTERDAY_TRADING_STRATEGY_ID,
                instrument_id=candidate.instrument_id,
                observed_at=observed_at,
                error_type=type(exc).__name__,
                detail=str(exc),
                research_only=True,
                execution_authority=False,
            )
            continue
        if outcome is None:
            continue
        if await asyncio.to_thread(
            persist_candidate_outcome,
            repo,
            outcome,
            session_date=session_date,
            observed_at=observed_at,
        ):
            labeled += 1
            already_labeled.add(candidate.instrument_id)

    outcomes = await asyncio.to_thread(
        session_outcomes,
        repo,
        session_date=session_date,
    )
    session_events = await asyncio.to_thread(event_repo.session_events, session_date)
    attribution: list[AttributionEvent] = []
    for event in session_events:
        if event.event_type != EVENT_ATTRIBUTION:
            continue
        try:
            attribution.append(AttributionEvent.model_validate(event.payload))
        except Exception:
            logger.debug("suppressed error in %s", "_run_interday_learning_once_core", exc_info=True)
            continue
    report = build_daily_discovery_report(
        session_date=session_date,
        generated_at=observed_at,
        candidates=candidates,
        attribution=tuple(attribution),
        outcomes=outcomes,
    )
    reported = await asyncio.to_thread(
        event_repo.persist_report,
        session_date=session_date,
        observed_at=observed_at,
        payload=report.model_dump(mode="json"),
    )

    # Qualification is independently idempotent from the daily report. This is
    # intentionally attempted on every post-close pass: if the process crashes
    # after the report append but before the qualification append, a later pass
    # can complete the missing evidence snapshot without rewriting the report.
    reliability = min(1.0, len(outcomes) / len(candidates)) if candidates else 1.0
    qualification = await asyncio.to_thread(
        qualification_from_persisted_evidence,
        repo,
        current_report=report,
        data_reliability_fraction=reliability,
        causality_violations=0,
    )
    qualification_persisted = await asyncio.to_thread(
        event_repo.persist_qualification,
        session_date=session_date,
        observed_at=observed_at,
        evidence=qualification,
    )

    return {
        "bridged": bridged,
        "reported": bool(reported),
        "labeled": labeled,
        "qualified": bool(qualification_persisted),
    }


async def run_interday_learning_once(
    *,
    now: datetime | None = None,
    repository: TradingStrategyRepository | None = None,
    market_service: TradingMarketDataService | None = None,
) -> dict[str, int | bool]:
    """Run interday lifecycle work and append complete post-close evidence."""

    from .strategy_dynamic_discovery_runtime import (
        _run_interday_learning_once_complete,
    )

    return await _run_interday_learning_once_complete(
        now=now,
        repository=repository,
        market_service=market_service,
    )


class InterdayLearningMonitor(ScheduledTradingMonitor):
    error_event = "interday_learning_monitor_error"

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
        self.bridged_count = 0
        self.report_count = 0
        self.outcome_count = 0
        self.qualification_count = 0

    async def run_once(self) -> dict[str, int | bool]:
        result = await run_interday_learning_once()
        self.bridged_count += int(result["bridged"])
        self.report_count += int(bool(result["reported"]))
        self.outcome_count += int(result["labeled"])
        self.qualification_count += int(bool(result["qualified"]))
        self.last_run_at = datetime.now(timezone.utc)
        self.last_error = None
        return result


def create_interday_learning_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, InterdayLearningMonitor):
        return None
    monitor = InterdayLearningMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=interday_learning_monitor_enabled)


__all__ = [
    "InterdayLearningMonitor",
    "interday_learning_monitor_enabled",
    "create_interday_learning_monitor_task",
    "run_interday_learning_once",
]
