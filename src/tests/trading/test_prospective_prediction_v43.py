from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.apps.trading.gapper_dataset import GapperCandidate
from app.apps.trading.prospective_prediction_v4 import (
    PremarketFeature,
    PremarketMarketStateSnapshot,
)
from app.apps.trading.prospective_prediction_v42 import (
    V42Forecast,
    V42MechanismHeads,
    V42ReturnDistribution,
    V42RiskInteractions,
)
from app.apps.trading.prospective_prediction_v43 import (
    derive_v43_cohort_regime,
    derive_v43_extension_overlay,
    freeze_v43_forecast,
    session_eligible_for_v43_forward_validation,
)


SESSION = date(2026, 9, 28)
CUTOFF = datetime(2026, 9, 28, 13, 29, tzinfo=timezone.utc)


def _feature(name: str, value: str, instrument: str) -> PremarketFeature:
    observed = datetime(2026, 9, 28, 13, 28, tzinfo=timezone.utc)
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
        provenance_fingerprint=f"{instrument}:{name}",
    )


def _state(
    instrument: str,
    *,
    vwap: str = "3",
    low: str = "25",
    range_position: str = "0.75",
    prior_1d: str = "5",
    prior_3d: str = "8",
    turnover: str = "0.5",
    acceleration: str = "0.5",
    late_volume: str = "0.25",
) -> PremarketMarketStateSnapshot:
    values = {
        "distance_from_premarket_vwap_pct": vwap,
        "distance_from_premarket_low_pct": low,
        "position_in_premarket_range": range_position,
        "prior_1d_return_pct": prior_1d,
        "prior_3d_return_pct": prior_3d,
        "float_turnover": turnover,
        "late_premarket_acceleration": acceleration,
        "late_premarket_volume_share": late_volume,
    }
    return PremarketMarketStateSnapshot(
        snapshot_id=f"state:{instrument}",
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        instrument_id=instrument,
        prediction_cutoff_at=CUTOFF,
        frozen_at=CUTOFF,
        features=tuple(_feature(name, value, instrument) for name, value in values.items()),
    )


def _candidate(instrument: str, gap: str = "50") -> GapperCandidate:
    gap_value = Decimal(gap)
    return GapperCandidate(
        instrument_id=instrument,
        observed_at=CUTOFF,
        previous_close=Decimal("10"),
        premarket_price=Decimal("10") * (Decimal("1") + gap_value / Decimal("100")),
        gap_pct=gap_value,
        premarket_volume=Decimal("1000000"),
        premarket_dollar_volume=Decimal("15000000"),
        tod_rvol=Decimal("8"),
        float_shares=Decimal("2000000"),
        spread_bps=Decimal("35"),
    )


def _v42(
    instrument: str,
    *,
    remaining: str = "0.65",
    exhaustion: str = "0.25",
    low_info: str = "0.10",
    probability: str = "0.62",
) -> V42Forecast:
    return V42Forecast(
        instrument_id=instrument,
        session_date=SESSION,
        cohort_id="cohort-1",
        cohort_fingerprint="cohort-fp",
        model_spec_fingerprint="v42-spec",
        feature_fingerprint=f"v42-feature:{instrument}",
        frozen_at=CUTOFF,
        p_close_above_open=Decimal(probability),
        mechanisms=V42MechanismHeads(
            fundamental_reprice_score=Decimal("0.75"),
            theme_squeeze_score=Decimal("0.25"),
            low_information_technical_score=Decimal(low_info),
            continuation_demand_score=Decimal("0.75"),
            remaining_upside_score=Decimal(remaining),
            opening_exhaustion_score=Decimal(exhaustion),
            supply_fade_score=Decimal("0.15"),
        ),
        interactions=V42RiskInteractions(
            extension_x_supply=Decimal("0.05"),
            extension_x_low_liquidity=Decimal("0.05"),
            extension_x_weak_finality=Decimal("0.05"),
        ),
        return_distribution=V42ReturnDistribution(
            q10=Decimal("-0.04"),
            q50=Decimal("0.04"),
            q90=Decimal("0.16"),
            expected_return=Decimal("0.055"),
            expected_shortfall_10pct=Decimal("-0.07"),
            p_return_gt_2pct=Decimal("0.65"),
            p_return_lt_minus_5pct=Decimal("0.20"),
            expected_mae=Decimal("-0.07"),
            expected_mfe=Decimal("0.14"),
        ),
        uncertainty="moderate",
    )


