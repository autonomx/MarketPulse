from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from app.apps.trading.gapper_dataset import GapperCandidate, freeze_gapper_universe
from app.apps.trading.models import MarketBar
from app.apps.trading.paper import PaperExecutionPolicy
from app.apps.trading.research.contracts import (
    ResearchValidationReport,
    StrategyResearchFeatures,
    ValidationFeatureResult,
)
from app.apps.trading.research.policy import ResearchPolicyDecision
from app.apps.trading.research.validation import build_validation_report
from app.apps.trading.strategies import evaluate_gap_pullback
from app.apps.trading.strategies.models import GapPullbackConfig, StrategyRiskProfile
from app.apps.trading.strategy_backtest import freeze_backtest_session, run_gap_pullback_backtest
from app.apps.trading.strategy_repository import TradingStrategyConfigDocument
from app.apps.trading.strategy_research_policy import resolve_strategy_research_policy


OPEN = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)


def _validation_rows(count: int) -> list[dict]:
    start = date(2026, 1, 2)
    rows = []
    for index in range(count):
        positive = index % 2 == 0
        rows.append({
            "outcome_id": f"o-{index}",
            "session_date": start + timedelta(days=index),
            "instrument_id": f"equity:NASDAQ:T{index % 5}",
            "features": {
                "primary_catalyst_confirmed": positive,
                "catalyst_same_day": positive,
                "immediate_supply_risk": not positive,
                "unresolved_supply": not positive,
                "source_authority_sufficient": positive,
            },
            "r_result": Decimal("1.5") if positive else Decimal("-0.75"),
            "two_r_before_minus_one_r": positive,
            "market_fidelity": "captured_point_in_time",
            "research_fidelity": "captured_exact",
        })
    return rows


def test_htr14_promotion_floors_cannot_be_lowered_by_request() -> None:
    report = build_validation_report(
        _validation_rows(40),
        minimum_sample=20,
        minimum_exact_sample=10,
    )
    catalyst = next(item for item in report.feature_results if item.feature == "primary_catalyst_confirmed")
    assert catalyst.sample_size == 40
    assert catalyst.exact_sample_size == 40
    assert catalyst.recommendation == "observe_only"
    assert "sample 40 < required 100" in catalyst.reason
    assert any("at least 100" in note for note in report.notes)


def test_htr14_feature_samples_exclude_unknown_missing_and_unlabeled_rows() -> None:
    rows = _validation_rows(60)
    for index in range(20):
        row = _validation_rows(1)[0].copy()
        row["outcome_id"] = f"unknown-{index}"
        row["session_date"] = date(2026, 5, 1) + timedelta(days=index)
        row["features"] = {**row["features"], "immediate_supply_risk": None}
        rows.append(row)
    for index in range(20):
        row = _validation_rows(1)[0].copy()
        row["outcome_id"] = f"missing-{index}"
        row["session_date"] = date(2026, 6, 1) + timedelta(days=index)
        row["features"] = {key: value for key, value in row["features"].items() if key != "immediate_supply_risk"}
        rows.append(row)
    for index in range(10):
        row = _validation_rows(1)[0].copy()
        row["outcome_id"] = f"unlabeled-{index}"
        row["session_date"] = date(2026, 7, 1) + timedelta(days=index)
        row["r_result"] = None
        rows.append(row)

    report = build_validation_report(rows)
    supply = next(item for item in report.feature_results if item.feature == "immediate_supply_risk")
    assert supply.sample_size == 60
    assert supply.exact_sample_size == 60


def _features() -> StrategyResearchFeatures:
    now = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    return StrategyResearchFeatures(
        feature_id="f-1",
        strategy_id="strategy-1",
        instrument_id="equity:NASDAQ:XYZ",
        fact_set_id="facts-1",
        decision_at=now,
        omnix_known_at=now,
        primary_catalyst_confirmed=True,
        catalyst_same_day=True,
        catalyst_fresh=True,
        immediate_supply_risk=False,
        supply_resolution_status="clear",
        research_status="complete",
        unresolved_supply=False,
        source_authority_sufficient=True,
        immutable_fingerprint="f" * 64,
    )


