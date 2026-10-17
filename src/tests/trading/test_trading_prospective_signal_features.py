from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.indicator_signals import multi_timeframe_indicator_context
from app.apps.trading.models import MarketBar
from app.apps.trading.providers.alpaca_iex_status import AlpacaIexStatusCache, AlpacaTradingStatus
from app.apps.trading.research.contracts import (
    CatalystFactSet,
    ResearchCoverage,
    SupplyFact,
    SupplyMetrics,
    TradingFactSet,
    TradingResearchReport,
)
from app.apps.trading.strategy_prospective_signal_features import (
    build_prospective_signal_features,
    premarket_structure_snapshot,
)


INSTRUMENT = "equity:NASDAQ:TEST"
SESSION = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)  # 04:00 ET
DECISION = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def _bars() -> list[MarketBar]:
    values: list[MarketBar] = []
    for index in range(360):
        start = SESSION + timedelta(minutes=index)
        base = Decimal("5") + Decimal(index) * Decimal("0.01")
        values.append(
            MarketBar(
                instrument_id=INSTRUMENT,
                interval="1m",
                start_time=start,
                end_time=start + timedelta(minutes=1),
                open=base,
                high=base + Decimal("0.05"),
                low=base - Decimal("0.04"),
                close=base + Decimal("0.02"),
                volume=Decimal("1000") + Decimal(index),
                is_final=True,
                session="extended_pre" if index < 330 else "regular",
                provider="alpaca_iex",
                provider_event_id=f"bar-{index}",
                received_at=start + timedelta(minutes=1),
            )
        )
    return values


def _fact_set() -> TradingFactSet:
    known = datetime(2026, 8, 24, 13, 10, tzinfo=timezone.utc)
    catalyst = CatalystFactSet(
        primary_confirmed=True,
        same_day=True,
        source_count_primary=2,
        source_count_secondary=1,
        catalyst_type="earnings",
        source_published_at=datetime(2026, 8, 24, 11, 30, tzinfo=timezone.utc),
        official_filing_present=True,
        company_release_present=True,
        unresolved=False,
        source_evidence_ids=("ev-1", "ev-2"),
        generated_at=known,
        omnix_known_at=known,
    )
    supply = SupplyFact(
        fact_id="supply-1",
        instrument_id=INSTRUMENT,
        supply_type="atm",
        status="terminated",
        shares=Decimal("1000000"),
        source_evidence_ids=("ev-3",),
        resolution_status="resolved",
        confidence=Decimal("0.95"),
        generated_at=known,
        omnix_known_at=known,
        immutable_fingerprint="supply-fp",
    )
    return TradingFactSet(
        fact_set_id="fact-set-1",
        strategy_id="strategy-1",
        instrument_id=INSTRUMENT,
        report_id="report-1",
        generated_at=known,
        omnix_known_at=known,
        catalyst=catalyst,
        supply=(supply,),
        supply_metrics=SupplyMetrics(
            potential_dilution_pct_float=Decimal("12.5"),
            immediate_supply_risk=False,
            supply_resolution_status="clear",
        ),
        completeness=ResearchCoverage(
            sec="complete",
            company_ir="complete",
            recent_news="complete",
            atm="complete",
            warrants="complete",
            resale_registration="complete",
            convertibles="complete",
        ),
        unresolved_facts=(),
        evidence_ids=("ev-1", "ev-2", "ev-3"),
        immutable_fingerprint="fact-set-fp",
    )


def _report() -> TradingResearchReport:
    known = datetime(2026, 8, 24, 13, 12, tzinfo=timezone.utc)
    return TradingResearchReport(
        report_id="report-1",
        report_version=1,
        strategy_id="strategy-1",
        instrument_id=INSTRUMENT,
        research_started_at=datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
        research_completed_at=datetime(2026, 8, 24, 13, 11, tzinfo=timezone.utc),
        evidence_cutoff_at=datetime(2026, 8, 24, 13, 10, tzinfo=timezone.utc),
        omnix_known_at=known,
        catalyst_status="confirmed",
        supply_status="clear",
        research_status="complete",
        coverage=ResearchCoverage(sec="complete", company_ir="complete", recent_news="complete"),
        unresolved_facts=(),
        source_evidence_ids=("ev-1", "ev-2", "ev-3"),
        planner_backend="local",
        immutable_fingerprint="report-fp",
    )


class _ResearchRepo:
    def latest_report_as_of(self, instrument_id: str, known_at_lte: datetime):
        assert instrument_id == INSTRUMENT
        assert known_at_lte == DECISION
        return _report()


class _FactRepo:
    def latest_fact_set_as_of(self, instrument_id: str, known_at_lte: datetime):
        assert instrument_id == INSTRUMENT
        assert known_at_lte == DECISION
        return _fact_set()


