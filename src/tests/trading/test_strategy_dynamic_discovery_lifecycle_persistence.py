from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.apps.trading.strategy_discovery_acquisition import CausalMarketObservation
from app.apps.trading.strategy_discovery_replay import (
    DiscoveryReplayObservation,
    replay_dynamic_discovery,
)
from app.apps.trading.strategy_dynamic_discovery import (
    AttributionEvent,
    AttributionStage,
    CandidateLifecycleState,
    DiscoveryExperimentArm,
    DynamicCandidate,
    EvaluationTier,
    ExperimentCandidateState,
    MarketAnomalyFeatures,
    ShadowQualificationEvidence,
    tier_candidates,
)
from app.apps.trading.strategy_dynamic_discovery_monitor import run_dynamic_discovery_once
from app.apps.trading.strategy_dynamic_discovery_repository import (
    DynamicDiscoveryEventRepository,
    EVENT_ATTRIBUTION,
)
from app.apps.trading.strategy_interday_attribution import bridge_strategy_events
from app.apps.trading.strategy_interday_learning_monitor import run_interday_learning_once
from app.apps.trading.strategy_repository import StrategyEvent


SESSION = date(2026, 9, 11)
T0 = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)


def _candidate(index: int, *, lifecycle=CandidateLifecycleState.ACTIVE) -> DynamicCandidate:
    at = T0 + timedelta(seconds=index)
    return DynamicCandidate(
        session_date=SESSION,
        instrument_id=f"equity:NASDAQ:T{index:02d}",
        first_seen_at=at,
        discovered_at=at,
        last_observed_at=at,
        lifecycle=lifecycle,
        tier=(
            EvaluationTier.A
            if lifecycle != CandidateLifecycleState.EXPIRED
            else EvaluationTier.EXPIRED
        ),
        attention_score=max(0, 100 - index),
        common_priority=max(0, 100 - index),
        expired_at=at if lifecycle == CandidateLifecycleState.EXPIRED else None,
    )


class _Ledger:
    def __init__(self) -> None:
        self.events = []

    def append_event(self, event):
        if any(row.idempotency_key == event.idempotency_key for row in self.events):
            return False
        self.events.append(event)
        return True

    def events_by_types_between(
        self,
        strategy_id,
        *,
        event_types,
        start_time,
        end_time,
        limit=50_000,
    ):
        return [
            row
            for row in self.events
            if row.strategy_id == strategy_id
            and row.event_type in event_types
            and start_time <= row.observed_at < end_time
        ][:limit]


class _ParentOnlyRepository:
    def get_config(self, strategy_id):
        return SimpleNamespace(enabled=True, archived_at=None)


class _MissingParentRepository:
    def get_config(self, strategy_id):
        raise ValueError("strategy_not_found")


def test_attribution_owner_rejects_a_trade_that_skips_prior_stages() -> None:
    ledger = _Ledger()
    trade = StrategyEvent(
        strategy_id="gap-pullback-arm",
        event_id="trade-event-1",
        instrument_id="equity:NASDAQ:TRADE",
        event_type="entry_order_submitted",
        state="entry_ready",
        observed_at=T0,
        idempotency_key="trade-event-1",
        payload={},
    )

    bridged = bridge_strategy_events(
        ledger,
        session_date=SESSION,
        events_by_strategy={"gap-pullback-arm": (trade,)},
    )

    assert bridged == 1
    persisted = DynamicDiscoveryEventRepository(ledger).session_events(SESSION)
    attribution = next(
        AttributionEvent.model_validate(row.payload)
        for row in persisted
        if row.event_type == EVENT_ATTRIBUTION
    )
    assert attribution.stage == AttributionStage.TRADED
    assert attribution.passed is False
    assert attribution.reason == "ATTRIBUTION_PREDECESSOR_MISSING"


def test_interday_learning_owner_delegates_to_core_before_post_close() -> None:
    result = asyncio.run(
        run_interday_learning_once(
            now=T0,
            repository=_MissingParentRepository(),
        )
    )

    assert result == {
        "bridged": 0,
        "reported": False,
        "labeled": 0,
        "qualified": False,
    }


def test_candidates_beyond_evaluation_cap_are_durably_demoted_to_watch() -> None:
    current = {
        _candidate(index).instrument_id: _candidate_with_experiment_priority(index)
        for index in range(41)
    }

    snapshots = tier_candidates(tuple(current.values()))
    by_id = {row.instrument_id: row for row in snapshots}

    assert len(snapshots) == 41
    assert by_id["equity:NASDAQ:T00"].tier == EvaluationTier.A
    assert by_id["equity:NASDAQ:T40"].tier == EvaluationTier.WATCH
    assert by_id["equity:NASDAQ:T40"].strategy_ranks == {}


