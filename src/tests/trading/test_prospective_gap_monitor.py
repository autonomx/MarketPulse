from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest


from app.apps.trading.prospective_gap_monitor import ProspectiveGapMonitor


class _Ledger:
    def __init__(self, runtime: "_Runtime") -> None:
        self.runtime = runtime

    def latest(self, *, kind: str, instrument_id: str | None = None):
        if (
            kind == "session_manifest"
            and instrument_id == "__session__"
            and self.runtime.has_manifest
        ):
            return SimpleNamespace(kind=kind, instrument_id=instrument_id)
        return None


class _Runtime:
    def __init__(self) -> None:
        self.has_manifest = False
        self.ingest_calls: list[tuple[date, datetime | None]] = []
        self.confirmation_calls: list[datetime] = []

    def session_ledger(self, session_date: date) -> _Ledger:
        return _Ledger(self)

    def try_freeze_scheduler_inbox(
        self,
        session_date: date,
        *,
        observed_at: datetime | None = None,
    ):
        self.ingest_calls.append((session_date, observed_at))
        self.has_manifest = True
        return SimpleNamespace(session_date=session_date)

    def run_confirmation(self, *, session_date: date, evaluated_at: datetime):
        self.confirmation_calls.append(evaluated_at)
        return SimpleNamespace()

    def finalize_postclose(self, *, session_date: date, evaluated_at: datetime):
        return SimpleNamespace()


@pytest.mark.anyio
async def test_monitor_waits_for_late_premarket_before_ingesting_handoff() -> None:
    runtime = _Runtime()
    monitor = ProspectiveGapMonitor(runtime_factory=lambda: runtime)

    # 09:23 ET / 13:23 UTC is intentionally too early.
    result = await monitor.run_once(
    now=datetime(2026, 9, 24, 13, 23, tzinfo=timezone.utc)
)

    assert result == 0
    assert runtime.ingest_calls == []
    assert monitor.scheduler_handoff_ingest_count == 0
    assert monitor.no_session_count == 0


@pytest.mark.anyio
async def test_monitor_ingests_handoff_inside_0924_to_092759_window() -> None:
    runtime = _Runtime()
    monitor = ProspectiveGapMonitor(runtime_factory=lambda: runtime)
    observed = datetime(2026, 9, 24, 13, 25, tzinfo=timezone.utc)

    result = await monitor.run_once(now=observed)

    assert result == 0
    assert runtime.ingest_calls == [(date(2026, 9, 24), observed)]
    assert runtime.has_manifest is True
    assert monitor.scheduler_handoff_ingest_count == 1


@pytest.mark.anyio
async def test_monitor_does_not_create_late_retroactive_session_after_ingest_window() -> None:
    runtime = _Runtime()
    monitor = ProspectiveGapMonitor(runtime_factory=lambda: runtime)

    # 09:28 ET is outside the allowed ingestion window.
    result = await monitor.run_once(
    now=datetime(2026, 9, 24, 13, 28, tzinfo=timezone.utc)
)

    assert result == 0
    assert runtime.ingest_calls == []
    assert runtime.has_manifest is False
    assert monitor.no_session_count == 1

    # Further ticks on the same missed session must not inflate the day count.
    await monitor.run_once(
    now=datetime(2026, 9, 24, 13, 29, tzinfo=timezone.utc)
)
    assert monitor.no_session_count == 1


@pytest.mark.anyio
async def test_existing_session_runs_confirmation_at_open_without_reingesting() -> None:
    runtime = _Runtime()
    runtime.has_manifest = True
    monitor = ProspectiveGapMonitor(runtime_factory=lambda: runtime)
    observed = datetime(2026, 9, 24, 13, 30, tzinfo=timezone.utc)

    result = await monitor.run_once(now=observed)

    assert result == 1
    assert runtime.ingest_calls == []
    assert runtime.confirmation_calls == [observed]
