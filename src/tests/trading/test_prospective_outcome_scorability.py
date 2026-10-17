from datetime import date
from decimal import Decimal

from app.apps.trading.prospective_prediction_evidence import (
    OutcomeMeasurementsV1,
    assess_outcome_scorability,
)


def _measurements(*, missing_minutes: str) -> OutcomeMeasurementsV1:
    missing = Decimal(missing_minutes)
    return OutcomeMeasurementsV1(
        instrument_id="AAA",
        session_date=date(2026, 9, 18),
        analysis_open_price=Decimal("10"),
        analysis_close_price=Decimal("11"),
        open_to_close_return=Decimal("0.10"),
        normalized_slope=Decimal("0.05"),
        vwap_occupancy=Decimal("0.70"),
        observed_bar_occupancy_above_open=Decimal("0.70"),
        wall_clock_observed_occupancy_above_open=Decimal("0.65"),
        observed_session_coverage=(Decimal("390") - missing) / Decimal("390"),
        directional_efficiency=Decimal("0.50"),
        mae_from_open=Decimal("-0.03"),
        mfe_from_open=Decimal("0.15"),
        closing_range_position=Decimal("0.80"),
        session_high=Decimal("11.20"),
        session_low=Decimal("9.70"),
        observed_bar_count=77,
        halt_or_gap_minutes=missing,
    )


def test_unresolved_whole_session_gap_is_unscorable():
    result = assess_outcome_scorability(_measurements(missing_minutes="5"))
    assert result.status == "UNSCORABLE"
    assert result.unresolved_minutes == Decimal("5")


def test_confirmed_nontrading_interval_can_explain_missing_session_minutes():
    result = assess_outcome_scorability(
        _measurements(missing_minutes="5"),
        confirmed_nontrading_minutes=Decimal("5"),
    )
    assert result.status == "SCORABLE"
    assert result.unresolved_minutes == 0
