from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.apps.trading.research.contracts import (
    ResearchCoverage,
    ResearchValidationReport,
    StrategyResearchFeatures,
    TradingResearchReport,
    ValidationFeatureResult,
)
from app.apps.trading.research.knowledge_time import latest_as_of
from app.apps.trading.research.policy import evaluate_research_policy


def _report(version: int, known_at: datetime) -> TradingResearchReport:
    return TradingResearchReport(
        report_id=f"report-v{version}",
        report_version=version,
        strategy_id="strategy-1",
        instrument_id="equity:NASDAQ:XYZ",
        research_started_at=known_at - timedelta(minutes=1),
        research_completed_at=known_at,
        evidence_cutoff_at=known_at,
        omnix_known_at=known_at,
        catalyst_status="confirmed",
        supply_status="unresolved",
        research_status="partial",
        coverage=ResearchCoverage(sec="complete"),
        source_evidence_ids=(f"e{version}",),
        planner_backend="fixture",
        immutable_fingerprint=str(version) * 64,
    )


def test_report_version_replay_selects_latest_record_known_at_decision():
    base = datetime(2026, 8, 20, 13, 27, tzinfo=timezone.utc)
    reports = [
        _report(1, base),
        _report(2, base + timedelta(minutes=5)),
        _report(3, base + timedelta(minutes=11)),
    ]
    assert latest_as_of(reports, base + timedelta(minutes=3)).report_version == 1
    assert latest_as_of(reports, base + timedelta(minutes=9)).report_version == 2
    assert latest_as_of(reports, base + timedelta(minutes=13)).report_version == 3


def test_future_report_is_invisible_to_earlier_decision():
    base = datetime(2026, 8, 20, 13, 27, tzinfo=timezone.utc)
    reports = [_report(1, base), _report(2, base + timedelta(minutes=5))]
    assert latest_as_of(reports, base + timedelta(minutes=2)).report_id == "report-v1"


def _features(**updates) -> StrategyResearchFeatures:
    now = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    payload = dict(
        feature_id="features-1",
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
    payload.update(updates)
    return StrategyResearchFeatures(**payload)


def _approved_validation(*results: ValidationFeatureResult) -> ResearchValidationReport:
    return ResearchValidationReport(
        validation_id="approved-1",
        policy_version="trading-research-1",
        generated_at=datetime(2026, 8, 20, 14, 5, tzinfo=timezone.utc),
        sample_size=500,
        exact_sample_size=300,
        feature_results=results,
        promotion_allowed=True,
        notes=("Reviewed fixture promotion.",),
        immutable_fingerprint="v" * 64,
    )


def test_v12_uses_reviewed_hard_gate_but_v11_ignores_it():
    validation = _approved_validation(ValidationFeatureResult(
        feature="immediate_supply_risk",
        sample_size=500,
        exact_sample_size=300,
        recommendation="hard_gate",
        reason="reviewed fixture",
    ))
    risky = _features(immediate_supply_risk=True, supply_resolution_status="risk_found")
    v12 = evaluate_research_policy(
        strategy_version="1.2.0", features=risky, validation=validation,
    )
    assert v12.allowed is False
    assert v12.reason_code == "RESEARCH_HARD_GATE_IMMEDIATE_SUPPLY_RISK"

    legacy = evaluate_research_policy(
        strategy_version="1.1.0", features=risky, validation=validation,
    )
    assert legacy.allowed is True
    assert legacy.authoritative is False
    assert legacy.reason_code == "LEGACY_RESEARCH_NON_AUTHORITATIVE"


def test_v12_passes_clean_reviewed_features():
    validation = _approved_validation(
        ValidationFeatureResult(
            feature="primary_catalyst_confirmed", sample_size=500, exact_sample_size=300,
            recommendation="hard_gate", reason="reviewed fixture",
        ),
        ValidationFeatureResult(
            feature="immediate_supply_risk", sample_size=500, exact_sample_size=300,
            recommendation="hard_gate", reason="reviewed fixture",
        ),
    )
    decision = evaluate_research_policy(
        strategy_version="1.2.0", features=_features(), validation=validation,
    )
    assert decision.allowed is True
    assert decision.authoritative is True
    assert decision.reason_code == "RESEARCH_POLICY_PASS"
