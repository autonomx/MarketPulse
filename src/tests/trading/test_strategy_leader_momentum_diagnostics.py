from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading import strategy_leader_momentum_continuation as leader
from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_leader_momentum_diagnostics import (
    LeaderMomentumCohortObservation,
    LeaderMomentumDiagnosticTrace,
    build_leader_momentum_cohort_report,
    diagnose_leader_momentum_continuation,
)


def _bar(
    start: datetime,
    *,
    interval: str = "1m",
    open_: Decimal = Decimal("2.00"),
    high: Decimal = Decimal("2.03"),
    low: Decimal = Decimal("1.99"),
    close: Decimal = Decimal("2.02"),
    volume: Decimal = Decimal("100000"),
) -> MarketBar:
    minutes = 1 if interval == "1m" else 3
    return MarketBar(
        instrument_id="equity:NASDAQ:TEST",
        interval=interval,
        start_time=start,
        end_time=start + timedelta(minutes=minutes),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_final=True,
        session="regular",
        provider="test",
    )


def _rising_one_minute_bars(count: int = 90) -> list[MarketBar]:
    start = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    bars: list[MarketBar] = []
    price = Decimal("2.00")
    for index in range(count):
        next_price = price * Decimal("1.0025")
        bars.append(
            _bar(
                start + timedelta(minutes=index),
                open_=price,
                high=next_price * Decimal("1.004"),
                low=price * Decimal("0.997"),
                close=next_price,
                volume=Decimal(100000 + index * 2500),
            )
        )
        price = next_price
    return bars


