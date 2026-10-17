from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.finviz_gapper_discovery import FINVIZ_ATOMIC_SOURCE_LOCATOR
from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.providers.errors import ProviderDataUnavailableError
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import GapPullbackConfig
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument
from app.apps.trading import strategy_universe_archiver as archiver


class FakeCatalystRepository:
    def __init__(self):
        self.saved = []

    def save_evidence(self, evidence):
        self.saved.append(evidence)
        return True


class FakeRepository:
    def __init__(self):
        self.values = {}

    def get_universe(self, universe_id):
        if universe_id not in self.values:
            raise ValueError("gapper_universe_not_found")
        return self.values[universe_id]

    def save_universe(self, snapshot):
        self.values[snapshot.universe_id] = snapshot
        return snapshot


def strategy(*, discovery_source="yahoo") -> TradingStrategyConfigDocument:
    return TradingStrategyConfigDocument(
        strategy_id="archive-test",
        account_id="paper-1",
        strategy_version="1.1.0",
        mode="shadow",
        enabled=True,
        config=GapPullbackConfig(
            strategy_version="1.1.0",
            universe_scan_time_et=time(9, 20),
            universe_discovery_source=discovery_source,
            auto_archive_daily_universe=True,
            universe_archive_grace_minutes=10,
        ),
    )


def test_archiver_saves_one_evidence_only_snapshot_and_is_idempotent(monkeypatch) -> None:
    repository = FakeRepository()
    calls = []

    def discover(**kwargs):
        calls.append(kwargs)
        observed = kwargs["evaluation_time"]
        return freeze_gapper_universe(
            universe_id=kwargs["universe_id"],
            session_date=observed.date(),
            evaluation_time=observed,
            discovery_source="provider",
            candidates=[],
            allow_empty=True,
        )

    monkeypatch.setattr(archiver, "discover_yahoo_gappers", discover)
    now = datetime(2026, 8, 19, 13, 22, tzinfo=timezone.utc)  # 09:22 ET

    first = archiver.archive_daily_universe_if_due(strategy(), repository, now=now)
    second = archiver.archive_daily_universe_if_due(strategy(), repository, now=now)

    assert first is not None
    assert second is None
    assert len(calls) == 1
    assert len(first.candidates) == 0
    assert first.universe_id.startswith("auto-archive-2026-08-19-0920-")


def test_archiver_preserves_completed_zero_candidate_scan(monkeypatch) -> None:
    repository = FakeRepository()

    def no_candidates(**kwargs):
        raise ProviderDataUnavailableError("Yahoo top-gainers produced no qualifying listed equities")

    monkeypatch.setattr(archiver, "discover_yahoo_gappers", no_candidates)
    now = datetime(2026, 8, 19, 13, 25, tzinfo=timezone.utc)  # 09:25 ET

    snapshot = archiver.archive_daily_universe_if_due(strategy(), repository, now=now)

    assert snapshot is not None
    assert snapshot.candidates == ()
    assert repository.get_universe(snapshot.universe_id) is snapshot


