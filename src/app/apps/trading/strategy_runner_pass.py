"""Gap pullback configurations the strategy runner runs (strategy runner WP).

``execution_owner="runner"``: the runner's own ``StrategyRunHost`` runs the
configuration's whole pass (protection reconciliation, candidate evaluation
and the shared entry path through the order gateway: entry authorization,
kill switches, daily loss) exactly as the monitor would. The monitor skips it.

``execution_owner="runner_shadow"``: the monitor stays the owner. The runner
runs the same pass without writing anything: strategy events are discarded,
every other strategy write and every paper account write is refused,
protections and the intraday LLM are left to the owner, and the pass ends at
its proposals, which are recorded as parity evidence.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

from .strategy_monitor import StrategyRunHost, _EntryProposal, _run_id, trading_strategy_monitor_enabled
from .strategy_runner_parity import record_parity_proposals, runner_owned, runner_shadowed
from .trade_logging import trade_log


class _DiscardingStrategyRepository:
    """The strategy repository's reads; appended events are discarded; anything else is refused."""

    _READS = frozenset({
        "list_configs",
        "get_config",
        "get_universe",
        "list_universes",
        "recent_events",
        "events_between",
        "events_by_types_between",
        "entry_events_between",
        "daily_paper_pnl",
        "list_protections",
    })

    # A parity pass writes nothing, so it takes no pass lock.
    exclusive_pass = None

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def append_event(self, event: Any) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        if name not in self._READS:
            raise PermissionError(f"the runner's parity pass cannot use strategy {name}")
        return getattr(self._repository, name)


class _ReadOnlyPaperRepository:
    """The paper account reads a parity pass may make; nothing else."""

    _ALLOWED = frozenset({"snapshot", "list_accounts"})

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        if name not in self._ALLOWED:
            raise PermissionError(f"the runner's parity pass cannot use paper {name}")
        return getattr(self._repository, name)


class RunnerParityHost(StrategyRunHost):
    """Runs a shadowed configuration's pass up to its proposals and records them."""

    # The real strategy repository, where the parity events go; set for each pass.
    parity_repository: Any = None

    async def _reconcile_protections(self, config, strategy_repository, paper_repository, market_service) -> None:
        return None

    async def _run_intraday_llm(self, config, strategy_repository, universe, ranked_learning) -> None:
        return None

    async def _record_diagnostic_v2_candidates(self, config, strategy_repository, market_service, universe) -> None:
        return None

    async def _proposals_evaluated(self, config, strategy_repository, proposals: list[_EntryProposal]) -> bool:
        if proposals:
            await record_parity_proposals(self, config, self.parity_repository, proposals, source="runner")
        return False


class RunnerOwnedConfigs:
    """The gap pullback configurations whose execution owner is the strategy runner."""

    def __init__(
        self,
        *,
        strategy_repository_factory: Callable[[], Any],
        paper_repository_factory: Callable[[], Any],
        market_service_factory: Callable[[], Any],
        enabled: Callable[[], bool] = trading_strategy_monitor_enabled,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.strategy_repository_factory = strategy_repository_factory
        self.paper_repository_factory = paper_repository_factory
        self.market_service_factory = market_service_factory
        # The monitor's switch also stops the configurations moved to the runner.
        self.enabled = enabled
        self.clock = clock
        self.host = StrategyRunHost()
        self.host.execution_role = "runner"
        # One parity host per shadowed strategy, so it keeps its evaluated-bar state.
        self.parity_hosts: dict[str, RunnerParityHost] = {}
        # The last cycle's configurations, for the operations status.
        self.owned_strategy_ids: list[str] = []
        self.shadowed_strategy_ids: list[str] = []
        self.last_run_at: datetime | None = None

    async def run_once(self) -> int:
        """Run every runner-owned and runner-shadowed configuration; return new paper orders."""
        if not self.enabled():
            return 0
        strategy_repository = self.strategy_repository_factory()
        configs = [
            config
            for config in await asyncio.to_thread(strategy_repository.list_configs, active_only=True)
            if config.strategy_kind == "gap_pullback_v1" and (runner_owned(config) or runner_shadowed(config))
        ]
        self.owned_strategy_ids = [config.strategy_id for config in configs if runner_owned(config)]
        self.shadowed_strategy_ids = [config.strategy_id for config in configs if runner_shadowed(config)]
        # Readiness describes this cycle's configurations only.
        self.host.auto_paper_readiness_by_strategy = {}
        self.last_run_at = self.clock()
        if not configs:
            return 0
        paper_repository = self.paper_repository_factory()
        market_service = self.market_service_factory()
        host = self.host
        before = host.paper_order_count
        host.current_run_id = _run_id("runner", self.clock())
        try:
            for config in configs:
                try:
                    if runner_owned(config):
                        await host._run_config(config, strategy_repository, paper_repository, market_service)
                    else:
                        parity = self.parity_hosts.setdefault(config.strategy_id, RunnerParityHost())
                        parity.parity_repository = strategy_repository
                        # Its trade log lines say they come from the parity pass.
                        parity.current_run_id = _run_id("runner-parity", self.clock())
                        await parity._run_config(
                            config,
                            # Stand-ins for the repositories: same reads, no writes.
                            cast(Any, _DiscardingStrategyRepository(strategy_repository)),
                            cast(Any, _ReadOnlyPaperRepository(paper_repository)),
                            market_service,
                        )
                except Exception as exc:
                    host.last_error = f"{config.strategy_id}: {type(exc).__name__}: {exc}"
                    if runner_owned(config):
                        host._set_auto_paper_readiness(
                            config, state="blocked", reason="runtime_error", observed_at=self.clock()
                        )
                    trade_log(
                        "auto_trading",
                        "strategy_cycle_error",
                        run_id=host.current_run_id,
                        strategy_id=config.strategy_id,
                        execution_owner=config.config.execution_owner,
                        error_type=type(exc).__name__,
                        detail=str(exc),
                    )
            return host.paper_order_count - before
        finally:
            host.current_run_id = None
