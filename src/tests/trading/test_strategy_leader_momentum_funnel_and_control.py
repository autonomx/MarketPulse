from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading import strategy_leader_momentum_continuation as leader
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_discovery_replay import (
    DiscoveryReplayObservation,
    replay_dynamic_discovery,
)
from app.apps.trading.strategy_dynamic_discovery import MarketAnomalyFeatures
from app.apps.trading.strategy_leader_momentum_control_replay import (
    build_control_cohort_observations,
    build_leader_momentum_control_plan,
)
from app.apps.trading.strategy_leader_momentum_diagnostics import (
    LeaderMomentumDiagnosticTrace,
    SetupGateDiagnostic,
    diagnose_leader_momentum_continuation,
)
from app.apps.trading.strategy_leader_momentum_funnel_diagnostics import (
    SetupFunnelAttempt,
    diagnose_leader_momentum_funnel,
    summarize_setup_funnel,
)


def _bar(start: datetime, price: Decimal, index: int) -> MarketBar:
    close = price * Decimal("1.003")
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval="1m",
        start_time=start + timedelta(minutes=index),
        end_time=start + timedelta(minutes=index + 1),
        open=price,
        high=close * Decimal("1.004"),
        low=price * Decimal("0.997"),
        close=close,
        volume=Decimal(100000 + index * 5000),
        is_final=True,
        session="regular",
        provider="test",
    )


def _rising_bars(count: int = 100) -> list[MarketBar]:
    start = datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)
    price = Decimal("2.00")
    bars: list[MarketBar] = []
    for index in range(count):
        bar = _bar(start, price, index)
        bars.append(bar)
        price = bar.close
    return bars


def _gate(name: str, passed: bool) -> SetupGateDiagnostic:
    return SetupGateDiagnostic(gate=name, passed=passed)


def test_ordered_funnel_counts_only_reached_gates() -> None:
    now = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    attempts = (
        SetupFunnelAttempt(
            observed_at=now,
            stage="controlled_pullback",
            mode="controlled_pullback",
            window_length=2,
            steps=(
                _gate("continuity", True),
                _gate("impulse_pct", False),
            ),
            first_failure_gate="impulse_pct",
        ),
        SetupFunnelAttempt(
            observed_at=now + timedelta(minutes=3),
            stage="controlled_pullback",
            mode="controlled_pullback",
            window_length=3,
            steps=(
                _gate("continuity", True),
                _gate("impulse_pct", True),
                _gate("pullback_retrace_max", False),
            ),
            first_failure_gate="pullback_retrace_max",
        ),
    )

    rows = {
        item.gate: item
        for item in summarize_setup_funnel(attempts)
        if item.stage == "controlled_pullback"
    }

    assert rows["continuity"].reached == 2
    assert rows["continuity"].conditional_pass_rate == Decimal("1")
    assert rows["impulse_pct"].reached == 2
    assert rows["impulse_pct"].passed == 1
    assert rows["impulse_pct"].failed == 1
    assert rows["impulse_pct"].first_failure_count == 1
    assert rows["impulse_pct"].conditional_pass_rate == Decimal("0.5")
    assert rows["pullback_retrace_max"].reached == 1
    assert rows["pullback_retrace_max"].failed == 1
    assert rows["pullback_retrace_max"].first_failure_count == 1


def test_funnel_diagnostics_do_not_change_frozen_strategy_snapshot() -> None:
    bars = _rising_bars()
    context = leader.LeaderMomentumContext(
        tod_rvol=Decimal("8"),
        relative_strength_pct=Decimal("20"),
        spread_bps=Decimal("80"),
        dollar_volume=Decimal("5000000"),
        volume_acceleration=Decimal("2"),
        hod_frequency_15m=3,
    )
    expected = diagnose_leader_momentum_continuation(bars, context=context)

    result = diagnose_leader_momentum_funnel(bars, context=context)

    assert result.base_trace.strategy_snapshot == expected.strategy_snapshot
    assert result.execution_authority is False
    assert result.base_trace.execution_authority is False
    assert result.gate_funnel
    assert any(item.stage == "common" for item in result.gate_funnel)


def _market(at: datetime, *, strong: bool) -> MarketAnomalyFeatures:
    return MarketAnomalyFeatures(
        observed_at=at,
        gap_pct=50 if strong else 1,
        tod_rvol=100 if strong else 1,
        volume_to_float=2 if strong else 0.01,
        dollar_volume=25_000_000 if strong else 10_000,
        volume_acceleration_5m=8 if strong else 0.1,
        volume_acceleration_15m=6 if strong else 0.1,
        price_acceleration_5m_pct=12 if strong else 0.1,
        range_expansion=4 if strong else 0.1,
        relative_strength_pct=20 if strong else 0.1,
        hod_frequency_15m=4 if strong else 0,
        spread_bps=50 if strong else 500,
    )


def _observation(symbol: str, at: datetime, *, strong: bool) -> DiscoveryReplayObservation:
    return DiscoveryReplayObservation(
        instrument_id=symbol,
        observed_at=at,
        source="historical_scanner",
        market=_market(at, strong=strong),
    )


def test_control_plan_freezes_causal_universe_before_winner_exclusion() -> None:
    session = date(2026, 9, 11)
    at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    observations = (
        _observation("equity:NASDAQ:WIN", at, strong=True),
        _observation("equity:NASDAQ:CTL1", at, strong=True),
        _observation("equity:NASDAQ:CTL2", at, strong=False),
    )
    replay = replay_dynamic_discovery(
        session_date=session,
        observations=observations,
    )

    observable = build_leader_momentum_control_plan(
        session_date=session,
        observations=observations,
        winner_instrument_ids=("equity:NASDAQ:WIN",),
        discovery_result=replay,
        scope="observable_scanner",
    )
    discovered = build_leader_momentum_control_plan(
        session_date=session,
        observations=observations,
        winner_instrument_ids=("equity:NASDAQ:WIN",),
        discovery_result=replay,
        scope="discovered_candidates",
    )

    assert observable.observable_symbol_count == 3
    assert observable.winner_exclusion_count == 1
    assert {item.instrument_id for item in observable.controls} == {
        "equity:NASDAQ:CTL1",
        "equity:NASDAQ:CTL2",
    }
    assert "equity:NASDAQ:WIN" not in {
        item.instrument_id for item in observable.controls
    }
    assert {item.instrument_id for item in discovered.controls} == {
        "equity:NASDAQ:CTL1"
    }
    assert observable.execution_authority is False
    assert discovered.execution_authority is False


def test_control_trace_attachment_requires_complete_plan_by_default() -> None:
    session = date(2026, 9, 11)
    at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    observations = (_observation("equity:NASDAQ:CTL", at, strong=True),)
    plan = build_leader_momentum_control_plan(
        session_date=session,
        observations=observations,
        winner_instrument_ids=(),
        scope="observable_scanner",
    )
    snapshot = leader.LeaderMomentumSnapshot(
        state="waiting_leader",
        reason_code="TEST",
    )
    trace = LeaderMomentumDiagnosticTrace(strategy_snapshot=snapshot)

    attached = build_control_cohort_observations(
        plan,
        {"equity:NASDAQ:CTL": trace},
    )

    assert len(attached) == 1
    assert attached[0].cohort == "control"
    assert attached[0].instrument_id == "equity:NASDAQ:CTL"
    assert attached[0].trace is trace
