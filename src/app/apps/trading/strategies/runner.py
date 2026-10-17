"""The generic strategy runner (WP-8.3).

One scheduled task evaluates every enabled, non-archived configuration whose
kind is a registered ``Strategy``: it fetches the finalized bars the strategy
declares, calls ``evaluate`` once, and records each proposal as a
``proposal`` strategy event. Runner strategies are shadow-only: proposals are
evidence and never reach the order gateway from here.

The runner also runs the gap pullback configurations whose
``execution_owner`` names it (strategy runner WP, ``strategy_runner_pass``):
those go through the monitor's own pass and entry path, so the order gateway's
authorization, kill switches and protection arming apply unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .contract import Proposal, Strategy, StrategyContext
from .registrations import STRATEGY_REGISTRY
from .registry import StrategyRegistry

if TYPE_CHECKING:
    from ..strategy_runner_pass import RunnerOwnedConfigs

logger = logging.getLogger(__name__)

TASK_ID = "trading.strategy_runner"
RUNTIME_STATE_KEY = "_omnix_trading_strategy_runner"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StrategyRunner:
    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], Any],
        market_service_factory: Callable[[], Any],
        paper_repository_factory: Callable[[], Any] | None = None,
        registry: StrategyRegistry = STRATEGY_REGISTRY,
        clock: Callable[[], datetime] = _now,
        owned_configs_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.market_service_factory = market_service_factory
        self.registry = registry
        self.clock = clock
        self.paper_repository_factory = paper_repository_factory
        self.owned_configs_enabled = owned_configs_enabled
        # Built on the first cycle, so booting the runner imports none of the
        # monitor's pass.
        self.owned_configs: RunnerOwnedConfigs | None = None
        self.interval_seconds: float | None = None
        self.last_run_at: datetime | None = None
        self.strategies_error: str | None = None

    def _owned_configs(self) -> RunnerOwnedConfigs | None:
        if self.owned_configs is None and self.paper_repository_factory is not None:
            from ..strategy_runner_pass import RunnerOwnedConfigs

            options: dict[str, Any] = {}
            if self.owned_configs_enabled is not None:
                options["enabled"] = self.owned_configs_enabled
            self.owned_configs = RunnerOwnedConfigs(
                strategy_repository_factory=self.strategy_repository_factory,
                paper_repository_factory=self.paper_repository_factory,
                market_service_factory=self.market_service_factory,
                clock=self.clock,
                **options,
            )
        return self.owned_configs

    async def run_cycle(self) -> int:
        """One scheduled cycle: the registered strategies, then the gap pullback
        configurations the runner owns or shadows. Returns the proposals recorded
        plus the paper orders placed."""
        recorded = 0
        # Runner-owned configurations first: they may hold open positions whose
        # protections their pass reconciles.
        owned_configs = self._owned_configs()
        if owned_configs is not None:
            recorded += await owned_configs.run_once()
        try:
            recorded += await asyncio.to_thread(self.run_once)
            self.strategies_error = None
        except Exception as exc:
            # A registered strategy's failure must not stop the owned configurations.
            self.strategies_error = f"{type(exc).__name__}: {exc}"
            logger.warning("strategy runner: registered strategies failed: %s", exc)
        self.last_run_at = self.clock()
        return recorded

    @property
    def last_error(self) -> str | None:
        owned_error = self.owned_configs.host.last_error if self.owned_configs is not None else None
        return owned_error or self.strategies_error

    @property
    def paper_order_count(self) -> int:
        return self.owned_configs.host.paper_order_count if self.owned_configs is not None else 0

    @property
    def owned_config_count(self) -> int:
        return len(self.owned_configs.owned_strategy_ids) if self.owned_configs is not None else 0

    @property
    def shadowed_config_count(self) -> int:
        return len(self.owned_configs.shadowed_strategy_ids) if self.owned_configs is not None else 0

    def diagnostics(self) -> dict[str, Any]:
        owned = self.owned_configs
        return {
            "owned_configs_enabled": owned.enabled() if owned is not None else None,
            "owned_strategy_ids": list(owned.owned_strategy_ids) if owned is not None else [],
            "shadowed_strategy_ids": list(owned.shadowed_strategy_ids) if owned is not None else [],
            "auto_paper_readiness_by_strategy": dict(owned.host.auto_paper_readiness_by_strategy)
            if owned is not None else {},
            "registered_strategy_kinds": [strategy.kind for strategy in self.registry.runner_strategies()],
            "strategies_error": self.strategies_error,
            "live_broker_enabled": False,
            "ai_order_placement_enabled": False,
        }

    async def close(self) -> None:
        """Stop the intraday LLM annotations a runner-owned pass started."""
        if self.owned_configs is not None:
            await self.owned_configs.host.intraday_llm_annotations.close()

    def run_once(self) -> int:
        """Evaluate every runnable configuration; return the proposals recorded."""
        strategies = {strategy.kind: strategy for strategy in self.registry.runner_strategies()}
        if not strategies:
            return 0
        repository = self.strategy_repository_factory()
        market = self.market_service_factory()
        recorded = 0
        for config in repository.list_configs(active_only=True):
            strategy = strategies.get(config.strategy_kind)
            if strategy is None or not config.enabled or config.archived_at is not None or config.mode == "off":
                continue
            recorded += self._run_config(strategy, config, repository, market)
        return recorded

    def _instruments(self, strategy: Strategy, config, repository) -> tuple[str, ...]:
        if config.active_universe_id:
            universe = repository.get_universe(config.active_universe_id)
            return tuple(candidate.instrument_id for candidate in universe.candidates)
        return strategy.data_requirements.instruments

    def _run_config(self, strategy: Strategy, config, repository, market) -> int:
        observed_at = self.clock()
        requirements = strategy.data_requirements
        bars = {}
        for instrument_id in self._instruments(strategy, config, repository):
            try:
                response = market.bars(instrument_id, requirements.interval, requirements.lookback_bars)
            except Exception as exc:
                logger.warning("strategy %s: bars for %s unavailable: %s", config.strategy_id, instrument_id, exc)
                continue
            # Only bars that had closed by the evaluation time.
            bars[instrument_id] = tuple(
                bar for bar in response.bars if bar.is_final and bar.end_time <= observed_at
            )
        context = StrategyContext(
            strategy_id=config.strategy_id,
            config=config.config,
            risk=config.risk,
            observed_at=observed_at,
            bars=bars,
        )
        recorded = 0
        for proposal in strategy.evaluate(context):
            recorded += bool(repository.append_event(_proposal_event(strategy, config, proposal, observed_at)))
        return recorded


def _proposal_event(strategy: Strategy, config, proposal: Proposal, observed_at: datetime):
    from ..strategy_repository import StrategyEvent

    # One proposal per strategy, instrument, reason and minute.
    minute = observed_at.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
    key = hashlib.sha256(
        "|".join((config.strategy_id, strategy.kind, proposal.instrument_id, proposal.reason_code, minute)).encode()
    ).hexdigest()
    return StrategyEvent(
        strategy_id=config.strategy_id,
        event_id=key[:32],
        instrument_id=proposal.instrument_id,
        event_type="proposal",
        state="proposed",
        reason_code=proposal.reason_code,
        observed_at=observed_at,
        idempotency_key=key,
        payload={
            "proposal": proposal.model_dump(mode="json"),
            "strategy_kind": strategy.kind,
            "strategy_version": strategy.version,
            "execution_authority": False,
        },
    )


def strategy_runner_task(context) -> Any:
    """The runner's scheduled task.

    It always runs: a gap pullback configuration can move to the runner at
    any time. Its cadence is the strategy monitor's, so a runner-owned
    configuration sees every finalized bar the monitor would.
    """
    from app.runtime.scheduler import ScheduledTaskSpec

    from ..paper_runtime_repository import default_runtime_paper_repository
    from ..service import default_market_data_service
    from ..strategy_monitor import _interval_seconds
    from ..strategy_repository import default_strategy_repository

    runner = StrategyRunner(
        strategy_repository_factory=default_strategy_repository,
        market_service_factory=default_market_data_service,
        paper_repository_factory=default_runtime_paper_repository,
    )
    interval_seconds = _interval_seconds()
    runner.interval_seconds = interval_seconds
    # The operations status reads it from the runtime state, as it reads the monitor.
    state = getattr(context, "runtime_state", None)
    if state is not None:
        setattr(state, RUNTIME_STATE_KEY, runner)

    async def run(_task_context) -> int:
        return await runner.run_cycle()

    return ScheduledTaskSpec(
        task_id=TASK_ID,
        run=run,
        interval_seconds=interval_seconds,
        jitter_seconds=min(1.0, interval_seconds * 0.05),
        timeout_seconds=max(120.0, interval_seconds),
        executor="async",
        on_shutdown=(runner.close,),
    )