def test_v43_starts_after_sep25_design_evidence() -> None:
    assert not session_eligible_for_v43_forward_validation(date(2026, 9, 25))
    assert session_eligible_for_v43_forward_validation(SESSION)


def test_v43_richer_extension_overlay_detects_saturation_and_deceleration() -> None:
    instrument = "equity:US:AAA"
    healthy = derive_v43_extension_overlay(
        candidate=_candidate(instrument, "50"),
        market_state=_state(instrument),
        base_v42=_v42(instrument),
    )
    stretched = derive_v43_extension_overlay(
        candidate=_candidate(instrument, "120"),
        market_state=_state(
            instrument,
            vwap="18",
            low="100",
            range_position="0.98",
            prior_1d="40",
            prior_3d="80",
            turnover="1.8",
            acceleration="-0.5",
            late_volume="0.05",
        ),
        base_v42=_v42(
            instrument,
            remaining="0.25",
            exhaustion="0.85",
            low_info="0.70",
        ),
    )

    assert stretched.composite_exhaustion_score > healthy.composite_exhaustion_score
    assert stretched.premarket_repricing_complete_score > healthy.premarket_repricing_complete_score
    assert stretched.demand_resilience_score < healthy.demand_resilience_score
    assert "multi_day_extension_score" in stretched.used_components


def _cohort(*, stretched: bool):
    rows = []
    for index in range(3):
        instrument = f"equity:US:T{index}"
        if stretched:
            candidate = _candidate(instrument, "100")
            base = _v42(
                instrument,
                remaining="0.20",
                exhaustion="0.85",
                low_info="0.75",
                probability="0.62",
            )
            state = _state(
                instrument,
                vwap="16",
                low="90",
                range_position="0.95",
                prior_1d="35",
                prior_3d="70",
                turnover="1.6",
                acceleration="-0.4",
                late_volume="0.06",
            )
        else:
            candidate = _candidate(instrument, "30")
            base = _v42(
                instrument,
                remaining="0.70",
                exhaustion="0.20",
                low_info="0.10",
                probability="0.62",
            )
            state = _state(instrument)
        overlay = derive_v43_extension_overlay(
            candidate=candidate,
            market_state=state,
            base_v42=base,
        )
        rows.append((candidate, base, overlay))
    return rows


def test_v43_cohort_regime_penalizes_board_wide_exhaustion() -> None:
    normal_rows = _cohort(stretched=False)
    exhausted_rows = _cohort(stretched=True)
    normal = derive_v43_cohort_regime(normal_rows)
    exhausted = derive_v43_cohort_regime(exhausted_rows)

    assert normal.classification == "NORMAL"
    assert exhausted.classification == "HIGH_EXHAUSTION"
    assert exhausted.risk_score > normal.risk_score

    candidate, base, overlay = normal_rows[0]
    normal_forecast = freeze_v43_forecast(
        candidate=candidate,
        base_v42=base,
        extension_overlay=overlay,
        cohort_regime=normal,
        frozen_climatology_probability=Decimal("0.38"),
        frozen_at=CUTOFF,
    )

    risky_candidate, risky_base, risky_overlay = exhausted_rows[0]
    exhausted_forecast = freeze_v43_forecast(
        candidate=risky_candidate,
        base_v42=risky_base,
        extension_overlay=risky_overlay,
        cohort_regime=exhausted,
        frozen_climatology_probability=Decimal("0.38"),
        frozen_at=CUTOFF,
    )

    assert exhausted_forecast.p_close_above_open < normal_forecast.p_close_above_open
    assert exhausted_forecast.expected_return < normal_forecast.expected_return
    assert (
        normal_forecast.probability_edge_over_climatology
        == normal_forecast.p_close_above_open - Decimal("0.38")
    )