def _promoted_report() -> ResearchValidationReport:
    return ResearchValidationReport(
        validation_id="reviewed-policy-1",
        policy_version="trading-research-1",
        generated_at=datetime(2026, 8, 20, 14, 5, tzinfo=timezone.utc),
        sample_size=200,
        exact_sample_size=120,
        feature_results=(ValidationFeatureResult(
            feature="primary_catalyst_confirmed",
            sample_size=200,
            exact_sample_size=120,
            recommendation="hard_gate",
            reason="reviewed",
        ),),
        promotion_allowed=True,
        notes=("reviewed",),
        immutable_fingerprint="v" * 64,
    )


def test_v12_resolution_uses_pinned_promoted_artifact_not_latest_analysis() -> None:
    class Repo:
        def research_features_as_of(self, instrument_id, decision_at):
            return _features()

        def promoted_validation_report(self, policy_version):
            return _promoted_report()

        def latest_validation_report(self, policy_version):
            raise AssertionError("authoritative resolution must not follow later analysis")

    decision = resolve_strategy_research_policy(
        strategy_version="1.2.0",
        instrument_id="equity:NASDAQ:XYZ",
        decision_at=datetime(2026, 8, 20, 14, 10, tzinfo=timezone.utc),
        fact_repository=Repo(),
    )
    assert decision.allowed is True
    assert decision.authoritative is True
    assert decision.policy_version == "trading-research-1"


def test_strategy_document_rejects_outer_inner_version_split_brain() -> None:
    with pytest.raises(ValueError, match="strategy_version_mismatch_between_document_and_config"):
        TradingStrategyConfigDocument(
            strategy_id="strategy-1",
            account_id="paper-1",
            strategy_version="1.2.0",
            config=GapPullbackConfig(strategy_version="1.1.0"),
        )


