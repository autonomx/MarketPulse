from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

"""Runtime services for causal interday discovery, replay, and evidence.

The public owner modules call these services explicitly. This module does not
rewrite module globals or install import-time behavior.
"""

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean, stdev
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence, cast

from . import strategy_dynamic_discovery as dd
from .strategy_repository import StrategyEvent, TradingStrategyRepository, default_strategy_repository
from app.apps.trading.us_equity_calendar import EASTERN as _ET

EVENT_OBSERVATION = "interday_discovery_observation"
EVENT_CAUSALITY_VIOLATION = "interday_discovery_causality_violation"
EVENT_COHORT = "interday_discovery_cohort"
EVENT_PARENT_EXPOSURE = "interday_parent_exposure"
EVENT_EXECUTION_ECONOMICS = "interday_discovery_execution_economics"

_MARKET_TRIGGERS = {
    dd.DiscoveryTriggerType.MARKET_ANOMALY,
    dd.DiscoveryTriggerType.EXTREME_ATTENTION,
    dd.DiscoveryTriggerType.UNEXPLAINED_ATTENTION_SPIKE,
}
_ARM_TOP_K = 10


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return value.astimezone(timezone.utc)


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _semantic_level(value: Any, *, unknown: float = 50.0) -> float:
    if value is None:
        return unknown
    text = str(value).strip().lower()
    mapping = {
        "none": 0.0,
        "very_low": 10.0,
        "low": 25.0,
        "medium": 50.0,
        "moderate": 50.0,
        "high": 75.0,
        "very_high": 90.0,
        "extreme": 100.0,
        "minutes": 25.0,
        "hours": 60.0,
        "session": 82.0,
        "multi_day": 100.0,
        "weak": 25.0,
        "mixed": 50.0,
        "durable": 85.0,
        "persistent": 90.0,
        "unknown": unknown,
        "unresolved": unknown,
    }
    if text in mapping:
        return mapping[text]
    try:
        return _clip(float(value))
    except (TypeError, ValueError):
        return unknown


def _current_candidate(value: dd.DynamicCandidate) -> dd.DynamicCandidate:
    payload = value.model_dump(mode="python")
    payload["peak_attention_score"] = max(
        value.peak_attention_score, value.attention_score
    )
    payload["peak_catalyst_score"] = max(
        value.peak_catalyst_score, value.catalyst_score
    )
    payload["catalyst_raw_score"] = max(
        value.catalyst_raw_score, value.catalyst_score
    )
    return dd.DynamicCandidate.model_validate(payload)


def _experiment_state(
    candidate: dd.DynamicCandidate,
    arm: dd.DiscoveryExperimentArm,
) -> dd.ExperimentCandidateState | None:
    raw = candidate.experiment_states.get(arm.value)
    if raw is None:
        return None
    return (
        raw
        if isinstance(raw, dd.ExperimentCandidateState)
        else dd.ExperimentCandidateState.model_validate(raw)
    )


def _update_experiment_state(
    state: dd.ExperimentCandidateState | None,
    *,
    arm: dd.DiscoveryExperimentArm,
    observed_at: datetime,
    current_priority: float,
    config: dd.DynamicDiscoveryConfig,
    admitted: bool = False,
) -> dd.ExperimentCandidateState:
    now = _utc(observed_at)
    priority = _clip(current_priority)
    if state is None:
        return dd.ExperimentCandidateState(
            arm=arm,
            first_seen_at=now,
            last_observed_at=now,
            current_priority=priority,
            peak_priority=priority,
            lifecycle=dd.CandidateLifecycleState.ACTIVE,
            below_retention_since=None if priority >= config.retention_score else now,
        )

    below = state.below_retention_since
    lifecycle = state.lifecycle
    expired_at = state.expired_at
    if priority >= config.retention_score:
        lifecycle = dd.CandidateLifecycleState.ACTIVE
        below = None
        expired_at = None
    else:
        below = below or now
        weak_age = now - below
        if weak_age >= timedelta(minutes=config.expire_after_minutes):
            lifecycle = dd.CandidateLifecycleState.EXPIRED
            expired_at = now
        elif weak_age >= timedelta(minutes=config.cooling_after_minutes):
            lifecycle = dd.CandidateLifecycleState.COOLING
        elif admitted and lifecycle == dd.CandidateLifecycleState.EXPIRED:
            lifecycle = dd.CandidateLifecycleState.ACTIVE
            expired_at = None
    return state.model_copy(
        update={
            "last_observed_at": now,
            "current_priority": priority,
            "peak_priority": max(state.peak_priority, priority),
            "lifecycle": lifecycle,
            "below_retention_since": below,
            "expired_at": expired_at,
        }
    )


def _duration_from_event(event: dd.DiscoveryEvent) -> str | None:
    payload = event.payload
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        value = snapshot.get("expected_attention_duration")
        return str(value) if value is not None else None
    value = payload.get("expected_attention_duration")
    return str(value) if value is not None else None


def _snapshot_from_event(event: dd.DiscoveryEvent) -> dict[str, Any] | None:
    value = event.payload.get("snapshot")
    return dict(value) if isinstance(value, dict) else None


