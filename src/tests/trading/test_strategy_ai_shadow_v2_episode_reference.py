from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.apps.trading.strategy_ai_shadow_v2_hardening import _episode_reference_event
from app.apps.trading.strategy_repository import StrategyEvent


AT = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
INSTRUMENT = "equity:NASDAQ:TEST"


def _decision(state: str, minute: int) -> StrategyEvent:
    observed = AT + timedelta(minutes=minute)
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_shadow",
        event_id=f"event-{minute}",
        run_id="run-1",
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision",
        state=state,
        reason_code="TEST",
        observed_at=observed,
        idempotency_key=f"idem-{minute}",
        payload={
            "arm": "full_session_catalyst",
            "effective_state": state,
            "decision": {"state": state},
        },
    )


def test_episode_reference_prefers_actual_entry_over_earlier_watch_and_armed() -> None:
    group = [_decision("watch", 0), _decision("armed", 5), _decision("enter", 6), _decision("manage", 10)]

    reference = _episode_reference_event(group)

    assert reference.payload["effective_state"] == "enter"
    assert reference.observed_at == AT + timedelta(minutes=6)


def test_episode_reference_uses_armed_when_entry_never_occurs() -> None:
    group = [_decision("watch", 0), _decision("armed", 5), _decision("watch", 10)]

    reference = _episode_reference_event(group)

    assert reference.payload["effective_state"] == "armed"
