from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.gapper_dataset import GapperCandidate, GapperUniverseSnapshot
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_deep_recovery_monitor import TradingStrategyDeepRecoveryShadowMonitor
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_shadow_execution import ShadowExecutionEvidence


INSTRUMENT = "equity:NASDAQ:RECOV"
OPEN = datetime(2026, 8, 24, 13, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 24, 13, 52, tzinfo=timezone.utc)


def _bar(index: int, open_: str, high: str, low: str, close: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def _candidate() -> GapperCandidate:
    return GapperCandidate(
        instrument_id=INSTRUMENT,
        binding_id="fixture:RECOV",
        previous_close=Decimal("7.50"),
        premarket_price=Decimal("10.00"),
        gap_pct=Decimal("33.3333"),
        premarket_volume=Decimal("30000"),
        premarket_dollar_volume=Decimal("300000"),
        tod_rvol=Decimal("8"),
        market_cap=Decimal("50000000"),
        float_shares=Decimal("5000000"),
        spread_bps=Decimal("40"),
        discovery_rank=1,
    )


def _config() -> GapPullbackConfig:
    return GapPullbackConfig(
        strategy_version="2.0.0",
        structure_interval="1m",
        execution_interval="1m",
        minimum_gap_pct=Decimal("20"),
        minimum_price=Decimal("0.50"),
        maximum_price=Decimal("20"),
        minimum_premarket_dollar_volume=Decimal("100000"),
        minimum_tod_rvol=Decimal("3"),
        maximum_spread_bps=Decimal("150"),
        require_catalyst_evidence=False,
        stop_buffer_bps=Decimal("15"),
        entry_start_et=time(9, 35),
        last_entry_et=time(11, 30),
    )


def _bars() -> list[MarketBar]:
    rows = [
        ("9.90", "10.00", "9.80", "9.90"),
        ("9.90", "9.95", "9.40", "9.50"),
        ("9.50", "9.55", "8.90", "9.00"),
        ("9.00", "9.05", "8.40", "8.50"),
        ("8.50", "8.55", "7.90", "8.00"),
        ("8.00", "8.10", "7.50", "7.70"),
        ("7.70", "7.90", "7.60", "7.85"),
        ("7.85", "8.10", "7.80", "8.05"),
        ("8.05", "8.30", "8.00", "8.25"),
        ("8.25", "8.45", "8.20", "8.40"),
        ("8.40", "8.60", "8.35", "8.55"),
        ("8.55", "8.75", "8.50", "8.70"),
        ("8.70", "8.90", "8.65", "8.85"),
        ("8.85", "9.05", "8.80", "9.00"),
        ("9.00", "9.15", "8.95", "9.10"),
        ("9.10", "9.20", "9.00", "9.15"),
        ("9.15", "9.30", "9.10", "9.25"),
        ("9.25", "9.40", "9.20", "9.35"),
        ("9.35", "9.50", "9.30", "9.45"),
        ("9.45", "9.60", "9.40", "9.55"),
        ("9.55", "9.90", "9.50", "9.80"),
    ]
    return [_bar(index, *row) for index, row in enumerate(rows)]


class FakeRepository:
    def __init__(self) -> None:
        config = _config()
        self.configs = [TradingStrategyConfigDocument(
            strategy_id="strategy-1",
            account_id="paper-1",
            strategy_version="2.0.0",
            mode="shadow",
            config=config,
        )]
        self.universe = GapperUniverseSnapshot(
            universe_id="universe-2026-08-24",
            session_date=NOW.date(),
            evaluation_time=OPEN - timedelta(minutes=10),
            discovery_source="import",
            source_fingerprint="f" * 64,
            candidates=[_candidate()],
        )
        self.events: list[StrategyEvent] = []

    def list_configs(self, *, active_only: bool = False):
        return list(self.configs)

    def get_universe(self, universe_id: str):
        assert universe_id == self.universe.universe_id
        return self.universe

    def events_by_types_between(self, strategy_id: str, *, event_types, start_time, end_time, limit=10000):
        return [
            event for event in self.events
            if event.strategy_id == strategy_id
            and event.event_type in event_types
            and start_time <= event.observed_at < end_time
        ][:limit]

    def append_event(self, event: StrategyEvent) -> bool:
        if any(existing.idempotency_key == event.idempotency_key for existing in self.events):
            return False
        self.events.append(event)
        return True


class FakeMarketService:
    def bars(self, instrument_id: str, interval: str, limit: int, binding_id: str | None):
        assert instrument_id == INSTRUMENT
        assert interval == "1m"
        assert limit == 500
        return SimpleNamespace(bars=_bars())


def test_deep_recovery_monitor_is_structurally_orderless() -> None:
    import app.apps.trading.strategy_deep_recovery_monitor as module

    source = inspect.getsource(module)
    assert "TradingPaperRepository" not in source
    assert "PaperOrderRequest" not in source
    assert "place_order" not in source


def test_deep_recovery_monitor_persists_one_shadow_signal_with_no_execution_authority(monkeypatch) -> None:
    repository = FakeRepository()
    repository.configs[0].active_universe_id = repository.universe.universe_id
    market = FakeMarketService()

    def fake_observe(*args, **kwargs):
        return ShadowExecutionEvidence(
            reason_code="SHADOW_EXECUTION_OBSERVED",
            execution={
                "execution_eligible": True,
                "prospective_signal_features": {
                    "schema_version": "v2-prospective-signal-features-1",
                    "immutable_fingerprint": "a" * 64,
                },
            },
        )

    monkeypatch.setattr(
        "app.apps.trading.strategy_deep_recovery_monitor.observe_shadow_execution",
        fake_observe,
    )
    monitor = TradingStrategyDeepRecoveryShadowMonitor(
        strategy_repository_factory=lambda: repository,
        market_service_factory=lambda: market,
        now_factory=lambda: NOW,
        interval_seconds=30,
    )

    assert asyncio.run(monitor.run_once()) == 1
    assert asyncio.run(monitor.run_once()) == 0

    states = [event for event in repository.events if event.event_type == "deep_recovery_state"]
    assert len(states) == 1
    assert states[0].state == "signal_ready"
    assert monitor.state_transition_count == 1

    signals = [event for event in repository.events if event.event_type == "deep_recovery_shadow"]
    assert len(signals) == 1
    signal = signals[0]
    assert signal.state == "signal_ready"
    assert signal.payload["execution_authority"] is False
    assert signal.payload["setup_id"] == "deep_recovery_continuation_v1"
    assert signal.payload["execution"]["prospective_signal_features"]["immutable_fingerprint"] == "a" * 64
    assert monitor.signal_count == 1
    assert monitor.execution_observation_count == 1


def test_deep_recovery_monitor_reads_no_bars_once_the_session_settles() -> None:
    class UntouchedMarket:
        def bars(self, *args, **kwargs):
            raise AssertionError("a settled session's bars are final; nothing is re-read")

    repository = FakeRepository()
    repository.configs[0].active_universe_id = repository.universe.universe_id
    settled = datetime(2026, 8, 24, 20, 31, tzinfo=timezone.utc)  # 16:31 ET
    monitor = TradingStrategyDeepRecoveryShadowMonitor(
        strategy_repository_factory=lambda: repository,
        market_service_factory=lambda: UntouchedMarket(),
        now_factory=lambda: settled,
        interval_seconds=30,
    )
    assert asyncio.run(monitor.run_once()) == 0
    assert repository.events == []
