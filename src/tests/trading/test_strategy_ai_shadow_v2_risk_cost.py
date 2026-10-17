from __future__ import annotations

from decimal import Decimal

from app.apps.trading.strategy_ai_shadow_v2 import AIShadowV2AlphaDecision
from app.apps.trading.strategy_ai_shadow_v2_monitor import deterministic_risk_geometry
from app.apps.trading.strategy_ai_shadow_v2_hardening import _OBSERVED_SPREAD_BPS


INSTRUMENT = "equity:NASDAQ:TEST"


def _decision() -> AIShadowV2AlphaDecision:
    return AIShadowV2AlphaDecision(
        instrument_id=INSTRUMENT,
        setup_family="trend_continuation",
        state="enter",
        quality_score=80,
        invalidation_price=Decimal("9.50"),
        target_1=Decimal("11.05"),
        extension_risk="low",
        thesis_changed=True,
        thesis="Fixture continuation entry.",
    )


def test_observed_spread_does_not_relax_authoritative_two_r_cost_assumption() -> None:
    baseline = deterministic_risk_geometry(
        _decision(),
        entry_reference=Decimal("10"),
        estimated_cost_bps=Decimal("160"),
        minimum_net_r=Decimal("2"),
    )
    assert baseline.valid is False
    assert baseline.estimated_cost_bps == Decimal("160")

    token = _OBSERVED_SPREAD_BPS.set({INSTRUMENT: Decimal("20")})
    try:
        observed = deterministic_risk_geometry(
            _decision(),
            entry_reference=Decimal("10"),
            estimated_cost_bps=Decimal("160"),
            minimum_net_r=Decimal("2"),
        )
    finally:
        _OBSERVED_SPREAD_BPS.reset(token)

    assert observed.estimated_cost_bps == Decimal("160")
    assert observed.valid is False
    assert observed.net_r == baseline.net_r
