from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .paper_monitor import TradingPaperMonitor, trading_paper_monitor_enabled
from .execution_observation_monitor import (
    TradingExecutionObservationMonitor,
    execution_observation_monitor_enabled,
)
from .paper_protection_repository import (
    TradingPaperProtectionRepository,
    default_paper_protection_repository,
)
from .paper_repository import TradingPaperRepository
from .paper_runtime_repository import default_runtime_paper_repository
from .prospective_gap_monitor import (
    ProspectiveGapMonitor,
    prospective_gap_monitor_enabled,
)
from .providers.alpaca_iex_status import (
    AlpacaIexStatusMonitor,
    alpaca_iex_status_monitor_enabled,
)
from .service import TradingMarketDataService, default_market_data_service
from .strategy_ai_shadow_v3_monitor import (
    TradingAIShadowV3Monitor,
    ai_shadow_v3_monitor_enabled,
)
from .strategy_deep_recovery_monitor import (
    TradingStrategyDeepRecoveryShadowMonitor,
    strategy_deep_recovery_shadow_monitor_enabled,
)
from .strategy_dynamic_discovery_monitor import (
    InterdayDynamicDiscoveryMonitor,
    dynamic_discovery_monitor_enabled,
)
from .strategy_interday_learning_monitor import (
    InterdayLearningMonitor,
    interday_learning_monitor_enabled,
)
from .strategies.runner import RUNTIME_STATE_KEY as RUNNER_STATE_KEY, StrategyRunner
from .strategy_monitor import TradingStrategyMonitor, trading_strategy_monitor_enabled
from .strategy_operations_health import (
    TradingOperationalHealth,
    account_risk_health,
    day_bounds_et,
    execution_health,
    operational_health,
)
from .strategy_prospective_economic_monitor import (
    TradingStrategyProspectiveEconomicMonitor,
    strategy_prospective_economic_monitor_enabled,
)
from .strategy_repository import TradingStrategyRepository, default_strategy_repository
from .strategy_universe_archive_monitor import (
    TradingStrategyUniverseArchiveMonitor,
    strategy_universe_archive_monitor_enabled,
)
from .session_reconciliation_monitor import (
    TradingSessionReconciliationMonitor,
    session_reconciliation_monitor_enabled,
)
from .strategy_v2_qualification_monitor import (
    TradingStrategyV2QualificationMonitor,
    strategy_v2_qualification_monitor_enabled,
)
from .yahoo_acquisition_monitor import (
    TradingYahooAcquisitionMonitor,
    yahoo_acquisition_monitor_enabled,
)


class StrategyRuntimeMonitorStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    configured_enabled: bool
    registered: bool
    running: bool
    interval_seconds: float | None = None
    last_run_at: datetime | None = None
    last_error: str | None = None
    counters: dict[str, int] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyOperationsStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    paper_monitor: StrategyRuntimeMonitorStatus
    strategy_monitor: StrategyRuntimeMonitorStatus
    # The gap pullback configurations it owns or shadows (strategy runner WP).
    strategy_runner: StrategyRuntimeMonitorStatus = StrategyRuntimeMonitorStatus(
        configured_enabled=False,
        registered=False,
        running=False,
    )
    execution_observation_monitor: StrategyRuntimeMonitorStatus = StrategyRuntimeMonitorStatus(
        configured_enabled=False,
        registered=False,
        running=False,
    )
    ai_shadow_v3_monitor: StrategyRuntimeMonitorStatus = StrategyRuntimeMonitorStatus(
        configured_enabled=False,
        registered=False,
        running=False,
    )
    session_reconciliation_monitor: StrategyRuntimeMonitorStatus = StrategyRuntimeMonitorStatus(
        configured_enabled=False,
        registered=False,
        running=False,
    )
    prospective_gap_monitor: StrategyRuntimeMonitorStatus = StrategyRuntimeMonitorStatus(
        configured_enabled=False,
        registered=False,
        running=False,
    )
    deep_recovery_shadow_monitor: StrategyRuntimeMonitorStatus
    prospective_economic_monitor: StrategyRuntimeMonitorStatus
    universe_archive_monitor: StrategyRuntimeMonitorStatus
    v2_qualification_monitor: StrategyRuntimeMonitorStatus
    alpaca_status_monitor: StrategyRuntimeMonitorStatus
    execution_authority: Literal[False] = False


class InterdayMonitorOperationsStatus(BaseModel):
    """Operator-only runtime status for the causal discovery/learning monitors."""

    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    dynamic_discovery_monitor: StrategyRuntimeMonitorStatus
    interday_learning_monitor: StrategyRuntimeMonitorStatus
    execution_authority: Literal[False] = False


