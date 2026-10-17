from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.apps.trading.us_equity_calendar import EASTERN as _ET
from app.config.env import env_str as _env_str
from app.runtime.features import FeatureContext

from .gapper_dataset import GapperCandidate
from .indicators.engine import relative_strength_index
from .monitor_task import ScheduledTradingMonitor, SingleFlightTasks, TradingMonitorTask
from .paper import PaperMarketObservation
from .paper_repository import TradingPaperRepository
from .paper_runtime_repository import default_runtime_paper_repository
from .providers.request_budget import in_provider_lane
from .service import TradingMarketDataService, default_market_data_service
from .strategies.models import GapPullbackResult
from .strategy_evaluability import (
    assess_bar_coverage,
)
from .strategy_intraday_learning import (
    IntradayLearningSnapshot,
)
from .strategy_intraday_llm import (
    IntradayLLMAnalyzer,
)
from .strategy_managed_finviz_shadow import (
    managed_finviz_shadow_autoprovision_enabled,
    provision_managed_finviz_shadow_strategy,
)
from .strategy_repository import (
    StrategyEvent,
    TradingStrategyConfigDocument,
    TradingStrategyRepository,
    default_strategy_repository,
)
from .strategy_shadow_universe import (
    resolve_stoch_rsi_5m_runtime_archive,
)
from .strategy_stoch_rsi_5m_monitor import record_stoch_rsi_5m_candidates
from .strategy_tape_prefetch import prefetch_candidate_tapes
from .strategy_timeframes import proposal_priority
from .strategy_v2_qualification import (
    V2_PROSPECTIVE_START,
    V2_QUALIFICATION_EVENT_TYPES,
)
from .trade_logging import trade_log

if TYPE_CHECKING:
    from .order_gateway import StrategyPaperAccess

_STATE_KEY = "_omnix_trading_strategy_monitor"
_REGULAR_OPEN = time(9, 30)
_DIAGNOSTIC_LOG_INTERVAL = timedelta(minutes=5)


def _finalized_bars_for_session(bars, session_date: date):
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_final
            and bar.start_time.astimezone(_ET).date() == session_date
        ),
        key=lambda bar: bar.start_time,
    )


def _current_session_1m_integrity(
    bars,
    *,
    session_date: date,
    observed_at: datetime,
) -> tuple[bool, str]:
    assessment = assess_bar_coverage(
        list(bars),
        session_date=session_date,
        observed_at=observed_at,
        provider="configured_history",
    )
    if assessment.ready:
        return True, "CURRENT_SESSION_1M_READY"
    return (
        False,
        assessment.reason_codes[0]
        if assessment.reason_codes
        else "CURRENT_SESSION_1M_UNAVAILABLE",
    )


STRATEGY_MONITOR_OWNER = "strategy_monitor"


def strategy_monitor_owns(config) -> bool:
    """Whether this monitor evaluates the configuration's kind (registry ``owner``)."""

    from .strategies.registry import MonitorOwnedStrategy

    entry = type(config).strategy_registry.get(config.strategy_kind)
    return isinstance(entry, MonitorOwnedStrategy) and entry.owner == STRATEGY_MONITOR_OWNER


def _flag(name: str, default: str = "1") -> bool:
    return _env_str(name, default).strip().lower() in {"1", "true", "yes", "on"}


def trading_strategy_monitor_enabled() -> bool:
    if _env_str("OMNIX_PERSISTENCE_MODE", "").strip() == "legacy_test":
        return _flag("OMNIX_TRADING_STRATEGY_MONITOR_IN_TESTS", "0")
    return _flag("OMNIX_TRADING_STRATEGY_MONITOR", "1")


