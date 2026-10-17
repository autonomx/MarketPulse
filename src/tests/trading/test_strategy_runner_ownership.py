"""Gap pullback execution ownership: monitor, runner shadow, runner (strategy runner WP)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any, cast

import pytest

from app.apps.trading import strategy_monitor_config_run
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_monitor import StrategyRunHost
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument
from app.apps.trading.strategy_runner_pass import (
    RunnerOwnedConfigs,
    _DiscardingStrategyRepository,
    _ReadOnlyPaperRepository,
)
from app.apps.trading.strategy_v2_qualification import (
    FROZEN_V2_PROFILE_FINGERPRINT,
    frozen_v2_config,
    v2_profile_fingerprint,
)


def test_the_monitor_owns_a_configuration_by_default() -> None:
    assert GapPullbackConfig().execution_owner == "monitor"
    with pytest.raises(ValueError):
        GapPullbackConfig(execution_owner="broker")


def test_moving_a_configuration_keeps_its_qualified_profile() -> None:
    for owner in ("runner_shadow", "runner"):
        moved = frozen_v2_config().model_copy(update={"execution_owner": owner})
        assert v2_profile_fingerprint(moved) == FROZEN_V2_PROFILE_FINGERPRINT


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            return name

        return call


def test_the_parity_pass_cannot_write() -> None:
    strategy = _Recorder()
    discarding = _DiscardingStrategyRepository(strategy)
    assert discarding.append_event(object()) is True
    assert discarding.recent_events("s", 10) == "recent_events"
    # Only the known reads pass; a write added later is refused too.
    for write in ("save_protection", "save_universe", "update_config", "create_config", "delete_config", "save_x"):
        with pytest.raises(PermissionError):
            getattr(discarding, write)
    assert strategy.calls == ["recent_events"]

    paper = _Recorder()
    read_only = _ReadOnlyPaperRepository(paper)
    assert read_only.snapshot("account") == "snapshot"
    for write in ("place_order", "cancel_order", "replace_order", "process_observation", "create_account"):
        with pytest.raises(PermissionError):
            getattr(read_only, write)
    assert paper.calls == ["snapshot"]


def test_the_monitor_switch_also_stops_runner_owned_configurations() -> None:
    def unused():
        raise AssertionError("a disabled runner must not open repositories")

    owned = RunnerOwnedConfigs(
        strategy_repository_factory=unused,
        paper_repository_factory=unused,
        market_service_factory=unused,
        enabled=lambda: False,
    )
    assert asyncio.run(owned.run_once()) == 0


class _LockingRepository:
    """A strategy repository with the pass lock, whose stored configuration can change owner."""

    def __init__(self, stored, *, acquired: bool = True) -> None:
        self.stored = stored
        self.acquired = acquired
        self.passes: list[str] = []

    @contextmanager
    def exclusive_pass(self, strategy_id: str):
        self.passes.append(strategy_id)
        yield self.acquired

    def get_config(self, strategy_id: str):
        return self.stored


def _gap_config(owner: str) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id="gap-1",
        account_id="paper",
        strategy_kind="gap_pullback_v1",
        mode="shadow",
        config=GapPullbackConfig(execution_owner=owner),
    )


def _run(host: StrategyRunHost, listed, repository, monkeypatch) -> list[str]:
    ran: list[str] = []

    async def fake_run_config(_host, config, *_args):
        ran.append(config.config.execution_owner)

    monkeypatch.setattr(strategy_monitor_config_run, "run_config", fake_run_config)
    asyncio.run(host._run_config(listed, repository, object(), object()))
    return ran


def test_a_pass_runs_on_the_current_configuration_of_its_owner(monkeypatch) -> None:
    monitor_host = StrategyRunHost()
    runner_host = StrategyRunHost()
    runner_host.execution_role = "runner"

    # Listed as the monitor's, but switched to the runner before the pass began.
    switched = _LockingRepository(_gap_config("runner"))
    assert _run(monitor_host, _gap_config("monitor"), switched, monkeypatch) == []
    assert _run(runner_host, _gap_config("monitor"), switched, monkeypatch) == ["runner"]
    # A runner shadow leaves the monitor as owner.
    shadowed = _LockingRepository(_gap_config("runner_shadow"))
    assert _run(monitor_host, _gap_config("runner_shadow"), shadowed, monkeypatch) == ["runner_shadow"]
    assert _run(runner_host, _gap_config("runner_shadow"), shadowed, monkeypatch) == []


def test_a_pass_already_running_elsewhere_is_skipped(monkeypatch) -> None:
    busy = _LockingRepository(_gap_config("monitor"), acquired=False)
    assert _run(StrategyRunHost(), _gap_config("monitor"), busy, monkeypatch) == []
    assert busy.passes == ["gap-1"]


def test_restoring_the_managed_profile_keeps_the_execution_owner() -> None:
    from app.apps.trading.strategy_managed_finviz_shadow import _desired_for_current

    current = _gap_config("runner")
    desired = _gap_config("monitor")
    assert _desired_for_current(current, desired).config.execution_owner == "runner"


def test_a_failing_registered_strategy_does_not_stop_the_owned_configurations() -> None:
    from app.apps.trading.strategies.runner import StrategyRunner

    class _Owned:
        host = StrategyRunHost()
        owned_strategy_ids = ["gap-1"]
        shadowed_strategy_ids: list[str] = []
        ran = 0

        def enabled(self) -> bool:
            return True

        async def run_once(self) -> int:
            self.ran += 1
            return 1

    runner = StrategyRunner(strategy_repository_factory=lambda: None, market_service_factory=lambda: None)
    owned = _Owned()
    runner.owned_configs = cast(Any, owned)

    def failing_strategies() -> int:
        raise RuntimeError("bars unavailable")

    runner.run_once = failing_strategies  # type: ignore[method-assign]
    assert asyncio.run(runner.run_cycle()) == 1
    assert owned.ran == 1
    assert runner.last_error == "RuntimeError: bars unavailable"
    assert runner.diagnostics()["owned_strategy_ids"] == ["gap-1"]
    assert runner.owned_config_count == 1


def test_the_parity_report_is_served_read_only() -> None:
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.apps.trading.strategy_api import create_trading_strategy_router
    from app.apps.trading.strategy_repository import StrategyEvent
    from app.apps.trading.strategy_runner_parity import MONITOR_PARITY_EVENT, RUNNER_PARITY_EVENT

    now = datetime.now(timezone.utc)

    def proposal(event_type: str) -> StrategyEvent:
        return StrategyEvent(
            strategy_id="gap-1", event_id=event_type, instrument_id="equity:TEST", event_type=event_type,
            state="entry_ready", observed_at=now, idempotency_key=event_type,
            payload={"trade_attempt_id": "attempt-1", "signal": {"entry_price": "1"}},
        )

    class _Events:
        def events_by_types_between(self, strategy_id, *, event_types, start_time, end_time, limit=10_000):
            assert set(event_types) == {MONITOR_PARITY_EVENT, RUNNER_PARITY_EVENT}
            return [proposal(MONITOR_PARITY_EVENT), proposal(RUNNER_PARITY_EVENT)]

    app = FastAPI()
    app.include_router(create_trading_strategy_router(lambda: _Events()))
    body = TestClient(app).get("/api/trading/strategies/gap-1/runner-parity?hours=6").json()
    assert body["parity"] is True
    assert body["matched"] == 1
    assert body["execution_authority"] is False
