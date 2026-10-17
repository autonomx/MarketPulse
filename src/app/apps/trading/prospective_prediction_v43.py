from __future__ import annotations

"""Forward-only prospective-gap v4.3 exhaustion/regime challenger.

v4.3 is intentionally separate from the frozen v3/v4/v4.1/v4.2 surfaces.
It turns lessons observed through 2026-09-25 into a preregistered challenger
for sessions beginning 2026-09-28.

The design focuses on four questions that are distinct from catalyst quality:

1. How saturated is the premarket repricing?
2. How much continuation demand appears to remain?
3. Is the whole Top-10 cohort in an exhaustion/fade regime?
4. Does the forecast still have meaningful edge over the frozen climatology?

No v4.2 forecast or historical prediction is rewritten.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Final, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gapper_dataset import GapperCandidate
from .prospective_prediction_v4 import PremarketMarketStateSnapshot
from .prospective_prediction_v42 import V42Forecast


V43_PREDICTOR_VERSION: Final = "prospective-gap-v4.3-shadow"
V43_FEATURE_SCHEMA_VERSION: Final = "prospective-gap-features-v4.3"
V43_SPEC_VERSION: Final = "prospective-gap-v4.3-preregistered-spec-v1"
V43_ACTIVATION_STATE: Final = "FORWARD_SHADOW_ACTIVE"
V43_FIRST_ELIGIBLE_FORWARD_SESSION = date(2026, 9, 28)
V43_DESIGN_EVIDENCE_THROUGH = date(2026, 9, 25)

V43CohortRegimeClass = Literal[
    "INSUFFICIENT",
    "NORMAL",
    "CAUTIOUS",
    "HIGH_EXHAUSTION",
]
V43ModelState = Literal["PRODUCED", "FAILED", "NOT_APPLICABLE"]


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _clamp01(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(high, max(low, value))


def _feature(snapshot: PremarketMarketStateSnapshot, name: str) -> Decimal | None:
    return snapshot.live_value(name)


def _median(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    center = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[center]
    return (ordered[center - 1] + ordered[center]) / Decimal("2")


class V43ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_version: Literal["prospective-gap-v4.3-preregistered-spec-v1"] = V43_SPEC_VERSION
    predictor_version: Literal["prospective-gap-v4.3-shadow"] = V43_PREDICTOR_VERSION
    feature_schema_version: Literal["prospective-gap-features-v4.3"] = V43_FEATURE_SCHEMA_VERSION
    activation_state: Literal["FORWARD_SHADOW_ACTIVE"] = V43_ACTIVATION_STATE

    first_eligible_forward_session: date = V43_FIRST_ELIGIBLE_FORWARD_SESSION
    design_evidence_through: date = V43_DESIGN_EVIDENCE_THROUGH
    minimum_cohort_members: int = Field(default=3, ge=1)
    minimum_forward_sessions_before_review: int = 10
    minimum_forward_observations_before_review: int = 100

    remaining_upside_probability_weight: Decimal = Decimal("0.08")
    demand_resilience_probability_weight: Decimal = Decimal("0.06")
    extension_exhaustion_probability_weight: Decimal = Decimal("-0.10")
    repricing_complete_probability_weight: Decimal = Decimal("-0.06")
    cautious_regime_probability_penalty: Decimal = Decimal("0.03")
    high_exhaustion_probability_penalty: Decimal = Decimal("0.07")

    remaining_upside_return_weight: Decimal = Decimal("0.05")
    demand_resilience_return_weight: Decimal = Decimal("0.03")
    extension_exhaustion_return_weight: Decimal = Decimal("-0.06")
    cautious_regime_return_penalty: Decimal = Decimal("0.015")
    high_exhaustion_return_penalty: Decimal = Decimal("0.04")

    cohort_cautious_threshold: Decimal = Field(default=Decimal("0.45"), ge=0, le=1)
    cohort_high_exhaustion_threshold: Decimal = Field(default=Decimal("0.62"), ge=0, le=1)
    high_exhaustion_member_threshold: Decimal = Field(default=Decimal("0.65"), ge=0, le=1)
    low_information_member_threshold: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    large_gap_threshold_pct: Decimal = Decimal("50")

    @property
    def implementation_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


DEFAULT_V43_SPEC = V43ModelSpec()


class V43ExtensionExhaustionOverlay(BaseModel):
    """Richer raw premarket saturation head; diagnostic, not calibrated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gap_extension_score: Decimal = Field(ge=0, le=1)
    vwap_extension_score: Decimal | None = Field(default=None, ge=0, le=1)
    distance_from_low_score: Decimal | None = Field(default=None, ge=0, le=1)
    range_position_score: Decimal | None = Field(default=None, ge=0, le=1)
    multi_day_extension_score: Decimal | None = Field(default=None, ge=0, le=1)
    float_turnover_score: Decimal | None = Field(default=None, ge=0, le=1)
    late_deceleration_score: Decimal | None = Field(default=None, ge=0, le=1)
    late_volume_fade_score: Decimal | None = Field(default=None, ge=0, le=1)

    composite_exhaustion_score: Decimal = Field(ge=0, le=1)
    demand_resilience_score: Decimal = Field(ge=0, le=1)
    premarket_repricing_complete_score: Decimal = Field(ge=0, le=1)
    used_components: tuple[str, ...]
    missing_components: tuple[str, ...]
    input_fingerprint: str


