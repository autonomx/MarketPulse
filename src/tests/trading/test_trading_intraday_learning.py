from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.models import MarketBar
from app.apps.trading.strategies.models import GapPullbackFeatures, GapPullbackResult
from app.apps.trading.strategy_intraday_learning import build_intraday_learning_snapshot


INSTRUMENT = "equity:NASDAQ:TEST"
OPEN = datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc)


def bar(i, open_, high, low, close, volume="100000"):
    start = OPEN + timedelta(minutes=i)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
        provider="fixture",
        session="regular",
    )


def candidate(**overrides):
    payload = dict(
        instrument_id=INSTRUMENT,
        previous_close=Decimal("8"),
        premarket_price=Decimal("10"),
        gap_pct=Decimal("25"),
        premarket_volume=Decimal("500000"),
        premarket_dollar_volume=Decimal("5000000"),
        tod_rvol=Decimal("5"),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal("40"),
        catalyst_evidence_ids=("ev-1",),
        dilution_flags=(),
        discovery_rank=1,
    )
    payload.update(overrides)
    return GapperCandidate(**payload)


def result(transitions=("discovered", "qualified_gap")):
    return GapPullbackResult(
        instrument_id=INSTRUMENT,
        state="qualified_gap",
        reason_code="WAITING",
        features=GapPullbackFeatures(gap_pct=Decimal("25")),
        transitions=transitions,
        evaluated_bar_count=1,
    )


def test_intraday_learning_detects_squeeze_without_mistaking_it_for_execution_authority():
    bars = [
        bar(0, "10", "10.5", "9.8", "10.4", "500000"),
        bar(1, "10.4", "11.3", "10.3", "11.2", "600000"),
        bar(2, "11.2", "12.2", "11.0", "12.0", "700000"),
        bar(3, "12.0", "13.4", "11.9", "13.2", "800000"),
        bar(4, "13.2", "14.5", "13.0", "14.2", "900000"),
    ]
    snapshot = build_intraday_learning_snapshot(candidate(float_shares=Decimal("1000000")), result(), bars)

    assert snapshot.squeeze_probability_score >= 8
    assert snapshot.pattern == "squeeze_momentum"
    assert snapshot.opportunity_score >= 8
    assert snapshot.raw_movement_score == snapshot.opportunity_score
    assert snapshot.execution_adjusted_opportunity_score <= snapshot.raw_movement_score
    assert snapshot.execution_authority is False


def test_intraday_learning_distinguishes_distribution_from_raw_volume():
    bars = [
        bar(0, "10", "10.5", "9.7", "9.9", "900000"),
        bar(1, "9.9", "10.0", "9.0", "9.2", "900000"),
        bar(2, "9.2", "9.4", "8.5", "8.7", "900000"),
        bar(3, "8.7", "8.9", "8.0", "8.1", "900000"),
    ]
    snapshot = build_intraday_learning_snapshot(candidate(float_shares=Decimal("1000000")), result(), bars)

    assert snapshot.turnover_to_float is not None
    assert snapshot.turnover_to_float > Decimal("3")
    assert snapshot.pattern == "distribution_fade"
    assert snapshot.gap_retention_score <= 3
    assert snapshot.raw_movement_score >= snapshot.execution_adjusted_opportunity_score


def test_failed_selloff_score_uses_recovery_and_higher_low_evidence():
    bars = [
        bar(0, "10", "10.5", "9.8", "10.2"),
        bar(1, "10.2", "10.3", "9.0", "9.2"),
        bar(2, "9.2", "9.8", "9.1", "9.7"),
        bar(3, "9.7", "10.0", "9.3", "9.9"),
        bar(4, "9.9", "10.4", "9.5", "10.3"),
        bar(5, "10.3", "10.8", "9.8", "10.7"),
    ]
    snapshot = build_intraday_learning_snapshot(
        candidate(),
        result(("discovered", "qualified_gap", "bounce_high_confirmed", "higher_low_confirmed")),
        bars,
    )
    assert snapshot.failed_selloff_probability_score >= 8
    assert snapshot.pattern in {"failed_selloff_watch", "opening_fade_recovery"}