def _candidate_with_experiment_priority(index: int) -> DynamicCandidate:
    candidate = _candidate(index)
    state = ExperimentCandidateState(
        arm=DiscoveryExperimentArm.COMBINED,
        first_seen_at=candidate.first_seen_at,
        last_observed_at=candidate.last_observed_at,
        current_priority=100.0 - index,
        peak_priority=100.0 - index,
    )
    return candidate.model_copy(
        update={"experiment_states": {DiscoveryExperimentArm.COMBINED.value: state}}
    )


def test_expired_candidate_snapshot_orders_after_last_market_observation() -> None:
    ledger = _Ledger()
    repository = DynamicDiscoveryEventRepository(ledger)
    active = _candidate(1)
    assert repository.persist_candidate(active) is True

    expired_at = active.last_observed_at + timedelta(minutes=61)
    expired = active.model_copy(
        update={
            "lifecycle": CandidateLifecycleState.EXPIRED,
            "tier": EvaluationTier.EXPIRED,
            "common_priority": 0.0,
            "expired_at": expired_at,
        }
    )
    assert repository.persist_candidate(expired, snapshot_at=expired_at) is True

    latest = repository.latest_candidates(SESSION)[active.instrument_id]
    assert latest.lifecycle == CandidateLifecycleState.EXPIRED
    assert latest.tier == EvaluationTier.EXPIRED
    rows = [row for row in ledger.events if row.instrument_id == active.instrument_id]
    assert max(row.observed_at for row in rows) == expired_at


def test_legacy_correlation_envelope_does_not_poison_candidate_round_trip() -> None:
    ledger = _Ledger()
    repository = DynamicDiscoveryEventRepository(ledger)
    candidate = _candidate(2)
    assert repository.persist_candidate(candidate) is True

    stored = ledger.events[0]
    ledger.events[0] = stored.model_copy(
        update={
            "payload": {
                **stored.payload,
                "correlation_version": "trade-lifecycle-v1",
                "strategy_revision": 7,
                "session_id": "session-legacy",
                "setup_id": "setup-legacy",
                "trade_attempt_id": "attempt-legacy",
                "trade_intent_id": "intent-legacy",
                "risk_decision_id": "risk-legacy",
            }
        }
    )

    restored = repository.latest_candidates(SESSION)[candidate.instrument_id]
    assert restored == candidate


def test_candidate_compatibility_does_not_ignore_arbitrary_extra_fields() -> None:
    ledger = _Ledger()
    repository = DynamicDiscoveryEventRepository(ledger)
    candidate = _candidate(3)
    assert repository.persist_candidate(candidate) is True

    stored = ledger.events[0]
    ledger.events[0] = stored.model_copy(
        update={"payload": {**stored.payload, "unexpected_domain_field": True}}
    )

    with pytest.raises(ValueError, match="unexpected_domain_field"):
        repository.latest_candidates(SESSION)


@pytest.mark.anyio
async def test_explicit_causal_watermark_rejects_future_discovery_observation() -> None:
    observation_time = T0 + timedelta(seconds=1)
    observation = CausalMarketObservation(
        instrument_id="equity:NASDAQ:FUTR",
        session_date=SESSION,
        observed_at=observation_time,
        source="fixture",
        market=MarketAnomalyFeatures(
            observed_at=observation_time,
            gap_pct=30,
            tod_rvol=50,
            dollar_volume=20_000_000,
        ),
    )

    with pytest.raises(ValueError, match="future_dated"):
        await run_dynamic_discovery_once(
            now=T0,
            repository=_ParentOnlyRepository(),
            observations=(observation,),
        )


def test_replay_rejects_observation_from_different_exchange_session() -> None:
    next_session = T0 + timedelta(days=1)
    observation = DiscoveryReplayObservation(
        instrument_id="equity:NASDAQ:NEXT",
        observed_at=next_session,
        source="fixture",
        market=MarketAnomalyFeatures(
            observed_at=next_session,
            gap_pct=30,
            tod_rvol=50,
            dollar_volume=20_000_000,
        ),
    )

    with pytest.raises(ValueError, match="outside_exchange_session"):
        replay_dynamic_discovery(
            session_date=SESSION,
            observations=(observation,),
        )


def test_qualification_snapshot_is_idempotent_per_session() -> None:
    ledger = _Ledger()
    repository = DynamicDiscoveryEventRepository(ledger)
    evidence = ShadowQualificationEvidence(
        independent_sessions=0,
        labeled_opportunities=0,
        discovery_recall=0,
        discovery_precision=0,
    )

    assert repository.persist_qualification(
        session_date=SESSION,
        observed_at=T0,
        evidence=evidence,
    ) is True
    assert repository.persist_qualification(
        session_date=SESSION,
        observed_at=T0 + timedelta(minutes=5),
        evidence=evidence,
    ) is False
    assert len(ledger.events) == 1