RepositoryFactory = Callable[[], TradingPaperRepository]
ProtectionRepositoryFactory = Callable[[], TradingPaperProtectionRepository]
StrategyRepositoryFactory = Callable[[], TradingStrategyRepository]
MarketServiceFactory = Callable[[], TradingMarketDataService]


def _monitor_status(
    monitor: object | None,
    *,
    expected_type: type,
    configured_enabled: bool,
    counter_names: tuple[str, ...],
    interval_attribute: str = "interval_seconds",
) -> StrategyRuntimeMonitorStatus:
    registered = isinstance(monitor, expected_type)
    if not registered:
        return StrategyRuntimeMonitorStatus(
            configured_enabled=configured_enabled,
            registered=False,
            running=False,
        )
    task = getattr(monitor, "_task", None)
    running = bool(task is not None and not task.done())
    counters = {
        name: int(getattr(monitor, name, 0) or 0)
        for name in counter_names
    }
    raw_interval = getattr(monitor, interval_attribute, None)
    details: dict[str, Any] = {}
    diagnostics = getattr(monitor, "diagnostics", None)
    if callable(diagnostics):
        try:
            raw_details = diagnostics()
            if isinstance(raw_details, dict):
                details = {
                    str(key): value
                    for key, value in raw_details.items()
                    if key not in {
                        "enabled",
                        "running",
                        "interval_seconds",
                        "last_run_at",
                        "last_error",
                    }
                }
        except Exception:
            details = {}
    return StrategyRuntimeMonitorStatus(
        configured_enabled=configured_enabled,
        registered=True,
        running=running,
        interval_seconds=float(raw_interval) if raw_interval is not None else None,
        last_run_at=getattr(monitor, "last_run_at", None),
        last_error=getattr(monitor, "last_error", None),
        counters=counters,
        details=details,
    )


def _paper_status(monitor: object | None) -> StrategyRuntimeMonitorStatus:
    status = _monitor_status(
        monitor,
        expected_type=TradingPaperMonitor,
        configured_enabled=trading_paper_monitor_enabled(),
        counter_names=(
            "active_target_count",
            "active_order_count",
            "active_protection_count",
            "quote_count",
            "rejected_quote_count",
            "fill_count",
            "protection_trigger_count",
        ),
    )
    if isinstance(monitor, TradingPaperMonitor):
        return status.model_copy(
            update={
                "interval_seconds": (
                    float(monitor.active_interval_seconds)
                    if monitor.active_target_count
                    else float(monitor.interval_seconds)
                )
            }
        )
    return status


def _alpaca_status(monitor: object | None) -> StrategyRuntimeMonitorStatus:
    configured_enabled = alpaca_iex_status_monitor_enabled()
    if not isinstance(monitor, AlpacaIexStatusMonitor):
        return StrategyRuntimeMonitorStatus(
            configured_enabled=configured_enabled,
            registered=False,
            running=False,
        )
    task = getattr(monitor, "_task", None)
    cache_snapshot = monitor.cache.snapshot()
    return StrategyRuntimeMonitorStatus(
        configured_enabled=configured_enabled,
        registered=True,
        running=bool(task is not None and not task.done()),
        interval_seconds=None,
        last_run_at=monitor.last_message_at,
        last_error=monitor.last_error,
        counters={
            "reconnect_count": int(monitor.reconnect_count),
            "disconnect_count": int(cache_snapshot.get("disconnect_count", 0) or 0),
            "known_halts": int(cache_snapshot.get("known_halts", 0) or 0),
            "history_symbols": int(cache_snapshot.get("history_symbols", 0) or 0),
        },
    )


def _interday_status(state: object) -> InterdayMonitorOperationsStatus:
    return InterdayMonitorOperationsStatus(
        observed_at=datetime.now(timezone.utc),
        dynamic_discovery_monitor=_monitor_status(
            getattr(state, "_omnix_interday_dynamic_discovery_monitor", None),
            expected_type=InterdayDynamicDiscoveryMonitor,
            configured_enabled=dynamic_discovery_monitor_enabled(),
            counter_names=("candidate_count",),
        ),
        interday_learning_monitor=_monitor_status(
            getattr(state, "_omnix_interday_learning_monitor", None),
            expected_type=InterdayLearningMonitor,
            configured_enabled=interday_learning_monitor_enabled(),
            counter_names=(
                "bridged_count",
                "report_count",
                "outcome_count",
                "qualification_count",
            ),
        ),
        execution_authority=False,
    )


