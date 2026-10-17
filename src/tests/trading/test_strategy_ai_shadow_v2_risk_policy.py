from __future__ import annotations

from decimal import Decimal

from app.apps.trading.strategy_ai_shadow_v2 import AIShadowV2AlphaDecision
from app.apps.trading.strategy_ai_shadow_v2_monitor import (
    AI_SHADOW_V2_MAX_STRUCTURAL_RISK_PCT,
    deterministic_risk_geometry,
)

INSTRUMENT = "equity:NASDAQ:TEST"


def _decision(stop: str, target: str) -> AIShadowV2AlphaDecision:
    return AIShadowV2AlphaDecision(
        instrument_id=INSTRUMENT,
        setup_family="trend_continuation",
        state="enter",
        quality_score=80,
        invalidation_price=Decimal(stop),
        target_1=Decimal(target),
        thesis="Fixture entry.",
    )


def test_wide_structural_stop_is_vetoed_even_when_target_claims_two_r() -> None:
    geometry = deterministic_risk_geometry(
        _decision("8.50", "13.50"),
        entry_reference=Decimal("10"),
        estimated_cost_bps=Decimal("20"),
        minimum_net_r=Decimal("2"),
    )

    assert AI_SHADOW_V2_MAX_STRUCTURAL_RISK_PCT == Decimal("8")
    assert geometry.risk_per_share == Decimal("1.50")
    assert geometry.valid is False
    assert geometry.reason == "maximum_structural_risk_pct_exceeded"


def test_normal_structural_stop_preserves_existing_two_r_gate() -> None:
    geometry = deterministic_risk_geometry(
        _decision("9.50", "11.20"),
        entry_reference=Decimal("10"),
        estimated_cost_bps=Decimal("20"),
        minimum_net_r=Decimal("2"),
    )

    assert geometry.risk_per_share == Decimal("0.50")
    assert geometry.valid is True
    assert geometry.reason == "ok"
