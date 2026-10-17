from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.prospective_prediction_v4 import (
    CatalystDecomposition,
    ExtensionExhaustionRisk,
    MechanismRiskScores,
    PremarketFeature,
    PremarketMarketStateSnapshot,
)
from app.apps.trading.prospective_prediction_v42 import (
    V42ReturnObservation,
    build_v42_feature_bundle,
    evaluate_v42_return_metrics,
    freeze_v42_forecast,
    session_eligible_for_v42_forward_validation,
)


CUTOFF = datetime(2026, 9, 23, 13, 25, tzinfo=timezone.utc)


def _feature(name: str, value: str) -> PremarketFeature:
    observed = datetime(2026, 9, 23, 13, 24, tzinfo=timezone.utc)
    return PremarketFeature(
        name=name,
        value=Decimal(value),
        source="yahoo",
        quality="GOOD",
        available=True,
        available_to_live_forecaster=True,
        event_at=observed,
        observed_at=observed,
        ingested_at=observed,
        provenance_fingerprint=f"fp:{name}",
    )


def _state(*, omit: str | None = None) -> PremarketMarketStateSnapshot:
    values = {
        "gap_from_prior_close_pct": "50",
        "distance_from_premarket_vwap_pct": "3",
        "position_in_premarket_range": "0.75",
        "float_turnover": "0.8",
        "late_premarket_acceleration": "0.4",
        "late_premarket_volume_share": "0.22",
    }
    features = tuple(
        _feature(name, value)
        for name, value in values.items()
        if name != omit
    )
    return PremarketMarketStateSnapshot(
        snapshot_id="state-1",
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        instrument_id="equity:US:AAA",
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF,
        features=features,
    )


def _candidate(
    *,
    spread_bps: str = "25",
    dollar_volume: str = "20000000",
    dilution_flags: tuple[str, ...] = (),
) -> GapperCandidate:
    return GapperCandidate(
        instrument_id="equity:US:AAA",
        observed_at=datetime(2026, 9, 23, 13, 24, tzinfo=timezone.utc),
        previous_close=Decimal("10"),
        premarket_price=Decimal("15"),
        gap_pct=Decimal("50"),
        premarket_volume=Decimal("1000000"),
        premarket_dollar_volume=Decimal(dollar_volume),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal(spread_bps),
        dilution_flags=dilution_flags,
    )


def _catalyst(*, finality: str = "0.85") -> CatalystDecomposition:
    return CatalystDecomposition(
        strength=Decimal("0.80"),
        finality=Decimal(finality),
        freshness=Decimal("0.90"),
        surprise=Decimal("0.70"),
        economic_materiality=Decimal("0.80"),
    )


V4_MECHANISMS = MechanismRiskScores(
    continuation_score=Decimal("0.75"),
    opening_exhaustion_score=Decimal("0.30"),
    squeeze_tail_score=Decimal("0.25"),
    fade_risk_score=Decimal("0.20"),
)

EXTENSION = ExtensionExhaustionRisk(
    score=Decimal("0.70"),
    used_components=("gap_from_prior_close_pct",),
    missing_components=(),
)


def test_v42_excludes_sep22_and_starts_forward_on_sep23() -> None:
    assert not session_eligible_for_v42_forward_validation(date(2026, 9, 22))
    assert session_eligible_for_v42_forward_validation(date(2026, 9, 23))


def test_v42_requires_complete_demand_evidence() -> None:
    with pytest.raises(ValueError, match="v42_missing_complete_demand_evidence"):
        build_v42_feature_bundle(
            candidate=_candidate(),
            market_state=_state(omit="late_premarket_acceleration"),
            catalyst=_catalyst(),
            v4_mechanisms=V4_MECHANISMS,
            extension_risk=EXTENSION,
        )


def test_v42_interaction_risk_reduces_probability_and_worsens_tail() -> None:
    clean = freeze_v42_forecast(
        candidate=_candidate(),
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        market_state=_state(),
        catalyst=_catalyst(finality="0.90"),
        v4_mechanisms=V4_MECHANISMS,
        extension_risk=EXTENSION,
        regime_tags=("FUNDAMENTAL_REPRICE",),
        frozen_at=CUTOFF,
    )
    risky = freeze_v42_forecast(
        candidate=_candidate(
            spread_bps="300",
            dollar_volume="1000000",
            dilution_flags=("ATM", "VARIABLE_PRICE_CONVERTIBLE"),
        ),
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        market_state=_state(),
        catalyst=_catalyst(finality="0.20"),
        v4_mechanisms=V4_MECHANISMS.model_copy(
            update={"fade_risk_score": Decimal("0.80")}
        ),
        extension_risk=EXTENSION,
        regime_tags=("SUPPLY_OVERHANG", "LOW_LIQUIDITY", "HIGH_EXTENSION"),
        frozen_at=CUTOFF,
    )

    assert risky.p_close_above_open < clean.p_close_above_open
    assert (
        risky.interactions.extension_x_supply
        > clean.interactions.extension_x_supply
    )
    assert (
        risky.return_distribution.p_return_lt_minus_5pct
        > clean.return_distribution.p_return_lt_minus_5pct
    )
    assert risky.return_distribution.q10 < clean.return_distribution.q10


def test_v42_return_metrics_score_distribution_not_only_direction() -> None:
    forecast = freeze_v42_forecast(
        candidate=_candidate(),
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        market_state=_state(),
        catalyst=_catalyst(),
        v4_mechanisms=V4_MECHANISMS,
        extension_risk=EXTENSION,
        regime_tags=("FUNDAMENTAL_REPRICE",),
        frozen_at=CUTOFF,
    )
    metrics = evaluate_v42_return_metrics(
        [
            V42ReturnObservation(
                instrument_id=forecast.instrument_id,
                forecast=forecast,
                realized_return=Decimal("0.08"),
            ),
            V42ReturnObservation(
                instrument_id=forecast.instrument_id,
                forecast=forecast,
                realized_return=Decimal("-0.12"),
            ),
        ]
    )
    assert metrics.n == 2
    assert metrics.expected_return_mae is not None
    assert metrics.q10_pinball_loss is not None
    assert metrics.p_gt_2_brier is not None
    assert metrics.p_lt_minus_5_brier is not None
    assert metrics.q10_breach_rate is not None


def test_historical_dilution_flags_do_not_create_active_supply_interaction() -> None:
    without_active_supply = freeze_v42_forecast(
        candidate=_candidate(
            dilution_flags=("shelf_registration", "warrant"),
        ),
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        market_state=_state(),
        catalyst=_catalyst(),
        v4_mechanisms=V4_MECHANISMS,
        extension_risk=EXTENSION,
        regime_tags=("FUNDAMENTAL_REPRICE",),
        frozen_at=CUTOFF,
    )
    with_active_supply = freeze_v42_forecast(
        candidate=_candidate(
            dilution_flags=("convertible", "warrant"),
        ),
        session_date=date(2026, 9, 23),
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        market_state=_state(),
        catalyst=_catalyst(),
        v4_mechanisms=V4_MECHANISMS,
        extension_risk=EXTENSION,
        regime_tags=("FUNDAMENTAL_REPRICE", "SUPPLY_OVERHANG"),
        frozen_at=CUTOFF,
    )

    assert without_active_supply.interactions.extension_x_supply == Decimal("0")
    assert with_active_supply.interactions.extension_x_supply > Decimal("0")
    assert with_active_supply.p_close_above_open < without_active_supply.p_close_above_open
