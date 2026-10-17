from __future__ import annotations

"""Causal dynamic discovery for ``interday-trading-strategy-shadow``.

The frozen morning universe remains the immutable benchmark.  This module owns a
second, evolving SHADOW-only candidate view.  Discovery is intentionally broad;
strategy qualification and execution remain separate authorities.
"""

import hashlib
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INTERDAY_TRADING_STRATEGY_ID = "interday-trading-strategy-shadow"
INTERDAY_SUBSTRATEGIES = (
    "deterministic-v2",
    "stoch-trend-capture",
    "leader-momentum-continuation",
    "ai-every-minute",
    "ai-event-driven",
    "stoch-rsi-5min",
    "stoch-rsi-5min-early-single",
    "gap-pullback-v2-prospective-20260825",
)


class DiscoveryTriggerType(StrEnum):
    FROZEN_BENCHMARK = "frozen_benchmark"
    MARKET_ANOMALY = "market_anomaly"
    EXTREME_ATTENTION = "extreme_attention"
    UNEXPLAINED_ATTENTION_SPIKE = "unexplained_attention_spike"
    CATALYST_DISCOVERY_EVENT = "catalyst_discovery_event"
    OPERATOR_IMPORT = "operator_import"


class CandidateLifecycleState(StrEnum):
    DISCOVERED = "discovered"
    ACTIVE = "active"
    COOLING = "cooling"
    EXPIRED = "expired"


class EvaluationTier(StrEnum):
    A = "A"
    B = "B"
    WATCH = "watch"
    EXPIRED = "expired"


class DiscoveryExperimentArm(StrEnum):
    FROZEN = "frozen"
    MARKET_ONLY = "market_only"
    CATALYST_ONLY = "catalyst_only"
    COMBINED = "combined"


class AttributionStage(StrEnum):
    DISCOVERED = "discovered"
    RESEARCHED = "researched"
    CHARACTERIZED = "characterized"
    RANKED = "ranked"
    QUALIFIED = "qualified"
    SIGNALLED = "signalled"
    EXECUTION_ELIGIBLE = "execution_eligible"
    TRADED = "traded"


_STAGE_ORDER = {stage: index for index, stage in enumerate(AttributionStage)}


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


def _ratio_score(value: float, full_at: float) -> float:
    if value <= 0 or full_at <= 0:
        return 0.0
    return _clip(value / full_at, 0.0, 1.0)


class MarketAnomalyFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    gap_pct: float = 0.0
    tod_rvol: float | None = None
    volume_to_float: float | None = None
    dollar_volume: float = 0.0
    volume_acceleration_5m: float | None = None
    volume_acceleration_15m: float | None = None
    price_acceleration_5m_pct: float | None = None
    range_expansion: float | None = None
    relative_strength_pct: float | None = None
    hod_frequency_15m: int = Field(default=0, ge=0)
    spread_bps: float | None = None

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        return _utc(value)


class DiscoveryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=12, max_length=80)
    strategy_id: str = INTERDAY_TRADING_STRATEGY_ID
    session_date: date
    instrument_id: str = Field(min_length=3, max_length=200)
    discovered_at: datetime
    trigger_type: DiscoveryTriggerType
    source: str = Field(min_length=1, max_length=200)
    source_locator: str | None = Field(default=None, max_length=2_000)
    causal_as_of: datetime
    attention_score: float = Field(default=0.0, ge=0.0, le=100.0)
    catalyst_score: float = Field(default=0.0, ge=0.0, le=100.0)
    unexplained_attention: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    shadow_only: bool = True
    execution_authority: bool = False

    @field_validator("discovered_at", "causal_as_of")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def enforce_causality_and_authority(self):
        if self.discovered_at < self.causal_as_of:
            raise ValueError("discovery_cannot_precede_causal_observation")
        if not self.shadow_only or self.execution_authority:
            raise ValueError("dynamic_discovery_is_shadow_only")
        return self


class RelationshipExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_entity: str
    affected_symbol: str
    relationship_type: str
    relationship_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    economic_exposure: float = Field(default=0.0, ge=0.0, le=1.0)
    direction_of_effect: str = "unknown"
    observed_at: datetime
    evidence_ids: tuple[str, ...] = ()

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class MarketContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    spy_return_pct: float | None = None
    iwm_return_pct: float | None = None
    sector_return_pct: float | None = None
    industry_return_pct: float | None = None
    peer_median_return_pct: float | None = None
    peer_positive_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    market_breadth: float | None = Field(default=None, ge=-1.0, le=1.0)
    sector_news_strength: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class OpportunityCharacterization(BaseModel):
    """Shared vector.  Strategies interpret this; it is not a universal trade score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    observed_at: datetime
    attention_intensity: float = Field(ge=0.0, le=100.0)
    catalyst_strength: float = Field(default=0.0, ge=0.0, le=100.0)
    catalyst_persistence: float = Field(default=0.0, ge=0.0, le=100.0)
    fundamental_materiality: float = Field(default=0.0, ge=0.0, le=100.0)
    event_certainty: float = Field(default=0.0, ge=0.0, le=100.0)
    supply_pressure: float = Field(default=0.0, ge=0.0, le=100.0)
    promotional_risk: float = Field(default=0.0, ge=0.0, le=100.0)
    continuation_prior: float = Field(default=0.0, ge=0.0, le=100.0)
    failed_selloff_prior: float = Field(default=0.0, ge=0.0, le=100.0)
    reversal_prior: float = Field(default=0.0, ge=0.0, le=100.0)
    squeeze_potential: float = Field(default=0.0, ge=0.0, le=100.0)
    gap_retention_prior: float = Field(default=0.0, ge=0.0, le=100.0)
    market_confirmation: float = Field(default=0.0, ge=0.0, le=100.0)
    execution_quality: float = Field(default=50.0, ge=0.0, le=100.0)
    market_context_confirmation: float = Field(default=50.0, ge=0.0, le=100.0)
    relationship_exposure: float = Field(default=0.0, ge=0.0, le=100.0)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class ExperimentCandidateState(BaseModel):
    """Candidate lifecycle and scores tracked independently for one experiment arm."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arm: DiscoveryExperimentArm
    first_seen_at: datetime
    last_observed_at: datetime
    current_priority: float = Field(default=0.0, ge=0.0, le=100.0)
    peak_priority: float = Field(default=0.0, ge=0.0, le=100.0)
    lifecycle: CandidateLifecycleState = CandidateLifecycleState.ACTIVE
    tier: EvaluationTier = EvaluationTier.WATCH
    below_retention_since: datetime | None = None
    expired_at: datetime | None = None

    @field_validator("first_seen_at", "last_observed_at", "below_retention_since", "expired_at")
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class DynamicCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = INTERDAY_TRADING_STRATEGY_ID
    session_date: date
    instrument_id: str
    first_seen_at: datetime
    discovered_at: datetime
    last_observed_at: datetime
    lifecycle: CandidateLifecycleState = CandidateLifecycleState.DISCOVERED
    tier: EvaluationTier = EvaluationTier.WATCH
    trigger_types: tuple[DiscoveryTriggerType, ...] = ()
    experiment_arms: tuple[DiscoveryExperimentArm, ...] = ()
    attention_score: float = Field(default=0.0, ge=0.0, le=100.0)
    peak_attention_score: float = Field(default=0.0, ge=0.0, le=100.0)
    catalyst_score: float = Field(default=0.0, ge=0.0, le=100.0)
    peak_catalyst_score: float = Field(default=0.0, ge=0.0, le=100.0)
    catalyst_raw_score: float = Field(default=0.0, ge=0.0, le=100.0)
    catalyst_last_seen_at: datetime | None = None
    catalyst_expected_attention_duration: str | None = None
    catalyst_snapshot: dict[str, Any] | None = None
    latest_candidate_payload: dict[str, Any] | None = None
    below_retention_since: datetime | None = None
    experiment_states: dict[str, ExperimentCandidateState] = Field(default_factory=dict)
    selected_for_strategies: tuple[str, ...] = ()
    common_priority: float = Field(default=0.0, ge=0.0, le=100.0)
    strategy_ranks: dict[str, int] = Field(default_factory=dict)
    characterization: OpportunityCharacterization | None = None
    cooling_since: datetime | None = None
    expired_at: datetime | None = None
    shadow_only: bool = True
    execution_authority: bool = False

    @field_validator(
        "first_seen_at",
        "discovered_at",
        "last_observed_at",
        "cooling_since",
        "expired_at",
        "catalyst_last_seen_at",
        "below_retention_since",
    )
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_candidate(self):
        if self.first_seen_at > self.discovered_at or self.discovered_at > self.last_observed_at:
            raise ValueError("invalid_dynamic_candidate_timeline")
        if not self.shadow_only or self.execution_authority:
            raise ValueError("dynamic_candidate_cannot_hold_execution_authority")
        return self


class AttributionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str = INTERDAY_TRADING_STRATEGY_ID
    session_date: date
    instrument_id: str
    stage: AttributionStage
    observed_at: datetime
    sub_strategy: str | None = None
    passed: bool = True
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class TrendDurabilityOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    discovered_at: datetime
    reference_price: float = Field(gt=0.0)
    return_15m_pct: float | None = None
    return_30m_pct: float | None = None
    return_60m_pct: float | None = None
    return_120m_pct: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    plus_1r_before_minus_1r: bool | None = None
    plus_2r_before_minus_1r: bool | None = None
    trend_survived_15m: bool | None = None
    trend_survived_30m: bool | None = None
    trend_survived_60m: bool | None = None
    trend_survived_120m: bool | None = None
    minutes_above_vwap: int | None = Field(default=None, ge=0)
    higher_high_low_persistence: float | None = Field(default=None, ge=0.0, le=1.0)
    hod_break_survived: bool | None = None
    label_kind: str = "discovery_path"
    discovery_at: datetime | None = None
    tradeable_reference_at: datetime | None = None
    tradeable_reference_price: float | None = None
    reference_mode: str = "discovery_close"
    normalized_stop_fraction: float = 0.05
    same_bar_ambiguity_policy: str = "stop_first"

    @field_validator("discovered_at", "discovery_at", "tradeable_reference_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class ShadowQualificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    independent_sessions: int = Field(ge=0)
    labeled_opportunities: int = Field(ge=0)
    discovery_recall: float = Field(ge=0.0, le=1.0)
    discovery_precision: float = Field(ge=0.0, le=1.0)
    median_discovery_latency_minutes: float | None = Field(default=None, ge=0.0)
    execution_adjusted_expectancy_r: float | None = None
    max_drawdown_r: float | None = None
    data_reliability_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    causality_violations: int = Field(default=0, ge=0)
    eligible_for_review: bool = False
    auto_paper_authorized: bool = False
    reasons: tuple[str, ...] = ()
    execution_sample_count: int = Field(default=0, ge=0)
    holdout_session_count: int = Field(default=0, ge=0)
    expectancy_lcb_r: float | None = None
    stressed_expectancy_r: float | None = None
    holdout_expectancy_r: float | None = None
    max_drawdown_limit_r: float = 5.0
    evidence_complete: bool = False


class DiscoveryScanViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    source: str
    observed_at: datetime
    watermark: datetime
    reason: str


class DiscoveryScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    watermark: datetime
    candidates: tuple[DynamicCandidate, ...]
    events: tuple[DiscoveryEvent, ...]
    violations: tuple[DiscoveryScanViolation, ...] = ()
    latest_observations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    experiment_cohorts: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class ParentExposureProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    sub_strategy: str
    desired_risk_fraction: float = Field(ge=0.0, le=1.0)
    conviction: float = Field(default=0.5, ge=0.0, le=1.0)


class ParentExposureAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    allocated_risk_fraction: float = Field(ge=0.0, le=1.0)
    contributing_substrategies: tuple[str, ...]
    shadow_only: bool = True


class DynamicDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    market_admission_score: float = Field(default=60.0, ge=0.0, le=100.0)
    retention_score: float = Field(default=42.0, ge=0.0, le=100.0)
    catalyst_admission_score: float = Field(default=62.0, ge=0.0, le=100.0)
    cooling_after_minutes: int = Field(default=30, ge=5, le=240)
    expire_after_minutes: int = Field(default=60, ge=10, le=390)
    tier_a_count: int = Field(default=5, ge=1, le=25)
    tier_b_count: int = Field(default=10, ge=0, le=50)
    max_active_candidates: int = Field(default=40, ge=5, le=200)
    max_symbol_parent_risk_fraction: float = Field(default=0.01, gt=0.0, le=0.1)


DEFAULT_DYNAMIC_DISCOVERY_CONFIG = DynamicDiscoveryConfig()


def market_attention_score(features: MarketAnomalyFeatures) -> float:
    """Cheap deterministic attention detector; no news is required."""

    gap = _ratio_score(abs(features.gap_pct), 50.0) * 18.0
    rvol = _ratio_score(_float(features.tod_rvol), 100.0) * 24.0
    turnover = _ratio_score(_float(features.volume_to_float), 2.0) * 18.0
    dollar = _ratio_score(features.dollar_volume, 25_000_000.0) * 10.0
    accel = max(
        _ratio_score(_float(features.volume_acceleration_5m), 8.0),
        _ratio_score(_float(features.volume_acceleration_15m), 6.0),
    ) * 10.0
    price = _ratio_score(abs(_float(features.price_acceleration_5m_pct)), 15.0) * 7.0
    range_score = _ratio_score(_float(features.range_expansion), 4.0) * 5.0
    relative = _ratio_score(max(0.0, _float(features.relative_strength_pct)), 20.0) * 4.0
    hod = _ratio_score(float(features.hod_frequency_15m), 4.0) * 4.0
    return round(_clip(gap + rvol + turnover + dollar + accel + price + range_score + relative + hod), 4)


def _event_id(instrument_id: str, trigger: DiscoveryTriggerType, causal_as_of: datetime, source: str) -> str:
    raw = "|".join((instrument_id, trigger.value, _utc(causal_as_of).isoformat(), source))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def market_discovery_event(
    instrument_id: str,
    features: MarketAnomalyFeatures,
    *,
    session_date: date,
    source: str,
    source_locator: str | None = None,
    catalyst_known: bool = False,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> DiscoveryEvent | None:
    score = market_attention_score(features)
    extreme = (
        _float(features.tod_rvol) >= 20.0
        or _float(features.volume_to_float) >= 1.0
        or (features.dollar_volume >= 10_000_000 and abs(features.gap_pct) >= 20.0)
    )
    if score < config.market_admission_score and not extreme:
        return None
    unexplained = extreme and not catalyst_known
    trigger = (
        DiscoveryTriggerType.UNEXPLAINED_ATTENTION_SPIKE
        if unexplained
        else DiscoveryTriggerType.EXTREME_ATTENTION
        if extreme
        else DiscoveryTriggerType.MARKET_ANOMALY
    )
    return DiscoveryEvent(
        event_id=_event_id(instrument_id, trigger, features.observed_at, source),
        session_date=session_date,
        instrument_id=instrument_id,
        discovered_at=features.observed_at,
        trigger_type=trigger,
        source=source,
        source_locator=source_locator,
        causal_as_of=features.observed_at,
        attention_score=score,
        unexplained_attention=unexplained,
        payload={"features": features.model_dump(mode="json")},
    )


def _semantic_level(value: object, *, unknown: float = 50.0) -> float:
    if value is None:
        return unknown
    text = str(value).strip().lower()
    return {
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
    }.get(text, _clip(_float(value)) if text else unknown)


def catalyst_discovery_event(
    instrument_id: str,
    intelligence: object,
    *,
    session_date: date,
    observed_at: datetime,
    source: str = "catalyst_intelligence_v2",
    source_locator: str | None = None,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> DiscoveryEvent | None:
    """Reuse existing Catalyst Intelligence; do not create a second semantic model."""

    components = (
        _semantic_level(getattr(intelligence, "fundamental_materiality", None)),
        _semantic_level(getattr(intelligence, "materiality_to_company_size", None)),
        _semantic_level(getattr(intelligence, "catalyst_novelty", None)),
        _semantic_level(getattr(intelligence, "attention_strength", None)),
        _semantic_level(getattr(intelligence, "event_certainty", None)),
        _semantic_level(getattr(intelligence, "catalyst_strength", None)),
        _semantic_level(getattr(intelligence, "expected_attention_duration", None)),
    )
    catalyst_score = round(sum(components) / len(components), 4)
    if catalyst_score < config.catalyst_admission_score:
        return None
    observed = _utc(observed_at)
    trigger = DiscoveryTriggerType.CATALYST_DISCOVERY_EVENT
    evidence_ids = tuple(getattr(intelligence, "evidence_ids", ()) or ())
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
    return DiscoveryEvent(
        event_id=_event_id(instrument_id, trigger, observed, source),
        session_date=session_date,
        instrument_id=instrument_id,
        discovered_at=observed,
        trigger_type=trigger,
        source=source,
        source_locator=source_locator,
        causal_as_of=observed,
        catalyst_score=catalyst_score,
        payload={
            "evidence_ids": list(evidence_ids),
            "catalyst_type": snapshot.get("catalyst_type"),
            "expected_attention_duration": snapshot.get(
                "expected_attention_duration"
            ),
            "intraday_persistence_class": snapshot.get(
                "intraday_persistence_class"
            ),
            "snapshot": snapshot,
        },
    )


def event_experiment_arms(event: DiscoveryEvent) -> tuple[DiscoveryExperimentArm, ...]:
    market = event.trigger_type in {
        DiscoveryTriggerType.MARKET_ANOMALY,
        DiscoveryTriggerType.EXTREME_ATTENTION,
        DiscoveryTriggerType.UNEXPLAINED_ATTENTION_SPIKE,
    }
    catalyst = event.trigger_type == DiscoveryTriggerType.CATALYST_DISCOVERY_EVENT
    arms: list[DiscoveryExperimentArm] = []
    if event.trigger_type == DiscoveryTriggerType.FROZEN_BENCHMARK:
        arms.append(DiscoveryExperimentArm.FROZEN)
    if market:
        arms.extend((DiscoveryExperimentArm.MARKET_ONLY, DiscoveryExperimentArm.COMBINED))
    if catalyst:
        arms.extend((DiscoveryExperimentArm.CATALYST_ONLY, DiscoveryExperimentArm.COMBINED))
    return tuple(dict.fromkeys(arms))


def merge_discovery_event(
    current: DynamicCandidate | None,
    event: DiscoveryEvent,
) -> DynamicCandidate:
    from .strategy_dynamic_discovery_runtime import _complete_merge_discovery_event

    return _complete_merge_discovery_event(current, event)


def apply_discovery_scan(
    *,
    previous_state: Mapping[str, DynamicCandidate],
    observations: Sequence[Any],
    watermark: datetime,
    session_date: date,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> DiscoveryScanResult:
    from .strategy_dynamic_discovery_quality import _apply_scan_refined

    return _apply_scan_refined(
        previous_state=previous_state,
        observations=observations,
        watermark=watermark,
        session_date=session_date,
        config=config,
    )


def advance_candidate_lifecycle(
    candidate: DynamicCandidate,
    *,
    observed_at: datetime,
    current_priority: float | None = None,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> DynamicCandidate:
    now = _utc(observed_at)
    if now < candidate.last_observed_at:
        raise ValueError("candidate_lifecycle_cannot_move_backwards")
    priority = candidate.common_priority if current_priority is None else _clip(current_priority)
    age = now - candidate.last_observed_at
    lifecycle = candidate.lifecycle
    cooling_since = candidate.cooling_since
    expired_at = candidate.expired_at
    if lifecycle != CandidateLifecycleState.EXPIRED:
        if priority >= config.retention_score:
            lifecycle = CandidateLifecycleState.ACTIVE
            cooling_since = None
        elif age >= timedelta(minutes=config.expire_after_minutes):
            lifecycle = CandidateLifecycleState.EXPIRED
            expired_at = now
        elif age >= timedelta(minutes=config.cooling_after_minutes):
            lifecycle = CandidateLifecycleState.COOLING
            cooling_since = cooling_since or now
    return candidate.model_copy(
        update={
            "lifecycle": lifecycle,
            "common_priority": priority,
            "cooling_since": cooling_since,
            "expired_at": expired_at,
            "tier": EvaluationTier.EXPIRED if lifecycle == CandidateLifecycleState.EXPIRED else candidate.tier,
        }
    )


def tier_candidates(
    candidates: Sequence[DynamicCandidate],
    *,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> tuple[DynamicCandidate, ...]:
    from .strategy_dynamic_discovery_runtime import _tier_complete_candidates

    return _tier_complete_candidates(candidates, config=config)


def build_opportunity_characterization(
    candidate: DynamicCandidate,
    *,
    observed_at: datetime,
    catalyst: object | None = None,
    market_structure: object | None = None,
    execution_quality: float = 50.0,
    market_context: MarketContext | None = None,
    relationships: Sequence[RelationshipExposure] = (),
) -> OpportunityCharacterization:
    from .strategy_dynamic_discovery_quality import _characterization_refined

    return _characterization_refined(
        candidate,
        observed_at=observed_at,
        catalyst=catalyst,
        market_structure=market_structure,
        execution_quality=execution_quality,
        market_context=market_context,
        relationships=relationships,
    )


def strategy_specific_score(arm: str, row: OpportunityCharacterization) -> float:
    """Different arms value the same shared vector differently."""

    if arm == "deterministic-v2":
        return _clip(row.failed_selloff_prior * 0.55 + row.market_confirmation * 0.30 + row.execution_quality * 0.15)
    if arm == "stoch-trend-capture":
        return _clip(row.continuation_prior * 0.45 + row.market_confirmation * 0.35 + row.attention_intensity * 0.20)
    if arm == "leader-momentum-continuation":
        return _clip(
            row.continuation_prior * 0.40
            + row.squeeze_potential * 0.20
            + row.attention_intensity * 0.20
            + row.market_confirmation * 0.10
            + row.execution_quality * 0.10
        )
    if arm == "ai-every-minute":
        return _clip(row.continuation_prior * 0.30 + row.catalyst_persistence * 0.20 + row.market_confirmation * 0.25 + row.execution_quality * 0.15 + row.market_context_confirmation * 0.10)
    if arm == "ai-event-driven":
        return _clip(row.attention_intensity * 0.25 + row.catalyst_strength * 0.20 + row.continuation_prior * 0.25 + row.market_confirmation * 0.20 + row.relationship_exposure * 0.10)
    if arm == "stoch-rsi-5min":
        return _clip(row.reversal_prior * 0.45 + row.market_confirmation * 0.30 + row.execution_quality * 0.25)
    if arm == "stoch-rsi-5min-early-single":
        # One trade per symbol: favor names whose single entry executes cleanly.
        return _clip(row.reversal_prior * 0.45 + row.market_confirmation * 0.25 + row.execution_quality * 0.30)
    if arm == "gap-pullback-v2-prospective-20260825":
        return _clip(row.failed_selloff_prior * 0.50 + row.catalyst_persistence * 0.15 + row.market_confirmation * 0.25 + row.execution_quality * 0.10)
    raise ValueError(f"unknown_interday_substrategy:{arm}")


def apply_strategy_rankings(candidates: Sequence[DynamicCandidate]) -> tuple[DynamicCandidate, ...]:
    from .strategy_dynamic_discovery_runtime import _complete_strategy_rankings

    return _complete_strategy_rankings(candidates)


def catalyst_decay_multiplier(
    expected_attention_duration: str | None,
    *,
    elapsed_minutes: float,
    refreshed: bool = False,
    continued_abnormal_participation: bool = False,
    strong_market_confirmation: bool = False,
) -> float:
    """Empirical-policy hook: semantic duration selects a transparent decay curve."""

    duration = str(expected_attention_duration or "uncertain").lower()
    half_life = {
        "minutes": 30.0,
        "hours": 120.0,
        "session": 300.0,
        "multi_day": 900.0,
        "uncertain": 75.0,
    }.get(duration, 75.0)
    multiplier = math.pow(0.5, max(0.0, elapsed_minutes) / half_life)
    if refreshed:
        multiplier = max(multiplier, 0.90)
    elif continued_abnormal_participation:
        multiplier = max(multiplier, 0.75)
    elif strong_market_confirmation:
        multiplier = max(multiplier, 0.68)
    return round(_clip(multiplier, 0.0, 1.0), 6)


def build_attribution_event(
    *,
    session_date: date,
    instrument_id: str,
    stage: AttributionStage,
    observed_at: datetime,
    prior_events: Sequence[AttributionEvent] = (),
    sub_strategy: str | None = None,
    passed: bool = True,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AttributionEvent:
    same = [row for row in prior_events if row.instrument_id == instrument_id and row.sub_strategy == sub_strategy and row.passed]
    if passed and same:
        highest = max(_STAGE_ORDER[row.stage] for row in same)
        if _STAGE_ORDER[stage] > highest + 1:
            raise ValueError("attribution_stage_cannot_skip_successful_predecessor")
    return AttributionEvent(
        session_date=session_date,
        instrument_id=instrument_id,
        stage=stage,
        observed_at=observed_at,
        sub_strategy=sub_strategy,
        passed=passed,
        reason=reason,
        payload=payload or {},
    )


def trend_durability_from_prices(
    instrument_id: str,
    *,
    discovered_at: datetime,
    reference_price: float,
    samples: Sequence[tuple[datetime, float]],
    stop_fraction: float = 0.05,
) -> TrendDurabilityOutcome:
    ordered = sorted((_utc(ts), float(price)) for ts, price in samples if _utc(ts) >= _utc(discovered_at))
    if not ordered:
        return TrendDurabilityOutcome(instrument_id=instrument_id, discovered_at=discovered_at, reference_price=reference_price)
    returns = [(ts, (price / reference_price - 1.0) * 100.0) for ts, price in ordered]

    def horizon(minutes: int) -> float | None:
        target = _utc(discovered_at) + timedelta(minutes=minutes)
        values = [ret for ts, ret in returns if ts <= target]
        return values[-1] if values else None

    mfe = max(ret for _, ret in returns)
    mae = min(ret for _, ret in returns)
    one_r = stop_fraction * 100.0
    plus1 = plus2 = None
    for target_r, name in ((1.0, "one"), (2.0, "two")):
        target = target_r * one_r
        target_time = next((ts for ts, ret in returns if ret >= target), None)
        stop_time = next((ts for ts, ret in returns if ret <= -one_r), None)
        result = target_time is not None and (stop_time is None or target_time < stop_time)
        if name == "one":
            plus1 = result
        else:
            plus2 = result

    def survived(minutes: int) -> bool | None:
        value = horizon(minutes)
        return None if value is None else value > 0.0

    return TrendDurabilityOutcome(
        instrument_id=instrument_id,
        discovered_at=discovered_at,
        reference_price=reference_price,
        return_15m_pct=horizon(15),
        return_30m_pct=horizon(30),
        return_60m_pct=horizon(60),
        return_120m_pct=horizon(120),
        mfe_pct=mfe,
        mae_pct=mae,
        plus_1r_before_minus_1r=plus1,
        plus_2r_before_minus_1r=plus2,
        trend_survived_15m=survived(15),
        trend_survived_30m=survived(30),
        trend_survived_60m=survived(60),
        trend_survived_120m=survived(120),
    )


def evaluate_shadow_qualification(metrics: Mapping[str, Any]) -> ShadowQualificationEvidence:
    from .strategy_dynamic_discovery_quality import _qualification_refined

    return _qualification_refined(metrics)


def allocate_parent_exposure(
    proposals: Sequence[ParentExposureProposal],
    *,
    config: DynamicDiscoveryConfig = DEFAULT_DYNAMIC_DISCOVERY_CONFIG,
) -> tuple[ParentExposureAllocation, ...]:
    grouped: dict[str, list[ParentExposureProposal]] = defaultdict(list)
    for proposal in proposals:
        if proposal.sub_strategy not in INTERDAY_SUBSTRATEGIES:
            raise ValueError(f"unknown_interday_substrategy:{proposal.sub_strategy}")
        grouped[proposal.instrument_id].append(proposal)
    rows: list[ParentExposureAllocation] = []
    for instrument_id, values in sorted(grouped.items()):
        # Multiple agreeing arms are correlated opinions about one symbol, not
        # independent risk budgets. Allocate the strongest proposal, capped at
        # the parent symbol limit, instead of summing all seven.
        strongest = max(values, key=lambda row: row.desired_risk_fraction * row.conviction)
        allocation = min(config.max_symbol_parent_risk_fraction, strongest.desired_risk_fraction)
        rows.append(
            ParentExposureAllocation(
                instrument_id=instrument_id,
                allocated_risk_fraction=allocation,
                contributing_substrategies=tuple(sorted({row.sub_strategy for row in values})),
            )
        )
    return tuple(rows)


__all__ = [
    "AttributionEvent",
    "AttributionStage",
    "CandidateLifecycleState",
    "DEFAULT_DYNAMIC_DISCOVERY_CONFIG",
    "DiscoveryEvent",
    "DiscoveryExperimentArm",
    "DiscoveryTriggerType",
    "DynamicCandidate",
    "DynamicDiscoveryConfig",
    "EvaluationTier",
    "INTERDAY_SUBSTRATEGIES",
    "INTERDAY_TRADING_STRATEGY_ID",
    "MarketAnomalyFeatures",
    "MarketContext",
    "OpportunityCharacterization",
    "ParentExposureAllocation",
    "ParentExposureProposal",
    "RelationshipExposure",
    "ShadowQualificationEvidence",
    "TrendDurabilityOutcome",
    "advance_candidate_lifecycle",
    "allocate_parent_exposure",
    "apply_strategy_rankings",
    "build_attribution_event",
    "build_opportunity_characterization",
    "catalyst_decay_multiplier",
    "catalyst_discovery_event",
    "event_experiment_arms",
    "evaluate_shadow_qualification",
    "market_attention_score",
    "market_discovery_event",
    "merge_discovery_event",
    "strategy_specific_score",
    "tier_candidates",
    "trend_durability_from_prices",
]