def _complete_catalyst_event(
    instrument_id: str,
    intelligence: object,
    *,
    session_date: date,
    observed_at: datetime,
    source: str = "catalyst_intelligence_v2",
    source_locator: str | None = None,
    config: dd.DynamicDiscoveryConfig = dd.DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> dd.DiscoveryEvent | None:
    event = dd.catalyst_discovery_event(
        instrument_id,
        intelligence,
        session_date=session_date,
        observed_at=observed_at,
        source=source,
        source_locator=source_locator,
        config=config,
    )
    if event is None:
        return None
    if hasattr(intelligence, "model_dump"):
        snapshot = intelligence.model_dump(mode="json")
    elif isinstance(intelligence, Mapping):
        snapshot = dict(intelligence)
    else:
        snapshot = {
            key: value
            for key, value in vars(intelligence).items()
            if not key.startswith("_")
        }
    return event.model_copy(
        update={
            "payload": {
                **event.payload,
                "snapshot": snapshot,
                "expected_attention_duration": snapshot.get(
                    "expected_attention_duration"
                ),
            }
        }
    )


def _complete_merge_discovery_event(
    current: dd.DynamicCandidate | None,
    event: dd.DiscoveryEvent,
) -> dd.DynamicCandidate:
    config = dd.DEFAULT_DYNAMIC_DISCOVERY_CONFIG
    if current is not None and (
        current.instrument_id != event.instrument_id
        or current.session_date != event.session_date
    ):
        raise ValueError("discovery_event_candidate_mismatch")
    candidate = _current_candidate(current) if current is not None else None
    now = event.discovered_at

    triggers = tuple(
        dict.fromkeys(
            (*((candidate.trigger_types if candidate else ())), event.trigger_type)
        )
    )
    arms = tuple(
        dict.fromkeys(
            (
                *((candidate.experiment_arms if candidate else ())),
                *dd.event_experiment_arms(event),
            )
        )
    )
    first_seen = (
        min(candidate.first_seen_at, event.causal_as_of)
        if candidate is not None
        else event.causal_as_of
    )
    discovered = (
        min(candidate.discovered_at, event.discovered_at)
        if candidate is not None
        else event.discovered_at
    )

    attention = candidate.attention_score if candidate is not None else 0.0
    peak_attention = (
        candidate.peak_attention_score if candidate is not None else 0.0
    )
    catalyst_raw = (
        candidate.catalyst_raw_score if candidate is not None else 0.0
    )
    catalyst_current = candidate.catalyst_score if candidate is not None else 0.0
    peak_catalyst = (
        candidate.peak_catalyst_score if candidate is not None else 0.0
    )
    catalyst_seen = (
        candidate.catalyst_last_seen_at if candidate is not None else None
    )
    duration = (
        candidate.catalyst_expected_attention_duration
        if candidate is not None
        else None
    )
    snapshot = candidate.catalyst_snapshot if candidate is not None else None
    latest_candidate_payload = (
        candidate.latest_candidate_payload if candidate is not None else None
    )

    if event.trigger_type in _MARKET_TRIGGERS:
        attention = event.attention_score
        peak_attention = max(peak_attention, event.attention_score)
    if event.trigger_type == dd.DiscoveryTriggerType.CATALYST_DISCOVERY_EVENT:
        catalyst_raw = event.catalyst_score
        catalyst_current = event.catalyst_score
        peak_catalyst = max(peak_catalyst, event.catalyst_score)
        catalyst_seen = now
        duration = _duration_from_event(event)
        snapshot = _snapshot_from_event(event)
    event_candidate = event.payload.get("candidate")
    if isinstance(event_candidate, dict):
        latest_candidate_payload = dict(event_candidate)

    common = round(
        _clip(
            max(
                attention,
                catalyst_current,
                attention * 0.65 + catalyst_current * 0.55,
            )
        ),
        4,
    )
    states = dict(candidate.experiment_states) if candidate is not None else {}
    if event.trigger_type in _MARKET_TRIGGERS:
        arm = dd.DiscoveryExperimentArm.MARKET_ONLY
        states[arm.value] = _update_experiment_state(
            _experiment_state(candidate, arm) if candidate is not None else None,
            arm=arm,
            observed_at=now,
            current_priority=attention,
            config=config,
            admitted=True,
        )
    if event.trigger_type == dd.DiscoveryTriggerType.CATALYST_DISCOVERY_EVENT:
        arm = dd.DiscoveryExperimentArm.CATALYST_ONLY
        states[arm.value] = _update_experiment_state(
            _experiment_state(candidate, arm) if candidate is not None else None,
            arm=arm,
            observed_at=now,
            current_priority=catalyst_current,
            config=config,
            admitted=True,
        )
    combined_arm = dd.DiscoveryExperimentArm.COMBINED
    states[combined_arm.value] = _update_experiment_state(
        _experiment_state(candidate, combined_arm) if candidate is not None else None,
        arm=combined_arm,
        observed_at=now,
        current_priority=common,
        config=config,
        admitted=True,
    )

    return dd.DynamicCandidate(
        session_date=event.session_date,
        instrument_id=event.instrument_id,
        first_seen_at=first_seen,
        discovered_at=discovered,
        last_observed_at=max(candidate.last_observed_at, now) if candidate else now,
        lifecycle=dd.CandidateLifecycleState.ACTIVE,
        tier=(
            candidate.tier
            if candidate is not None
            and candidate.lifecycle != dd.CandidateLifecycleState.EXPIRED
            else dd.EvaluationTier.WATCH
        ),
        trigger_types=triggers,
        experiment_arms=arms,
        attention_score=attention,
        catalyst_score=catalyst_current,
        common_priority=common,
        strategy_ranks=candidate.strategy_ranks if candidate else {},
        characterization=candidate.characterization if candidate else None,
        cooling_since=candidate.cooling_since if candidate else None,
        expired_at=None,
        peak_attention_score=peak_attention,
        peak_catalyst_score=peak_catalyst,
        catalyst_raw_score=catalyst_raw,
        catalyst_last_seen_at=catalyst_seen,
        catalyst_expected_attention_duration=duration,
        catalyst_snapshot=snapshot,
        latest_candidate_payload=latest_candidate_payload,
        below_retention_since=(
            candidate.below_retention_since if candidate else None
        ),
        experiment_states=states,
        selected_for_strategies=(
            candidate.selected_for_strategies if candidate else ()
        ),
    )


def _decayed_catalyst(
    candidate: dd.DynamicCandidate,
    *,
    at: datetime,
    market_priority: float,
) -> float:
    if candidate.catalyst_raw_score <= 0 or candidate.catalyst_last_seen_at is None:
        return 0.0
    elapsed = max(
        0.0,
        (_utc(at) - candidate.catalyst_last_seen_at).total_seconds() / 60.0,
    )
    multiplier = dd.catalyst_decay_multiplier(
        candidate.catalyst_expected_attention_duration,
        elapsed_minutes=elapsed,
        continued_abnormal_participation=market_priority >= 65.0,
        strong_market_confirmation=market_priority >= 55.0,
    )
    return round(candidate.catalyst_raw_score * multiplier, 4)


def _advance_complete_candidate(
    candidate: dd.DynamicCandidate,
    *,
    observed_at: datetime,
    current_market_priority: float | None,
    config: dd.DynamicDiscoveryConfig,
) -> dd.DynamicCandidate:
    now = _utc(observed_at)
    current_market = (
        candidate.attention_score
        if current_market_priority is None
        else _clip(current_market_priority)
    )
    current_catalyst = _decayed_catalyst(
        candidate, at=now, market_priority=current_market
    )
    common = round(
        _clip(
            max(
                current_market,
                current_catalyst,
                current_market * 0.65 + current_catalyst * 0.55,
            )
        ),
        4,
    )

    below = candidate.below_retention_since
    lifecycle = candidate.lifecycle
    cooling_since = candidate.cooling_since
    expired_at = candidate.expired_at
    if common >= config.retention_score:
        lifecycle = dd.CandidateLifecycleState.ACTIVE
        below = None
        cooling_since = None
        expired_at = None
    else:
        below = below or now
        weak_age = now - below
        if weak_age >= timedelta(minutes=config.expire_after_minutes):
            lifecycle = dd.CandidateLifecycleState.EXPIRED
            expired_at = now
        elif weak_age >= timedelta(minutes=config.cooling_after_minutes):
            lifecycle = dd.CandidateLifecycleState.COOLING
            cooling_since = cooling_since or now

    states = dict(candidate.experiment_states)
    for arm, priority in (
        (dd.DiscoveryExperimentArm.MARKET_ONLY, current_market),
        (dd.DiscoveryExperimentArm.CATALYST_ONLY, current_catalyst),
        (dd.DiscoveryExperimentArm.COMBINED, common),
    ):
        prior = _experiment_state(candidate, arm)
        if prior is None:
            continue
        states[arm.value] = _update_experiment_state(
            prior,
            arm=arm,
            observed_at=now,
            current_priority=priority,
            config=config,
        )
    return candidate.model_copy(
        update={
            "last_observed_at": max(candidate.last_observed_at, now),
            "attention_score": current_market,
            "peak_attention_score": max(
                candidate.peak_attention_score, current_market
            ),
            "catalyst_score": current_catalyst,
            "peak_catalyst_score": max(
                candidate.peak_catalyst_score, current_catalyst
            ),
            "common_priority": common,
            "lifecycle": lifecycle,
            "below_retention_since": below,
            "cooling_since": cooling_since,
            "expired_at": expired_at,
            "tier": (
                dd.EvaluationTier.EXPIRED
                if lifecycle == dd.CandidateLifecycleState.EXPIRED
                else candidate.tier
            ),
            "experiment_states": states,
        }
    )


def _tier_from_index(index: int, config: dd.DynamicDiscoveryConfig) -> dd.EvaluationTier:
    if index < config.tier_a_count:
        return dd.EvaluationTier.A
    if index < config.tier_a_count + config.tier_b_count:
        return dd.EvaluationTier.B
    return dd.EvaluationTier.WATCH


def _tier_complete_candidates(
    candidates: Sequence[dd.DynamicCandidate],
    *,
    config: dd.DynamicDiscoveryConfig = dd.DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> tuple[dd.DynamicCandidate, ...]:
    rows = list(candidates)
    states_by_symbol: dict[str, dict[str, dd.ExperimentCandidateState]] = {
        row.instrument_id: dict(row.experiment_states) for row in rows
    }
    for arm in (
        dd.DiscoveryExperimentArm.MARKET_ONLY,
        dd.DiscoveryExperimentArm.CATALYST_ONLY,
        dd.DiscoveryExperimentArm.COMBINED,
    ):
        eligible: list[tuple[dd.DynamicCandidate, dd.ExperimentCandidateState]] = []
        for row in rows:
            state = _experiment_state(row, arm)
            if state is None or state.lifecycle == dd.CandidateLifecycleState.EXPIRED:
                continue
            eligible.append((row, state))
        eligible.sort(
            key=lambda pair: (
                -pair[1].current_priority,
                pair[1].first_seen_at,
                pair[0].instrument_id,
            )
        )
        for index, (row, state) in enumerate(eligible):
            tier = (
                _tier_from_index(index, config)
                if index < config.max_active_candidates
                else dd.EvaluationTier.WATCH
            )
            states_by_symbol[row.instrument_id][arm.value] = state.model_copy(
                update={"tier": tier}
            )

    result: list[dd.DynamicCandidate] = []
    for row in rows:
        combined = states_by_symbol[row.instrument_id].get(
            dd.DiscoveryExperimentArm.COMBINED.value
        )
        tier = (
            dd.EvaluationTier.EXPIRED
            if row.lifecycle == dd.CandidateLifecycleState.EXPIRED
            else combined.tier
            if combined is not None
            else dd.EvaluationTier.WATCH
        )
        result.append(
            row.model_copy(
                update={
                    "tier": tier,
                    "experiment_states": states_by_symbol[row.instrument_id],
                }
            )
        )
    result.sort(
        key=lambda row: (
            row.lifecycle == dd.CandidateLifecycleState.EXPIRED,
            -row.common_priority,
            row.discovered_at,
            row.instrument_id,
        )
    )
    return tuple(result)


def _leader_fallback_event(observation, *, config: dd.DynamicDiscoveryConfig):
    if (
        observation.market is None
        or observation.source != "finviz_live_leaders"
        or abs(observation.market.gap_pct) < 5.0
    ):
        return None
    score = max(35.0, dd.market_attention_score(observation.market))
    trigger = dd.DiscoveryTriggerType.MARKET_ANOMALY
    event = dd.DiscoveryEvent(
        event_id=_key(
            observation.instrument_id,
            trigger.value,
            observation.observed_at.isoformat(),
            observation.source,
        )[:32],
        session_date=observation.session_date,
        instrument_id=observation.instrument_id,
        discovered_at=observation.observed_at,
        trigger_type=trigger,
        source=observation.source,
        source_locator=observation.source_locator,
        causal_as_of=observation.observed_at,
        attention_score=score,
        unexplained_attention=not observation.catalyst_known,
        payload={
            "leaderboard_membership": True,
            "features": observation.market.model_dump(mode="json"),
        },
    )
    if observation.candidate_payload is not None:
        event = event.model_copy(
            update={
                "payload": {
                    **event.payload,
                    "candidate": observation.candidate_payload,
                }
            }
        )
    return event


def _events_from_observation(observation, *, config: dd.DynamicDiscoveryConfig):
    events: list[dd.DiscoveryEvent] = []
    if observation.market is not None:
        market = dd.market_discovery_event(
            observation.instrument_id,
            observation.market,
            session_date=observation.session_date,
            source=observation.source,
            source_locator=observation.source_locator,
            catalyst_known=observation.catalyst_known,
            config=config,
        )
        if market is None:
            market = _leader_fallback_event(observation, config=config)
        elif observation.candidate_payload is not None:
            market = market.model_copy(
                update={
                    "payload": {
                        **market.payload,
                        "candidate": observation.candidate_payload,
                    }
                }
            )
        if market is not None:
            events.append(market)
    if observation.catalyst_payload is not None:
        payload = observation.catalyst_payload
        intelligence = SimpleNamespace(**payload)
        catalyst = _complete_catalyst_event(
            observation.instrument_id,
            intelligence,
            session_date=observation.session_date,
            observed_at=observation.observed_at,
            source=observation.source,
            source_locator=observation.source_locator,
            config=config,
        )
        if catalyst is not None:
            events.append(catalyst)
    return tuple(events)


def apply_discovery_scan(
    *,
    previous_state: Mapping[str, dd.DynamicCandidate],
    observations: Sequence[Any],
    watermark: datetime,
    session_date: date,
    config: dd.DynamicDiscoveryConfig = dd.DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> dd.DiscoveryScanResult:
    """One deterministic state transition used by live discovery and replay."""

    now = _utc(watermark)
    state = {
        symbol: _current_candidate(candidate)
        for symbol, candidate in previous_state.items()
    }
    emitted: list[dd.DiscoveryEvent] = []
    violations: list[dd.DiscoveryScanViolation] = []
    latest: dict[str, Any] = {}

    ordered = sorted(
        observations,
        key=lambda row: (row.observed_at, row.source, row.instrument_id),
    )
    for observation in ordered:
        if observation.session_date != session_date:
            violations.append(
                dd.DiscoveryScanViolation(
                    instrument_id=observation.instrument_id,
                    source=observation.source,
                    observed_at=observation.observed_at,
                    watermark=now,
                    reason="SESSION_DATE_MISMATCH",
                )
            )
            continue
        if observation.observed_at > now:
            violations.append(
                dd.DiscoveryScanViolation(
                    instrument_id=observation.instrument_id,
                    source=observation.source,
                    observed_at=observation.observed_at,
                    watermark=now,
                    reason="FUTURE_OBSERVATION",
                )
            )
            continue
        latest[observation.instrument_id] = observation
        events = _events_from_observation(observation, config=config)
        for event in events:
            if event.causal_as_of > now or event.discovered_at > now:
                violations.append(
                    dd.DiscoveryScanViolation(
                        instrument_id=event.instrument_id,
                        source=event.source,
                        observed_at=event.discovered_at,
                        watermark=now,
                        reason="FUTURE_DISCOVERY_EVENT",
                    )
                )
                continue
            state[event.instrument_id] = _complete_merge_discovery_event(
                state.get(event.instrument_id), event
            )
            emitted.append(event)

        # Candidate evidence can become available after catalyst-first admission
        # even when the market observation itself is below a new-admission threshold.
        if (
            observation.instrument_id in state
            and observation.candidate_payload is not None
        ):
            current = state[observation.instrument_id]
            state[observation.instrument_id] = current.model_copy(
                update={
                    "latest_candidate_payload": observation.candidate_payload,
                    "last_observed_at": max(
                        current.last_observed_at, observation.observed_at
                    ),
                }
            )

    for symbol, candidate in tuple(state.items()):
        observation = latest.get(symbol)
        market_priority = (
            dd.market_attention_score(observation.market)
            if observation is not None and observation.market is not None
            else 0.0
        )
        state[symbol] = _advance_complete_candidate(
            candidate,
            observed_at=now,
            current_market_priority=market_priority,
            config=config,
        )

    tiered = _tier_complete_candidates(tuple(state.values()), config=config)
    cohorts: dict[str, tuple[str, ...]] = {}
    for arm in (
        dd.DiscoveryExperimentArm.MARKET_ONLY,
        dd.DiscoveryExperimentArm.CATALYST_ONLY,
        dd.DiscoveryExperimentArm.COMBINED,
    ):
        members = []
        for row in tiered:
            state_row = _experiment_state(row, arm)
            if (
                state_row is not None
                and state_row.lifecycle != dd.CandidateLifecycleState.EXPIRED
            ):
                members.append(row.instrument_id)
        cohorts[arm.value] = tuple(members)

    return dd.DiscoveryScanResult(
        session_date=session_date,
        watermark=now,
        candidates=tiered,
        events=tuple(emitted),
        violations=tuple(violations),
        latest_observations={
            symbol: observation.model_dump(mode="json")
            for symbol, observation in latest.items()
        },
        experiment_cohorts=cohorts,
    )


def _neutral_market_structure():
    return SimpleNamespace(confirmation_score=0.5)


def _snapshot_object(candidate: dd.DynamicCandidate):
    if not candidate.catalyst_snapshot:
        return None
    try:
        from .strategy_ai_shadow_v2 import CatalystIntelligenceSnapshot

        return CatalystIntelligenceSnapshot.model_validate(
            candidate.catalyst_snapshot
        )
    except Exception:
        return SimpleNamespace(**candidate.catalyst_snapshot)


def _market_structure_for(instrument_id: str, market_service, observed_at: datetime):
    try:
        response = market_service.bars(instrument_id, "1m", 240, None)
        bars = [
            bar
            for bar in list(getattr(response, "bars", ()) or ())
            if bool(getattr(bar, "is_final", False))
            and bar.end_time <= observed_at
            and bar.start_time.astimezone(_ET).date()
            == observed_at.astimezone(_ET).date()
        ]
        if len(bars) < 2:
            return _neutral_market_structure()
        from .strategy_ai_shadow_v2 import build_market_structure_snapshot

        snapshot = build_market_structure_snapshot(bars)
        return SimpleNamespace(
            confirmation_score=_market_confirmation_from_snapshot(snapshot),
            snapshot=snapshot,
        )
    except Exception:
        return _neutral_market_structure()


def _market_confirmation_from_snapshot(snapshot: object) -> float:
    price = _float(getattr(snapshot, "current_price", 0.0))
    vwap = _float(getattr(snapshot, "vwap", 0.0))
    ema20 = _float(getattr(snapshot, "ema20", 0.0))
    ema50 = _float(getattr(snapshot, "ema50", 0.0))
    score = 0.5
    if price > 0 and vwap > 0:
        score += 0.15 if price >= vwap else -0.15
    if price > 0 and ema20 > 0:
        score += 0.10 if price >= ema20 else -0.10
    if ema20 > 0 and ema50 > 0:
        score += 0.10 if ema20 >= ema50 else -0.10
    return max(0.0, min(1.0, score))


def _session_return(market_service, instrument_id: str, observed_at: datetime) -> float | None:
    try:
        response = market_service.bars(instrument_id, "1m", 480, None)
        bars = [
            bar
            for bar in list(getattr(response, "bars", ()) or ())
            if bool(getattr(bar, "is_final", False))
            and bar.end_time <= observed_at
            and bar.start_time.astimezone(_ET).date()
            == observed_at.astimezone(_ET).date()
        ]
        if not bars:
            return None
        first = float(bars[0].open)
        last = float(bars[-1].close)
        return (last / first - 1.0) * 100.0 if first > 0 else None
    except Exception:
        logger.debug("suppressed error in %s", "_session_return", exc_info=True)
        return None


def _market_context(market_service, observed_at: datetime):
    return dd.MarketContext(
        observed_at=observed_at,
        spy_return_pct=_session_return(
            market_service, "equity:ARCA:SPY", observed_at
        ),
        iwm_return_pct=_session_return(
            market_service, "equity:ARCA:IWM", observed_at
        ),
    )


def _relationships_from_snapshot(candidate: dd.DynamicCandidate):
    snapshot = candidate.catalyst_snapshot or {}
    values = snapshot.get("relationships")
    if not isinstance(values, list):
        return ()
    rows = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(dd.RelationshipExposure.model_validate(item))
        except Exception:
            logger.debug("suppressed error in %s", "_relationships_from_snapshot", exc_info=True)
            continue
    return tuple(rows)


def _build_complete_characterization(
    candidate: dd.DynamicCandidate,
    *,
    observed_at: datetime,
    observation: object | None,
    market_service,
):
    catalyst = _snapshot_object(candidate)
    structure = _market_structure_for(
        candidate.instrument_id, market_service, observed_at
    )
    context = _market_context(market_service, observed_at)
    relationships = _relationships_from_snapshot(candidate)
    execution_quality = 50.0
    market = getattr(observation, "market", None) if observation is not None else None
    if market is not None and market.spread_bps is not None:
        execution_quality = max(
            0.0, min(100.0, 100.0 - market.spread_bps / 2.0)
        )

    # Unknown catalyst risk is neutral, not benign. A real snapshot may override it.
    if catalyst is None:
        catalyst = SimpleNamespace(
            catalyst_strength=None,
            expected_attention_duration=None,
            intraday_persistence_class=None,
            fundamental_materiality=None,
            materiality_to_company_size=None,
            event_certainty=None,
            supply_pressure="unknown",
            promotional_risk="unknown",
        )
    return _complete_opportunity_characterization(
        candidate,
        observed_at=observed_at,
        catalyst=catalyst,
        market_structure=structure,
        execution_quality=execution_quality,
        market_context=context,
        relationships=relationships,
    )


def _complete_opportunity_characterization(
    candidate: dd.DynamicCandidate,
    *,
    observed_at: datetime,
    catalyst: object | None = None,
    market_structure: object | None = None,
    execution_quality: float = 50.0,
    market_context: dd.MarketContext | None = None,
    relationships: Sequence[dd.RelationshipExposure] = (),
):
    confirmation_raw = _float(
        getattr(market_structure, "confirmation_score", 0.5), 0.5
    )
    confirmation = _clip(
        confirmation_raw * 100.0
        if confirmation_raw <= 1.0
        else confirmation_raw
    )
    catalyst_strength = _semantic_level(
        getattr(catalyst, "catalyst_strength", None)
    )
    persistence = max(
        _semantic_level(
            getattr(catalyst, "expected_attention_duration", None)
        ),
        _semantic_level(
            getattr(catalyst, "intraday_persistence_class", None)
        ),
    )
    materiality = max(
        _semantic_level(getattr(catalyst, "fundamental_materiality", None)),
        _semantic_level(
            getattr(catalyst, "materiality_to_company_size", None)
        ),
    )
    certainty = _semantic_level(getattr(catalyst, "event_certainty", None))
    supply = _semantic_level(
        getattr(catalyst, "supply_pressure", None), unknown=50.0
    )
    promo = _semantic_level(
        getattr(catalyst, "promotional_risk", None), unknown=50.0
    )
    context_score = 50.0
    if market_context is not None:
        components = [
            value
            for value in (
                market_context.sector_return_pct,
                market_context.industry_return_pct,
                market_context.peer_median_return_pct,
                market_context.iwm_return_pct,
                market_context.spy_return_pct,
            )
            if value is not None
        ]
        if components:
            context_score = _clip(
                50.0 + sum(float(value) for value in components) * 1.5
            )
    relation = max(
        (
            item.relationship_strength
            * item.economic_exposure
            * 100.0
            for item in relationships
        ),
        default=0.0,
    )
    attention = candidate.attention_score
    continuation = _clip(
        attention * 0.34
        + persistence * 0.24
        + confirmation * 0.30
        + context_score * 0.12
        - supply * 0.20
    )
    failed_selloff = _clip(
        attention * 0.25
        + confirmation * 0.45
        + materiality * 0.20
        - supply * 0.15
        + execution_quality * 0.10
    )
    reversal = _clip(
        attention * 0.20
        + max(0.0, 60.0 - confirmation) * 0.35
        + context_score * 0.15
        + execution_quality * 0.10
    )
    squeeze = _clip(
        attention * 0.55 + confirmation * 0.15 - execution_quality * 0.05
    )
    gap_retention = _clip(
        materiality * 0.28
        + certainty * 0.22
        + persistence * 0.20
        + confirmation * 0.20
        - supply * 0.18
    )
    return dd.OpportunityCharacterization(
        instrument_id=candidate.instrument_id,
        observed_at=observed_at,
        attention_intensity=attention,
        catalyst_strength=catalyst_strength,
        catalyst_persistence=persistence,
        fundamental_materiality=materiality,
        event_certainty=certainty,
        supply_pressure=supply,
        promotional_risk=promo,
        continuation_prior=continuation,
        failed_selloff_prior=failed_selloff,
        reversal_prior=reversal,
        squeeze_potential=squeeze,
        gap_retention_prior=gap_retention,
        market_confirmation=confirmation,
        execution_quality=_clip(execution_quality),
        market_context_confirmation=context_score,
        relationship_exposure=_clip(relation),
    )


def _complete_strategy_rankings(
    candidates: Sequence[dd.DynamicCandidate],
) -> tuple[dd.DynamicCandidate, ...]:
    rows = [_current_candidate(item) for item in candidates]
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    selected: dict[str, set[str]] = defaultdict(set)
    eligible = [
        item
        for item in rows
        if item.characterization is not None
        and item.lifecycle != dd.CandidateLifecycleState.EXPIRED
    ]
    for arm in dd.INTERDAY_SUBSTRATEGIES:
        ordered = sorted(
            eligible,
            key=lambda item: (
                -dd.strategy_specific_score(arm, cast(dd.OpportunityCharacterization, item.characterization)),
                item.discovered_at,
                item.instrument_id,
            ),
        )
        for index, item in enumerate(ordered, start=1):
            ranks[item.instrument_id][arm] = index
            if index <= _ARM_TOP_K:
                selected[item.instrument_id].add(arm)
    return tuple(
        item.model_copy(
            update={
                "strategy_ranks": ranks.get(item.instrument_id, {}),
                "selected_for_strategies": tuple(
                    sorted(selected.get(item.instrument_id, set()))
                ),
            }
        )
        for item in rows
    )


def _append_parent_event(
    repository: TradingStrategyRepository,
    *,
    event_type: str,
    session_date: date,
    observed_at: datetime,
    instrument_id: str,
    state: str,
    payload: dict[str, Any],
    reason_code: str | None = None,
    identity: Sequence[Any] = (),
) -> bool:
    event_id = _key(
        event_type,
        session_date.isoformat(),
        instrument_id,
        *identity,
    )[:32]
    return repository.append_event(
        StrategyEvent(
            strategy_id=dd.INTERDAY_TRADING_STRATEGY_ID,
            event_id=event_id,
            run_id=f"interday-discovery:{session_date.isoformat()}",
            instrument_id=instrument_id,
            event_type=event_type,
            state=state,
            reason_code=reason_code,
            observed_at=observed_at,
            idempotency_key=f"{event_type}:{event_id}",
            payload=payload,
        )
    )


def _persist_observation(
    repository: TradingStrategyRepository,
    observation,
    *,
    watermark: datetime,
) -> bool:
    payload = {
        "observation": observation.model_dump(mode="json"),
        "scan_watermark": _utc(watermark).isoformat(),
    }
    fingerprint = _key(
        observation.instrument_id,
        observation.source,
        observation.observed_at.isoformat(),
        json.dumps(payload["observation"], sort_keys=True, default=str),
    )
    return _append_parent_event(
        repository,
        event_type=EVENT_OBSERVATION,
        session_date=observation.session_date,
        observed_at=min(observation.observed_at, _utc(watermark)),
        instrument_id=observation.instrument_id,
        state="captured",
        payload=payload,
        identity=(fingerprint,),
    )


def _persist_violation(
    repository: TradingStrategyRepository,
    violation: dd.DiscoveryScanViolation,
    *,
    session_date: date,
) -> bool:
    return _append_parent_event(
        repository,
        event_type=EVENT_CAUSALITY_VIOLATION,
        session_date=session_date,
        observed_at=violation.watermark,
        instrument_id=violation.instrument_id,
        state="rejected",
        reason_code=violation.reason,
        payload=violation.model_dump(mode="json"),
        identity=(
            violation.source,
            violation.observed_at.isoformat(),
            violation.watermark.isoformat(),
            violation.reason,
        ),
    )


def _persist_cohorts(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
    observed_at: datetime,
    result: dd.DiscoveryScanResult,
    frozen_symbols: Sequence[str] = (),
) -> None:
    cohorts = {
        dd.DiscoveryExperimentArm.FROZEN.value: tuple(frozen_symbols),
        **result.experiment_cohorts,
    }
    for arm, symbols in cohorts.items():
        _append_parent_event(
            repository,
            event_type=EVENT_COHORT,
            session_date=session_date,
            observed_at=observed_at,
            instrument_id=f"portfolio:interday-discovery:{arm}",
            state="snapshot",
            payload={
                "arm": arm,
                "symbols": list(symbols),
                "watermark": observed_at.isoformat(),
                "research_only": True,
                "execution_authority": False,
            },
            identity=(arm, observed_at.isoformat()),
        )


async def _run_dynamic_discovery_once_complete(
    *,
    now: datetime | None = None,
    repository: TradingStrategyRepository | None = None,
    observations=None,
):
    import asyncio
    from . import strategy_dynamic_discovery_monitor as monitor
    from .strategy_discovery_acquisition import (
        capture_discovery_observations,
        install_default_discovery_sources,
    )
    from .strategy_dynamic_discovery_repository import (
        DynamicDiscoveryEventRepository,
    )
    from .strategy_shadow_universe import resolve_v2_evidence_archive_for_session
    from .service import default_market_data_service

    scan_started = _utc(now or datetime.now(timezone.utc))
    repo = repository or default_strategy_repository()
    try:
        parent = await asyncio.to_thread(
            repo.get_config, dd.INTERDAY_TRADING_STRATEGY_ID
        )
    except ValueError:
        return ()
    if not parent.enabled or parent.archived_at is not None:
        return ()

    supplied = observations is not None
    if observations is None:
        if not monitor._inside_discovery_window(scan_started):
            return ()
        install_default_discovery_sources()
        observations = await asyncio.to_thread(
            capture_discovery_observations,
            observed_at=scan_started,
        )
    observations = tuple(observations or ())
    watermark = (
        scan_started
        if supplied and now is not None
        else max(scan_started, datetime.now(timezone.utc))
    )
    session_date = (
        observations[0].session_date
        if observations
        else watermark.astimezone(_ET).date()
    )
    event_repo = DynamicDiscoveryEventRepository(repo)
    current = await asyncio.to_thread(
        event_repo.latest_candidates, session_date
    )
    for observation in observations:
        await asyncio.to_thread(
            _persist_observation, repo, observation, watermark=watermark
        )

    from .strategy_dynamic_discovery_quality import _apply_scan_refined

    scan = _apply_scan_refined(
        previous_state=current,
        observations=observations,
        watermark=watermark,
        session_date=session_date,
    )
    for violation in scan.violations:
        await asyncio.to_thread(
            _persist_violation, repo, violation, session_date=session_date
        )
    for event in scan.events:
        await asyncio.to_thread(event_repo.persist_discovery, event)

    latest_observation = {
        row.instrument_id: row
        for row in observations
        if row.observed_at <= watermark
    }
    market_service = default_market_data_service()
    characterized = []
    for candidate in scan.candidates:
        if candidate.lifecycle == dd.CandidateLifecycleState.EXPIRED:
            characterized.append(candidate)
            continue
        char = await asyncio.to_thread(
            _build_complete_characterization,
            candidate,
            observed_at=watermark,
            observation=latest_observation.get(candidate.instrument_id),
            market_service=market_service,
        )
        characterized.append(
            candidate.model_copy(update={"characterization": char})
        )
    ranked = _complete_strategy_rankings(tuple(characterized))

    prior = {
        symbol: _current_candidate(value)
        for symbol, value in current.items()
    }
    for candidate in ranked:
        if candidate != prior.get(candidate.instrument_id):
            await asyncio.to_thread(
                event_repo.persist_candidate,
                candidate,
                snapshot_at=watermark,
            )

    frozen_symbols: tuple[str, ...] = ()
    try:
        frozen = await asyncio.to_thread(
            resolve_v2_evidence_archive_for_session,
            parent,
            repo,
            session_date=session_date,
        )
        if frozen is not None:
            frozen_symbols = tuple(
                row.instrument_id for row in frozen.candidates
            )
    except Exception:
        logger.debug("suppressed error in %s", "_run_dynamic_discovery_once_complete", exc_info=True)
    await asyncio.to_thread(
        _persist_cohorts,
        repo,
        session_date=session_date,
        observed_at=watermark,
        result=scan,
        frozen_symbols=frozen_symbols,
    )
    return tuple(
        row
        for row in ranked
        if row.lifecycle != dd.CandidateLifecycleState.EXPIRED
    )


def _complete_evaluate_shadow_qualification(metrics: Mapping[str, Any]):
    sessions = int(metrics.get("independent_sessions", 0) or 0)
    opportunities = int(metrics.get("labeled_opportunities", 0) or 0)
    recall = _float(metrics.get("discovery_recall"))
    precision = _float(metrics.get("discovery_precision"))
    latency = metrics.get("median_discovery_latency_minutes")
    expectancy = metrics.get("execution_adjusted_expectancy_r")
    drawdown = metrics.get("max_drawdown_r")
    reliability = _float(metrics.get("data_reliability_fraction"))
    violations = int(metrics.get("causality_violations", 0) or 0)
    samples = int(metrics.get("execution_sample_count", 0) or 0)
    holdout_sessions = int(metrics.get("holdout_session_count", 0) or 0)
    lcb = metrics.get("expectancy_lcb_r")
    stressed = metrics.get("stressed_expectancy_r")
    holdout = metrics.get("holdout_expectancy_r")
    reasons = []
    if sessions < 20:
        reasons.append("insufficient_independent_sessions")
    if opportunities < 100:
        reasons.append("insufficient_labeled_opportunities")
    if recall < 0.70:
        reasons.append("discovery_recall_below_gate")
    if precision < 0.10:
        reasons.append("discovery_precision_below_gate")
    if samples < 20:
        reasons.append("insufficient_execution_samples")
    if holdout_sessions < 5:
        reasons.append("insufficient_holdout_sessions")
    if expectancy is None or _float(expectancy) <= 0:
        reasons.append("non_positive_execution_adjusted_expectancy")
    if lcb is None or _float(lcb) <= 0:
        reasons.append("expectancy_lower_bound_not_positive")
    if stressed is None or _float(stressed) <= 0:
        reasons.append("stressed_expectancy_not_positive")
    if holdout is None or _float(holdout) <= 0:
        reasons.append("holdout_expectancy_not_positive")
    if drawdown is None or abs(_float(drawdown)) > 5.0:
        reasons.append("max_drawdown_exceeds_gate")
    if reliability < 0.95:
        reasons.append("data_reliability_below_gate")
    if violations:
        reasons.append("causality_violations_present")
    eligible = not reasons
    return dd.ShadowQualificationEvidence(
        independent_sessions=sessions,
        labeled_opportunities=opportunities,
        discovery_recall=_clip(recall, 0.0, 1.0),
        discovery_precision=_clip(precision, 0.0, 1.0),
        median_discovery_latency_minutes=(
            _float(latency) if latency is not None else None
        ),
        execution_adjusted_expectancy_r=(
            _float(expectancy) if expectancy is not None else None
        ),
        max_drawdown_r=(
            _float(drawdown) if drawdown is not None else None
        ),
        data_reliability_fraction=_clip(reliability, 0.0, 1.0),
        causality_violations=violations,
        eligible_for_review=eligible,
        auto_paper_authorized=False,
        reasons=tuple(reasons),
        execution_sample_count=samples,
        holdout_session_count=holdout_sessions,
        expectancy_lcb_r=_float(lcb) if lcb is not None else None,
        stressed_expectancy_r=(
            _float(stressed) if stressed is not None else None
        ),
        holdout_expectancy_r=(
            _float(holdout) if holdout is not None else None
        ),
        evidence_complete=all(
            value is not None
            for value in (
                expectancy,
                drawdown,
                lcb,
                stressed,
                holdout,
            )
        ),
    )


def _one_sided_90_lcb(values: Sequence[float]) -> float | None:
    if not values:
        return None
    avg = mean(values)
    if len(values) < 2:
        return None
    sigma = stdev(values)
    return avg - 1.28155 * sigma / math.sqrt(len(values))


def _max_drawdown_r(values: Sequence[float]) -> float | None:
    if not values:
        return None
    equity = peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _execution_economics(
    repository: TradingStrategyRepository,
    *,
    dynamic_pairs: set[tuple[date, str]],
):
    events = repository.recent_events(
        dd.INTERDAY_TRADING_STRATEGY_ID, 50_000
    )
    rows: list[tuple[date, float]] = []
    for event in events:
        session = event.observed_at.astimezone(_ET).date()
        if (session, event.instrument_id) not in dynamic_pairs:
            continue
        if event.event_type == "v2_shadow_replay_trade":
            value = event.payload.get("r_result")
            if value is not None:
                try:
                    rows.append((session, float(value)))
                except (TypeError, ValueError):
                    pass
    values = [value for _, value in rows]
    sessions = sorted({session for session, _ in rows})
    holdout_count = max(5, math.ceil(len(sessions) * 0.20)) if sessions else 0
    holdout_sessions = set(sessions[-holdout_count:]) if len(sessions) >= 5 else set()
    holdout_values = [
        value for session, value in rows if session in holdout_sessions
    ]
    expectancy = mean(values) if values else None
    return {
        "execution_sample_count": len(values),
        "execution_adjusted_expectancy_r": expectancy,
        "max_drawdown_r": _max_drawdown_r(values),
        "expectancy_lcb_r": _one_sided_90_lcb(values),
        "stressed_expectancy_r": (
            expectancy - 0.05 if expectancy is not None else None
        ),
        "holdout_session_count": len(holdout_sessions),
        "holdout_expectancy_r": (
            mean(holdout_values) if holdout_values else None
        ),
    }


def _complete_trend_outcome_from_ohlc(
    instrument_id: str,
    *,
    discovery_at: datetime,
    reference_at: datetime,
    reference_price: float,
    bars: Sequence[Any],
    stop_fraction: float = 0.05,
    reference_mode: str = "discovery_close",
):
    ordered = sorted(
        [
            bar
            for bar in bars
            if bar.end_time >= reference_at
        ],
        key=lambda bar: bar.end_time,
    )
    if not ordered:
        return dd.TrendDurabilityOutcome(
            instrument_id=instrument_id,
            discovered_at=reference_at,
            discovery_at=discovery_at,
            tradeable_reference_at=reference_at,
            tradeable_reference_price=reference_price,
            reference_price=reference_price,
            reference_mode=reference_mode,
            normalized_stop_fraction=stop_fraction,
        )
    closes = [
        (
            bar.end_time,
            (float(bar.close) / reference_price - 1.0) * 100.0,
        )
        for bar in ordered
    ]
    high_returns = [
        (float(bar.high) / reference_price - 1.0) * 100.0 for bar in ordered
    ]
    low_returns = [
        (float(bar.low) / reference_price - 1.0) * 100.0 for bar in ordered
    ]
    one_r = stop_fraction * 100.0

    def hit_before(target: float) -> bool:
        for bar in ordered:
            low = (float(bar.low) / reference_price - 1.0) * 100.0
            high = (float(bar.high) / reference_price - 1.0) * 100.0
            if low <= -one_r:
                return False  # conservative same-bar ambiguity: stop first
            if high >= target:
                return True
        return False

    def horizon(minutes: int) -> float | None:
        target = reference_at + timedelta(minutes=minutes)
        eligible = [value for ts, value in closes if ts <= target]
        return eligible[-1] if eligible else None

    def survived(minutes: int) -> bool | None:
        value = horizon(minutes)
        return None if value is None else value > 0

    return dd.TrendDurabilityOutcome(
        instrument_id=instrument_id,
        discovered_at=reference_at,
        discovery_at=discovery_at,
        tradeable_reference_at=reference_at,
        tradeable_reference_price=reference_price,
        reference_price=reference_price,
        reference_mode=reference_mode,
        normalized_stop_fraction=stop_fraction,
        return_15m_pct=horizon(15),
        return_30m_pct=horizon(30),
        return_60m_pct=horizon(60),
        return_120m_pct=horizon(120),
        mfe_pct=max(high_returns),
        mae_pct=min(low_returns),
        plus_1r_before_minus_1r=hit_before(one_r),
        plus_2r_before_minus_1r=hit_before(2.0 * one_r),
        trend_survived_15m=survived(15),
        trend_survived_30m=survived(30),
        trend_survived_60m=survived(60),
        trend_survived_120m=survived(120),
    )


def _label_candidate_outcome_complete(
    market_service,
    candidate,
    *,
    observed_at: datetime,
):
    from . import strategy_interday_postclose as postclose

    response = market_service.bars(candidate.instrument_id, "1m", 500, None)
    bars = postclose._regular_session_bars(
        response,
        session_date=candidate.session_date,
        observed_at=observed_at,
    )
    if not bars:
        return None
    discovered = candidate.discovered_at.astimezone(timezone.utc)
    causal_reference = [
        bar
        for bar in bars
        if bar.end_time.astimezone(timezone.utc) <= discovered
    ]
    if causal_reference:
        ref_bar = causal_reference[-1]
        reference_price = float(ref_bar.close)
        reference_at = ref_bar.end_time.astimezone(timezone.utc)
        mode = "discovery_close"
    else:
        ref_bar = bars[0]
        reference_price = float(ref_bar.open)
        reference_at = ref_bar.start_time.astimezone(timezone.utc)
        mode = "regular_open_after_premarket_discovery"
    if reference_price <= 0:
        return None
    return _complete_trend_outcome_from_ohlc(
        candidate.instrument_id,
        discovery_at=discovered,
        reference_at=reference_at,
        reference_price=reference_price,
        bars=bars,
        reference_mode=mode,
    )


def _attribution_transition(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
    event: dd.AttributionEvent,
) -> bool:
    from .strategy_dynamic_discovery_repository import (
        DynamicDiscoveryEventRepository,
        EVENT_ATTRIBUTION,
    )

    event_repo = DynamicDiscoveryEventRepository(repository)
    prior = []
    for raw in event_repo.session_events(session_date):
        if raw.event_type != EVENT_ATTRIBUTION:
            continue
        try:
            prior.append(dd.AttributionEvent.model_validate(raw.payload))
        except Exception:
            logger.debug("suppressed error in %s", "_attribution_transition", exc_info=True)
            continue
    if not event.passed:
        return event_repo.persist_attribution(event)

    same = [
        row
        for row in prior
        if row.instrument_id == event.instrument_id
        and row.passed
        and (
            row.sub_strategy == event.sub_strategy
            or row.sub_strategy is None
        )
    ]
    order = {
        stage: index for index, stage in enumerate(dd.AttributionStage)
    }
    target = order[event.stage]
    highest = max((order[row.stage] for row in same), default=-1)
    if target > highest + 1:
        gap = event.model_copy(
            update={
                "passed": False,
                "reason": "ATTRIBUTION_PREDECESSOR_MISSING",
                "payload": {
                    **event.payload,
                    "requested_stage": event.stage.value,
                    "highest_successful_stage": (
                        None
                        if highest < 0
                        else list(dd.AttributionStage)[highest].value
                    ),
                },
            }
        )
        return event_repo.persist_attribution(gap)
    return event_repo.persist_attribution(event)


def _bridge_strategy_events_complete(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
    events_by_strategy: Mapping[str, Iterable[StrategyEvent]],
) -> int:
    from . import strategy_interday_attribution as attribution

    count = 0
    for source_strategy_id, events in events_by_strategy.items():
        for source in events:
            if (
                source.instrument_id.startswith("portfolio:")
                or source.instrument_id.startswith("__")
            ):
                continue
            stage = attribution.attribution_stage_for_strategy_event(source)
            if stage is None:
                if source.event_type == "shadow_execution":
                    execution = source.payload.get("execution")
                    if (
                        isinstance(execution, dict)
                        and execution.get("execution_eligible") is False
                    ):
                        stage = dd.AttributionStage.EXECUTION_ELIGIBLE
                    else:
                        continue
                else:
                    continue
            passed = True
            reason = source.reason_code
            if source.event_type == "shadow_execution":
                execution = source.payload.get("execution")
                if (
                    isinstance(execution, dict)
                    and execution.get("execution_eligible") is False
                ):
                    passed = False
                    rejection = execution.get("rejection_reasons")
                    reason = (
                        ",".join(str(item) for item in rejection)
                        if isinstance(rejection, (list, tuple))
                        else reason or "EXECUTION_INELIGIBLE"
                    )
            row = dd.AttributionEvent(
                session_date=session_date,
                instrument_id=source.instrument_id,
                stage=stage,
                observed_at=source.observed_at,
                sub_strategy=attribution._arm_for_event(
                    source, source_strategy_id
                ),
                passed=passed,
                reason=reason,
                payload={
                    "source_strategy_id": source_strategy_id,
                    "source_event_id": source.event_id,
                    "source_event_type": source.event_type,
                    "source_state": source.state,
                    "research_only": (
                        source.event_type.startswith("ai_")
                        or source.event_type.startswith("intraday_")
                    ),
                    "execution_authority": (
                        source.event_type == "entry_order_submitted"
                    ),
                },
            )
            if _attribution_transition(
                repository, session_date=session_date, event=row
            ):
                count += 1
    return count


def _dynamic_pairs_from_events(
    repository: TradingStrategyRepository,
) -> set[tuple[date, str]]:
    pairs = set()
    for event in repository.recent_events(
        dd.INTERDAY_TRADING_STRATEGY_ID, 50_000
    ):
        if event.event_type != "interday_discovery_event":
            continue
        try:
            trigger = str(event.payload.get("trigger_type") or "")
        except Exception:
            logger.debug("suppressed error in %s", "_dynamic_pairs_from_events", exc_info=True)
            continue
        if trigger == dd.DiscoveryTriggerType.FROZEN_BENCHMARK.value:
            continue
        pairs.add(
            (
                event.observed_at.astimezone(_ET).date(),
                event.instrument_id,
            )
        )
    return pairs


def _historical_quality(
    repository: TradingStrategyRepository,
):
    events = repository.recent_events(
        dd.INTERDAY_TRADING_STRATEGY_ID, 50_000
    )
    reports = {
        event.observed_at.astimezone(_ET).date(): event
        for event in events
        if event.event_type == "interday_discovery_daily_report"
    }
    total_candidates = total_labels = 0
    violations = 0
    replays = []
    for event in events:
        if event.event_type == EVENT_CAUSALITY_VIOLATION:
            violations += 1
        elif event.event_type == "interday_discovery_replay":
            if (
                event.payload.get("discovery_recall") is not None
                and event.payload.get("discovery_precision") is not None
            ):
                replays.append(event.payload)
        elif event.event_type == "interday_discovery_daily_report":
            total_candidates += int(event.payload.get("discovered_count", 0) or 0)
            total_labels += int(event.payload.get("durability_labeled_count", 0) or 0)
    true_positive = positive = discovered_labeled = 0
    latencies = []
    for replay in replays:
        discovered_count = int(
            replay.get("discovered_symbol_count", 0) or 0
        )
        false_positive = replay.get("false_positive_symbols") or ()
        missed = replay.get("missed_opportunity_symbols") or ()
        fp = len(false_positive) if isinstance(false_positive, (list, tuple)) else 0
        miss = len(missed) if isinstance(missed, (list, tuple)) else 0
        tp = max(0, discovered_count - fp)
        true_positive += tp
        positive += tp + miss
        discovered_labeled += discovered_count
        latency = replay.get("median_discovery_latency_minutes")
        if latency is not None:
            latencies.append(float(latency))
    reliability = (
        total_labels / total_candidates if total_candidates else 0.0
    )
    return {
        "independent_sessions": len(reports),
        "labeled_opportunities": max(positive, total_labels),
        "discovery_recall": (
            true_positive / positive if positive else 0.0
        ),
        "discovery_precision": (
            true_positive / discovered_labeled
            if discovered_labeled
            else 0.0
        ),
        "median_discovery_latency_minutes": (
            sorted(latencies)[len(latencies) // 2]
            if latencies
            else None
        ),
        "data_reliability_fraction": min(1.0, reliability),
        "causality_violations": violations,
    }


def _qualification_from_persisted_evidence_complete(
    repository: TradingStrategyRepository,
    *,
    current_report=None,
    data_reliability_fraction: float = 1.0,
    causality_violations: int = 0,
):
    quality = _historical_quality(repository)
    economics = _execution_economics(
        repository,
        dynamic_pairs=_dynamic_pairs_from_events(repository),
    )
    # Callers may supply stricter values, never overwrite measured failures.
    quality["data_reliability_fraction"] = min(
        _float(quality.get("data_reliability_fraction")),
        _float(data_reliability_fraction),
    )
    quality["causality_violations"] = max(
        int(quality.get("causality_violations", 0) or 0),
        int(causality_violations or 0),
    )
    return _complete_evaluate_shadow_qualification(
        {**quality, **economics}
    )


def _observations_for_session(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
):
    from .strategy_dynamic_discovery_repository import session_bounds
    from .strategy_discovery_acquisition import CausalMarketObservation

    start, end = session_bounds(session_date)
    rows = repository.events_by_types_between(
        dd.INTERDAY_TRADING_STRATEGY_ID,
        event_types=(EVENT_OBSERVATION,),
        start_time=start,
        end_time=end,
        limit=50_000,
    )
    result = []
    for row in rows:
        raw = row.payload.get("observation")
        if not isinstance(raw, dict):
            continue
        try:
            observation = CausalMarketObservation.model_validate(raw)
        except Exception:
            logger.debug("suppressed error in %s", "_observations_for_session", exc_info=True)
            continue
        result.append((row.payload.get("scan_watermark"), observation))
    return tuple(result)


def _persist_parent_exposure(
    repository: TradingStrategyRepository,
    *,
    session_date: date,
    parent_config,
    observed_at: datetime,
):
    from .strategy_dynamic_discovery_repository import (
        DynamicDiscoveryEventRepository,
        EVENT_ATTRIBUTION,
    )

    event_repo = DynamicDiscoveryEventRepository(repository)
    latest: dict[tuple[str, str], dd.AttributionEvent] = {}
    for event in event_repo.session_events(session_date):
        if event.event_type != EVENT_ATTRIBUTION:
            continue
        try:
            row = dd.AttributionEvent.model_validate(event.payload)
        except Exception:
            logger.debug("suppressed error in %s", "_persist_parent_exposure", exc_info=True)
            continue
        if (
            not row.passed
            or row.stage != dd.AttributionStage.SIGNALLED
            or not row.sub_strategy
        ):
            continue
        latest[(row.instrument_id, row.sub_strategy)] = row
    proposals = []
    desired = float(parent_config.risk.risk_per_trade_pct) / 100.0
    for (instrument_id, arm), row in latest.items():
        conviction = _float(
            row.payload.get("conviction")
            or row.payload.get("quality_score")
            or 0.5
        )
        if conviction > 1.0:
            conviction /= 100.0
        proposals.append(
            dd.ParentExposureProposal(
                instrument_id=instrument_id,
                sub_strategy=arm,
                desired_risk_fraction=desired,
                conviction=max(0.0, min(1.0, conviction)),
            )
        )
    allocations = dd.allocate_parent_exposure(proposals)
    for allocation in allocations:
        _append_parent_event(
            repository,
            event_type=EVENT_PARENT_EXPOSURE,
            session_date=session_date,
            observed_at=observed_at,
            instrument_id=allocation.instrument_id,
            state="shadow_allocation",
            payload={
                **allocation.model_dump(mode="json"),
                "research_only": True,
                "execution_authority": False,
            },
            identity=(
                allocation.instrument_id,
                observed_at.isoformat(),
            ),
        )
    return allocations


async def _run_interday_learning_once_complete(
    *,
    now: datetime | None = None,
    repository: TradingStrategyRepository | None = None,
    market_service=None,
):
    # First run the original monitor for bridging/outcomes/report persistence.
    from .strategy_interday_learning_monitor import (
        _run_interday_learning_once_core,
    )

    result = await _run_interday_learning_once_core(
        now=now,
        repository=repository,
        market_service=market_service,
    )
    observed_at = _utc(now or datetime.now(timezone.utc))
    if observed_at.astimezone(_ET).time().replace(tzinfo=None) < time(16, 10):
        return result
    repo = repository or default_strategy_repository()
    try:
        parent = repo.get_config(dd.INTERDAY_TRADING_STRATEGY_ID)
    except ValueError:
        return result
    from .strategy_interday_postclose import session_outcomes

    session_date = observed_at.astimezone(_ET).date()
    outcomes = session_outcomes(repo, session_date=session_date)
    from .strategy_dynamic_discovery_quality import (
        _persist_automatic_replay_refined,
    )

    _persist_automatic_replay_refined(
        repo,
        session_date=session_date,
        outcomes=outcomes,
        observed_at=observed_at,
    )
    _persist_parent_exposure(
        repo,
        session_date=session_date,
        parent_config=parent,
        observed_at=observed_at,
    )
    # Persist a versioned completeness qualification snapshot after replay/economics.
    evidence = _qualification_from_persisted_evidence_complete(
        repo,
        data_reliability_fraction=1.0,
        causality_violations=0,
    )
    _append_parent_event(
        repo,
        event_type="interday_discovery_qualification_v2",
        session_date=session_date,
        observed_at=observed_at,
        instrument_id="portfolio:interday-discovery",
        state=(
            "eligible_for_review"
            if evidence.eligible_for_review
            else "not_qualified"
        ),
        reason_code=(
            None
            if evidence.eligible_for_review
            else "SHADOW_EVIDENCE_GATE_NOT_MET"
        ),
        payload=evidence.model_dump(mode="json"),
        identity=(session_date.isoformat(), "v2"),
    )
    return result


def _union_complete(config, repository, frozen, *, session_date, observed_at=None):
    if (
        frozen is None
        or config.mode != "shadow"
        or not (
            config.strategy_id == dd.INTERDAY_TRADING_STRATEGY_ID
            or config.parent_strategy_id == dd.INTERDAY_TRADING_STRATEGY_ID
        )
    ):
        return frozen
    from .strategy_dynamic_discovery_repository import DynamicDiscoveryEventRepository
    from .gapper_dataset import GapperCandidate, freeze_gapper_universe

    event_repo = DynamicDiscoveryEventRepository(repository)
    latest = event_repo.latest_candidates(session_date)

    if config.strategy_id == dd.INTERDAY_TRADING_STRATEGY_ID:
        applicable = set(dd.INTERDAY_SUBSTRATEGIES[:4])
    else:
        applicable = {config.strategy_id}
    eligible = {
        symbol: _current_candidate(candidate)
        for symbol, candidate in latest.items()
        if candidate.lifecycle != dd.CandidateLifecycleState.EXPIRED
        and (
            bool(
                set(_current_candidate(candidate).selected_for_strategies)
                & applicable
            )
            or candidate.tier
            in {dd.EvaluationTier.A, dd.EvaluationTier.B}
        )
    }
    if not eligible:
        return frozen

    additions = []
    existing = {row.instrument_id for row in frozen.candidates}
    for symbol, candidate in eligible.items():
        if symbol in existing or not candidate.latest_candidate_payload:
            continue
        try:
            additions.append(
                GapperCandidate.model_validate(
                    candidate.latest_candidate_payload
                )
            )
        except Exception:
            logger.debug("suppressed error in %s", "_union_complete", exc_info=True)
            continue
    if not additions:
        return frozen
    evaluation_time = observed_at or max(
        [
            frozen.evaluation_time,
            *[
                row.observed_at
                for row in additions
                if row.observed_at is not None
            ],
        ]
    )
    return freeze_gapper_universe(
        universe_id=(
            f"{frozen.universe_id}-live-shadow-"
            f"{evaluation_time.astimezone(timezone.utc).strftime('%H%M%S')}"
        ),
        session_date=session_date,
        evaluation_time=evaluation_time,
        discovery_source="import",
        source_locator="omnix:interday-dynamic-discovery-complete",
        candidates=[*frozen.candidates, *additions],
    )


__all__ = [
    "apply_discovery_scan",
    "_bridge_strategy_events_complete",
    "_complete_catalyst_event",
    "_complete_evaluate_shadow_qualification",
    "_complete_merge_discovery_event",
    "_complete_opportunity_characterization",
    "_complete_strategy_rankings",
    "_label_candidate_outcome_complete",
    "_qualification_from_persisted_evidence_complete",
    "_run_dynamic_discovery_once_complete",
    "_run_interday_learning_once_complete",
    "_tier_complete_candidates",
    "_union_complete",
]