def _interval_seconds() -> float:
    try:
        value = float(_env_str("OMNIX_TRADING_STRATEGY_INTERVAL_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return max(5.0, value)


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _trade_attempt_id(strategy_id: str, instrument_id: str, observed_at: datetime) -> str:
    """Stable identity for one causal finalized-bar entry opportunity."""
    digest = _key(
        strategy_id,
        instrument_id,
        observed_at.astimezone(timezone.utc).isoformat(),
        "long-entry-signal",
    )
    return f"attempt-{digest[:24]}"


def _v2_qualification_events(
    repository: TradingStrategyRepository,
    strategy_id: str,
    *,
    now: datetime,
) -> list[StrategyEvent]:
    start = datetime(
        V2_PROSPECTIVE_START.year,
        V2_PROSPECTIVE_START.month,
        V2_PROSPECTIVE_START.day,
        tzinfo=timezone.utc,
    )
    end = now.astimezone(timezone.utc) + timedelta(seconds=1)
    if hasattr(repository, "events_by_types_between"):
        return repository.events_by_types_between(
            strategy_id,
            event_types=V2_QUALIFICATION_EVENT_TYPES,
            start_time=start,
            end_time=end,
            limit=20_000,
        )
    return [
        event
        for event in repository.recent_events(strategy_id, 20_000)
        if event.event_type in V2_QUALIFICATION_EVENT_TYPES
        and start <= event.observed_at.astimezone(timezone.utc) < end
    ]


# Start no new configuration once a pass has used this share of its timeout.
PASS_BUDGET_FRACTION = 0.8


def _recovery_snapshot(market_service) -> dict[str, dict[str, float]] | None:
    metrics = getattr(market_service, "recovery_metrics", None)
    return metrics.snapshot() if metrics is not None else None


def _recovery_delta(before, after) -> dict[str, dict[str, float]] | None:
    """Bar-recovery work done process-wide while the pass ran (all monitors)."""
    if before is None or after is None:
        return None
    delta = {}
    for stage, values in after.items():
        calls = values["calls"] - before.get(stage, {}).get("calls", 0)
        if calls:
            seconds = values["seconds"] - before.get(stage, {}).get("seconds", 0.0)
            delta[stage] = {"calls": calls, "seconds": round(seconds, 3)}
    return delta


def _run_id(prefix: str, observed_at: datetime) -> str:
    return f"{prefix}-{observed_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"


def _execution_audit_payload(execution) -> dict[str, Any]:
    fields = (
        "instrument_id",
        "binding_id",
        "provider",
        "last",
        "bid",
        "ask",
        "bid_size",
        "ask_size",
        "high",
        "low",
        "bar_volume",
        "bar_start_time",
        "source_time",
        "spread_bps",
        "execution_eligible",
        "freshness_mode",
        "rejection_reasons",
        "halted",
    )
    return {field: getattr(execution, field, None) for field in fields}


def _bar_audit_payload(bar) -> dict[str, Any]:
    fields = (
        "instrument_id",
        "interval",
        "start_time",
        "end_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "provider",
        "session",
        "is_final",
    )
    return {field: getattr(bar, field, None) for field in fields}


def _paper_observation(execution) -> PaperMarketObservation:
    return PaperMarketObservation(
        instrument_id=execution.instrument_id,
        binding_id=execution.binding_id,
        provider=execution.provider,
        price=execution.last,
        bid=execution.bid,
        ask=execution.ask,
        bid_size=execution.bid_size,
        ask_size=execution.ask_size,
        high=execution.high,
        low=execution.low,
        volume=execution.bar_volume,
        bar_start_time=execution.bar_start_time,
        source_time=execution.source_time,
        evaluated_at=datetime.now(timezone.utc),
        execution_eligible=execution.execution_eligible,
        freshness_mode=execution.freshness_mode,
        rejection_reasons=execution.rejection_reasons,
        halted=execution.halted is True,
    )


_BASIC_MARKET_REJECTIONS = {
    "DATA_INCOMPLETE",
    "GAP_BELOW_MINIMUM",
    "PRICE_OUT_OF_RANGE",
    "PREMARKET_DOLLAR_VOLUME_LOW",
    "TOD_RVOL_MISSING",
    "TOD_RVOL_LOW",
    "SPREAD_MISSING",
    "SPREAD_TOO_WIDE",
}
_RESEARCH_SUPPLY_REJECTIONS = {
    "CATALYST_EVIDENCE_REQUIRED",
    "DILUTION_SUPPLY_RISK",
    "FLOAT_OUTSIDE_REQUIRED_RANGE",
}


def _candidate_lifecycle_stage(result: GapPullbackResult) -> int:
    """Highest lifecycle rank actually proven by this causal evaluation."""
    transitions = set(result.transitions)
    if result.state == "entry_ready":
        return 4
    if "higher_low_confirmed" in transitions or result.state in {
        "higher_low_confirmed", "vwap_reclaim", "lower_high_break", "breakout_hold"
    }:
        return 3
    if result.reason_code in _BASIC_MARKET_REJECTIONS:
        return 0
    if result.reason_code in _RESEARCH_SUPPLY_REJECTIONS:
        return 1
    if "qualified_gap" in transitions or result.state not in {"discovered", "rejected"}:
        return 2
    return 0


def _rsi_crossed_after_activation(
    bars,
    *,
    period: int,
    threshold: Decimal,
    activated_at: datetime,
    observed_at: datetime,
) -> bool:
    """Return true when any finalized post-entry bar confirms the configured RSI cross.

    RSI values use the same shared indicator implementation as the portfolio
    backtester. Looking across all bars since activation prevents a 30-second
    monitor from missing a cross that occurred between polling cycles.
    """
    session_date = activated_at.astimezone(_ET).date()
    finalized = sorted(
        (
            bar for bar in bars
            if bar.is_final
            and bar.end_time <= observed_at
            and bar.start_time.astimezone(_ET).date() == session_date
        ),
        key=lambda bar: bar.start_time,
    )
    values = relative_strength_index([bar.close for bar in finalized], period)
    for index in range(1, len(values)):
        bar_index = period + index
        if bar_index >= len(finalized) or finalized[bar_index].end_time <= activated_at:
            continue
        if values[index - 1] >= threshold and values[index] < threshold:
            return True
    return False


@dataclass(frozen=True)
class _EntryProposal:
    candidate: GapperCandidate
    result: GapPullbackResult
    observed_at: datetime

    @property
    def priority(self) -> tuple[datetime, int, int, str]:
        quality_score = (
            self.result.signal.quality_score
            if self.result.signal is not None
            else self.result.features.quality_score
        )
        return proposal_priority(
            observed_at=self.observed_at,
            quality_score=quality_score,
            discovery_rank=self.candidate.discovery_rank,
            instrument_id=self.candidate.instrument_id,
        )


class StrategyRunHost:
    """The state and helpers one strategy configuration pass uses.

    The scheduled strategy monitor is one host; the strategy runner owns
    another for the gap pullback configurations it runs. The pass itself
    (``strategy_monitor_config_run.run_config``) and the entry submission path
    (``strategy_entry_path``) are shared.
    """

    # Strategy diagnostics count their evaluations here.
    diagnostic_evaluation_count: int = 0
    # Which owner this host runs passes for (strategy runner WP): the monitor
    # runs every configuration the strategy runner does not own.
    execution_role: str = "monitor"

    def __init__(
        self,
        *,
        intraday_llm_analyzer_factory: Callable[[], IntradayLLMAnalyzer] = IntradayLLMAnalyzer,
    ) -> None:
        self.intraday_llm_analyzer_factory = intraday_llm_analyzer_factory
        self.current_run_id: str | None = None
        self.last_error: str | None = None
        self.evaluation_count = 0
        self.signal_count = 0
        self.paper_order_count = 0
        self.rejection_count = 0
        self.yahoo_recovered_candidate_evaluation_count = 0
        self.yahoo_unresolved_candidate_evaluation_count = 0
        self.intraday_learning_snapshot_count = 0
        self.intraday_llm_call_count = 0
        self.intraday_llm_assessment_count = 0
        self.intraday_llm_error_count = 0
        self.intraday_llm_input_character_count = 0
        self.intraday_llm_input_token_count = 0
        self.intraday_llm_output_token_count = 0
        self.intraday_llm_total_token_count = 0
        self.intraday_llm_estimated_usage_count = 0
        self._last_evaluated_bar_end: dict[tuple[str, str, str], datetime] = {}
        self._last_diagnostic_log_at: dict[tuple[str, ...], datetime] = {}
        self.auto_paper_readiness_by_strategy: dict[str, dict[str, Any]] = {}
        self.auto_paper_ready_strategy_count = 0
        self.auto_paper_blocked_strategy_count = 0
        self.auto_paper_archive_not_ready_strategy_count = 0
        self.auto_paper_qualification_blocked_strategy_count = 0
        # Intraday LLM annotations run beside the cycle, one per strategy (WP-8.3).
        self.intraday_llm_annotations = SingleFlightTasks(on_error=self._intraday_llm_failed)

    def _intraday_llm_failed(self, exc: BaseException) -> None:
        self.intraday_llm_error_count += 1
        trade_log("auto_trading", "intraday_llm_error", error_type=type(exc).__name__, detail=str(exc),
                  execution_authority=False)

    def _set_auto_paper_readiness(
        self,
        config: TradingStrategyConfigDocument,
        *,
        state: str,
        reason: str,
        observed_at: datetime,
        universe_id: str | None = None,
    ) -> None:
        if config.mode != "auto_paper":
            return
        self.auto_paper_readiness_by_strategy[config.strategy_id] = {
            "state": state,
            "reason": reason,
            "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            "universe_id": universe_id,
            "paper_execution_authority": state == "ready",
        }

    def _should_log_diagnostic(
        self,
        key: tuple[str, ...],
        observed_at: datetime,
    ) -> bool:
        previous = self._last_diagnostic_log_at.get(key)
        if previous is not None and observed_at < previous + _DIAGNOSTIC_LOG_INTERVAL:
            return False
        self._last_diagnostic_log_at[key] = observed_at
        return True

    async def _event(
        self,
        repository: TradingStrategyRepository,
        config: TradingStrategyConfigDocument,
        *,
        instrument_id: str,
        event_type: str,
        state: str,
        reason_code: str,
        observed_at: datetime,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        idem = _key(
            config.strategy_id,
            instrument_id,
            event_type,
            state,
            reason_code,
            observed_at.isoformat(),
        )
        return await asyncio.to_thread(
            repository.append_event,
            StrategyEvent(
                strategy_id=config.strategy_id,
                event_id=idem[:32],
                run_id=self.current_run_id,
                instrument_id=instrument_id,
                event_type=event_type,
                state=state,
                reason_code=reason_code,
                observed_at=observed_at,
                idempotency_key=idem,
                payload=payload or {},
            ),
        )

    async def _run_intraday_llm(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        universe,
        ranked_learning: list[tuple[GapperCandidate, GapPullbackResult, datetime, IntradayLearningSnapshot]],
    ) -> None:
        from .strategy_monitor_intraday_llm import run_intraday_llm

        return await run_intraday_llm(self, config, strategy_repository, universe, ranked_learning)

    @in_provider_lane("protective")
    async def _reconcile_protections(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        paper_repository: StrategyPaperAccess,
        market_service: TradingMarketDataService,
    ) -> None:
        from .strategy_monitor_protections import reconcile_protections

        return await reconcile_protections(self, config, strategy_repository, paper_repository, market_service)

    async def _evaluate_candidates(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        universe,
    ) -> list[_EntryProposal]:
        from .strategy_monitor_candidates import evaluate_candidates

        return await evaluate_candidates(self, config, strategy_repository, market_service, universe)

    async def _record_diagnostic_v2_candidates(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        universe,
    ) -> None:
        from .strategy_monitor_diagnostics import record_diagnostic_v2_candidates

        return await record_diagnostic_v2_candidates(self, config, strategy_repository, market_service, universe)

    async def _proposals_evaluated(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        proposals: list[_EntryProposal],
    ) -> bool:
        """See the pass's proposals before shadow observation or entry; False ends the pass.

        While the strategy runner shadows the configuration, the owner records
        its proposals as parity evidence.
        """
        from .strategy_runner_parity import record_parity_proposals, runner_shadowed

        if proposals and runner_shadowed(config):
            await record_parity_proposals(self, config, strategy_repository, proposals, source="monitor")
        return True


    async def _run_stoch_rsi_5m_config(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        market_service: TradingMarketDataService,
        *,
        now_utc: datetime,
    ) -> None:
        if config.mode != "shadow":
            trade_log(
                "auto_trading",
                "stoch_rsi_5m_skipped",
                run_id=self.current_run_id,
                strategy_id=config.strategy_id,
                reason="shadow_only",
                execution_authority=False,
            )
            return

        universe_source = "active_universe"
        if config.active_universe_id is not None:
            universe = await asyncio.to_thread(
                strategy_repository.get_universe,
                config.active_universe_id,
            )
        else:
            universe = await asyncio.to_thread(
                resolve_stoch_rsi_5m_runtime_archive,
                config,
                strategy_repository,
                now=now_utc,
            )
            universe_source = "auto_archive_shadow"
        if universe is None:
            trade_log(
                "auto_trading",
                "strategy_cycle_skipped",
                run_id=self.current_run_id,
                strategy_id=config.strategy_id,
                reason="stoch_rsi_5m_universe_not_ready",
                execution_authority=False,
            )
            return

        today_et = now_utc.astimezone(_ET).date()
        if universe.session_date != today_et:
            await self._event(
                strategy_repository,
                config,
                instrument_id="__universe__",
                event_type="stoch_rsi_5m",
                state="data_gap",
                reason_code="STOCH_RSI_5M_UNIVERSE_SESSION_MISMATCH",
                observed_at=now_utc,
                payload={
                    "universe_id": universe.universe_id,
                    "universe_session_date": universe.session_date.isoformat(),
                    "runtime_session_date": today_et.isoformat(),
                    "research_only": True,
                    "execution_authority": False,
                },
            )
            return

        await prefetch_candidate_tapes(market_service, universe, interval="5m", as_of=now_utc)
        await record_stoch_rsi_5m_candidates(
            record_event=self._event,
            current_run_id=self.current_run_id,
            config=config,
            strategy_repository=strategy_repository,
            market_service=market_service,
            universe=universe,
            observed_at=now_utc,
        )
        trade_log(
            "auto_trading",
            "strategy_cycle_no_entry_work",
            run_id=self.current_run_id,
            strategy_id=config.strategy_id,
            mode=config.mode,
            strategy_kind=config.strategy_kind,
            universe_id=universe.universe_id,
            runtime_universe_source=universe_source,
            proposal_count=0,
            research_only=True,
            execution_authority=False,
        )

    async def _run_config(
        self,
        config: TradingStrategyConfigDocument,
        strategy_repository: TradingStrategyRepository,
        paper_repository: TradingPaperRepository,
        market_service: TradingMarketDataService,
    ) -> None:
        from .strategy_monitor_config_run import run_config

        exclusive = getattr(strategy_repository, "exclusive_pass", None)
        if exclusive is None:
            return await run_config(self, config, strategy_repository, paper_repository, market_service)
        # One pass of a configuration at a time, across processes; then re-read
        # it, because its execution owner may have changed since the cycle
        # listed it.
        lock = exclusive(config.strategy_id)
        acquired = await asyncio.to_thread(lock.__enter__)
        try:
            if not acquired:
                trade_log(
                    "auto_trading",
                    "strategy_cycle_skipped",
                    run_id=self.current_run_id,
                    strategy_id=config.strategy_id,
                    reason="pass_in_progress",
                )
                return None
            current = await asyncio.to_thread(strategy_repository.get_config, config.strategy_id)
            if not self._owns(current):
                trade_log(
                    "auto_trading",
                    "strategy_cycle_skipped",
                    run_id=self.current_run_id,
                    strategy_id=config.strategy_id,
                    reason="execution_owner_changed",
                    execution_role=self.execution_role,
                )
                return None
            return await run_config(self, current, strategy_repository, paper_repository, market_service)
        finally:
            await asyncio.to_thread(lock.__exit__, None, None, None)

    def _owns(self, config: TradingStrategyConfigDocument) -> bool:
        owner = getattr(config.config, "execution_owner", "monitor")
        return owner == "runner" if self.execution_role == "runner" else owner != "runner"



class TradingStrategyMonitor(ScheduledTradingMonitor, StrategyRunHost):
    """Deterministic strategy runner with OFF/SHADOW/AUTO_PAPER modes only.

    AUTO_PAPER can create orders exclusively in the existing paper repository.
    There is intentionally no live-broker adapter or AI order-placement path.
    """

    error_event = "monitor_loop_error"
    # Configurations a previous pass did not finish; they run first next pass.
    _resume_first: tuple[str, ...] = ()

    def error_log_fields(self) -> dict[str, Any]:
        return {"run_id": self.current_run_id}

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], TradingStrategyRepository] = default_strategy_repository,
        paper_repository_factory: Callable[[], TradingPaperRepository] = default_runtime_paper_repository,
        market_service_factory: Callable[[], TradingMarketDataService] = default_market_data_service,
        intraday_llm_analyzer_factory: Callable[[], IntradayLLMAnalyzer] = IntradayLLMAnalyzer,
        interval_seconds: float | None = None,
    ) -> None:
        StrategyRunHost.__init__(self, intraday_llm_analyzer_factory=intraday_llm_analyzer_factory)
        self.strategy_repository_factory = strategy_repository_factory
        self.paper_repository_factory = paper_repository_factory
        self.market_service_factory = market_service_factory
        self.interval_seconds = interval_seconds or _interval_seconds()
        self.last_run_at: datetime | None = None
        self.managed_finviz_shadow_provision: dict[str, Any] | None = None
        self.managed_finviz_shadow_provision_error: str | None = None

    async def run_once(self) -> int:
        from .strategy_runner_parity import runner_owned

        strategy_repository = self.strategy_repository_factory()
        paper_repository = self.paper_repository_factory()
        market_service = self.market_service_factory()
        configs = await asyncio.to_thread(strategy_repository.list_configs, active_only=True)
        before = self.paper_order_count
        self.auto_paper_readiness_by_strategy = {}
        self.auto_paper_ready_strategy_count = 0
        self.auto_paper_blocked_strategy_count = 0
        self.auto_paper_archive_not_ready_strategy_count = 0
        self.auto_paper_qualification_blocked_strategy_count = 0
        started_at = datetime.now(timezone.utc)
        self.current_run_id = _run_id("auto", started_at)
        log_monitor_heartbeat = self._should_log_diagnostic(
            ("monitor_heartbeat",),
            started_at,
        )
        if log_monitor_heartbeat:
            trade_log(
                "auto_trading",
                "monitor_run_start",
                run_id=self.current_run_id,
                started_at=started_at,
                active_strategy_count=len(configs),
                interval_seconds=self.interval_seconds,
                evaluation_count_before=self.evaluation_count,
                signal_count_before=self.signal_count,
                paper_order_count_before=self.paper_order_count,
                rejection_count_before=self.rejection_count,
            )
        pass_started = perf_counter()
        recovery_before = _recovery_snapshot(market_service)
        budget_seconds = self.run_timeout_seconds() * PASS_BUDGET_FRACTION
        timings: dict[str, float] = {}
        deferred: list[str] = []
        # The strategy runner runs runner-owned configurations; kinds owned by
        # another monitor (e.g. overnight_shadow_v1) are never evaluated here.
        eligible = [
            config for config in configs
            if not runner_owned(config) and strategy_monitor_owns(config)
        ]
        unfinished = [config.strategy_id for config in self._pass_order(eligible)]
        try:
            for config in self._pass_order(eligible):
                if perf_counter() - pass_started >= budget_seconds:
                    # Out of time: start no new configuration; it runs first next pass.
                    deferred.append(config.strategy_id)
                    continue
                config_started = perf_counter()
                try:
                    await self._run_config(config, strategy_repository, paper_repository, market_service)
                    unfinished.remove(config.strategy_id)
                except Exception as exc:
                    unfinished.remove(config.strategy_id)
                    self.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                    self._set_auto_paper_readiness(
                        config,
                        state="blocked",
                        reason="runtime_error",
                        observed_at=datetime.now(timezone.utc),
                    )
                    trade_log(
                        "auto_trading",
                        "strategy_cycle_error",
                        run_id=self.current_run_id,
                        strategy_id=config.strategy_id,
                        error_type=type(exc).__name__,
                        detail=str(exc),
                    )
                finally:
                    timings[config.strategy_id] = round(perf_counter() - config_started, 3)
            readiness = list(self.auto_paper_readiness_by_strategy.values())
            self.auto_paper_ready_strategy_count = sum(
                1 for item in readiness if item.get("state") == "ready"
            )
            self.auto_paper_blocked_strategy_count = sum(
                1 for item in readiness if item.get("state") == "blocked"
            )
            self.auto_paper_archive_not_ready_strategy_count = sum(
                1
                for item in readiness
                if item.get("reason") == "daily_universe_not_ready"
            )
            self.auto_paper_qualification_blocked_strategy_count = sum(
                1
                for item in readiness
                if item.get("reason") == "qualification_not_authorized"
            )
            self.last_run_at = datetime.now(timezone.utc)
            new_orders = self.paper_order_count - before
            if log_monitor_heartbeat or new_orders:
                trade_log(
                    "auto_trading",
                    "monitor_run_complete",
                    run_id=self.current_run_id,
                    started_at=started_at,
                    completed_at=self.last_run_at,
                    new_paper_orders=new_orders,
                    last_error=self.last_error,
                    evaluation_count=self.evaluation_count,
                    signal_count=self.signal_count,
                    paper_order_count=self.paper_order_count,
                    rejection_count=self.rejection_count,
                )
            return new_orders
        finally:
            # Anything not finished (out of budget, or interrupted by the
            # scheduler's timeout) runs first next pass, so no configuration is
            # starved by the ones ahead of it.
            self._resume_first = tuple(unfinished)
            trade_log(
                "auto_trading",
                "monitor_pass_timing",
                run_id=self.current_run_id,
                pass_seconds=round(perf_counter() - pass_started, 3),
                strategy_seconds=timings,
                unfinished=list(unfinished),
                deferred_for_budget=deferred,
                bar_recovery=_recovery_delta(recovery_before, _recovery_snapshot(market_service)),
                execution_authority=False,
            )
            self.current_run_id = None

    def _pass_order(self, configs: list[TradingStrategyConfigDocument]) -> list[TradingStrategyConfigDocument]:
        first = [config for config in configs if config.strategy_id in self._resume_first]
        return first + [config for config in configs if config.strategy_id not in self._resume_first]

    def diagnostics(self) -> dict[str, Any]:
        return {
            "enabled": trading_strategy_monitor_enabled(),
            "running": self.scheduled,
            "interval_seconds": self.interval_seconds,
            "current_run_id": self.current_run_id,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "evaluation_count": self.evaluation_count,
            "signal_count": self.signal_count,
            "paper_order_count": self.paper_order_count,
            "rejection_count": self.rejection_count,
            "yahoo_recovered_candidate_evaluation_count": self.yahoo_recovered_candidate_evaluation_count,
            "yahoo_unresolved_candidate_evaluation_count": self.yahoo_unresolved_candidate_evaluation_count,
            "auto_paper_ready_strategy_count": self.auto_paper_ready_strategy_count,
            "auto_paper_blocked_strategy_count": self.auto_paper_blocked_strategy_count,
            "auto_paper_archive_not_ready_strategy_count": self.auto_paper_archive_not_ready_strategy_count,
            "auto_paper_qualification_blocked_strategy_count": self.auto_paper_qualification_blocked_strategy_count,
            "auto_paper_readiness_by_strategy": self.auto_paper_readiness_by_strategy,
            "candidate_arbitration": "observed_at_quality_score_discovery_rank_instrument",
            "live_broker_enabled": False,
            "ai_order_placement_enabled": False,
            "managed_finviz_shadow_provision": self.managed_finviz_shadow_provision,
            "managed_finviz_shadow_provision_error": self.managed_finviz_shadow_provision_error,
        }

