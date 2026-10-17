from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


from app.apps.trading.execution_observation_plane import ExecutionObservationPlane
from app.apps.trading.strategy_ai_shadow_monitor import TradingAIShadowMonitor
from app.apps.trading.execution_observation_monitor import TradingExecutionObservationMonitor


NOW = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)


class FailingMarketService:
    def __init__(self) -> None:
        self.calls = 0

    def execution_observation(self, instrument_id, binding_id):
        self.calls += 1
        raise RuntimeError("429 fixture")


@pytest.mark.anyio
async def test_execution_observation_capture_backs_off_after_provider_failure():
    service = FailingMarketService()
    monitor = TradingExecutionObservationMonitor(
        plane=ExecutionObservationPlane(),
        now_factory=lambda: NOW,
        interval_seconds=3,
    )
    candidate = SimpleNamespace(
        instrument_id="equity:NASDAQ:TEST",
        binding_id="alpaca:TEST",
    )

    await monitor._capture_one(service, candidate, now=NOW)
    await monitor._capture_one(
        service,
        candidate,
        now=NOW + timedelta(seconds=1),
    )

    assert service.calls == 1
    assert monitor.capture_error_count == 1
    assert monitor.backoff_skip_count == 1


def test_ai_shadow_monitor_uses_shared_execution_plane_dependency():
    plane = ExecutionObservationPlane()
    monitor = TradingAIShadowMonitor(execution_plane=plane, interval_seconds=5)
    assert monitor.execution_plane is plane


@pytest.mark.anyio
async def test_captures_run_on_the_dedicated_observation_pool():
    import threading

    threads: list[str] = []

    class RecordingMarketService:
        def execution_observation(self, instrument_id, binding_id):
            threads.append(threading.current_thread().name)
            raise RuntimeError("fixture")

    monitor = TradingExecutionObservationMonitor(
        plane=ExecutionObservationPlane(), now_factory=lambda: NOW, interval_seconds=3
    )
    candidate = SimpleNamespace(instrument_id="equity:NASDAQ:TEST", binding_id="alpaca:TEST")

    await monitor._capture_one(RecordingMarketService(), candidate, now=NOW)

    assert threads and threads[0].startswith("omnix-trading-observation")


def test_backoff_state_is_kept_only_for_the_current_universe():
    monitor = TradingExecutionObservationMonitor(
        plane=ExecutionObservationPlane(), now_factory=lambda: NOW, interval_seconds=3
    )
    monitor._consecutive_failures.update({"equity:NASDAQ:OLD": 3, "equity:NASDAQ:KEEP": 1})
    monitor._next_capture_at.update({"equity:NASDAQ:OLD": NOW, "equity:NASDAQ:KEEP": NOW})

    monitor._forget_instruments_outside({"equity:NASDAQ:KEEP"})

    assert monitor._consecutive_failures == {"equity:NASDAQ:KEEP": 1}
    assert monitor._next_capture_at == {"equity:NASDAQ:KEEP": NOW}
