from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.apps.trading.models import MarketBar
from app.apps.trading.strategy_ai_shadow_v2 import (
    AI_SHADOW_V2_POLICY_VERSION,
    CatalystIntelligenceSnapshot,
    derive_catalyst_influence,
)
from app.apps.trading.strategy_ai_shadow_v2_hardening import (
    _active_stop_price,
    _lift_metrics,
    _morning_snapshot,
    _sanitized_alpha_feature,
    _stop_was_breached,
)
from app.apps.trading.strategy_repository import StrategyEvent


INSTRUMENT = "equity:NASDAQ:TEST"
AT = datetime(2026, 9, 10, 13, 35, tzinfo=timezone.utc)


def _snapshot(snapshot_id: str, *, hurdle: int, persistence: str = "high") -> CatalystIntelligenceSnapshot:
    influence = derive_catalyst_influence(
        persistence_class=persistence,
        primary_source_verified=True,
        supply_pressure="low",
        promotional_risk="low",
        gap_already_prices_in_news="low",
    ).model_copy(update={"confirmation_hurdle": hurdle})
    return CatalystIntelligenceSnapshot(
        snapshot_id=snapshot_id,
        instrument_id=INSTRUMENT,
        as_of=AT,
        provider="fixture",
        evidence_fingerprint=("a" if snapshot_id == "morning" else "b") * 64,
        evidence_ids=(f"ev-{snapshot_id}",),
        evidence_quality="primary_verified",
        primary_source_verified=True,
        catalyst_type="contract",
        catalyst_summary="Fixture catalyst.",
        intraday_persistence_class=persistence,
        source_quality_score=95,
        catalyst_strength=85,
        ambiguity="low",
        influence=influence,
        reasoning="Fixture reasoning.",
    )


def _event(
    *,
    event_type: str,
    at: datetime,
    payload: dict[str, object],
    state: str = "snapshot",
    suffix: str = "1",
) -> StrategyEvent:
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_shadow",
        event_id=f"event-{suffix}",
        run_id="run-1",
        instrument_id=INSTRUMENT,
        event_type=event_type,
        state=state,
        reason_code="TEST",
        observed_at=at,
        idempotency_key=f"idem-{suffix}",
        payload=payload,
    )


def _bar(index: int, *, low: str, close: str = "10.00") -> MarketBar:
    start = AT + timedelta(minutes=index)
    price = Decimal(close)
    return MarketBar(
        instrument_id=INSTRUMENT,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=price,
        high=price + Decimal("0.10"),
        low=Decimal(low),
        close=price,
        volume=Decimal("1000"),
        session="regular",
        provider="fixture",
    )


def test_morning_arm_uses_durable_freeze_not_later_refresh() -> None:
    morning = _snapshot("morning", hurdle=49)
    refreshed = _snapshot("refresh", hurdle=72, persistence="low")
    events = [
        _event(
            event_type="ai_v2_catalyst_freeze",
            at=AT,
            payload={"snapshot": morning.model_dump(mode="json")},
            suffix="freeze",
        ),
        _event(
            event_type="ai_v2_catalyst_snapshot",
            at=AT + timedelta(hours=2),
            payload={"snapshot": refreshed.model_dump(mode="json")},
            suffix="refresh",
        ),
    ]

    resolved = _morning_snapshot(events, INSTRUMENT)

    assert resolved is not None
    assert resolved.snapshot_id == "morning"
    assert resolved.influence.confirmation_hurdle == 49
    assert resolved.intraday_persistence_class == "high"


def test_alpha_projection_removes_execution_microstructure_and_freezes_prior() -> None:
    morning = _snapshot("morning", hurdle=50)
    feature = {
        "market_structure": {"current_price": "10"},
        "market_microstructure": {"bid": "9.99", "ask": "10.01", "spread_bps": "20"},
        "execution_status_visible": True,
        "catalyst_intelligence": _snapshot("refresh", hurdle=70, persistence="low").model_dump(mode="json"),
        "alpha_confirmation_hurdle": 70,
        "empirical_setup_calibration": {"trend_continuation": {"sample_size": 100}},
    }

    projected = _sanitized_alpha_feature(feature, frozen_catalyst=morning)

    assert "market_microstructure" not in projected
    assert projected["execution_status_visible"] is False
    assert projected["alpha_confirmation_hurdle"] == 50
    assert projected["catalyst_snapshot_mode"] == "morning_frozen"
    assert projected["catalyst_intelligence"]["snapshot_id"] == "morning"
    assert "empirical_setup_calibration" not in projected


