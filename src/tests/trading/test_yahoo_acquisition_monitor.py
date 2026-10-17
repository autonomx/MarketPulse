from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.apps.trading.yahoo_acquisition_monitor import TradingYahooAcquisitionMonitor


ET = ZoneInfo("America/New_York")
INSTRUMENT = "equity:NASDAQ:TEST"


class _Repository:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def list_universes(self, *, start_date: date, end_date: date):
        assert start_date == end_date == self.now.astimezone(ET).date()
        return [
            SimpleNamespace(
                evaluation_time=self.now - timedelta(minutes=10),
                candidates=(
                    SimpleNamespace(
                        instrument_id=INSTRUMENT,
                        binding_id="yahoo:test",
                    ),
                ),
            )
        ]

    def events_by_types_between(self, *args, **kwargs):
        return []


class _EvidenceStore:
    def __init__(self) -> None:
        self.records = []

    def record_acquisition(self, **kwargs) -> None:
        self.records.append(kwargs)


class _Yahoo:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls = []

    def get_intraday_bars_range(
        self,
        instrument_id,
        *,
        start,
        end,
        include_extended_hours=True,
        cancellation=None,
    ):
        self.calls.append((instrument_id, start, end, include_extended_hours))
        return SimpleNamespace(
            bars=(
                SimpleNamespace(end_time=self.now - timedelta(minutes=1)),
            )
        )


class _Registry:
    def __init__(self, yahoo) -> None:
        self.yahoo = yahoo

    def provider(self, provider_id: str):
        assert provider_id == "yahoo"
        return self.yahoo


def test_acquisition_monitor_captures_frozen_universe_independent_of_strategy_loop() -> None:
    now = datetime(2026, 9, 17, 10, 0, tzinfo=ET)
    repository = _Repository(now)
    yahoo = _Yahoo(now)
    evidence = _EvidenceStore()
    service = SimpleNamespace(
        registry=_Registry(yahoo),
        yahoo_evidence_store=evidence,
    )
    monitor = TradingYahooAcquisitionMonitor(
        repository_factory=lambda: repository,
        market_service_factory=lambda: service,
        now_factory=lambda: now,
        interval_seconds=30,
    )

    captured = asyncio.run(monitor.run_once())

    assert captured == 1
    assert monitor.active_symbol_count == 1
    assert monitor.capture_count == 1
    assert yahoo.calls[0][0] == INSTRUMENT
    assert yahoo.calls[0][3] is True
    assert evidence.records == [
        {
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "symbols": 1,
        }
    ]
