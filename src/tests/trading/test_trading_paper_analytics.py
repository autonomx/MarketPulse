from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.paper_analytics import (
    PaperAnalyticsTrade,
    lifecycle_funnel,
    performance_summary,
    r_distribution,
    rolling_expectancy,
)
from app.apps.trading.strategy_repository import StrategyEvent


def _trade(index: int, result: str, *, mae: str = "-0.4", mfe: str = "1.2") -> PaperAnalyticsTrade:
    started = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc) + timedelta(days=index)
    return PaperAnalyticsTrade(
        trade_id=f"trade-{index}",
        source="shadow_replay",
        strategy_id="gap-v2",
        strategy_version="2.0.0",
        profile_fingerprint="frozen",
        instrument_id=f"equity:NASDAQ:T{index}",
        session_date=date(2026, 8, 24) + timedelta(days=index),
        entry_time=started,
        exit_time=started + timedelta(minutes=20),
        r_result=Decimal(result),
        mae_r=Decimal(mae),
        mfe_r=Decimal(mfe),
    )


def _event(index: int, *, event_type: str, state: str, reason: str | None = None, payload=None) -> StrategyEvent:
    observed = datetime(2026, 8, 24, 14, index, tzinfo=timezone.utc)
    return StrategyEvent(
        strategy_id="gap-v2",
        event_id=f"event-{index}",
        run_id="run-1",
        instrument_id="equity:NASDAQ:XYZ",
        event_type=event_type,
        state=state,
        reason_code=reason,
        observed_at=observed,
        idempotency_key=f"idem-{index}",
        payload={"session_date": "2026-08-24", "universe_id": "u-1", **(payload or {})},
    )


def test_performance_summary_uses_r_not_account_size() -> None:
    trades = [_trade(0, "1.5"), _trade(1, "-1"), _trade(2, "0.5")]
    summary = performance_summary(trades)
    assert summary.trade_count == 3
    assert summary.wins == 2
    assert summary.losses == 1
    assert summary.expectancy_r == Decimal("1") / Decimal("3")
    assert summary.total_r == Decimal("1.0")
    assert summary.max_drawdown_r == Decimal("1")
    assert summary.profit_factor == Decimal("2")


def test_rolling_expectancy_carries_statistical_uncertainty() -> None:
    trades = [_trade(0, "1"), _trade(1, "-1"), _trade(2, "2"), _trade(3, "1")]
    points = rolling_expectancy(trades, 3)
    assert len(points) == 4
    assert points[-1].sample_size == 3
    assert points[-1].expectancy_r == Decimal("2") / Decimal("3")
    assert points[-1].one_sided_90_lcb_r is not None


def test_r_distribution_keeps_stop_and_target_shape_visible() -> None:
    trades = [_trade(0, "-1"), _trade(1, "-0.2"), _trade(2, "0.25"), _trade(3, "1.5"), _trade(4, "2.3")]
    buckets = {bucket.label: bucket.count for bucket in r_distribution(trades)}
    assert buckets["-1R to -0.5R"] == 1
    assert buckets["-0.5R to 0R"] == 1
    assert buckets["0R to +0.5R"] == 1
    assert buckets["+1.5R to +2R"] == 1
    assert buckets[">= +2R"] == 1


def test_lifecycle_funnel_counts_symbol_session_once_not_raw_events() -> None:
    events = [
        _event(0, event_type="state", state="pullback_forming", payload={"features": {"quality_score": 4}}),
        _event(1, event_type="state", state="pullback_forming", payload={"features": {"quality_score": 5}}),
        _event(2, event_type="state", state="entry_ready", payload={"features": {"quality_score": 8}}),
        _event(3, event_type="shadow_execution", state="entry_ready", payload={"execution": {"execution_eligible": True}}),
        _event(4, event_type="entry_order_submitted", state="entry_ready"),
        _event(5, event_type="protection", state="active"),
    ]
    funnel = {stage.stage: stage.count for stage in lifecycle_funnel(events)}
    assert funnel["DISCOVERED"] == 1
    assert funnel["STRUCTURE FORMED"] == 1
    assert funnel["ENTRY READY"] == 1
    assert funnel["EXECUTION ELIGIBLE"] == 1
    assert funnel["ORDER SUBMITTED"] == 1
    assert funnel["FILLED"] == 1


def test_lifecycle_funnel_rejected_feature_payload_does_not_fake_structure() -> None:
    event = _event(0, event_type="state", state="rejected", reason="GAP_BELOW_MINIMUM", payload={"features": {"quality_score": 1}, "transitions": ["discovered"]})
    funnel = {stage.stage: stage.count for stage in lifecycle_funnel([event])}
    assert funnel["DISCOVERED"] == 1
    assert funnel["BASIC MARKET FILTER PASS"] == 0
    assert funnel["STRUCTURE FORMED"] == 0