def _bar(instrument_id: str, index: int, o: str, h: str, l: str, c: str, volume: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    return MarketBar(
        instrument_id=instrument_id,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(volume),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def _bars(instrument_id: str) -> list[MarketBar]:
    values = [
        ("10", "10.4", "9.9", "10.3", "100"),
        ("10.3", "11.2", "10.2", "11.0", "120"),
        ("11.0", "11.05", "10.5", "10.6", "80"),
        ("10.6", "10.7", "9.8", "10.0", "70"),
        ("10.0", "10.9", "9.95", "10.8", "80"),
        ("10.8", "11.1", "10.7", "11.0", "90"),
        ("10.9", "11.0", "10.4", "10.5", "80"),
        ("10.5", "10.7", "10.2", "10.3", "70"),
        ("10.3", "11.0", "10.25", "10.9", "80"),
        ("10.9", "11.8", "10.85", "11.7", "400"),
        ("11.75", "12.0", "11.6", "11.9", "1000"),
        ("11.9", "15.5", "11.8", "15.0", "1000"),
    ]
    return [_bar(instrument_id, index, *row) for index, row in enumerate(values)]


def _candidate(instrument_id: str, rank: int) -> GapperCandidate:
    return GapperCandidate(
        instrument_id=instrument_id,
        binding_id=f"fixture:{instrument_id.rsplit(':', 1)[-1]}",
        previous_close=Decimal("8"),
        premarket_price=Decimal("10.4"),
        gap_pct=Decimal("30"),
        premarket_volume=Decimal("100000"),
        premarket_dollar_volume=Decimal("1040000"),
        tod_rvol=Decimal("3"),
        market_cap=Decimal("50000000"),
        float_shares=Decimal("5000000"),
        spread_bps=Decimal("40"),
        discovery_rank=rank,
    )


def test_v12_backtest_arbitrates_with_research_adjusted_quality() -> None:
    first = "equity:NASDAQ:AAA"
    second = "equity:NASDAQ:BBB"
    candidates = [_candidate(first, 1), _candidate(second, 2)]
    universe = freeze_gapper_universe(
        universe_id="htr-ranking-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc),
        discovery_source="import",
        candidates=candidates,
    )
    config = GapPullbackConfig(
        strategy_version="1.2.0",
        research_score_adjustment_enabled=True,
        pivot_left_bars=1,
        pivot_right_bars=1,
        volume_lookback_bars=5,
        breakout_volume_ratio=Decimal("1.25"),
        entry_start_et=time(9, 30),
        minimum_quality_score=0,
    )
    bars_by_instrument = {first: _bars(first), second: _bars(second)}
    dataset = freeze_backtest_session(
        session_date=date(2026, 8, 18),
        universe=universe,
        bars_by_instrument=bars_by_instrument,
    )
    base = evaluate_gap_pullback(candidates[1], tuple(bars_by_instrument[second][:10]), config)
    assert base.signal is not None
    base_quality = base.signal.quality_score

    def resolver(instrument_id: str, decision_at: datetime) -> ResearchPolicyDecision:
        return ResearchPolicyDecision(
            allowed=True,
            authoritative=True,
            reason_code="RESEARCH_POLICY_PASS",
            score_adjustment=1 if instrument_id == second else -1,
            policy_version="trading-research-1",
        )

    result = run_gap_pullback_backtest(
        dataset,
        config,
        PaperExecutionPolicy(slippage_bps=Decimal("10"), max_volume_participation_pct=Decimal("1"), latency_ms=0),
        max_concurrent_positions=1,
        risk_profile=StrategyRiskProfile(max_positions=1),
        research_policy_resolver=resolver,
    )
    assert result.trades
    assert result.trades[0].instrument_id == second
    assert result.trades[0].quality_score == base_quality + 1


def test_backtest_returns_decision_row_for_every_candidate() -> None:
    triggered = "equity:NASDAQ:AAA"
    rejected = "equity:NASDAQ:ZZZ"
    candidates = [_candidate(triggered, 1), _candidate(rejected, 2).model_copy(update={"premarket_price": Decimal("8.4"), "gap_pct": Decimal("5")})]
    universe = freeze_gapper_universe(
        universe_id="htr-candidate-outcomes-2026-08-18",
        session_date=date(2026, 8, 18),
        evaluation_time=datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc),
        discovery_source="import",
        candidates=candidates,
    )
    dataset = freeze_backtest_session(
        session_date=date(2026, 8, 18),
        universe=universe,
        bars_by_instrument={triggered: _bars(triggered), rejected: _bars(rejected)},
    )
    result = run_gap_pullback_backtest(
        dataset,
        GapPullbackConfig(pivot_left_bars=1, pivot_right_bars=1, volume_lookback_bars=5, entry_start_et=time(9, 30)),
        PaperExecutionPolicy(slippage_bps=Decimal("10"), max_volume_participation_pct=Decimal("1"), latency_ms=0),
    )
    assert len(result.candidate_decisions) == 2
    by_symbol = {item.instrument_id: item for item in result.candidate_decisions}
    assert by_symbol[triggered].triggered is True
    assert by_symbol[rejected].state == "rejected"
    assert by_symbol[rejected].selected_trade is False


def test_deterministic_harvest_resolution_rule_skips_only_fully_resolved_primary_case() -> None:
    from app.apps.trading.research.contracts import ResearchCoverage
    from app.apps.trading.research.coordinator import _deterministic_harvest_resolved
    from app.apps.trading.research.facts.extraction import build_fact_set
    from app.apps.trading.research.contracts import TradingEvidence, fingerprint

    captured = datetime(2026, 8, 20, 13, 30, tzinfo=timezone.utc)
    catalyst = TradingEvidence(
        evidence_id="ir-catalyst", instrument_id="equity:NASDAQ:XYZ", evidence_type="company_release",
        source_type="company_ir", source_locator="https://example.test/ir", source_authority_tier=1,
        source_published_at=captured, source_available_at=captured, captured_at=captured, omnix_known_at=captured,
        title="Company announces contract award", content="The company announced a material contract award today.",
        content_hash="c" * 64, extraction_status="completed", metadata={},
        immutable_fingerprint=fingerprint({"id": "ir-catalyst"}),
    )
    supply = TradingEvidence(
        evidence_id="sec-supply", instrument_id="equity:NASDAQ:XYZ", evidence_type="sec_filing_content",
        source_type="sec", source_locator="https://sec.gov/Archives/example", source_authority_tier=1,
        source_published_at=captured, source_available_at=captured, captured_at=captured, omnix_known_at=captured,
        title="ATM termination", content="The previous at-the-market offering was terminated and is no longer available.",
        content_hash="d" * 64, extraction_status="completed", metadata={"form": "8-K"},
        immutable_fingerprint=fingerprint({"id": "sec-supply"}),
    )
    facts = build_fact_set(instrument_id="equity:NASDAQ:XYZ", evidence=(catalyst, supply), decision_at=captured)
    complete = ResearchCoverage(sec="complete", company_ir="complete", recent_news="complete")
    assert _deterministic_harvest_resolved(complete, facts) is True
    assert _deterministic_harvest_resolved(complete.model_copy(update={"sec": "failed"}), facts) is False
