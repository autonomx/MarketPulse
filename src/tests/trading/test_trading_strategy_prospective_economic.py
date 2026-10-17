from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.strategies.models import StrategyRiskProfile
from app.apps.trading.strategy_prospective_economic import (
    PROSPECTIVE_ECONOMIC_VERSION,
    evaluate_prospective_economic_status,
    prospective_economic_profile_fingerprint,
)
from app.apps.trading.strategy_repository import StrategyEvent, TradingStrategyConfigDocument
from app.apps.trading.strategy_v2_qualification import frozen_v2_config


def _strategy() -> TradingStrategyConfigDocument:
    config = frozen_v2_config()
    return TradingStrategyConfigDocument(
        strategy_id="prospective-economic-test",
        account_id="paper-1",
        strategy_kind="gap_pullback_v1",
        strategy_version="2.0.0",
        mode="shadow",
        active_universe_id=None,
        config=config,
        risk=StrategyRiskProfile(),
        enabled=True,
    )


def _event(
    *,
    event_type: str,
    instrument_id: str,
    observed_at: datetime,
    payload: dict[str, object],
    suffix: str,
    state: str = "observed",
) -> StrategyEvent:
    idem = hashlib.sha256(f"{event_type}|{instrument_id}|{observed_at.isoformat()}|{suffix}".encode()).hexdigest()
    return StrategyEvent(
        strategy_id="prospective-economic-test",
        event_id=idem[:32],
        instrument_id=instrument_id,
        event_type=event_type,
        state=state,
        reason_code=event_type.upper(),
        observed_at=observed_at,
        idempotency_key=idem,
        payload=payload,
    )


def _sample(
    strategy: TradingStrategyConfigDocument,
    *,
    start: datetime,
    count: int,
    loss_every: int | None,
    suffix: str,
) -> list[StrategyEvent]:
    profile = prospective_economic_profile_fingerprint(strategy)
    events: list[StrategyEvent] = []
    for index in range(count):
        signal_at = start + timedelta(days=index)
        instrument = f"equity:SYM{index % 15}"
        signal = _event(
            event_type="prospective_economic_signal",
            instrument_id=instrument,
            observed_at=signal_at,
            suffix=f"{suffix}-signal-{index}",
            payload={
                "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                "profile_fingerprint": profile,
                "session_date": signal_at.date().isoformat(),
                "entry_time": signal_at.isoformat(),
                "execution_eligible": True,
                "matched_signal": True,
                "entry_price": "10.00",
                "stop_price": "9.00",
                "risk_per_share": "1.00",
                "execution_authority": False,
            },
        )
        is_loss = loss_every is not None and (index + 1) % loss_every == 0
        result = Decimal("-1") if is_loss else Decimal("1")
        outcome_at = signal_at + timedelta(minutes=60)
        outcome = _event(
            event_type="prospective_economic_outcome",
            instrument_id=instrument,
            observed_at=outcome_at,
            suffix=f"{suffix}-outcome-{index}",
            state="loss" if is_loss else "win",
            payload={
                "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
                "profile_fingerprint": profile,
                "signal_event_id": signal.event_id,
                "session_date": signal_at.date().isoformat(),
                "entry_time": signal_at.isoformat(),
                "data_complete": True,
                "matched_signal": True,
                "one_r_before_minus_one_r": not is_loss,
                "r_result_60m": str(result),
                "execution_authority": False,
            },
        )
        events.extend([signal, outcome])
    return events


def _passing_evaluation(strategy: TradingStrategyConfigDocument, events: list[StrategyEvent]) -> StrategyEvent:
    before = evaluate_prospective_economic_status(strategy, events)
    observed_at = max(event.observed_at for event in events) + timedelta(minutes=1)
    return _event(
        event_type="prospective_economic_evaluation",
        instrument_id="strategy:prospective-economic-test",
        observed_at=observed_at,
        suffix="evaluation",
        state="passed",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": before.profile_fingerprint,
            "evidence_fingerprint": before.evidence_fingerprint,
            "metrics": before.metrics.model_dump(mode="json"),
            "thresholds": before.thresholds.model_dump(mode="json"),
            "passed": True,
            "immutable_one_shot": True,
            "execution_authority": False,
        },
    )