class V43CohortRegime(BaseModel):
    """Cross-sectional Top-10 regime frozen before the open."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: V43CohortRegimeClass
    member_count: int = Field(ge=0)
    median_gap_pct: Decimal
    fraction_gap_ge_50: Decimal = Field(ge=0, le=1)
    fraction_high_opening_exhaustion: Decimal = Field(ge=0, le=1)
    fraction_low_information_technical: Decimal = Field(ge=0, le=1)
    mean_remaining_upside: Decimal = Field(ge=0, le=1)
    risk_score: Decimal = Field(ge=0, le=1)
    cohort_fingerprint: str


class V43Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    cohort_id: str
    cohort_fingerprint: str
    predictor_version: Literal["prospective-gap-v4.3-shadow"] = V43_PREDICTOR_VERSION
    feature_schema_version: Literal["prospective-gap-features-v4.3"] = V43_FEATURE_SCHEMA_VERSION
    model_spec_fingerprint: str
    frozen_at: datetime

    base_v42_forecast_fingerprint: str
    base_v42_probability: Decimal = Field(ge=0, le=1)
    p_close_above_open: Decimal = Field(ge=0, le=1)
    frozen_climatology_probability: Decimal | None = Field(default=None, ge=0, le=1)
    probability_edge_over_climatology: Decimal | None = None
    expected_return: Decimal

    remaining_upside_score: Decimal = Field(ge=0, le=1)
    opening_exhaustion_score: Decimal = Field(ge=0, le=1)
    premarket_repricing_complete_score: Decimal = Field(ge=0, le=1)
    extension_overlay: V43ExtensionExhaustionOverlay
    cohort_regime: V43CohortRegime
    uncertainty: Literal["moderate", "high"]

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v43_frozen_at_must_be_timezone_aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def edge_consistent(self):
        if self.frozen_climatology_probability is None:
            if self.probability_edge_over_climatology is not None:
                raise ValueError("v43_edge_requires_climatology")
        else:
            expected = self.p_close_above_open - self.frozen_climatology_probability
            if self.probability_edge_over_climatology != expected:
                raise ValueError("v43_probability_edge_mismatch")
        return self

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class V43ForecastAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    attempted_at: datetime
    model_state: V43ModelState
    failure_reason: str | None = None
    forecast_fingerprint: str | None = None

    @field_validator("attempted_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v43_attempted_at_must_be_timezone_aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def consistent(self):
        if self.model_state == "PRODUCED" and not self.forecast_fingerprint:
            raise ValueError("v43_produced_attempt_requires_forecast")
        if self.model_state != "PRODUCED" and not self.failure_reason:
            raise ValueError("v43_nonproduced_attempt_requires_reason")
        return self


def session_eligible_for_v43_forward_validation(
    session_date: date,
    *,
    spec: V43ModelSpec = DEFAULT_V43_SPEC,
) -> bool:
    return session_date >= spec.first_eligible_forward_session


def derive_v43_extension_overlay(
    *,
    candidate: GapperCandidate,
    market_state: PremarketMarketStateSnapshot,
    base_v42: V42Forecast,
) -> V43ExtensionExhaustionOverlay:
    if candidate.instrument_id != base_v42.instrument_id:
        raise ValueError("v43_candidate_v42_instrument_mismatch")

    gap_score = _clamp01(
        (max(Decimal("0"), candidate.gap_pct) - Decimal("20")) / Decimal("100")
    )
    vwap_distance = _feature(market_state, "distance_from_premarket_vwap_pct")
    distance_from_low = _feature(market_state, "distance_from_premarket_low_pct")
    range_position = _feature(market_state, "position_in_premarket_range")
    prior_1d = _feature(market_state, "prior_1d_return_pct")
    prior_3d = _feature(market_state, "prior_3d_return_pct")
    turnover = _feature(market_state, "float_turnover")
    acceleration = _feature(market_state, "late_premarket_acceleration")
    late_volume_share = _feature(market_state, "late_premarket_volume_share")

    vwap_score = (
        _clamp01(max(Decimal("0"), vwap_distance) / Decimal("20"))
        if vwap_distance is not None
        else None
    )
    low_score = (
        _clamp01(max(Decimal("0"), distance_from_low) / Decimal("100"))
        if distance_from_low is not None
        else None
    )
    range_score = _clamp01(range_position) if range_position is not None else None

    multi_values: list[Decimal] = []
    if prior_1d is not None:
        multi_values.append(_clamp01(max(Decimal("0"), prior_1d) / Decimal("50")))
    if prior_3d is not None:
        multi_values.append(_clamp01(max(Decimal("0"), prior_3d) / Decimal("100")))
    multi_day_score = max(multi_values) if multi_values else None

    turnover_score = (
        _clamp01(max(Decimal("0"), turnover) / Decimal("2"))
        if turnover is not None
        else None
    )
    deceleration_score = (
        _clamp01((Decimal("0.25") - acceleration) / Decimal("1.25"))
        if acceleration is not None
        else None
    )
    late_volume_fade_score = (
        Decimal("1") - _clamp01(late_volume_share / Decimal("0.30"))
        if late_volume_share is not None
        else None
    )

    components: tuple[tuple[str, Decimal | None, Decimal], ...] = (
        ("gap_extension_score", gap_score, Decimal("0.22")),
        ("vwap_extension_score", vwap_score, Decimal("0.16")),
        ("distance_from_low_score", low_score, Decimal("0.12")),
        ("range_position_score", range_score, Decimal("0.10")),
        ("multi_day_extension_score", multi_day_score, Decimal("0.12")),
        ("float_turnover_score", turnover_score, Decimal("0.10")),
        ("late_deceleration_score", deceleration_score, Decimal("0.10")),
        ("late_volume_fade_score", late_volume_fade_score, Decimal("0.08")),
    )
    available = [(name, value, weight) for name, value, weight in components if value is not None]
    if not available:
        raise ValueError("v43_extension_overlay_has_no_observed_components")
    weighted_sum = sum((value * weight for _, value, weight in available), Decimal("0"))
    weight_sum = sum((weight for _, _, weight in available), Decimal("0"))
    composite = _clamp01(weighted_sum / weight_sum)

    accel01 = (
        _clamp01((acceleration + Decimal("1")) / Decimal("2"))
        if acceleration is not None
        else Decimal("0.5")
    )
    late_volume01 = (
        _clamp01(late_volume_share / Decimal("0.30"))
        if late_volume_share is not None
        else Decimal("0.5")
    )
    demand_resilience = _clamp01(
        base_v42.mechanisms.continuation_demand_score * Decimal("0.35")
        + base_v42.mechanisms.remaining_upside_score * Decimal("0.35")
        + accel01 * Decimal("0.15")
        + late_volume01 * Decimal("0.15")
    )
    repricing_complete = _clamp01(
        max(composite, base_v42.mechanisms.opening_exhaustion_score)
        * Decimal("0.85")
        + base_v42.mechanisms.low_information_technical_score * Decimal("0.15")
    )
    used = tuple(name for name, value, _ in components if value is not None)
    missing = tuple(name for name, value, _ in components if value is None)
    fingerprint = _hash(
        {
            "candidate": {
                "instrument_id": candidate.instrument_id,
                "gap_pct": candidate.gap_pct,
                "tod_rvol": candidate.tod_rvol,
                "premarket_volume": candidate.premarket_volume,
            },
            "market_state": market_state.live_feature_fingerprint,
            "base_v42": base_v42.immutable_fingerprint,
            "used": used,
            "values": {
                name: str(value) if value is not None else None
                for name, value, _ in components
            },
        }
    )
    return V43ExtensionExhaustionOverlay(
        gap_extension_score=gap_score,
        vwap_extension_score=vwap_score,
        distance_from_low_score=low_score,
        range_position_score=range_score,
        multi_day_extension_score=multi_day_score,
        float_turnover_score=turnover_score,
        late_deceleration_score=deceleration_score,
        late_volume_fade_score=late_volume_fade_score,
        composite_exhaustion_score=composite,
        demand_resilience_score=demand_resilience,
        premarket_repricing_complete_score=repricing_complete,
        used_components=used,
        missing_components=missing,
        input_fingerprint=fingerprint,
    )


def derive_v43_cohort_regime(
    rows: Sequence[tuple[GapperCandidate, V42Forecast, V43ExtensionExhaustionOverlay]],
    *,
    spec: V43ModelSpec = DEFAULT_V43_SPEC,
) -> V43CohortRegime:
    count = len(rows)
    if count == 0:
        return V43CohortRegime(
            classification="INSUFFICIENT",
            member_count=0,
            median_gap_pct=Decimal("0"),
            fraction_gap_ge_50=Decimal("0"),
            fraction_high_opening_exhaustion=Decimal("0"),
            fraction_low_information_technical=Decimal("0"),
            mean_remaining_upside=Decimal("0"),
            risk_score=Decimal("1"),
            cohort_fingerprint=_hash({"rows": (), "reason": "empty"}),
        )

    n = Decimal(count)
    gaps = [candidate.gap_pct for candidate, _, _ in rows]
    gap_fraction = (
        Decimal(sum(candidate.gap_pct >= spec.large_gap_threshold_pct for candidate, _, _ in rows))
        / n
    )
    exhaustion_fraction = (
        Decimal(
            sum(
                forecast.mechanisms.opening_exhaustion_score
                >= spec.high_exhaustion_member_threshold
                for _, forecast, _ in rows
            )
        )
        / n
    )
    low_information_fraction = (
        Decimal(
            sum(
                forecast.mechanisms.low_information_technical_score
                >= spec.low_information_member_threshold
                for _, forecast, _ in rows
            )
        )
        / n
    )
    mean_remaining = (
        sum((forecast.mechanisms.remaining_upside_score for _, forecast, _ in rows), Decimal("0"))
        / n
    )
    risk = _clamp01(
        gap_fraction * Decimal("0.30")
        + exhaustion_fraction * Decimal("0.30")
        + low_information_fraction * Decimal("0.20")
        + (Decimal("1") - mean_remaining) * Decimal("0.20")
    )

    if count < spec.minimum_cohort_members:
        classification: V43CohortRegimeClass = "INSUFFICIENT"
    elif risk >= spec.cohort_high_exhaustion_threshold:
        classification = "HIGH_EXHAUSTION"
    elif risk >= spec.cohort_cautious_threshold:
        classification = "CAUTIOUS"
    else:
        classification = "NORMAL"

    fingerprint = _hash(
        {
            "members": [
                {
                    "instrument_id": candidate.instrument_id,
                    "gap_pct": candidate.gap_pct,
                    "v42": forecast.immutable_fingerprint,
                    "overlay": overlay.input_fingerprint,
                }
                for candidate, forecast, overlay in rows
            ],
            "classification": classification,
            "risk": risk,
        }
    )
    return V43CohortRegime(
        classification=classification,
        member_count=count,
        median_gap_pct=_median(gaps),
        fraction_gap_ge_50=gap_fraction,
        fraction_high_opening_exhaustion=exhaustion_fraction,
        fraction_low_information_technical=low_information_fraction,
        mean_remaining_upside=mean_remaining,
        risk_score=risk,
        cohort_fingerprint=fingerprint,
    )


def freeze_v43_forecast(
    *,
    candidate: GapperCandidate,
    base_v42: V42Forecast,
    extension_overlay: V43ExtensionExhaustionOverlay,
    cohort_regime: V43CohortRegime,
    frozen_climatology_probability: Decimal | None,
    frozen_at: datetime,
    spec: V43ModelSpec = DEFAULT_V43_SPEC,
) -> V43Forecast:
    if not session_eligible_for_v43_forward_validation(base_v42.session_date, spec=spec):
        raise ValueError("v43_session_not_forward_eligible")
    if candidate.instrument_id != base_v42.instrument_id:
        raise ValueError("v43_candidate_v42_instrument_mismatch")
    if frozen_at.tzinfo is None:
        raise ValueError("v43_frozen_at_must_be_timezone_aware")
    frozen_at = frozen_at.astimezone(timezone.utc)
    if frozen_at < base_v42.frozen_at:
        raise ValueError("v43_cannot_freeze_before_base_v42")
    if cohort_regime.classification == "INSUFFICIENT":
        raise ValueError("v43_requires_sufficient_cohort_regime")

    regime_probability_penalty = {
        "NORMAL": Decimal("0"),
        "CAUTIOUS": spec.cautious_regime_probability_penalty,
        "HIGH_EXHAUSTION": spec.high_exhaustion_probability_penalty,
    }[cohort_regime.classification]
    regime_return_penalty = {
        "NORMAL": Decimal("0"),
        "CAUTIOUS": spec.cautious_regime_return_penalty,
        "HIGH_EXHAUSTION": spec.high_exhaustion_return_penalty,
    }[cohort_regime.classification]

    remaining = base_v42.mechanisms.remaining_upside_score
    demand = extension_overlay.demand_resilience_score
    exhaustion = extension_overlay.composite_exhaustion_score
    repricing = extension_overlay.premarket_repricing_complete_score

    probability = _clamp(
        base_v42.p_close_above_open
        + (remaining - Decimal("0.50")) * spec.remaining_upside_probability_weight
        + (demand - Decimal("0.50")) * spec.demand_resilience_probability_weight
        + exhaustion * spec.extension_exhaustion_probability_weight
        + repricing * spec.repricing_complete_probability_weight
        - regime_probability_penalty,
        Decimal("0.05"),
        Decimal("0.95"),
    )
    expected_return = _clamp(
        base_v42.return_distribution.expected_return
        + (remaining - Decimal("0.50")) * spec.remaining_upside_return_weight
        + (demand - Decimal("0.50")) * spec.demand_resilience_return_weight
        + exhaustion * spec.extension_exhaustion_return_weight
        - regime_return_penalty,
        Decimal("-0.30"),
        Decimal("0.30"),
    )
    edge = (
        probability - frozen_climatology_probability
        if frozen_climatology_probability is not None
        else None
    )
    uncertainty: Literal["moderate", "high"] = (
        "high"
        if base_v42.uncertainty == "high"
        or cohort_regime.classification == "HIGH_EXHAUSTION"
        or len(extension_overlay.missing_components) >= 2
        else "moderate"
    )
    return V43Forecast(
        instrument_id=base_v42.instrument_id,
        session_date=base_v42.session_date,
        cohort_id=base_v42.cohort_id,
        cohort_fingerprint=base_v42.cohort_fingerprint,
        model_spec_fingerprint=spec.implementation_fingerprint,
        frozen_at=frozen_at,
        base_v42_forecast_fingerprint=base_v42.immutable_fingerprint,
        base_v42_probability=base_v42.p_close_above_open,
        p_close_above_open=probability,
        frozen_climatology_probability=frozen_climatology_probability,
        probability_edge_over_climatology=edge,
        expected_return=expected_return,
        remaining_upside_score=remaining,
        opening_exhaustion_score=base_v42.mechanisms.opening_exhaustion_score,
        premarket_repricing_complete_score=repricing,
        extension_overlay=extension_overlay,
        cohort_regime=cohort_regime,
        uncertainty=uncertainty,
    )


__all__ = [
    "DEFAULT_V43_SPEC",
    "V43_ACTIVATION_STATE",
    "V43_FEATURE_SCHEMA_VERSION",
    "V43_FIRST_ELIGIBLE_FORWARD_SESSION",
    "V43_PREDICTOR_VERSION",
    "V43_SPEC_VERSION",
    "V43CohortRegime",
    "V43ExtensionExhaustionOverlay",
    "V43Forecast",
    "V43ForecastAttempt",
    "V43ModelSpec",
    "derive_v43_cohort_regime",
    "derive_v43_extension_overlay",
    "freeze_v43_forecast",
    "session_eligible_for_v43_forward_validation",
]
