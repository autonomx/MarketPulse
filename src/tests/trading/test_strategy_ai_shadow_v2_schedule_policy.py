from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.strategy_ai_shadow_v2 import MarketStructureSnapshot, StructuredAlphaTrigger
from app.apps.trading.strategy_ai_shadow_v2_roadmap_policy import (
    _due_reasons,
    _previous_trigger_satisfied as _effective_armed_trigger_satisfied,
)
from app.apps.trading.strategy_repository import StrategyEvent

INSTRUMENT = "equity:NASDAQ:TEST"
AT = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)


def _structure(price: str = "10.10", high: str = "10.20") -> MarketStructureSnapshot:
    return MarketStructureSnapshot(
        observed_at=AT,
        current_price=Decimal(price),
        session_open=Decimal("9.00"),
        session_high=Decimal(high),
        session_low=Decimal("8.80"),
        session_vwap=Decimal("9.80"),
        vwap_distance_pct=Decimal("3"),
        hod_distance_pct=Decimal("1"),
        session_return_pct=Decimal("12"),
        pullback_from_hod_pct=Decimal("1"),
        range_location=Decimal("0.9"),
        confirmation_score=70,
    )


def _decision_event(
    *,
    arm: str,
    event_id: str,
    effective_state: str,
    raw_state: str = "armed",
    fingerprint: str = "a" * 64,
    observed_at: datetime = AT - timedelta(minutes=5),
) -> StrategyEvent:
    trigger = StructuredAlphaTrigger(
        trigger_type="bar_close_above",
        price=Decimal("10"),
    )
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision",
        state=effective_state,
        observed_at=observed_at,
        idempotency_key=event_id,
        payload={
            "arm": arm,
            "effective_state": effective_state,
            "decision": {
                "instrument_id": INSTRUMENT,
                "setup_family": "trend_continuation",
                "state": raw_state,
                "quality_score": 80,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "invalidation_price": "9.50",
                "target_1": "11.50",
                "target_2": None,
                "trigger": trigger.model_dump(mode="json"),
                "extension_risk": "low",
                "evidence_for": [],
                "evidence_against": [],
                "thesis_changed": False,
                "thesis": "fixture",
                "execution_authority": False,
            },
            "feature_snapshot": {
                "market_structure": _structure("9.95", "10.00").model_dump(mode="json"),
                "catalyst_intelligence": {"evidence_fingerprint": fingerprint},
            },
        },
    )


def test_raw_armed_but_effective_watch_does_not_trigger_fast_recheck() -> None:
    previous = _decision_event(
        arm="full_session_catalyst",
        event_id="catalyst",
        effective_state="watch",
        raw_state="armed",
    )

    assert _effective_armed_trigger_satisfied(previous, structure=_structure()) is False


def test_effective_armed_trigger_can_request_fast_recheck() -> None:
    previous = _decision_event(
        arm="full_session_catalyst",
        event_id="catalyst",
        effective_state="armed",
        raw_state="armed",
    )

    assert _effective_armed_trigger_satisfied(previous, structure=_structure()) is True


def test_changed_catalyst_fingerprint_triggers_both_paired_arms() -> None:
    events = [
        _decision_event(
            arm="full_session_control",
            event_id="control",
            effective_state="watch",
        ),
        _decision_event(
            arm="full_session_catalyst",
            event_id="catalyst",
            effective_state="watch",
            fingerprint="a" * 64,
        ),
    ]
    control_reasons = _due_reasons(
        events,
        arm="full_session_control",
        paired_arm="full_session_catalyst",
        instrument_id=INSTRUMENT,
        at=AT,
        structure=_structure(),
        current_catalyst_fingerprint="b" * 64,
    )
    catalyst_reasons = _due_reasons(
        events,
        arm="full_session_catalyst",
        paired_arm="full_session_control",
        instrument_id=INSTRUMENT,
        at=AT,
        structure=_structure(),
        current_catalyst_fingerprint="b" * 64,
    )

    assert "paired_catalyst_evidence_changed" in control_reasons
    assert "paired_catalyst_evidence_changed" in catalyst_reasons


def test_unchanged_catalyst_fingerprint_does_not_create_spurious_event() -> None:
    events = [
        _decision_event(
            arm="full_session_control",
            event_id="control",
            effective_state="watch",
            observed_at=AT - timedelta(minutes=1),
        ),
        _decision_event(
            arm="full_session_catalyst",
            event_id="catalyst",
            effective_state="watch",
            fingerprint="a" * 64,
            observed_at=AT - timedelta(minutes=1),
        ),
    ]
    reasons = _due_reasons(
        events,
        arm="full_session_control",
        paired_arm="full_session_catalyst",
        instrument_id=INSTRUMENT,
        at=AT,
        structure=_structure(),
        current_catalyst_fingerprint="a" * 64,
    )

    assert "paired_catalyst_evidence_changed" not in reasons
