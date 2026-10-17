from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.strategy_ai_shadow_v2 import MarketStructureSnapshot, StructuredAlphaTrigger
from app.apps.trading.strategy_ai_shadow_v2_roadmap_policy import (
    _previous_trigger_satisfied as _effective_armed_trigger_satisfied,
)
from app.apps.trading.strategy_repository import StrategyEvent

INSTRUMENT = "equity:NASDAQ:TEST"
AT = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)


def test_expired_effective_armed_trigger_does_not_fast_recheck() -> None:
    trigger = StructuredAlphaTrigger(
        trigger_type="bar_close_above",
        price=Decimal("10"),
        expiry_minutes=10,
    )
    previous = StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id="armed",
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision",
        state="armed",
        observed_at=AT - timedelta(minutes=11),
        idempotency_key="armed",
        payload={
            "arm": "full_session_catalyst",
            "effective_state": "armed",
            "decision": {
                "state": "armed",
                "trigger": trigger.model_dump(mode="json"),
            },
            "feature_snapshot": {},
        },
    )
    structure = MarketStructureSnapshot(
        observed_at=AT,
        current_price=Decimal("10.10"),
        session_open=Decimal("9"),
        session_high=Decimal("10.20"),
        session_low=Decimal("8.80"),
        session_vwap=Decimal("9.80"),
        vwap_distance_pct=Decimal("3"),
        hod_distance_pct=Decimal("1"),
        session_return_pct=Decimal("12"),
        pullback_from_hod_pct=Decimal("1"),
        range_location=Decimal("0.9"),
        confirmation_score=70,
    )

    assert _effective_armed_trigger_satisfied(previous, structure=structure) is False
