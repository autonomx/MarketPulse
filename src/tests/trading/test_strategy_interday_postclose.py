from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_dynamic_discovery import (
    CandidateLifecycleState,
    DynamicCandidate,
    EvaluationTier,
)
from app.apps.trading.strategy_dynamic_discovery_learning import DiscoveryDailyReport
from app.apps.trading.strategy_interday_postclose import (
    label_candidate_outcome,
    qualification_from_persisted_evidence,
)


SESSION = date(2026, 9, 11)
OPEN = datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)
DISCOVERED = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
CLOSE = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)


class _Market:
    def __init__(self, bars):
        self._bars = bars

    def bars(self, instrument_id, interval, limit, binding_id):
        assert instrument_id == "equity:NASDAQ:TRUG"
        assert interval == "1m"
        assert limit == 500
        assert binding_id is None
        return SimpleNamespace(bars=list(self._bars))


class _Repository:
    def recent_events(self, strategy_id: str, limit: int):
        assert strategy_id == "interday-trading-strategy-shadow"
        return []


def _bar(index: int, price: str) -> MarketBar:
    start = OPEN + timedelta(minutes=index)
    value = Decimal(price)
    return MarketBar(
        instrument_id="equity:NASDAQ:TRUG",
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=value,
        high=value + Decimal("0.05"),
        low=value - Decimal("0.05"),
        close=value,
        volume=Decimal("10000"),
        is_final=True,
        session="regular",
        provider="fixture",
        received_at=start + timedelta(minutes=1),
    )


def _candidate() -> DynamicCandidate:
    return DynamicCandidate(
        session_date=SESSION,
        instrument_id="equity:NASDAQ:TRUG",
        first_seen_at=DISCOVERED,
        discovered_at=DISCOVERED,
        last_observed_at=DISCOVERED,
        lifecycle=CandidateLifecycleState.ACTIVE,
        tier=EvaluationTier.A,
        attention_score=90,
        common_priority=90,
    )


def test_postclose_outcome_uses_last_causally_known_close_as_reference() -> None:
    bars = [_bar(index, "10") for index in range(30)]
    bars.extend(_bar(index, str(10 + (index - 29) * 0.10)) for index in range(30, 61))

    outcome = label_candidate_outcome(
        _Market(bars),
        _candidate(),
        observed_at=CLOSE,
    )

    assert outcome is not None
    assert outcome.discovered_at == DISCOVERED
    assert outcome.reference_price == 10.0
    assert outcome.return_15m_pct is not None and outcome.return_15m_pct > 0
    assert outcome.mfe_pct is not None and outcome.mfe_pct > 0


def test_postclose_qualification_cannot_promote_without_execution_economics() -> None:
    report = DiscoveryDailyReport(
        session_date=SESSION,
        generated_at=CLOSE,
        discovered_count=1,
        active_count=1,
        tier_a_count=1,
        tier_b_count=0,
        trigger_counts={"market_anomaly": 1},
        experiment_arm_counts={"market_only": 1},
        attribution_counts={"discovered": 1},
        strategy_signal_counts={},
        durability_labeled_count=1,
    )

    evidence = qualification_from_persisted_evidence(
        _Repository(),
        current_report=report,
        data_reliability_fraction=1.0,
    )

    assert evidence.eligible_for_review is False
    assert evidence.auto_paper_authorized is False
    assert "non_positive_execution_adjusted_expectancy" in evidence.reasons
