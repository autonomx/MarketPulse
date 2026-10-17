"""A strategy pass stops starting configurations near its timeout and resumes them first."""
from __future__ import annotations

import asyncio

import pytest

from app.apps.trading import strategy_monitor as monitor_module
from app.apps.trading.strategies.models import StochRsi5mConfig
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument


def _config(strategy_id: str) -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id=strategy_id, account_id="paper-test", strategy_kind="stoch_rsi_5m_v1",
        strategy_version="1.0.0", mode="shadow", active_universe_id="u", config=StochRsi5mConfig(),
    )


class _Repository:
    def __init__(self, configs) -> None:
        self.configs = configs

    def list_configs(self, *, active_only: bool = False):
        return list(self.configs)


def _monitor(monkeypatch, run_config):
    logged: list[dict] = []
    monkeypatch.setattr(
        monitor_module, "trade_log",
        lambda channel, event, **fields: logged.append({"event": event, **fields}),
    )
    configs = [_config(name) for name in ("a", "b", "c")]
    monitor = TradingStrategyMonitor(
        strategy_repository_factory=lambda: _Repository(configs),
        paper_repository_factory=object,
        market_service_factory=object,
        interval_seconds=30,
    )
    monitor._run_config = run_config
    return monitor, logged


def _timing(logged: list[dict]) -> dict:
    return [entry for entry in logged if entry["event"] == "monitor_pass_timing"][-1]


def test_an_over_budget_pass_defers_the_rest_and_runs_them_first_next_time(monkeypatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(monitor_module, "perf_counter", lambda: clock[0])
    ran: list[str] = []

    async def run_config(config, *args):
        ran.append(config.strategy_id)
        clock[0] += 150.0  # each configuration takes 150 s

    monitor, logged = _monitor(monkeypatch, run_config)
    asyncio.run(monitor.run_once())

    # Budget is 80% of the 300 s timeout: "b" starts at 150 s, "c" would start at 300 s.
    assert ran == ["a", "b"]
    timing = _timing(logged)
    assert timing["strategy_seconds"] == {"a": 150.0, "b": 150.0}
    assert timing["deferred_for_budget"] == ["c"] and timing["unfinished"] == ["c"]
    assert timing["pass_seconds"] == 300.0

    ran.clear()
    asyncio.run(monitor.run_once())
    assert ran == ["c", "a"]


def test_a_pass_interrupted_by_the_timeout_resumes_where_it_stopped(monkeypatch) -> None:
    ran: list[str] = []
    interrupted: list[str] = []

    async def run_config(config, *args):
        ran.append(config.strategy_id)
        if config.strategy_id == "b" and not interrupted:
            interrupted.append("b")
            raise asyncio.CancelledError  # what the scheduler's timeout delivers

    monitor, logged = _monitor(monkeypatch, run_config)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.run_once())
    assert _timing(logged)["unfinished"] == ["b", "c"]  # logged even when interrupted

    ran.clear()
    asyncio.run(monitor.run_once())
    assert ran == ["b", "c", "a"]