def test_pipeline_is_sequential_and_final_review_binds_exact_soak_snapshot() -> None:
    strategy = _strategy()
    initial = _sample(
        strategy,
        start=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        count=30,
        loss_every=5,
        suffix="initial",
    )
    before = evaluate_prospective_economic_status(strategy, initial)

    assert before.metrics.matched_outcome_count == 30
    assert before.metrics.distinct_sessions == 30
    assert before.metrics.distinct_symbols == 15
    assert before.metrics.execution_match_rate == Decimal("1")
    assert before.metrics.win_rate == Decimal("0.8")
    assert before.metrics.expectancy_r == Decimal("0.6")
    assert before.sample_ready is True
    assert before.quantitative_pass is True
    assert before.evaluation_recorded is False
    assert before.sealed_holdout_unlocked is False

    evaluation = _passing_evaluation(strategy, initial)
    evaluation_at = evaluation.observed_at
    after_evaluation = evaluate_prospective_economic_status(strategy, [*initial, evaluation])
    assert after_evaluation.evaluation_passed is True
    assert after_evaluation.sealed_holdout_unlocked is True
    assert after_evaluation.holdout_reviewed is False

    holdout_at = evaluation_at + timedelta(minutes=1)
    holdout = _event(
        event_type="prospective_economic_holdout_review",
        instrument_id="strategy:prospective-economic-test",
        observed_at=holdout_at,
        suffix="holdout",
        state="passed",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": before.profile_fingerprint,
            "evaluation_event_id": evaluation.event_id,
            "holdout_verdict": "GOLD",
            "approved": True,
            "execution_authority": False,
        },
    )
    after_holdout = evaluate_prospective_economic_status(strategy, [*initial, evaluation, holdout])
    assert after_holdout.holdout_reviewed is True
    assert after_holdout.holdout_verdict == "GOLD"
    assert after_holdout.soak_passed is False

    soak = _sample(
        strategy,
        start=holdout_at + timedelta(days=1),
        count=10,
        loss_every=None,
        suffix="soak",
    )
    after_soak = evaluate_prospective_economic_status(strategy, [*initial, evaluation, holdout, *soak])
    assert after_soak.soak_metrics.matched_outcome_count == 10
    assert after_soak.soak_metrics.distinct_sessions == 10
    assert after_soak.soak_metrics.distinct_symbols == 10
    assert after_soak.soak_passed is True
    assert after_soak.auto_paper_research_authorized is False

    review_at = max(event.observed_at for event in soak) + timedelta(minutes=1)
    review = _event(
        event_type="prospective_economic_auto_paper_review",
        instrument_id="strategy:prospective-economic-test",
        observed_at=review_at,
        suffix="auto-review",
        state="approved",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": before.profile_fingerprint,
            "pipeline_evidence_fingerprint": after_soak.pipeline_evidence_fingerprint,
            "approved": True,
            "execution_authority": False,
        },
    )
    final = evaluate_prospective_economic_status(strategy, [*initial, evaluation, holdout, *soak, review])
    assert final.auto_paper_reviewed is True
    assert final.auto_paper_research_authorized is True


def test_failed_one_shot_cannot_be_rescued_by_later_winning_data() -> None:
    strategy = _strategy()
    weak = _sample(
        strategy,
        start=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        count=30,
        loss_every=2,
        suffix="weak",
    )
    before = evaluate_prospective_economic_status(strategy, weak)
    assert before.sample_ready is True
    assert before.quantitative_pass is False

    evaluation_at = max(event.observed_at for event in weak) + timedelta(minutes=1)
    failed = _event(
        event_type="prospective_economic_evaluation",
        instrument_id="strategy:prospective-economic-test",
        observed_at=evaluation_at,
        suffix="failed-evaluation",
        state="failed",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": before.profile_fingerprint,
            "evidence_fingerprint": before.evidence_fingerprint,
            "passed": False,
            "immutable_one_shot": True,
            "execution_authority": False,
        },
    )
    later_wins = _sample(
        strategy,
        start=evaluation_at + timedelta(days=1),
        count=40,
        loss_every=None,
        suffix="later",
    )
    result = evaluate_prospective_economic_status(strategy, [*weak, failed, *later_wins])

    assert result.quantitative_pass is True
    assert result.evaluation_recorded is True
    assert result.evaluation_passed is False
    assert result.sealed_holdout_unlocked is False
    assert result.auto_paper_research_authorized is False
    assert "PROSPECTIVE_ECONOMIC_ONE_SHOT_EVALUATION_FAILED" in result.reason_codes


