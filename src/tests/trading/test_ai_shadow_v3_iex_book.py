"""AI v3 research fills on IEX quotes: IEX's own book is not the NBBO."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.execution import ExecutionObservation, assess_execution_observation
from app.apps.trading.strategy_ai_shadow import simulate_ai_shadow_fill
from app.apps.trading.execution_observation_plane import ExecutionObservationPlane
from app.apps.trading.strategy_ai_shadow_v3_monitor import _causal_selection, _iex_book_spread_only


ACTIONABLE_AT = datetime(2026, 10, 16, 13, 32, tzinfo=timezone.utc)
PLAN_MAX_SPREAD_BPS = Decimal("300")


def _observation(*, provider="alpaca_iex", age_seconds=1, halted=None, bid="2.51", ask="3.35"):
    source_time = ACTIONABLE_AT + timedelta(seconds=4)
    return assess_execution_observation(
        ExecutionObservation(
            instrument_id="equity:NASDAQ:TCRT",
            binding_id="yahoo:historical_polling:equity:NASDAQ:TCRT",
            provider=provider,
            bid=Decimal(bid),
            ask=Decimal(ask),
            last=Decimal("2.91"),
            source_time=source_time,
            received_at=source_time + timedelta(seconds=age_seconds),
            session="regular",
            freshness_mode="live",
            halted=halted,
        )
    )


def _simulate(observation: ExecutionObservation):
    execution = observation.model_dump(mode="python")
    execution.update(bid=None, ask=None, spread_bps=None)
    return simulate_ai_shadow_fill(
        execution,
        side="buy",
        instrument_id=observation.instrument_id,
        binding_id=observation.binding_id,
        decision_at=ACTIONABLE_AT,
        requested_units=Decimal("1"),
        reference_price=Decimal("2.90"),
        max_capture_lag_seconds=Decimal("10"),
        allow_degraded_price_only=True,
    )


def test_a_wide_iex_book_becomes_a_hypothetical_fill_at_last_with_the_assumed_spread():
    observation = _observation()
    assert observation.rejection_reasons == ("SPREAD_TOO_WIDE",)

    assert _iex_book_spread_only(observation, PLAN_MAX_SPREAD_BPS)
    simulation = _simulate(observation)

    assert simulation.should_fill
    assert simulation.hypothetical
    assert simulation.fill_reason == "degraded_price_only_hypothetical"
    assert simulation.fill_price == Decimal("2.91") * (
        1 + simulation.assumed_friction_bps / Decimal("10000")
    )


def test_a_late_iex_quote_is_still_rejected_by_the_capture_lag_gate():
    observation = _observation()
    late = observation.model_copy(
        update={
            "source_time": ACTIONABLE_AT + timedelta(seconds=15),
            "received_at": ACTIONABLE_AT + timedelta(seconds=15),
        }
    )

    assert _iex_book_spread_only(late, PLAN_MAX_SPREAD_BPS)
    assert not _simulate(late).should_fill


def test_only_the_iex_spread_is_waived():
    # A consolidated-quote provider's spread is the market's spread.
    assert not _iex_book_spread_only(_observation(provider="ibkr"), PLAN_MAX_SPREAD_BPS)
    # Stale or halted IEX data keeps failing closed.
    assert not _iex_book_spread_only(_observation(age_seconds=30), PLAN_MAX_SPREAD_BPS)
    assert not _iex_book_spread_only(_observation(halted=True), PLAN_MAX_SPREAD_BPS)
    # A tight IEX book takes the normal book-priced path.
    assert not _iex_book_spread_only(_observation(bid="2.90", ask="2.91"), PLAN_MAX_SPREAD_BPS)


def _plane_quote(plane, *, provider, purpose, seconds_after, bid, ask):
    source_time = ACTIONABLE_AT + timedelta(seconds=seconds_after)
    observation = assess_execution_observation(
        ExecutionObservation(
            instrument_id="equity:NASDAQ:TCRT",
            binding_id=f"{provider}:TCRT",
            provider=provider,
            binding_purpose=purpose,
            bid=Decimal(bid),
            ask=Decimal(ask),
            last=Decimal("2.91"),
            source_time=source_time,
            received_at=source_time + timedelta(milliseconds=200),
            session="regular",
            freshness_mode="live",
            market_data_type="LIVE",
            live_entitled=True,
        ),
        binding_purpose=purpose,
    )
    plane.record(observation, recorded_at=source_time + timedelta(milliseconds=300))


def _select(plane, *, ibkr_live):
    return _causal_selection(
        plane,
        "equity:NASDAQ:TCRT",
        decision_completed_at=ACTIONABLE_AT,
        actionable_at=ACTIONABLE_AT,
        market_snapshot_as_of=ACTIONABLE_AT,
        binding_id="alpaca_iex:TCRT",
        ibkr_live=ibkr_live,
    )


def test_authorized_ibkr_live_quotes_replace_the_stale_wide_iex_book():
    plane = ExecutionObservationPlane()
    # IEX's own book only changes 12.6 s after the decision; IBKR's NBBO updates at once.
    _plane_quote(plane, provider="alpaca_iex", purpose="EXECUTION", seconds_after=12.6, bid="2.51", ask="3.35")
    _plane_quote(plane, provider="ibkr", purpose="LIVE_DATA", seconds_after=0.4, bid="2.90", ask="2.92")
    _plane_quote(plane, provider="ibkr", purpose="LIVE_DATA", seconds_after=1.4, bid="2.95", ask="2.97")

    live = _select(plane, ibkr_live=True)
    assert live.provider == "ibkr"
    assert live.observation.bid == Decimal("2.90")  # the first post-decision quote, never a later one
    assert live.observation.execution_eligible
    assert live.capture_lag_seconds < 1

    # With the IBKR live-data gate off, the IEX source is unchanged.
    assert _select(plane, ibkr_live=False).provider == "alpaca_iex"


def test_without_a_post_decision_ibkr_quote_the_iex_quote_is_used():
    plane = ExecutionObservationPlane()
    _plane_quote(plane, provider="ibkr", purpose="LIVE_DATA", seconds_after=-5, bid="2.90", ask="2.92")
    _plane_quote(plane, provider="alpaca_iex", purpose="EXECUTION", seconds_after=3, bid="2.89", ask="2.93")

    assert _select(plane, ibkr_live=True).provider == "alpaca_iex"