def _three_minute_fixture() -> list[MarketBar]:
    start = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    rows: list[tuple[Decimal, Decimal, Decimal, Decimal, Decimal]] = []
    price = Decimal("2.00")
    for _ in range(20):
        close = price + Decimal("0.012")
        rows.append(
            (
                price,
                close + Decimal("0.025"),
                price - Decimal("0.020"),
                close,
                Decimal("100000"),
            )
        )
        price = close
    rows.extend(
        [
            tuple(map(Decimal, ("2.24", "2.32", "2.22", "2.30", "320000"))),
            tuple(map(Decimal, ("2.30", "2.40", "2.28", "2.38", "360000"))),
            tuple(map(Decimal, ("2.38", "2.48", "2.36", "2.46", "400000"))),
            tuple(map(Decimal, ("2.46", "2.54", "2.44", "2.52", "440000"))),
            tuple(map(Decimal, ("2.52", "2.53", "2.43", "2.47", "145000"))),
            tuple(map(Decimal, ("2.47", "2.49", "2.41", "2.45", "140000"))),
            tuple(map(Decimal, ("2.45", "2.50", "2.43", "2.48", "150000"))),
            tuple(map(Decimal, ("2.48", "2.61", "2.47", "2.59", "520000"))),
            tuple(map(Decimal, ("2.59", "2.66", "2.56", "2.64", "280000"))),
            tuple(map(Decimal, ("2.64", "2.70", "2.60", "2.67", "260000"))),
        ]
    )
    return [
        _bar(
            start + timedelta(minutes=3 * index),
            interval="3m",
            open_=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
        for index, (open_, high, low, close, volume) in enumerate(rows)
    ]


def _explode_to_one_minute(bars: list[MarketBar]) -> list[MarketBar]:
    output: list[MarketBar] = []
    for bar in bars:
        piece = bar.volume / Decimal("3")
        volumes = (piece, piece, bar.volume - piece - piece)
        minute_specs = (
            (bar.open, bar.open, bar.low, bar.open),
            (bar.open, bar.high, min(bar.open, bar.close), bar.close),
            (bar.close, bar.close, bar.close, bar.close),
        )
        for offset, ((open_, high, low, close), volume) in enumerate(
            zip(minute_specs, volumes, strict=True)
        ):
            output.append(
                _bar(
                    bar.start_time + timedelta(minutes=offset),
                    open_=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
    return output


def _strong_context() -> leader.LeaderMomentumContext:
    return leader.LeaderMomentumContext(
        tod_rvol=Decimal("8"),
        relative_strength_pct=Decimal("20"),
        spread_bps=Decimal("80"),
        dollar_volume=Decimal("5000000"),
        volume_acceleration=Decimal("2"),
        hod_frequency_15m=3,
    )


def test_diagnostics_preserve_frozen_strategy_snapshot() -> None:
    bars = _explode_to_one_minute(_three_minute_fixture())
    expected = leader.evaluate_leader_momentum_continuation(
        bars,
        context=_strong_context(),
    )

    trace = diagnose_leader_momentum_continuation(
        bars,
        context=_strong_context(),
    )

    assert trace.strategy_snapshot == expected
    assert trace.policy_version == "leader-momentum-continuation-v1.2"
    assert trace.execution_authority is False


def test_score_breakdown_exposes_spread_and_liquidity_penalties() -> None:
    bars = _rising_one_minute_bars()
    context = leader.LeaderMomentumContext(
        tod_rvol=Decimal("8"),
        relative_strength_pct=Decimal("15"),
        spread_bps=Decimal("300"),
        dollar_volume=Decimal("1000000"),
        volume_acceleration=Decimal("2"),
        hod_frequency_15m=3,
    )

    trace = diagnose_leader_momentum_continuation(bars, context=context)

    assert trace.max_score is not None
    assert trace.max_score.spread_penalty_points == Decimal("-10")
    assert trace.max_score.dollar_volume_penalty_points == Decimal("-5")
    assert (
        trace.max_score.total_score
        == trace.max_score.market_leadership_points
        + trace.max_score.context_execution_points
    )


def test_research_one_minute_view_becomes_ready_before_decision_loop() -> None:
    trace = diagnose_leader_momentum_continuation(_rising_one_minute_bars())

    assert trace.research_1m_first_score is not None
    assert trace.first_score is not None
    assert trace.research_1m_first_score.observed_at < trace.first_score.observed_at


def test_trace_records_leadership_transitions_and_setup_gate_counts() -> None:
    bars = _explode_to_one_minute(_three_minute_fixture())

    trace = diagnose_leader_momentum_continuation(
        bars,
        context=_strong_context(),
    )

    assert trace.first_leader_confirmed_at is not None
    assert trace.last_leader_confirmed_at is not None
    assert trace.bars_in_confirmed_state > 0
    assert any(item.kind == "confirmed" for item in trace.transitions)
    assert trace.best_mode_a is not None
    assert trace.best_mode_b is not None
    assert trace.gate_counts
    assert (trace.first_setup_candidate is None) == (
        trace.first_setup_candidate_at is None
    )
    assert any(item.gate == "impulse_pct" for item in trace.gate_counts)
    assert any(item.gate == "breakout_volume_ratio" for item in trace.gate_counts)
    assert {item.mode for item in trace.gate_counts} == {
        "controlled_pullback",
        "momentum_compression",
    }


def test_best_candidate_contains_threshold_distances_and_risk() -> None:
    bars = _explode_to_one_minute(_three_minute_fixture())

    trace = diagnose_leader_momentum_continuation(
        bars,
        context=_strong_context(),
    )

    assert trace.best_mode_a is not None
    gates = {item.gate: item for item in trace.best_mode_a.gates}
    assert "pullback_retrace_min" in gates
    assert "pullback_retrace_max" in gates
    assert "pullback_volume_ratio" in gates
    assert "proposed_risk_pct" in gates
    assert trace.best_mode_a.proposed_risk_pct is not None


def _trace(
    *,
    confirmed: bool,
    traded: bool,
) -> LeaderMomentumDiagnosticTrace:
    snapshot = leader.LeaderMomentumSnapshot(
        state="completed" if traded else "waiting_leader",
        reason_code="TEST",
        trades=(
            leader.LeaderMomentumTrade(
                mode="controlled_pullback",
                signal_time=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
                entry_time=datetime(2026, 9, 10, 14, 3, tzinfo=timezone.utc),
                entry_price=Decimal("2"),
                initial_stop_price=Decimal("1.9"),
                exit_time=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
                exit_price=Decimal("2.2"),
                exit_reason_code="TEST",
                return_pct=Decimal("10"),
                mfe_pct=Decimal("12"),
                mae_pct=Decimal("-2"),
            ),
        )
        if traded
        else (),
    )
    confirmed_at = (
        datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
        if confirmed
        else None
    )
    return LeaderMomentumDiagnosticTrace(
        session_date="2026-09-10",
        strategy_snapshot=snapshot,
        first_leader_confirmed_at=confirmed_at,
        last_leader_confirmed_at=confirmed_at,
    )


def test_winner_control_report_separates_recall_false_positives_and_precision() -> None:
    observations = [
        LeaderMomentumCohortObservation(
            cohort="winner",
            instrument_id="equity:NASDAQ:WIN1",
            trace=_trace(confirmed=True, traded=True),
        ),
        LeaderMomentumCohortObservation(
            cohort="winner",
            instrument_id="equity:NASDAQ:WIN2",
            trace=_trace(confirmed=False, traded=False),
        ),
        LeaderMomentumCohortObservation(
            cohort="control",
            instrument_id="equity:NASDAQ:CTL1",
            trace=_trace(confirmed=True, traded=False),
        ),
        LeaderMomentumCohortObservation(
            cohort="control",
            instrument_id="equity:NASDAQ:CTL2",
            trace=_trace(confirmed=False, traded=False),
        ),
    ]

    report = build_leader_momentum_cohort_report(observations)

    assert report.leader_recall == Decimal("0.5")
    assert report.leader_false_positive_rate == Decimal("0.5")
    assert report.leader_precision == Decimal("0.5")
    assert report.trade_precision == Decimal("1")
