"""The strategy contract, registry and generic runner (WP-8.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.models import GapPullbackConfig, StochRsi5mConfig
from app.apps.trading.strategies.registrations import STRATEGY_REGISTRY
from app.apps.trading.strategies.registry import MonitorOwnedStrategy, StrategyRegistry, UnknownStrategyKind
from app.apps.trading.strategies.runner import StrategyRunner
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument, strategy_config_document_model

from src.tests.trading.fake_breakout_strategy import FakeBreakoutConfig, FakeBreakoutStrategy

NOW = datetime(2026, 10, 2, 15, 0, 30, tzinfo=timezone.utc)


def _bar(close: str, *, minutes_ago: int, final: bool = True) -> MarketBar:
    end = NOW.replace(second=0) - timedelta(minutes=minutes_ago)
    price = Decimal(close)
    return MarketBar(
        instrument_id="equity:NASDAQ:FAKE",
        interval="1m",
        start_time=end - timedelta(minutes=1),
        end_time=end,
        open=price,
        high=price,
        low=price - Decimal("0.5"),
        close=price,
        provider="test",
        is_final=final,
    )


class _Repository:
    def __init__(self, configs) -> None:
        self.configs = configs
        self.events = {}

    def list_configs(self, *, active_only=False):
        return list(self.configs)

    def append_event(self, event):
        if event.idempotency_key in self.events:
            return False
        self.events[event.idempotency_key] = event
        return True


class _Market:
    def __init__(self, bars) -> None:
        self.bars_returned = bars
        self.requests = []

    def bars(self, instrument_id, interval, limit=500, binding_id=None, cancellation=None):
        self.requests.append((instrument_id, interval, limit))
        return SimpleNamespace(bars=self.bars_returned)


def test_a_strategy_is_added_by_one_module_and_one_registration() -> None:
    registry = STRATEGY_REGISTRY.with_entries(FakeBreakoutStrategy())
    document_model = strategy_config_document_model(registry)

    config = document_model(
        strategy_id="fake-breakout",
        account_id="paper",
        strategy_kind="fake_breakout_v1",
        mode="shadow",
        config={"breakout_price": "10"},
    )
    assert isinstance(config.config, FakeBreakoutConfig)
    with pytest.raises(ValidationError, match="strategy_mode_not_allowed"):
        document_model(
            strategy_id="fake-breakout",
            account_id="paper",
            strategy_kind="fake_breakout_v1",
            mode="auto_paper",
            config={"breakout_price": "10"},
        )

    market = _Market([_bar("9", minutes_ago=2), _bar("11", minutes_ago=1), _bar("13", minutes_ago=0, final=False)])
    repository = _Repository([config])
    runner = StrategyRunner(
        strategy_repository_factory=lambda: repository,
        market_service_factory=lambda: market,
        registry=registry,
        clock=lambda: NOW,
    )

    assert runner.run_once() == 1
    [event] = repository.events.values()
    assert event.event_type == "proposal"
    assert event.payload["execution_authority"] is False
    # The open bar (13) is not visible to the strategy.
    assert event.payload["proposal"]["entry_price"] == "11"
    assert market.requests == [("equity:NASDAQ:FAKE", "1m", 30)]
    # The same minute records the proposal once.
    assert runner.run_once() == 0


def test_the_runner_skips_monitor_owned_and_disabled_configurations() -> None:
    registry = STRATEGY_REGISTRY.with_entries(FakeBreakoutStrategy())
    document_model = strategy_config_document_model(registry)
    off = document_model(strategy_id="off", account_id="paper", strategy_kind="fake_breakout_v1", mode="off",
                         config=FakeBreakoutConfig())
    monitor_owned = document_model(strategy_id="gap", account_id="paper", strategy_kind="gap_pullback_v1",
                                   mode="shadow")
    market = _Market([_bar("11", minutes_ago=1)])
    runner = StrategyRunner(
        strategy_repository_factory=lambda: _Repository([off, monitor_owned]),
        market_service_factory=lambda: market,
        registry=registry,
        clock=lambda: NOW,
    )

    assert runner.run_once() == 0
    assert market.requests == []


def test_the_production_registry_validates_persisted_kinds() -> None:
    assert STRATEGY_REGISTRY.kinds() == ("gap_pullback_v1", "stoch_rsi_5m_v1", "overnight_shadow_v1")
    assert STRATEGY_REGISTRY.runner_strategies() == ()
    document = TradingStrategyConfigDocument(
        strategy_id="stoch", account_id="paper", strategy_kind="stoch_rsi_5m_v1", mode="shadow",
        strategy_version=StochRsi5mConfig().strategy_version, config=StochRsi5mConfig().model_dump(),
    )
    # The config is parsed by its kind's model, not guessed from the union.
    assert isinstance(document.config, StochRsi5mConfig)
    with pytest.raises(ValidationError, match="stoch_rsi_5m_is_shadow_only"):
        TradingStrategyConfigDocument(
            strategy_id="stoch", account_id="paper", strategy_kind="stoch_rsi_5m_v1", mode="auto_paper",
            strategy_version=StochRsi5mConfig().strategy_version, config=StochRsi5mConfig(),
        )
    with pytest.raises(ValidationError, match="strategy_kind_config_mismatch"):
        TradingStrategyConfigDocument(
            strategy_id="gap", account_id="paper", strategy_kind="stoch_rsi_5m_v1", mode="shadow",
            config=GapPullbackConfig(),
        )
    with pytest.raises(ValidationError):
        TradingStrategyConfigDocument(strategy_id="x", account_id="paper", strategy_kind="unregistered_v1")


def test_the_registry_rejects_ambiguous_or_authority_widening_entries() -> None:
    entry = MonitorOwnedStrategy("gap_pullback_v1", GapPullbackConfig, ("off",), owner="test")
    with pytest.raises(ValueError, match="registered twice"):
        STRATEGY_REGISTRY.with_entries(entry)

    class AutoPaper(FakeBreakoutStrategy):
        kind = "auto_paper_v1"
        allowed_modes = ("off", "shadow", "auto_paper")

    with pytest.raises(ValueError, match="shadow-only"):
        StrategyRegistry((AutoPaper(),))
    with pytest.raises(TypeError, match="contract"):
        StrategyRegistry((object(),))
    with pytest.raises(UnknownStrategyKind):
        STRATEGY_REGISTRY.get("unregistered_v1")


def test_the_runner_task_always_runs_on_the_monitor_cadence(monkeypatch) -> None:
    from app.runtime.scheduler import ScheduledTaskSpec, TaskExecutor
    from app.apps.trading.strategies import runner

    # A gap pullback configuration can move to the runner at any time, so the
    # task exists even with no registered runner strategy.
    monkeypatch.setenv("OMNIX_TRADING_STRATEGY_INTERVAL_SECONDS", "30")
    task = runner.strategy_runner_task(None)
    assert isinstance(task, ScheduledTaskSpec)
    assert task.task_id == "trading.strategy_runner"
    assert task.interval_seconds == 30.0
    assert task.executor == TaskExecutor.ASYNC
