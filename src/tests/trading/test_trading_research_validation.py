from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.apps.trading.research.outcome_dataset import attribution_summary
from app.apps.trading.research.validation import build_validation_report


def _outcomes(count: int = 120):
    start = date(2026, 1, 2)
    values = []
    for index in range(count):
        good = index % 2 == 0
        instrument = f"equity:NASDAQ:T{index % 4}"
        values.append({
            "outcome_id": f"o-{index:04d}",
            "session_date": start + timedelta(days=index),
            "instrument_id": instrument,
            "features": {
                "primary_catalyst_confirmed": good,
                "catalyst_same_day": good,
                "immediate_supply_risk": not good,
                "unresolved_supply": not good,
                "source_authority_sufficient": good,
                "_research_context": {
                    "novelty_shadow": {"novelty": "new" if good else "recycled"},
                    "supply_metrics": {
                        "in_the_money_warrant_pct_float": "0" if good else "125",
                    },
                },
            },
            "research_status": "complete" if good else "partial",
            "r_result": Decimal("1.25") if good else Decimal("-0.50"),
            "two_r_before_minus_one_r": good,
            "market_fidelity": "captured_point_in_time",
            "research_fidelity": "captured_exact",
            "mfe_r": Decimal("2.2") if good else Decimal("0.4"),
            "mae_r": Decimal("-0.2") if good else Decimal("-0.9"),
            "data_quality_flags": (),
        })
    return values


def test_htr14_reports_holdout_probability_uncertainty_and_never_auto_promotes():
    report = build_validation_report(
        _outcomes(),
        minimum_sample=100,
        minimum_exact_sample=50,
    )
    catalyst = next(item for item in report.feature_results if item.feature == "primary_catalyst_confirmed")
    assert catalyst.in_sample_effect_r is not None and catalyst.in_sample_effect_r > Decimal("1")
    assert catalyst.out_of_sample_effect_r is not None and catalyst.out_of_sample_effect_r > Decimal("1")
    assert catalyst.win_probability_delta == Decimal("1")
    assert catalyst.confidence_interval_low is not None and catalyst.confidence_interval_low > 0
    assert catalyst.recommendation == "score_only"
    assert report.exact_sample_size == 120
    assert report.promotion_allowed is False
    assert any("cannot grant execution authority" in note for note in report.notes)


def test_htr13_attribution_includes_novelty_supply_completeness_and_fidelity():
    summary = attribution_summary(_outcomes(20))
    assert summary["baseline"]["n"] == 20
    assert summary["exact_causal_subset"]["n"] == 20
    assert summary["novelty_shadow"]["new"]["n"] == 10
    assert summary["novelty_shadow"]["recycled"]["n"] == 10
    assert summary["research_status"]["complete"]["n"] == 10
    assert summary["itm_warrant_pct_float_buckets"][">=100%"]["n"] == 10
    assert summary["feature_comparisons"]["primary_catalyst_confirmed"]["true"]["expectancy_r"] == Decimal("1.25")
    assert "downstream-only" in summary["anti_leakage"]