def test_active_stop_is_carried_from_filled_entry_geometry() -> None:
    decision = _event(
        event_type="ai_v2_decision",
        at=AT,
        state="enter",
        payload={
            "arm": "full_session_catalyst",
            "effective_state": "enter",
            "geometry": {
                "valid": True,
                "invalidation_price": "9.50",
            },
        },
        suffix="decision",
    )
    fill = _event(
        event_type="ai_v2_fill",
        at=AT + timedelta(seconds=3),
        state="filled",
        payload={
            "arm": "full_session_catalyst",
            "side": "buy",
            "trade_id": "trade-1",
            "position_after": {"units": "1"},
        },
        suffix="fill",
    )

    assert _active_stop_price(
        [decision, fill],
        arm="full_session_catalyst",
        instrument_id=INSTRUMENT,
    ) == Decimal("9.50")


def test_stop_breach_only_uses_bars_after_actual_entry() -> None:
    entry_time = AT + timedelta(minutes=2, seconds=5)
    row = {
        "bars": [
            _bar(0, low="9.20"),
            _bar(1, low="9.30"),
            _bar(2, low="9.60"),
            _bar(3, low="9.40"),
        ]
    }

    assert _stop_was_breached(row, stop=Decimal("9.50"), entry_time=entry_time) is True

    safe = {"bars": row["bars"][:3]}
    assert _stop_was_breached(safe, stop=Decimal("9.50"), entry_time=entry_time) is False


def _episode_event(
    arm: str,
    *,
    entered: bool,
    positive: bool,
    two_r: bool,
    peak_r: str,
    mae: str,
    suffix: str,
) -> StrategyEvent:
    return _event(
        event_type="ai_v2_opportunity_episode",
        at=AT + timedelta(hours=7),
        state="positive" if positive else "negative",
        suffix=suffix,
        payload={
            "arm": arm,
            "policy_version": AI_SHADOW_V2_POLICY_VERSION,
            "outcome": {
                "entered": entered,
                "positive_opportunity": positive,
                "plus_two_r_before_minus_one_r": two_r,
                "peak_r": peak_r,
                "mae_pct": mae,
            },
        },
    )


def test_postclose_lift_metrics_compare_catalyst_to_identical_control() -> None:
    events = [
        _episode_event(
            "morning_control", entered=False, positive=True, two_r=True,
            peak_r="2.5", mae="-2", suffix="mc",
        ),
        _episode_event(
            "morning_catalyst", entered=True, positive=True, two_r=True,
            peak_r="2.5", mae="-2", suffix="ma",
        ),
        _episode_event(
            "full_session_control", entered=True, positive=False, two_r=False,
            peak_r="0.4", mae="-6", suffix="fc",
        ),
        _episode_event(
            "full_session_catalyst", entered=True, positive=True, two_r=True,
            peak_r="2.2", mae="-1", suffix="fa",
        ),
        _event(
            event_type="ai_v2_decision",
            at=AT + timedelta(minutes=10),
            payload={
                "arm": "morning_control",
                "feature_snapshot": {
                    "experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION,
                },
            },
            suffix="mc-decision",
        ),
        _event(
            event_type="ai_v2_decision",
            at=AT + timedelta(minutes=10),
            payload={
                "arm": "morning_catalyst",
                "feature_snapshot": {
                    "experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION,
                },
            },
            suffix="ma-decision",
        ),
        _event(
            event_type="ai_v2_decision",
            at=AT + timedelta(minutes=20),
            payload={
                "arm": "full_session_control",
                "feature_snapshot": {
                    "experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION,
                },
            },
            suffix="fc-decision",
        ),
        _event(
            event_type="ai_v2_decision",
            at=AT + timedelta(minutes=20),
            payload={
                "arm": "full_session_catalyst",
                "feature_snapshot": {
                    "experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION,
                },
            },
            suffix="fa-decision",
        ),
    ]

    metrics = _lift_metrics(events)

    assert Decimal(metrics["morning"]["catalyst_minus_control"]["good_entry_recall"]) > 0
    assert Decimal(metrics["full_session"]["catalyst_minus_control"]["entry_precision"]) > 0
    assert Decimal(
        metrics["full_session"]["catalyst_minus_control"]["two_r_before_minus_one_r_rate"]
    ) > 0
