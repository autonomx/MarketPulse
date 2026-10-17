"""Prospective SHADOW-only monitor for the deep-recovery continuation hypothesis.

This monitor is intentionally isolated from TradingStrategyMonitor and from every
paper-order/protection repository. It may evaluate finalized bars, persist research
state and capture the same execution/prospective feature evidence used by V2 SHADOW,
but it can never place or authorize an order.
"""

from __future__ import annotations
from app.config.env import env_str as _env_str

import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.runtime.features import FeatureContext


from .monitor_task import ScheduledTradingMonitor, TradingMonitorTask
from .service import TradingMarketDataService, default_market_data_service
from .strategy_deep_recovery import (
    DEEP_RECOVERY_RULE_VERSION,
    DEEP_RECOVERY_SETUP_ID,
    evaluate_deep_recovery_shadow,
)
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_execution import observe_shadow_execution
from .strategy_shadow_universe import resolve_v2_shadow_archive
from .strategy_v2_qualification import v2_profile_fingerprint
from .trade_logging import trade_log
from .market_evidence_guards import _CoverageMarketService
from .strategy_session_evidence import (
    _FullSessionMarketServiceProxy,
    _PartialCurrentSessionMarketDataProxy,
)
from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.apps.trading.us_equity_calendar import regular_session_settled
from typing import Any, cast


_STATE_KEY = "_omnix_trading_strategy_deep_recovery_shadow_monitor"
_STATE_EVENT_TYPE = "deep_recovery_state"
_SIGNAL_EVENT_TYPE = "deep_recovery_shadow"


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def strategy_deep_recovery_shadow_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_DEEP_RECOVERY_SHADOW_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_DEEP_RECOVERY_SHADOW_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_DEEP_RECOVERY_SHADOW_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(5.0, value)


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _eligible(config: TradingStrategyConfigDocument) -> bool:
    return (
        config.enabled
        and config.mode == "shadow"
        and config.strategy_version == "2.0.0"
        and config.config.strategy_version == "2.0.0"
    )