async def prepare_trading_strategy_monitor_for_scheduled_execution(
    monitor: TradingStrategyMonitor,
) -> None:
    if not managed_finviz_shadow_autoprovision_enabled():
        return
    try:
        provision = await asyncio.to_thread(
            provision_managed_finviz_shadow_strategy,
            strategy_repository=monitor.strategy_repository_factory(),
            paper_repository=monitor.paper_repository_factory(),
        )
        monitor.managed_finviz_shadow_provision = provision.model_dump(mode="json")
        monitor.managed_finviz_shadow_provision_error = None
        if (
            monitor.last_error is not None
            and monitor.last_error.startswith("managed_finviz_shadow_provision:")
        ):
            monitor.last_error = None
    except Exception as exc:
        monitor.managed_finviz_shadow_provision = None
        monitor.managed_finviz_shadow_provision_error = (
            f"{type(exc).__name__}: {exc}"
        )
        monitor.last_error = (
            "managed_finviz_shadow_provision: "
            f"{type(exc).__name__}: {exc}"
        )
        trade_log(
            "auto_trading",
            "managed_finviz_shadow_provision_error",
            error_type=type(exc).__name__,
            detail=str(exc),
        )


def create_trading_strategy_monitor_task(context: FeatureContext) -> TradingMonitorTask | None:
    state = context.runtime_state
    existing = getattr(state, _STATE_KEY, None)
    if isinstance(existing, TradingStrategyMonitor):
        return None
    monitor = TradingStrategyMonitor()
    setattr(state, _STATE_KEY, monitor)
    return TradingMonitorTask(
        name=__name__,
        monitor=monitor,
        enabled=trading_strategy_monitor_enabled,
        startup=(lambda: prepare_trading_strategy_monitor_for_scheduled_execution(monitor),),
        shutdown=(monitor.intraday_llm_annotations.close,),
    )
