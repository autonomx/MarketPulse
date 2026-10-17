from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.apps.trading.models import MarketBar
from app.apps.trading.research.contracts import TradingEvidence
from app.apps.trading.strategy_ai_shadow_v2 import AIShadowV2AlphaDecision, build_market_structure_snapshot
from app.apps.trading.strategy_ai_shadow_v2_roadmap_policy import (
    AI_SHADOW_V2_POLICY_VERSION,
    _causal_evidence,
    _cohort_context,
    _decision_groups,
    _due_reasons,
    _enforce_confirmation_hurdle,
    _historical_episodes_policy,
    _morning_freeze_allowed,
    _multi_timeframe_context,
    _recent_primary_catalyst_evidence,
    _stable_snapshot_id,
)
from app.apps.trading.strategy_repository import StrategyEvent

INSTRUMENT = "equity:NASDAQ:TEST"
START = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def _bar(index: int, close: str, *, volume: str = "1000", instrument_id: str = INSTRUMENT) -> MarketBar:
    price = Decimal(close)
    start = START + timedelta(minutes=index)
    return MarketBar(
        instrument_id=instrument_id,
        interval="1m",
        start_time=start,
        end_time=start + timedelta(minutes=1),
        open=price - Decimal("0.02"),
        high=price + Decimal("0.05"),
        low=price - Decimal("0.05"),
        close=price,
        volume=Decimal(volume),
        session="regular",
        provider="fixture",
    )


def _evidence(
    suffix: str,
    *,
    source_type: str = "company_ir",
    tier: int = 1,
    known_at: datetime | None = None,
    published_at: datetime | None = None,
    form: str | None = None,
) -> TradingEvidence:
    known = known_at or START - timedelta(minutes=30)
    published = published_at or known
    metadata = {"form": form} if form is not None else {}
    return TradingEvidence(
        evidence_id=f"ev-{suffix}",
        instrument_id=INSTRUMENT,
        evidence_type="catalyst",
        source_type=source_type,
        source_locator=f"https://example.test/{suffix}",
        source_authority_tier=tier,
        source_published_at=published,
        source_available_at=published,
        captured_at=known,
        omnix_known_at=known,
        title="Fixture catalyst",
        content="A causal fixture source.",
        content_hash=("a" if suffix == "1" else "b") * 64,
        extraction_status="completed",
        metadata=metadata,
        immutable_fingerprint=("c" if suffix == "1" else "d") * 64,
    )


def _decision_event(
    *,
    arm: str,
    observed_at: datetime,
    state: str = "watch",
    event_id: str = "event-1",
) -> StrategyEvent:
    return StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id=event_id,
        instrument_id=INSTRUMENT,
        event_type="ai_v2_decision",
        state=state,
        reason_code="AI_V2_ALPHA_DECISION",
        observed_at=observed_at,
        idempotency_key=event_id,
        payload={
            "arm": arm,
            "alpha_state": state,
            "effective_state": state,
            "decision": {
                "instrument_id": INSTRUMENT,
                "setup_family": "trend_continuation",
                "state": state,
                "quality_score": 60,
                "entry_zone_low": None,
                "entry_zone_high": None,
                "invalidation_price": None,
                "target_1": None,
                "target_2": None,
                "trigger": None,
                "extension_risk": "medium",
                "evidence_for": [],
                "evidence_against": [],
                "thesis_changed": False,
                "thesis": "fixture",
                "execution_authority": False,
            },
            "feature_snapshot": {
                "experiment_policy_version": AI_SHADOW_V2_POLICY_VERSION,
            },
        },
    )


def test_causal_evidence_rejects_future_capture_or_publication() -> None:
    cutoff = START
    causal = _evidence("1", known_at=cutoff - timedelta(minutes=1))
    future_capture = _evidence("2", known_at=cutoff + timedelta(minutes=1))
    future_publication = _evidence(
        "3",
        known_at=cutoff - timedelta(minutes=2),
        published_at=cutoff + timedelta(minutes=1),
    )

    assert _causal_evidence([causal, future_capture, future_publication], as_of=cutoff) == [causal]


def test_primary_catalyst_verification_excludes_supply_forms_and_stale_documents() -> None:
    current_ir = _evidence("1", source_type="company_ir", tier=1)
    supply = _evidence("2", source_type="sec", tier=1, form="S-3")
    stale = _evidence(
        "3",
        source_type="sec",
        tier=1,
        form="8-K",
        known_at=START - timedelta(days=5),
        published_at=START - timedelta(days=5),
    )

    result = _recent_primary_catalyst_evidence([current_ir, supply, stale], as_of=START)
    assert result == [current_ir]


def test_empty_evidence_snapshot_identity_is_instrument_scoped() -> None:
    fingerprint = "0" * 64
    assert _stable_snapshot_id("equity:NASDAQ:AAA", fingerprint) != _stable_snapshot_id(
        "equity:NASDAQ:BBB", fingerprint
    )


def test_morning_freeze_is_forbidden_after_entry_window_opens() -> None:
    config = SimpleNamespace(risk=SimpleNamespace(entry_start_et=time(9, 35)))
    before = datetime(2026, 9, 10, 13, 34, tzinfo=timezone.utc)  # 09:34 ET
    after = datetime(2026, 9, 10, 13, 36, tzinfo=timezone.utc)   # 09:36 ET

    assert _morning_freeze_allowed(before, config) is True
    assert _morning_freeze_allowed(after, config) is False