class TradingStrategyDeepRecoveryShadowMonitor(ScheduledTradingMonitor):
    """Collect a second setup family beside V2 without sharing execution authority."""

    error_event = "deep_recovery_shadow_monitor_error"

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        now_factory: Callable[[], datetime] | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.evaluation_count = 0
        self.state_transition_count = 0
        self.signal_count = 0
        self.execution_observation_count = 0

    async def _append_event(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        instrument_id: str,
        event_type: str,
        state: str,
        reason_code: str,
        observed_at: datetime,
        payload: dict[str, Any],
        identity: tuple[object, ...],
    ) -> bool:
        idem = _key(
            config.strategy_id,
            DEEP_RECOVERY_SETUP_ID,
            DEEP_RECOVERY_RULE_VERSION,
            event_type,
            instrument_id,
            *identity,
        )
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=idem[:32],
                run_id=None,
                instrument_id=instrument_id,
                event_type=event_type,
                state=state,
                reason_code=reason_code,
                observed_at=observed_at,
                idempotency_key=idem,
                payload=payload,
            ),
        )

    async def _day_events(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        day_start: datetime,
        day_end: datetime,
    ) -> list[StrategyEvent]:
        event_types = (_STATE_EVENT_TYPE, _SIGNAL_EVENT_TYPE)
        if hasattr(repository, "events_by_types_between"):
            return await asyncio.to_thread(
                repository.events_by_types_between,
                config.strategy_id,
                event_types=event_types,
                start_time=day_start,
                end_time=day_end,
                limit=10_000,
            )
        events = await asyncio.to_thread(repository.recent_events, config.strategy_id, 10_000)
        return [
            event for event in events
            if event.event_type in event_types
            and day_start <= event.observed_at.astimezone(timezone.utc) < day_end
        ]

    async def _universe(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        now: datetime,
    ):
        if config.active_universe_id is not None:
            return await asyncio.to_thread(repository.get_universe, config.active_universe_id), "active_universe"
        snapshot = await asyncio.to_thread(
            resolve_v2_shadow_archive,
            config,
            repository,
            now=now,
        )
        return snapshot, "auto_archive_shadow"

    async def _run_config(
        self,
        repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        config: TradingStrategyConfigDocument,
        *,
        now: datetime,
    ) -> int:
        if not getattr(market_service, "allow_partial_current_session", False):
            market_service = cast(TradingMarketDataService, _PartialCurrentSessionMarketDataProxy(
                market_service,
                session_date=now.astimezone(_ET).date(),
                observed_at=now,
            ))
        market_service = cast(TradingMarketDataService, _FullSessionMarketServiceProxy(
            market_service,
            session_date=now.astimezone(_ET).date(),
            observed_at=now,
            allow_shadow_fallback=True,
        ))
        market_service = cast(TradingMarketDataService, _CoverageMarketService(
            market_service,
            session_date=now.astimezone(_ET).date(),
            observed_at=now,
        ))
        if not _eligible(config):
            return 0
        if regular_session_settled(now):
            return 0  # the session's bars are final; a later pass repeats the last one
        today_et = now.astimezone(_ET).date()
        day_start_et = datetime(today_et.year, today_et.month, today_et.day, tzinfo=_ET)
        day_start = day_start_et.astimezone(timezone.utc)
        day_end = (day_start_et + timedelta(days=1)).astimezone(timezone.utc)
        try:
            universe, universe_source = await self._universe(repository, config, now=now)
        except Exception as exc:
            self.last_error = f"{config.strategy_id}/universe: {type(exc).__name__}: {exc}"
            trade_log(
                "auto_trading",
                "deep_recovery_shadow_universe_error",
                strategy_id=config.strategy_id,
                error_type=type(exc).__name__,
                detail=str(exc),
                execution_authority=False,
            )
            return 0
        if universe is None or universe.session_date != today_et:
            return 0

        day_events = await self._day_events(
            repository,
            config,
            day_start=day_start,
            day_end=day_end,
        )
        signaled = {
            event.instrument_id for event in day_events
            if event.event_type == _SIGNAL_EVENT_TYPE
        }
        latest_states: dict[str, StrategyEvent] = {}
        for event in day_events:
            if event.event_type != _STATE_EVENT_TYPE:
                continue
            prior = latest_states.get(event.instrument_id)
            if prior is None or event.observed_at > prior.observed_at:
                latest_states[event.instrument_id] = event

        emitted = 0
        for candidate in universe.candidates:
            try:
                response = await asyncio.to_thread(
                    market_service.bars,
                    candidate.instrument_id,
                    "1m",
                    240,
                    candidate.binding_id,
                )
                finalized = [
                    bar for bar in response.bars
                    if bar.is_final and bar.end_time <= now
                ]
                evaluation = evaluate_deep_recovery_shadow(candidate, finalized, config.config)
            except Exception as exc:
                self.last_error = f"{config.strategy_id}/{candidate.instrument_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "deep_recovery_shadow_evaluation_error",
                    strategy_id=config.strategy_id,
                    instrument_id=candidate.instrument_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
                continue

            self.evaluation_count += 1
            observed_at = evaluation.observed_at or (finalized[-1].end_time if finalized else now)
            state_payload = {
                "setup_id": evaluation.setup_id,
                "rule_version": evaluation.rule_version,
                "evaluation": evaluation.model_dump(mode="json"),
                "universe_id": universe.universe_id,
                "universe_source": universe_source,
                "profile_fingerprint": v2_profile_fingerprint(config.config),
                "finalized_bar_count": len(finalized),
                "execution_authority": False,
            }
            prior_state = latest_states.get(candidate.instrument_id)
            state_changed = (
                prior_state is None
                or prior_state.state != evaluation.state
                or prior_state.reason_code != evaluation.reason_code
            )
            if state_changed:
                persisted_state = await self._append_event(
                    repository,
                    config,
                    instrument_id=candidate.instrument_id,
                    event_type=_STATE_EVENT_TYPE,
                    state=evaluation.state,
                    reason_code=evaluation.reason_code,
                    observed_at=observed_at,
                    payload=state_payload,
                    identity=(observed_at.astimezone(timezone.utc).isoformat(), evaluation.state, evaluation.reason_code),
                )
                if persisted_state:
                    self.state_transition_count += 1
                    latest_states[candidate.instrument_id] = StrategyEvent(
                        strategy_id=config.strategy_id,
                        event_id="shadow-state-cache",
                        run_id=None,
                        instrument_id=candidate.instrument_id,
                        event_type=_STATE_EVENT_TYPE,
                        state=evaluation.state,
                        reason_code=evaluation.reason_code,
                        observed_at=observed_at,
                        idempotency_key="shadow-state-cache",
                        payload=state_payload,
                    )

            if not evaluation.signal_ready or candidate.instrument_id in signaled:
                continue

            signal_payload = dict(state_payload)
            try:
                evidence = await asyncio.to_thread(
                    observe_shadow_execution,
                    market_service,
                    instrument_id=candidate.instrument_id,
                    binding_id=candidate.binding_id,
                )
                self.execution_observation_count += 1
                signal_payload["execution"] = evidence.execution
                signal_reason: str = evidence.reason_code
            except Exception as exc:
                signal_payload["execution"] = None
                signal_payload["execution_error"] = f"{type(exc).__name__}: {exc}"
                signal_reason = "SHADOW_EXECUTION_UNAVAILABLE"
                self.last_error = f"{config.strategy_id}/{candidate.instrument_id}/execution: {type(exc).__name__}: {exc}"

            persisted = await self._append_event(
                repository,
                config,
                instrument_id=candidate.instrument_id,
                event_type=_SIGNAL_EVENT_TYPE,
                state="signal_ready",
                reason_code=signal_reason,
                observed_at=observed_at,
                payload=signal_payload,
                # One immutable first signal per setup/symbol/session.
                identity=(today_et.isoformat(),),
            )
            if persisted:
                self.signal_count += 1
                emitted += 1
            signaled.add(candidate.instrument_id)
            trade_log(
                "auto_trading",
                "deep_recovery_shadow_signal",
                strategy_id=config.strategy_id,
                instrument_id=candidate.instrument_id,
                observed_at=observed_at,
                signal_reason=signal_reason,
                setup_id=DEEP_RECOVERY_SETUP_ID,
                rule_version=DEEP_RECOVERY_RULE_VERSION,
                recovery_pct=evaluation.recovery_pct,
                selloff_pct=evaluation.selloff_pct,
                research_risk_pct=evaluation.research_risk_pct,
                persisted=persisted,
                execution_authority=False,
            )
        return emitted

    async def run_once(self) -> int:
        now = self.now_factory()
        if now.tzinfo is None:
            raise ValueError("deep_recovery_shadow_clock_must_be_timezone_aware")
        now = now.astimezone(timezone.utc)
        repository = self.strategy_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(repository.list_configs, active_only=True)
        emitted = 0
        for config in configs:
            try:
                emitted += await self._run_config(repository, market_service, config, now=now)
            except Exception as exc:
                self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                trade_log(
                    "auto_trading",
                    "deep_recovery_shadow_config_error",
                    strategy_id=config.strategy_id,
                    error_type=type(exc).__name__,
                    detail=str(exc),
                    execution_authority=False,
                )
        self.last_run_at = now
        return emitted

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": strategy_deep_recovery_shadow_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "evaluation_count": self.evaluation_count,
            "state_transition_count": self.state_transition_count,
            "signal_count": self.signal_count,
            "execution_observation_count": self.execution_observation_count,
            "setup_id": DEEP_RECOVERY_SETUP_ID,
            "rule_version": DEEP_RECOVERY_RULE_VERSION,
            "execution_authority": False,
        }


def create_trading_strategy_deep_recovery_shadow_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingStrategyDeepRecoveryShadowMonitor):
        return None
    monitor = TradingStrategyDeepRecoveryShadowMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(name=__name__, monitor=monitor, enabled=strategy_deep_recovery_shadow_monitor_enabled)


__all__ = [
    "TradingStrategyDeepRecoveryShadowMonitor",
    "create_trading_strategy_deep_recovery_shadow_monitor_task",
    "strategy_deep_recovery_shadow_monitor_enabled",
]