def create_trading_strategy_operations_router(
    repository_factory: RepositoryFactory = default_runtime_paper_repository,
    protection_repository_factory: ProtectionRepositoryFactory = default_paper_protection_repository,
    strategy_repository_factory: StrategyRepositoryFactory = default_strategy_repository,
    market_service_factory: MarketServiceFactory = default_market_data_service,
) -> APIRouter:
    router = APIRouter(prefix="/api/trading/strategy-operations", tags=["trading-strategy-operations"])

    @router.get("/status", response_model=StrategyOperationsStatus)
    def strategy_operations_status(request: Request) -> StrategyOperationsStatus:
        state = request.app.state
        return StrategyOperationsStatus(
            observed_at=datetime.now(timezone.utc),
            paper_monitor=_paper_status(
                getattr(state, "_omnix_trading_paper_monitor", None),
            ),
            strategy_monitor=_monitor_status(
                getattr(state, "_omnix_trading_strategy_monitor", None),
                expected_type=TradingStrategyMonitor,
                configured_enabled=trading_strategy_monitor_enabled(),
                counter_names=(
                    "evaluation_count",
                    "signal_count",
                    "paper_order_count",
                    "intraday_learning_snapshot_count",
                    "intraday_llm_call_count",
                    "intraday_llm_assessment_count",
                    "intraday_llm_error_count",
                    "intraday_llm_input_character_count",
                    "intraday_llm_input_token_count",
                    "intraday_llm_output_token_count",
                    "intraday_llm_total_token_count",
                    "intraday_llm_estimated_usage_count",
                    "auto_paper_ready_strategy_count",
                    "auto_paper_blocked_strategy_count",
                    "auto_paper_archive_not_ready_strategy_count",
                    "auto_paper_qualification_blocked_strategy_count",
                ),
            ),
            strategy_runner=_monitor_status(
                getattr(state, RUNNER_STATE_KEY, None),
                expected_type=StrategyRunner,
                configured_enabled=trading_strategy_monitor_enabled(),
                counter_names=("paper_order_count", "owned_config_count", "shadowed_config_count"),
            ),
            execution_observation_monitor=_monitor_status(
                getattr(state, "_omnix_trading_execution_observation_monitor", None),
                expected_type=TradingExecutionObservationMonitor,
                configured_enabled=execution_observation_monitor_enabled(),
                counter_names=(
                    "capture_count",
                    "capture_error_count",
                    "skipped_non_execution_binding_count",
                ),
            ),
            ai_shadow_v3_monitor=_monitor_status(
                getattr(state, "_omnix_trading_ai_shadow_v3_monitor", None),
                expected_type=TradingAIShadowV3Monitor,
                configured_enabled=ai_shadow_v3_monitor_enabled(),
                counter_names=(
                    "decision_count",
                    "trigger_count",
                    "fill_count",
                    "rejection_count",
                    "geometry_challenger_count",
                    "agreement_count",
                    "data_gap_count",
                ),
            ),
            session_reconciliation_monitor=_monitor_status(
                getattr(state, "_omnix_trading_session_reconciliation_monitor", None),
                expected_type=TradingSessionReconciliationMonitor,
                configured_enabled=session_reconciliation_monitor_enabled(),
                counter_names=(
                    "manifest_create_count",
                    "attempt_count",
                    "final_count",
                    "deferred_count",
                    "permanently_unscorable_count",
                ),
            ),
            prospective_gap_monitor=_monitor_status(
                getattr(state, "_omnix_prospective_gap_monitor", None),
                expected_type=ProspectiveGapMonitor,
                configured_enabled=prospective_gap_monitor_enabled(),
                counter_names=(
                    "confirmation_run_count",
                    "postclose_finalize_count",
                    "no_session_count",
                    "scheduler_handoff_ingest_count",
                    "scheduler_handoff_error_count",
                ),
            ),
            deep_recovery_shadow_monitor=_monitor_status(
                getattr(state, "_omnix_trading_strategy_deep_recovery_shadow_monitor", None),
                expected_type=TradingStrategyDeepRecoveryShadowMonitor,
                configured_enabled=strategy_deep_recovery_shadow_monitor_enabled(),
                counter_names=(
                    "evaluation_count",
                    "state_transition_count",
                    "signal_count",
                    "execution_observation_count",
                ),
            ),
            prospective_economic_monitor=_monitor_status(
                getattr(state, "_omnix_trading_strategy_prospective_economic_monitor", None),
                expected_type=TradingStrategyProspectiveEconomicMonitor,
                configured_enabled=strategy_prospective_economic_monitor_enabled(),
                counter_names=(
                    "candidate_capture_count",
                    "signal_capture_count",
                    "outcome_capture_count",
                    "incomplete_outcome_count",
                ),
            ),
            universe_archive_monitor=_monitor_status(
                getattr(state, "_omnix_trading_strategy_universe_archive_monitor", None),
                expected_type=TradingStrategyUniverseArchiveMonitor,
                configured_enabled=strategy_universe_archive_monitor_enabled(),
                counter_names=("archive_count",),
            ),
            v2_qualification_monitor=_monitor_status(
                getattr(state, "_omnix_trading_strategy_v2_qualification_monitor", None),
                expected_type=TradingStrategyV2QualificationMonitor,
                configured_enabled=strategy_v2_qualification_monitor_enabled(),
                counter_names=("replay_count",),
            ),
            alpaca_status_monitor=_alpaca_status(
                getattr(state, "_omnix_alpaca_iex_status_monitor", None),
            ),
            execution_authority=False,
        )

    @router.get(
        "/yahoo-acquisition-status",
        response_model=StrategyRuntimeMonitorStatus,
    )
    def yahoo_acquisition_operations_status(
        request: Request,
    ) -> StrategyRuntimeMonitorStatus:
        return _monitor_status(
            getattr(
                request.app.state,
                "_omnix_trading_yahoo_acquisition_monitor",
                None,
            ),
            expected_type=TradingYahooAcquisitionMonitor,
            configured_enabled=yahoo_acquisition_monitor_enabled(),
            counter_names=(
                "capture_count",
                "capture_error_count",
                "active_symbol_count",
            ),
        )

    @router.get(
        "/interday-status",
        response_model=InterdayMonitorOperationsStatus,
    )
    def interday_monitor_operations_status(request: Request) -> InterdayMonitorOperationsStatus:
        # This is an operator/Codex diagnostic endpoint rather than a public UI
        # contract. Keeping it out of shared OpenAPI avoids coupling generated
        # clients to monitor-internal counters while preserving direct runtime
        # observability for live validation.
        return _interday_status(request.app.state)

    @router.get("/health", response_model=TradingOperationalHealth)
    async def strategy_operational_health(
        account_id: str = Query(min_length=1, max_length=200),
        instrument_id: str | None = Query(default=None, min_length=3, max_length=200),
        binding_id: str | None = Query(default=None, max_length=240),
    ) -> TradingOperationalHealth:
        observed_at = datetime.now(timezone.utc)
        repository = repository_factory()
        protection_repository = protection_repository_factory()
        strategy_repository = strategy_repository_factory()
        try:
            snapshot, manual_protections, configs = await asyncio.gather(
                asyncio.to_thread(repository.snapshot, account_id),
                asyncio.to_thread(protection_repository.list, account_id, active_only=True),
                asyncio.to_thread(strategy_repository.list_configs, active_only=True),
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if "account_not_found" in detail else 422
            raise HTTPException(status_code=status, detail=detail) from exc

        account_configs = [config for config in configs if config.account_id == account_id]
        strategy_protections = []
        for config in account_configs:
            strategy_protections.extend(
                await asyncio.to_thread(
                    strategy_repository.list_protections,
                    config.strategy_id,
                    active_only=True,
                )
            )
        start_time, end_time = day_bounds_et(observed_at)
        daily_realized = await asyncio.to_thread(
            strategy_repository.daily_paper_pnl,
            account_id,
            start_time=start_time,
            end_time=end_time,
        )
        risk = account_risk_health(
            snapshot=snapshot,
            manual_protections=manual_protections,
            strategy_protections=strategy_protections,
            strategy_configs=account_configs,
            daily_realized_pnl=daily_realized,
        )

        observation = None
        execution_error = None
        if instrument_id is not None:
            try:
                observation = await asyncio.to_thread(
                    market_service_factory().execution_observation,
                    instrument_id,
                    binding_id,
                )
            except Exception as exc:
                execution_error = f"{type(exc).__name__}: {exc}"
        execution = execution_health(
            observation,
            instrument_id=instrument_id,
            requested_binding_id=binding_id,
            error=execution_error,
            observed_at=observed_at,
        )
        return operational_health(
            observed_at=observed_at,
            risk=risk,
            execution=execution,
        )

    return router


__all__ = [
    "InterdayMonitorOperationsStatus",
    "StrategyOperationsStatus",
    "StrategyRuntimeMonitorStatus",
    "create_trading_strategy_operations_router",
]