def test_archiver_attaches_current_catalyst_evidence_before_saving_finviz_snapshot(monkeypatch) -> None:
    repository = FakeRepository()
    catalyst_repository = FakeCatalystRepository()
    now = datetime(2026, 8, 19, 13, 22, tzinfo=timezone.utc)

    def finviz(**kwargs):
        observed = kwargs["evaluation_time"]
        candidate = GapperCandidate(
            instrument_id="equity:NASDAQ:TEST",
            binding_id="yahoo:historical_polling:equity:NASDAQ:TEST",
            observed_at=observed,
            evidence_observed_at={"finviz_top_gainers": observed},
            previous_close=Decimal("8"),
            premarket_price=Decimal("10"),
            gap_pct=Decimal("25"),
            premarket_volume=Decimal("50000"),
            premarket_dollar_volume=Decimal("500000"),
            premarket_bar_count=10,
            tod_rvol=Decimal("5"),
            market_data_complete=True,
            spread_bps=Decimal("40"),
            discovery_rank=1,
        )
        return freeze_gapper_universe(
            universe_id=kwargs["universe_id"],
            session_date=observed.astimezone(timezone.utc).date(),
            evaluation_time=observed,
            discovery_source="finviz",
            source_locator=FINVIZ_ATOMIC_SOURCE_LOCATOR,
            source_candidate_symbols=("TEST",),
            candidates=[candidate],
        )

    def catalyst(**kwargs):
        captured = kwargs["evaluation_time"]
        return (
            SimpleNamespace(
                evidence_id="ev-current-news",
                dilution_flags=(),
                captured_at=captured,
            ),
        )

    monkeypatch.setattr(archiver, "discover_finviz_gappers", finviz)
    snapshot = archiver.archive_daily_universe_if_due(
        strategy(discovery_source="finviz"),
        repository,
        now=now,
        catalyst_repository=catalyst_repository,
        catalyst_discovery=catalyst,
    )

    assert snapshot is not None
    assert snapshot.candidates[0].catalyst_evidence_ids == ("ev-current-news",)
    assert "catalyst:ev-current-news" in snapshot.candidates[0].evidence_observed_at
    assert len(catalyst_repository.saved) == 1
    assert snapshot.source_locator == FINVIZ_ATOMIC_SOURCE_LOCATOR


