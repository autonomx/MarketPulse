"""Live strategy passes stop once the regular session has settled (close + 30 minutes)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone

from app.apps.trading import strategy_monitor_config_run as run_module
from app.apps.trading.execution_observation_monitor import TradingExecutionObservationMonitor
from app.apps.trading.execution_observation_plane import ExecutionObservationPlane
from app.apps.trading.strategies.models import StochRsi5mConfig
from app.apps.trading.strategy_monitor import TradingStrategyMonitor
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument
from app.apps.trading.us_equity_calendar import EASTERN, regular_session_settled


def _et(day: date, hh: int, mm: int) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=EASTERN)


def test_a_session_settles_thirty_minutes_after_its_regular_close() -> None:
    day = date(2026, 10, 14)
    assert not regular_session_settled(_et(day, 15, 59))
    assert not regular_session_settled(_et(day, 16, 29))  # final bars may still arrive
    assert regular_session_settled(_et(day, 16, 30))
    assert regular_session_settled(_et(day, 23, 59))
    assert not regular_session_settled(_et(date(2026, 10, 15), 0, 1))  # a new day has not closed yet
    early = date(2026, 11, 27)  # day after Thanksgiving: 13:00 close
    assert not regular_session_settled(_et(early, 13, 29))
    assert regular_session_settled(_et(early, 13, 30))


def _frozen(moment: datetime) -> type[datetime]:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return moment.astimezone(tz) if tz is not None else moment

    return Frozen


def test_strategy_passes_reconcile_protections_but_stop_evaluating_once_settled(monkeypatch) -> None:
    calls: list[str] = []
    monitor = TradingStrategyMonitor(interval_seconds=30)
    monitor.current_run_id = "settled-test"

    async def reconcile(*args, **kwargs):
        calls.append("protections")

    async def evaluate(*args, **kwargs):
        calls.append("evaluate")

    monitor._reconcile_protections = reconcile
    monitor._run_stoch_rsi_5m_config = evaluate
    config = TradingStrategyConfigDocument(
        strategy_id="stoch-rsi-5min", account_id="paper-test", strategy_kind="stoch_rsi_5m_v1",
        strategy_version="1.0.0", mode="shadow", active_universe_id="u", config=StochRsi5mConfig(),
    )
    day = date(2026, 10, 14)
    for moment, expected in (
        (_et(day, 16, 10), ["protections", "evaluate"]),  # the close's last bars are still arriving
        (_et(day, 16, 31), ["protections"]),              # settled: open positions only
    ):
        calls.clear()
        monkeypatch.setattr(run_module, "datetime", _frozen(moment))
        asyncio.run(run_module.run_config(monitor, config, object(), object(), object()))
        assert calls == expected, moment


def test_execution_observation_stops_before_touching_providers_once_settled() -> None:
    def untouched():
        raise AssertionError("a settled session reads no universe and captures no quote")

    monitor = TradingExecutionObservationMonitor(
        plane=ExecutionObservationPlane(),
        strategy_repository_factory=untouched,
        market_service_factory=untouched,
        now_factory=lambda: _et(date(2026, 10, 14), 20, 8).astimezone(timezone.utc),
        interval_seconds=3,
    )
    assert asyncio.run(monitor.run_once()) == 0
