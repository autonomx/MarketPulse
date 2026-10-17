from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.apps.trading.strategy_ai_shadow_v2_hardening import _episode_metrics, _lift_metrics
from app.apps.trading.strategy_ai_shadow_v2_roadmap_policy import _decision_outcome_metrics
from app.apps.trading.strategy_ai_shadow_v2_roadmap_policy import AI_SHADOW_V2_POLICY_VERSION
from app.apps.trading.strategy_repository import StrategyEvent

AT = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
INSTRUMENT = "equity:NASDAQ:TEST"


def _episode(
    event_id: str,
    *,
    arm: str = "full_session_catalyst",
    policy_version: str,
    positive: bool,
    entered: bool,
) -> StrategyEvent:
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="ai_v2_opportunity_episode",
        state="positive" if positive else "negative",
        observed_at=AT,
        idempotency_key=event_id,
        payload={
            "arm": arm,
            "policy_version": policy_version,
            "episode_id": event_id,
            "outcome": {
                "positive_opportunity": positive,
                "entered": entered,
                "peak_r": "2.5" if positive else "0.2",
                "mae_pct": "-1.0",
                "plus_two_r_before_minus_one_r": positive,
            },
        },
    )


def _decision(event_id: str, *, arm: str, observed_at: datetime) -> StrategyEvent:
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision",
        state="avoid",
        observed_at=observed_at,
        idempotency_key=event_id,
        payload={
            "arm": arm,
            "alpha_state": "avoid",
            "effective_state": "avoid",
            "feature_snapshot": {"experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION},
        },
    )


def _decision_outcome(event_id: str, *, arm: str, decision_event_id: str) -> StrategyEvent:
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision_outcome",
        state="positive",
        observed_at=AT + timedelta(hours=1),
        idempotency_key=event_id,
        payload={
            "arm": arm,
            "policy_version": AI_SHADOW_V2_POLICY_VERSION,
            "decision_event_id": decision_event_id,
            "alpha_state": "avoid",
            "effective_state": "avoid",
            "outcome": {
                "positive_opportunity": True,
                "mfe_pct": "8",
            },
        },
    )


def test_metrics_ignore_stale_policy_episodes() -> None:
    metrics = _episode_metrics(
        [
            _episode("current", policy_version=AI_SHADOW_V2_POLICY_VERSION, positive=True, entered=True),
            _episode("stale", policy_version="old-policy", positive=False, entered=True),
        ],
        "full_session_catalyst",
    )

    assert metrics["episode_count"] == 1
    assert metrics["entered_episode_count"] == 1
    assert metrics["false_entry_count"] == 0
    assert metrics["good_entry_recall"] == "1"
    assert metrics["entry_precision"] == "1"


def test_decision_metrics_use_only_observations_present_in_both_arms() -> None:
    control_1 = _decision("control-1", arm="full_session_control", observed_at=AT)
    control_2 = _decision(
        "control-2",
        arm="full_session_control",
        observed_at=AT + timedelta(minutes=5),
    )
    catalyst_1 = _decision("catalyst-1", arm="full_session_catalyst", observed_at=AT)
    events = [
        control_1,
        control_2,
        catalyst_1,
        _decision_outcome("out-control-1", arm="full_session_control", decision_event_id="control-1"),
        _decision_outcome("out-control-2", arm="full_session_control", decision_event_id="control-2"),
        _decision_outcome("out-catalyst-1", arm="full_session_catalyst", decision_event_id="catalyst-1"),
    ]

    metrics = _decision_outcome_metrics(events, "full_session_control")

    assert metrics["paired_only"] is True
    assert metrics["paired_decision_count"] == 1
    assert metrics["decision_outcome_count"] == 1
    assert metrics["avoid_decision_count"] == 1


def test_episode_lift_is_not_claimed_when_decision_schedule_is_unpaired() -> None:
    events = [
        _decision("control", arm="full_session_control", observed_at=AT),
        _decision(
            "catalyst",
            arm="full_session_catalyst",
            observed_at=AT + timedelta(minutes=1),
        ),
        _episode(
            "control-episode",
            arm="full_session_control",
            policy_version=AI_SHADOW_V2_POLICY_VERSION,
            positive=False,
            entered=True,
        ),
        _episode(
            "catalyst-episode",
            arm="full_session_catalyst",
            policy_version=AI_SHADOW_V2_POLICY_VERSION,
            positive=True,
            entered=True,
        ),
    ]

    metrics = _lift_metrics(events)["full_session"]

    assert metrics["comparison_valid"] is False
    assert metrics["requires_same_decision_schedule"] is True
    assert all(value is None for value in metrics["catalyst_minus_control"].values())