def test_multi_timeframe_context_contains_planned_trend_range_and_compressed_path() -> None:
    bars = [
        _bar(
            index,
            str(Decimal("10") + Decimal(index) / Decimal("50")),
            volume="700" if index >= 25 else "1000",
        )
        for index in range(36)
    ]
    structure = build_market_structure_snapshot(bars)
    row = {"bars": bars, "structure": structure}

    context = _multi_timeframe_context(row)

    assert context["ema9_distance_pct"] is not None
    assert context["ema20_distance_pct"] is not None
    assert context["atr14_pct"] is not None
    assert context["three_minute_trend_slope_pct_per_bar"] is not None
    assert context["five_minute_trend_slope_pct_per_bar"] is not None
    assert context["short_volume_to_prior10_ratio"] is not None
    assert 1 <= len(context["compressed_5m_path_last_120m"]) <= 24


def test_cohort_context_ranks_relative_strength_without_catalyst_information() -> None:
    slow_bars = [_bar(i, str(Decimal("10") + Decimal(i) / Decimal("200")), instrument_id="equity:NASDAQ:SLOW") for i in range(12)]
    fast_bars = [_bar(i, str(Decimal("10") + Decimal(i) / Decimal("50")), instrument_id="equity:NASDAQ:FAST") for i in range(12)]
    slow = build_market_structure_snapshot(slow_bars)
    fast = build_market_structure_snapshot(fast_bars)
    rows = [
        {"candidate": SimpleNamespace(instrument_id="equity:NASDAQ:SLOW"), "structure": slow},
        {"candidate": SimpleNamespace(instrument_id="equity:NASDAQ:FAST"), "structure": fast},
    ]

    context = _cohort_context(rows)

    assert context["equity:NASDAQ:FAST"]["session_return_rank"] == 1
    assert context["equity:NASDAQ:SLOW"]["session_return_rank"] == 2


def test_paired_schedule_uses_common_five_minute_heartbeat() -> None:
    observed = START
    events = [
        _decision_event(arm="full_session_control", observed_at=observed, event_id="control"),
        _decision_event(arm="full_session_catalyst", observed_at=observed, event_id="catalyst"),
    ]
    structure = build_market_structure_snapshot([_bar(i, "10.00") for i in range(6)])

    reasons = _due_reasons(
        events,
        arm="full_session_control",
        paired_arm="full_session_catalyst",
        instrument_id=INSTRUMENT,
        at=observed + timedelta(minutes=5),
        structure=structure,
    )

    assert "paired_five_minute_heartbeat" in reasons


def test_alpha_confirmation_hurdle_downgrades_entry_without_changing_risk_contract() -> None:
    decision = AIShadowV2AlphaDecision(
        instrument_id=INSTRUMENT,
        setup_family="trend_continuation",
        state="enter",
        quality_score=80,
        invalidation_price=Decimal("9.50"),
        target_1=Decimal("11.50"),
        thesis="Catalyst plus structure supports entry.",
    )
    feature = {
        "alpha_confirmation_hurdle": 55,
        "market_structure": {"confirmation_score": 50},
    }

    adjusted, reason = _enforce_confirmation_hurdle(decision, feature=feature)

    assert adjusted.state == "watch"
    assert adjusted.invalidation_price == decision.invalidation_price
    assert adjusted.target_1 == decision.target_1
    assert reason == "AI_V2_ALPHA_CONFIRMATION_HURDLE_NOT_MET"


def test_avoid_decisions_form_counterfactual_episode_instead_of_disappearing() -> None:
    decisions = [
        _decision_event(arm="full_session_control", observed_at=START, state="avoid", event_id="a1"),
        _decision_event(arm="full_session_control", observed_at=START + timedelta(minutes=5), state="avoid", event_id="a2"),
        _decision_event(arm="full_session_control", observed_at=START + timedelta(minutes=10), state="watch", event_id="w1"),
    ]

    groups = _decision_groups(decisions)

    assert [kind for kind, _ in groups] == ["avoid", "active"]
    assert len(groups[0][1]) == 2


class _HistoryRepository:
    def __init__(self, events: list[StrategyEvent]) -> None:
        self.events = events

    def recent_events(self, strategy_id: str, limit: int):
        return list(self.events)


def test_empirical_calibration_uses_only_versioned_full_session_treatment() -> None:
    outcome = {
        "arm": "full_session_catalyst",
        "instrument_id": INSTRUMENT,
        "episode_id": "episode-1",
        "started_at": START.isoformat(),
        "ended_at": (START + timedelta(minutes=60)).isoformat(),
        "setup_family": "trend_continuation",
        "catalyst_persistence_class": "high",
        "entered": True,
        "entry_price": "10",
        "plus_two_r_before_minus_one_r": True,
    }
    good = StrategyEvent(
        strategy_id="managed_finviz_gap_pullback_v1",
        event_id="good",
        instrument_id=INSTRUMENT,
        event_type="ai_v2_opportunity_episode",
        state="positive",
        observed_at=START,
        idempotency_key="good",
        payload={
            "arm": "full_session_catalyst",
            "policy_version": AI_SHADOW_V2_POLICY_VERSION,
            "episode_id": "episode-1",
            "outcome": outcome,
        },
    )
    control = good.model_copy(
        update={
            "event_id": "control",
            "idempotency_key": "control",
            "payload": {**good.payload, "arm": "full_session_control", "episode_id": "episode-2"},
        }
    )
    stale = good.model_copy(
        update={
            "event_id": "stale",
            "idempotency_key": "stale",
            "payload": {**good.payload, "policy_version": "old-policy", "episode_id": "episode-3"},
        }
    )

    rows = _historical_episodes_policy(_HistoryRepository([good, control, stale]), good.strategy_id)

    assert rows == [outcome]