def test_candidate_diagnostics_never_change_promotion_metrics_or_evidence_fingerprint() -> None:
    strategy = _strategy()
    initial = _sample(
        strategy,
        start=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        count=30,
        loss_every=5,
        suffix="initial-candidate-proof",
    )
    baseline = evaluate_prospective_economic_status(strategy, initial)
    diagnostic = _event(
        event_type="prospective_economic_candidate",
        instrument_id="equity:DIAGNOSTIC",
        observed_at=datetime(2026, 8, 24, 13, 45, tzinfo=timezone.utc),
        suffix="diagnostic-only",
        state="watching",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": baseline.profile_fingerprint,
            "source_state": "watching",
            "diagnostic_only": True,
            "promotion_metric_eligible": False,
            "execution_authority": False,
        },
    )

    with_diagnostic = evaluate_prospective_economic_status(strategy, [diagnostic, *initial])

    assert with_diagnostic.metrics == baseline.metrics
    assert with_diagnostic.evidence_fingerprint == baseline.evidence_fingerprint
    assert with_diagnostic.quantitative_pass == baseline.quantitative_pass


def test_failed_holdout_review_is_terminal_and_cannot_open_soak_path() -> None:
    strategy = _strategy()
    initial = _sample(
        strategy,
        start=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        count=30,
        loss_every=5,
        suffix="holdout-fail-initial",
    )
    evaluation = _passing_evaluation(strategy, initial)
    profile = prospective_economic_profile_fingerprint(strategy)
    failed_holdout = _event(
        event_type="prospective_economic_holdout_review",
        instrument_id="strategy:prospective-economic-test",
        observed_at=evaluation.observed_at + timedelta(minutes=1),
        suffix="failed-holdout",
        state="failed",
        payload={
            "policy_version": PROSPECTIVE_ECONOMIC_VERSION,
            "profile_fingerprint": profile,
            "evaluation_event_id": evaluation.event_id,
            "holdout_verdict": "FAIL",
            "approved": False,
            "execution_authority": False,
        },
    )
    later_wins = _sample(
        strategy,
        start=failed_holdout.observed_at + timedelta(days=1),
        count=20,
        loss_every=None,
        suffix="post-failed-holdout",
    )

    result = evaluate_prospective_economic_status(
        strategy,
        [*initial, evaluation, failed_holdout, *later_wins],
    )

    assert result.holdout_reviewed is True
    assert result.holdout_verdict == "FAIL"
    assert result.holdout_event_id == failed_holdout.event_id
    assert result.soak_passed is False
    assert result.auto_paper_research_authorized is False
    assert "PROSPECTIVE_ECONOMIC_SEALED_HOLDOUT_FAILED" in result.reason_codes


def test_end_to_end_completion_rate_keeps_missing_outcomes_in_denominator() -> None:
    strategy = _strategy()
    complete = _sample(
        strategy,
        start=datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc),
        count=30,
        loss_every=5,
        suffix="complete-coverage",
    )
    pending_pairs = _sample(
        strategy,
        start=datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc),
        count=4,
        loss_every=None,
        suffix="pending-coverage",
    )
    pending_signals = [
        event for event in pending_pairs
        if event.event_type == "prospective_economic_signal"
    ]

    censored = evaluate_prospective_economic_status(strategy, [*complete, *pending_signals])

    assert censored.metrics.signal_count == 34
    assert censored.metrics.matched_signal_count == 34
    assert censored.metrics.matched_outcome_count == 30
    assert censored.metrics.execution_match_rate == Decimal(30) / Decimal(34)
    assert censored.metrics.execution_match_rate < Decimal("0.90")
    assert censored.sample_ready is True
    assert censored.quantitative_pass is False

    completed = evaluate_prospective_economic_status(strategy, [*complete, *pending_pairs])
    assert completed.metrics.matched_outcome_count == 34
    assert completed.metrics.execution_match_rate == Decimal("1")
    assert completed.quantitative_pass is True