def test_premarket_snapshot_stops_at_regular_open() -> None:
    snapshot = premarket_structure_snapshot(_bars(), decision_at=DECISION)

    assert snapshot["available"] is True
    assert snapshot["bar_count"] == 330
    assert snapshot["first_bar_at"] == "2026-08-24T08:00:00+00:00"
    assert snapshot["last_bar_at"] == "2026-08-24T13:30:00+00:00"
    assert snapshot["high_at"] == "2026-08-24T13:29:00+00:00"
    assert Decimal(str(snapshot["volume"])) > 0
    assert Decimal(str(snapshot["dollar_volume"])) > 0
    assert snapshot["vwap"] is not None
    assert snapshot["close_vs_high_pct"] is not None
    assert snapshot["last_30m_return_pct"] is not None


def test_halt_history_is_complete_only_with_continuous_0400_coverage() -> None:
    cache = AlpacaIexStatusCache()
    cache.set_connected(True, observed_at=datetime(2026, 8, 24, 7, 55, tzinfo=timezone.utc))
    cache.record(
        AlpacaTradingStatus(
            symbol="TEST",
            status_code="H",
            reason_code="T1",
            message="news pending",
            observed_at=datetime(2026, 8, 24, 13, 45, tzinfo=timezone.utc),
            halted=True,
        )
    )
    cache.record(
        AlpacaTradingStatus(
            symbol="TEST",
            status_code="T",
            reason_code=None,
            message="resumed",
            observed_at=datetime(2026, 8, 24, 13, 50, tzinfo=timezone.utc),
            halted=False,
        )
    )

    snapshot = cache.history_snapshot("TEST", as_of=DECISION)
    assert snapshot["session_history_complete"] is True
    assert snapshot["halted_at_decision"] is False
    assert snapshot["halt_event_count"] == 1
    assert snapshot["resume_event_count"] == 1
    assert snapshot["last_halt_at"] == "2026-08-24T13:45:00+00:00"
    assert snapshot["last_resume_at"] == "2026-08-24T13:50:00+00:00"

    cache.set_connected(False, observed_at=datetime(2026, 8, 24, 13, 55, tzinfo=timezone.utc))
    cache.set_connected(True, observed_at=datetime(2026, 8, 24, 13, 56, tzinfo=timezone.utc))
    interrupted = cache.history_snapshot("TEST", as_of=DECISION + timedelta(minutes=1))
    assert interrupted["session_history_complete"] is False
    assert interrupted["disconnect_count"] == 1


def test_prospective_feature_row_combines_market_research_halt_and_momentum() -> None:
    bars = _bars()
    context = multi_timeframe_indicator_context(bars)
    cache = AlpacaIexStatusCache()
    cache.set_connected(True, observed_at=datetime(2026, 8, 24, 7, 50, tzinfo=timezone.utc))

    features = build_prospective_signal_features(
        instrument_id=INSTRUMENT,
        decision_at=DECISION,
        bars=bars,
        indicator_context=context,
        indicator_full_warmup=True,
        research_repository=_ResearchRepo(),
        fact_repository=_FactRepo(),
        status_cache=cache,
    )

    assert features["schema_version"] == "v2-prospective-signal-features-2"
    assert features["execution_authority"] is False
    assert features["market_data_source"] == "canonical_causal_bars"
    assert features["source_providers"] == ["alpaca_iex"]
    assert features["partial_market"] is True
    assert features["premarket"]["bar_count"] == 330
    assert features["research"]["report"]["research_status"] == "complete"
    assert features["research"]["catalyst"]["primary_confirmed"] is True
    assert features["research"]["catalyst"]["catalyst_type"] == "earnings"
    assert features["research"]["supply"]["resolution_status"] == "clear"
    assert features["research"]["supply"]["facts"][0]["status"] == "terminated"
    assert features["halt_history"]["session_history_complete"] is True
    assert features["halt_history"]["halted_at_decision"] is False
    assert features["momentum"]["full_warmup"] is True
    assert features["momentum"]["one_minute"]["macd"] is not None
    assert features["momentum"]["five_minute"]["stochastic_rsi_k"] is not None
    assert features["completeness"]["all_core_available"] is True
    assert len(str(features["immutable_fingerprint"])) == 64


def test_prospective_feature_row_preserves_missing_research() -> None:
    class _BrokenResearchRepo:
        def latest_report_as_of(self, instrument_id: str, known_at_lte: datetime):
            raise RuntimeError("research unavailable")

    features = build_prospective_signal_features(
        instrument_id=INSTRUMENT,
        decision_at=DECISION,
        bars=_bars(),
        indicator_context=None,
        indicator_full_warmup=False,
        research_repository=_BrokenResearchRepo(),
        fact_repository=_FactRepo(),
        status_cache=AlpacaIexStatusCache(),
    )

    assert features["research"]["available"] is False
    assert features["research"]["error"] == "RuntimeError: research unavailable"
    assert features["momentum"]["available"] is False
    assert features["completeness"]["all_core_available"] is False
    assert features["execution_authority"] is False