def test_archiver_does_not_backfill_hours_after_configured_scan(monkeypatch) -> None:
    repository = FakeRepository()
    called = False

    def discover(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not run")

    monkeypatch.setattr(archiver, "discover_yahoo_gappers", discover)
    now = datetime(2026, 8, 19, 16, 0, tzinfo=timezone.utc)  # noon ET

    assert archiver.archive_daily_universe_if_due(strategy(), repository, now=now) is None
    assert called is False



def test_archiver_dispatches_to_finviz_when_configured(monkeypatch) -> None:
    repository = FakeRepository()
    yahoo_called = False
    finviz_called = False

    def yahoo(**kwargs):
        nonlocal yahoo_called
        yahoo_called = True
        raise AssertionError("Yahoo discovery must not run for a Finviz strategy")

    def finviz(**kwargs):
        nonlocal finviz_called
        finviz_called = True
        observed = kwargs["evaluation_time"]
        return freeze_gapper_universe(
            universe_id=kwargs["universe_id"],
            session_date=observed.date(),
            evaluation_time=observed,
            discovery_source="finviz",
            source_locator=FINVIZ_ATOMIC_SOURCE_LOCATOR,
            source_candidate_symbols=("TEST",),
            candidates=[],
            allow_empty=True,
        )

    monkeypatch.setattr(archiver, "discover_yahoo_gappers", yahoo)
    monkeypatch.setattr(archiver, "discover_finviz_gappers", finviz)
    now = datetime(2026, 8, 19, 13, 22, tzinfo=timezone.utc)

    snapshot = archiver.archive_daily_universe_if_due(
        strategy(discovery_source="finviz"),
        repository,
        now=now,
    )

    assert snapshot is not None
    assert finviz_called is True
    assert yahoo_called is False
    assert snapshot.discovery_source == "finviz"
    assert "-finviz-" in snapshot.universe_id


def test_finviz_archive_survives_catalyst_persistence_failure(monkeypatch) -> None:
    repository = FakeRepository()
    now = datetime(2026, 8, 19, 13, 22, tzinfo=timezone.utc)
    logs = []

    class FailingCatalystRepository:
        def save_evidence(self, evidence):
            raise RuntimeError("duplicate key value violates unique constraint")

    def finviz(**kwargs):
        observed = kwargs["evaluation_time"]
        candidate = GapperCandidate(
            instrument_id="equity:NASDAQ:TEST",
            binding_id="yahoo:historical_polling:equity:NASDAQ:TEST",
            observed_at=observed,
            evidence_observed_at={"finviz_top_gainers": observed},
            previous_close=Decimal("8"),
            premarket_price=Decimal("10"),
            gap_pct=Decimal("25"),
            premarket_volume=Decimal("50000"),
            premarket_dollar_volume=Decimal("500000"),
            premarket_bar_count=10,
            tod_rvol=Decimal("5"),
            market_data_complete=True,
            spread_bps=Decimal("40"),
            discovery_rank=1,
        )
        return freeze_gapper_universe(
            universe_id=kwargs["universe_id"],
            session_date=observed.astimezone(timezone.utc).date(),
            evaluation_time=observed,
            discovery_source="finviz",
            source_locator=FINVIZ_ATOMIC_SOURCE_LOCATOR,
            source_candidate_symbols=("TEST",),
            candidates=[candidate],
        )

    def catalyst(**kwargs):
        captured = kwargs["evaluation_time"]
        return (
            SimpleNamespace(
                evidence_id="ev-duplicate",
                dilution_flags=(),
                captured_at=captured,
            ),
        )

    def capture_log(*args, **kwargs):
        logs.append((args, kwargs))

    monkeypatch.setattr(archiver, "discover_finviz_gappers", finviz)
    monkeypatch.setattr(archiver, "trade_log", capture_log)

    snapshot = archiver.archive_daily_universe_if_due(
        strategy(discovery_source="finviz"),
        repository,
        now=now,
        catalyst_repository=FailingCatalystRepository(),
        catalyst_discovery=catalyst,
    )

    assert snapshot is not None
    assert repository.get_universe(snapshot.universe_id) is snapshot
    assert len(snapshot.candidates) == 1
    assert snapshot.candidates[0].catalyst_evidence_ids == ()
    deterministic = evaluate_gap_pullback(
        snapshot.candidates[0],
        [],
        GapPullbackConfig(
            minimum_premarket_dollar_volume=Decimal("1"),
            require_catalyst_evidence=True,
        ),
    )
    assert deterministic.state == "rejected"
    assert deterministic.reason_code == "CATALYST_EVIDENCE_REQUIRED"

    archived = next(kwargs for args, kwargs in logs if len(args) >= 2 and args[1] == "daily_universe_archived")
    assert "evidence_save=RuntimeError: duplicate key value violates unique constraint" in (
        archived["catalyst_capture_errors"]["equity:NASDAQ:TEST"]
    )

def test_late_finviz_startup_recovery_is_research_only(monkeypatch) -> None:
    from app.apps.trading.strategy_data_integrity import assess_universe_integrity

    repository = FakeRepository()
    calls = []

    def finviz(**kwargs):
        calls.append(kwargs)
        observed = kwargs["evaluation_time"]
        return freeze_gapper_universe(
            universe_id=kwargs["universe_id"],
            session_date=observed.astimezone(archiver._ET).date(),
            evaluation_time=observed,
            discovery_source="finviz",
            source_locator=FINVIZ_ATOMIC_SOURCE_LOCATOR,
            source_candidate_symbols=("TEST",),
            candidates=[],
            allow_empty=True,
        )

    monkeypatch.setattr(archiver, "discover_finviz_gappers", finviz)
    # 10:06 ET: the machine came back after the 09:20+10m archive window.
    now = datetime(2026, 9, 9, 14, 6, tzinfo=timezone.utc)
    value = strategy(discovery_source="finviz")

    assert archiver.archive_daily_universe_if_due(value, repository, now=now) is None
    recovered = archiver.archive_daily_universe_if_due(
        value,
        repository,
        now=now,
        allow_late_recovery=True,
    )

    assert recovered is not None
    assert len(calls) == 1
    integrity = assess_universe_integrity(recovered)
    assert integrity.capture_on_time is False
    assert integrity.prospective_eligible is False
    assert "FINVIZ_CAPTURE_NOT_PREOPEN" in integrity.reason_codes


def test_late_finviz_startup_recovery_stops_at_regular_close(monkeypatch) -> None:
    repository = FakeRepository()
    called = False

    def finviz(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("late recovery must not capture after regular close")

    monkeypatch.setattr(archiver, "discover_finviz_gappers", finviz)
    now = datetime(2026, 9, 9, 20, 1, tzinfo=timezone.utc)  # 16:01 ET

    assert archiver.archive_daily_universe_if_due(
        strategy(discovery_source="finviz"),
        repository,
        now=now,
        allow_late_recovery=True,
    ) is None
    assert called is False
