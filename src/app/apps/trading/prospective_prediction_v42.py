from __future__ import annotations

"""Forward-only prospective-gap v4.2 challenger.

v4.2 is the first challenger allowed to use lessons observed through 2026-09-22.
It does not mutate v3, v4, or the preregistered-but-never-activated v4.1 spec.

The model is deliberately transparent and deterministic. It requires complete
causal premarket demand evidence before it can produce a forecast.
"""

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Final, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .gapper_dataset import GapperCandidate
from .prospective_prediction_v4 import (
    CatalystDecomposition,
    ExtensionExhaustionRisk,
    MechanismRiskScores,
    PremarketMarketStateSnapshot,
    RegimeTag,
)


V42_PREDICTOR_VERSION: Final = "prospective-gap-v4.2-shadow"
V42_FEATURE_SCHEMA_VERSION: Final = "prospective-gap-features-v4.2"
V42_SPEC_VERSION: Final = "prospective-gap-v4.2-preregistered-spec-v1"
V42_ACTIVATION_STATE: Final = "FORWARD_SHADOW_ACTIVE"
V42_FIRST_ELIGIBLE_FORWARD_SESSION = date(2026, 9, 23)
V42_DESIGN_EVIDENCE_SESSIONS = (
    date(2026, 9, 15),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 9, 18),
    date(2026, 9, 21),
    date(2026, 9, 22),
)

V42_REQUIRED_DEMAND_FEATURES = (
    "distance_from_premarket_vwap_pct",
    "position_in_premarket_range",
    "float_turnover",
    "late_premarket_acceleration",
    "late_premarket_volume_share",
)

ModelState = Literal["PRODUCED", "FAILED", "NOT_APPLICABLE"]


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


class V42RiskInteractions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extension_x_supply: Decimal = Field(ge=0, le=1)
    extension_x_low_liquidity: Decimal = Field(ge=0, le=1)
    extension_x_weak_finality: Decimal = Field(ge=0, le=1)


class V42MechanismHeads(BaseModel):
    """Mechanism-specific continuation state, not calibrated probabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fundamental_reprice_score: Decimal = Field(ge=0, le=1)
    theme_squeeze_score: Decimal = Field(ge=0, le=1)
    low_information_technical_score: Decimal = Field(ge=0, le=1)
    continuation_demand_score: Decimal = Field(ge=0, le=1)
    remaining_upside_score: Decimal = Field(ge=0, le=1)
    opening_exhaustion_score: Decimal = Field(ge=0, le=1)
    supply_fade_score: Decimal = Field(ge=0, le=1)


class V42ReturnDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    q10: Decimal
    q50: Decimal
    q90: Decimal
    expected_return: Decimal
    expected_shortfall_10pct: Decimal
    p_return_gt_2pct: Decimal = Field(ge=0, le=1)
    p_return_lt_minus_5pct: Decimal = Field(ge=0, le=1)
    expected_mae: Decimal
    expected_mfe: Decimal

    @model_validator(mode="after")
    def ordered(self):
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError("v42_return_quantiles_must_be_monotonic")
        if self.expected_shortfall_10pct > self.q10:
            raise ValueError("v42_expected_shortfall_must_not_exceed_q10")
        return self


class V42ModelSpec(BaseModel):
    """Frozen post-2026-09-22 research specification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_version: Literal[
        "prospective-gap-v4.2-preregistered-spec-v1"
    ] = V42_SPEC_VERSION
    predictor_version: Literal[
        "prospective-gap-v4.2-shadow"
    ] = V42_PREDICTOR_VERSION
    feature_schema_version: Literal[
        "prospective-gap-features-v4.2"
    ] = V42_FEATURE_SCHEMA_VERSION
    activation_state: Literal[
        "FORWARD_SHADOW_ACTIVE"
    ] = V42_ACTIVATION_STATE

    intercept: Decimal = Decimal("0.36")
    catalyst_strength_weight: Decimal = Decimal("0.06")
    catalyst_finality_weight: Decimal = Decimal("0.05")
    catalyst_freshness_weight: Decimal = Decimal("0.03")
    catalyst_materiality_weight: Decimal = Decimal("0.05")
    fundamental_reprice_weight: Decimal = Decimal("0.03")
    theme_squeeze_weight: Decimal = Decimal("0.06")
    low_information_technical_weight: Decimal = Decimal("0.04")
    continuation_demand_weight: Decimal = Decimal("0.15")
    remaining_upside_weight: Decimal = Decimal("0.10")
    opening_exhaustion_weight: Decimal = Decimal("-0.12")
    extension_exhaustion_weight: Decimal = Decimal("-0.10")
    supply_fade_weight: Decimal = Decimal("-0.08")
    extension_x_supply_weight: Decimal = Decimal("-0.08")
    extension_x_low_liquidity_weight: Decimal = Decimal("-0.06")
    extension_x_weak_finality_weight: Decimal = Decimal("-0.07")

    minimum_premarket_coverage: Decimal = Decimal("0.90")
    minimum_total_premarket_bars: int = 12
    minimum_late_window_bars: int = 3
    maximum_latest_bar_lag_seconds: int = 300
    minimum_forward_sessions_before_review: int = 10
    minimum_forward_observations_before_review: int = 100
    design_evidence_sessions: tuple[date, ...] = V42_DESIGN_EVIDENCE_SESSIONS
    first_eligible_forward_session: date = V42_FIRST_ELIGIBLE_FORWARD_SESSION

    @property
    def implementation_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


DEFAULT_V42_SPEC = V42ModelSpec()


class V42FeatureBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mechanisms: V42MechanismHeads
    interactions: V42RiskInteractions
    feature_fingerprint: str


class V42Forecast(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    cohort_id: str
    cohort_fingerprint: str
    predictor_version: Literal["prospective-gap-v4.2-shadow"] = V42_PREDICTOR_VERSION
    feature_schema_version: Literal["prospective-gap-features-v4.2"] = V42_FEATURE_SCHEMA_VERSION
    model_spec_fingerprint: str
    feature_fingerprint: str
    frozen_at: datetime
    p_close_above_open: Decimal = Field(ge=0, le=1)
    mechanisms: V42MechanismHeads
    interactions: V42RiskInteractions
    return_distribution: V42ReturnDistribution
    uncertainty: Literal["moderate", "high"]

    @field_validator("frozen_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v42_frozen_at_must_be_timezone_aware")
        return value.astimezone(timezone.utc)

    @property
    def immutable_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class V42ForecastAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    session_date: date
    attempted_at: datetime
    model_state: ModelState
    failure_reason: str | None = None
    forecast_fingerprint: str | None = None

    @field_validator("attempted_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("v42_attempted_at_must_be_timezone_aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def consistent(self):
        if self.model_state == "PRODUCED" and self.forecast_fingerprint is None:
            raise ValueError("v42_produced_attempt_requires_forecast")
        if self.model_state != "PRODUCED" and not self.failure_reason:
            raise ValueError("v42_nonproduced_attempt_requires_reason")
        return self


def session_eligible_for_v42_forward_validation(
    session_date: date,
    *,
    spec: V42ModelSpec = DEFAULT_V42_SPEC,
) -> bool:
    return (
        session_date >= spec.first_eligible_forward_session
        and session_date not in set(spec.design_evidence_sessions)
    )


def demand_evidence_missing(
    snapshot: PremarketMarketStateSnapshot,
) -> tuple[str, ...]:
    missing = []
    for name in V42_REQUIRED_DEMAND_FEATURES:
        value = _feature(snapshot, name)
        if value is None:
            missing.append(name)
    return tuple(missing)


def build_v42_feature_bundle(
    *,
    candidate: GapperCandidate,
    market_state: PremarketMarketStateSnapshot,
    catalyst: CatalystDecomposition,
    v4_mechanisms: MechanismRiskScores,
    extension_risk: ExtensionExhaustionRisk,
    regime_tags: Sequence[RegimeTag] = (),
) -> V42FeatureBundle:
    missing = demand_evidence_missing(market_state)
    if missing:
        raise ValueError("v42_missing_complete_demand_evidence:" + ",".join(missing))

    vwap_distance = _feature(market_state, "distance_from_premarket_vwap_pct")
    range_position = _feature(market_state, "position_in_premarket_range")
    turnover = _feature(market_state, "float_turnover")
    acceleration = _feature(market_state, "late_premarket_acceleration")
    late_volume_share = _feature(market_state, "late_premarket_volume_share")
    assert vwap_distance is not None
    assert range_position is not None
    assert turnover is not None
    assert acceleration is not None
    assert late_volume_share is not None

    vwap_support = _clamp01((vwap_distance + Decimal("5")) / Decimal("15"))
    acceleration01 = _clamp01((acceleration + Decimal("1")) / Decimal("2"))
    turnover01 = _clamp01(turnover / Decimal("2"))
    volume_share01 = _clamp01(late_volume_share / Decimal("0.30"))
    demand = _clamp01(
        range_position * Decimal("0.25")
        + vwap_support * Decimal("0.25")
        + acceleration01 * Decimal("0.25")
        + volume_share01 * Decimal("0.15")
        + turnover01 * Decimal("0.10")
    )

    fundamental = _clamp01(
        (
            catalyst.strength
            + catalyst.finality
            + catalyst.economic_materiality
        )
        / Decimal("3")
    )
    tags = set(regime_tags)
    theme_squeeze = max(
        v4_mechanisms.squeeze_tail_score,
        Decimal("0.80") if "SQUEEZE_MOMENTUM" in tags else Decimal("0"),
    )
    catalyst_information = (
        catalyst.strength + catalyst.finality + catalyst.freshness
    ) / Decimal("3")
    low_info = _clamp01(
        (Decimal("1") - catalyst_information)
        * (Decimal("0.45") + demand * Decimal("0.55"))
    )

    exhaustion_tape = _clamp01(
        extension_risk.score * Decimal("0.45")
        + range_position * Decimal("0.20")
        + vwap_support * Decimal("0.15")
        + (Decimal("1") - acceleration01) * Decimal("0.10")
        + (Decimal("1") - volume_share01) * Decimal("0.10")
    )
    opening_exhaustion = max(
        exhaustion_tape,
        v4_mechanisms.opening_exhaustion_score,
    )

    dilution_count = len(candidate.dilution_flags)
    active_supply = "SUPPLY_OVERHANG" in tags
    supply_severity = (
        Decimal(min(3, dilution_count)) / Decimal("3")
        if active_supply
        else Decimal("0")
    )
    supply_pressure = _clamp01(
        max(
            Decimal("1") if active_supply else Decimal("0"),
            supply_severity,
        )
    )
    supply_fade = _clamp01(
        max(
            supply_pressure,
            v4_mechanisms.fade_risk_score * Decimal("0.75"),
        )
    )

    spread_risk = (
        _clamp01((candidate.spread_bps or Decimal("0")) / Decimal("300"))
    )
    dollar_volume_risk = _clamp01(
        (Decimal("10000000") - min(candidate.premarket_dollar_volume, Decimal("10000000")))
        / Decimal("10000000")
    )
    low_liquidity = max(
        spread_risk,
        dollar_volume_risk,
        Decimal("0.85") if "LOW_LIQUIDITY" in tags else Decimal("0"),
    )
    interactions = V42RiskInteractions(
        extension_x_supply=extension_risk.score * supply_pressure,
        extension_x_low_liquidity=extension_risk.score * low_liquidity,
        extension_x_weak_finality=extension_risk.score * (Decimal("1") - catalyst.finality),
    )
    remaining_upside = _clamp01(
        demand
        * (Decimal("1") - _clamp01(opening_exhaustion))
        * (Decimal("0.60") + catalyst.economic_materiality * Decimal("0.40"))
    )
    mechanisms = V42MechanismHeads(
        fundamental_reprice_score=fundamental,
        theme_squeeze_score=_clamp01(theme_squeeze),
        low_information_technical_score=low_info,
        continuation_demand_score=demand,
        remaining_upside_score=remaining_upside,
        opening_exhaustion_score=_clamp01(opening_exhaustion),
        supply_fade_score=supply_fade,
    )
    fingerprint = _hash(
        {
            "candidate": {
                "instrument_id": candidate.instrument_id,
                "spread_bps": str(candidate.spread_bps),
                "premarket_dollar_volume": str(candidate.premarket_dollar_volume),
                "dilution_flags": candidate.dilution_flags,
            },
            "market_state": market_state.live_feature_fingerprint,
            "catalyst": catalyst.model_dump(mode="json"),
            "v4_mechanisms": v4_mechanisms.model_dump(mode="json"),
            "extension": extension_risk.model_dump(mode="json"),
            "regime_tags": tuple(regime_tags),
            "mechanisms": mechanisms.model_dump(mode="json"),
            "interactions": interactions.model_dump(mode="json"),
        }
    )
    return V42FeatureBundle(
        mechanisms=mechanisms,
        interactions=interactions,
        feature_fingerprint=fingerprint,
    )


def score_v42_probability(
    *,
    catalyst: CatalystDecomposition,
    extension_risk: ExtensionExhaustionRisk,
    features: V42FeatureBundle,
    spec: V42ModelSpec = DEFAULT_V42_SPEC,
) -> Decimal:
    m = features.mechanisms
    x = features.interactions
    probability = (
        spec.intercept
        + catalyst.strength * spec.catalyst_strength_weight
        + catalyst.finality * spec.catalyst_finality_weight
        + catalyst.freshness * spec.catalyst_freshness_weight
        + catalyst.economic_materiality * spec.catalyst_materiality_weight
        + m.fundamental_reprice_score * spec.fundamental_reprice_weight
        + m.theme_squeeze_score * spec.theme_squeeze_weight
        + m.low_information_technical_score * spec.low_information_technical_weight
        + m.continuation_demand_score * spec.continuation_demand_weight
        + m.remaining_upside_score * spec.remaining_upside_weight
        + m.opening_exhaustion_score * spec.opening_exhaustion_weight
        + extension_risk.score * spec.extension_exhaustion_weight
        + m.supply_fade_score * spec.supply_fade_weight
        + x.extension_x_supply * spec.extension_x_supply_weight
        + x.extension_x_low_liquidity * spec.extension_x_low_liquidity_weight
        + x.extension_x_weak_finality * spec.extension_x_weak_finality_weight
    )
    return _clamp(probability, Decimal("0.05"), Decimal("0.95"))


def build_v42_return_distribution(
    *,
    probability: Decimal,
    mechanisms: V42MechanismHeads,
    interactions: V42RiskInteractions,
    extension_risk: ExtensionExhaustionRisk,
) -> V42ReturnDistribution:
    positive = _clamp01(
        mechanisms.remaining_upside_score * Decimal("0.40")
        + mechanisms.continuation_demand_score * Decimal("0.30")
        + mechanisms.theme_squeeze_score * Decimal("0.15")
        + mechanisms.fundamental_reprice_score * Decimal("0.10")
        + mechanisms.low_information_technical_score * Decimal("0.05")
    )
    downside = _clamp01(
        mechanisms.opening_exhaustion_score * Decimal("0.28")
        + mechanisms.supply_fade_score * Decimal("0.24")
        + extension_risk.score * Decimal("0.16")
        + interactions.extension_x_supply * Decimal("0.12")
        + interactions.extension_x_low_liquidity * Decimal("0.10")
        + interactions.extension_x_weak_finality * Decimal("0.10")
    )

    expected = _clamp(
        (probability - Decimal("0.50")) * Decimal("0.20")
        + positive * Decimal("0.045")
        - downside * Decimal("0.060"),
        Decimal("-0.20"),
        Decimal("0.20"),
    )
    downside_width = Decimal("0.035") + downside * Decimal("0.165")
    upside_width = Decimal("0.035") + positive * Decimal("0.145")
    q10 = _clamp(expected - downside_width, Decimal("-0.60"), Decimal("0.25"))
    q50 = expected
    q90 = _clamp(expected + upside_width, Decimal("-0.20"), Decimal("0.60"))
    expected_shortfall = _clamp(
        q10 - downside_width * Decimal("0.45"),
        Decimal("-0.80"),
        q10,
    )
    p_gt_2 = _clamp01(
        probability
        + positive * Decimal("0.20")
        - downside * Decimal("0.12")
        - Decimal("0.08")
    )
    p_lt_minus_5 = _clamp01(
        (Decimal("1") - probability) * Decimal("0.45")
        + downside * Decimal("0.50")
        - positive * Decimal("0.12")
    )
    expected_mae = _clamp(
        -(Decimal("0.025") + downside * Decimal("0.20")),
        Decimal("-0.40"),
        Decimal("-0.01"),
    )
    expected_mfe = _clamp(
        Decimal("0.025") + positive * Decimal("0.20"),
        Decimal("0.01"),
        Decimal("0.40"),
    )
    return V42ReturnDistribution(
        q10=q10,
        q50=q50,
        q90=q90,
        expected_return=expected,
        expected_shortfall_10pct=expected_shortfall,
        p_return_gt_2pct=p_gt_2,
        p_return_lt_minus_5pct=p_lt_minus_5,
        expected_mae=expected_mae,
        expected_mfe=expected_mfe,
    )


def freeze_v42_forecast(
    *,
    candidate: GapperCandidate,
    session_date: date,
    cohort_id: str,
    cohort_fingerprint: str,
    market_state: PremarketMarketStateSnapshot,
    catalyst: CatalystDecomposition,
    v4_mechanisms: MechanismRiskScores,
    extension_risk: ExtensionExhaustionRisk,
    regime_tags: Sequence[RegimeTag],
    frozen_at: datetime,
    spec: V42ModelSpec = DEFAULT_V42_SPEC,
) -> V42Forecast:
    if not session_eligible_for_v42_forward_validation(session_date, spec=spec):
        raise ValueError("v42_session_not_forward_eligible")
    if frozen_at.tzinfo is None:
        raise ValueError("v42_frozen_at_must_be_timezone_aware")
    if frozen_at.astimezone(timezone.utc) > market_state.prediction_cutoff_at:
        raise ValueError("v42_forecast_after_market_state_cutoff")

    features = build_v42_feature_bundle(
        candidate=candidate,
        market_state=market_state,
        catalyst=catalyst,
        v4_mechanisms=v4_mechanisms,
        extension_risk=extension_risk,
        regime_tags=regime_tags,
    )
    probability = score_v42_probability(
        catalyst=catalyst,
        extension_risk=extension_risk,
        features=features,
        spec=spec,
    )
    distribution = build_v42_return_distribution(
        probability=probability,
        mechanisms=features.mechanisms,
        interactions=features.interactions,
        extension_risk=extension_risk,
    )
    uncertainty: Literal["moderate", "high"] = (
        "high"
        if max(
            features.mechanisms.supply_fade_score,
            features.mechanisms.opening_exhaustion_score,
            features.interactions.extension_x_low_liquidity,
        ) >= Decimal("0.70")
        else "moderate"
    )
    return V42Forecast(
        instrument_id=candidate.instrument_id,
        session_date=session_date,
        cohort_id=cohort_id,
        cohort_fingerprint=cohort_fingerprint,
        model_spec_fingerprint=spec.implementation_fingerprint,
        feature_fingerprint=features.feature_fingerprint,
        frozen_at=frozen_at,
        p_close_above_open=probability,
        mechanisms=features.mechanisms,
        interactions=features.interactions,
        return_distribution=distribution,
        uncertainty=uncertainty,
    )


class V42ReturnObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    forecast: V42Forecast
    realized_return: Decimal


class V42ReturnMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    n: int
    expected_return_mae: Decimal | None = None
    q10_pinball_loss: Decimal | None = None
    q50_pinball_loss: Decimal | None = None
    q90_pinball_loss: Decimal | None = None
    p_gt_2_brier: Decimal | None = None
    p_lt_minus_5_brier: Decimal | None = None
    q10_breach_rate: Decimal | None = None
    mean_predicted_expected_return: Decimal | None = None
    mean_realized_return: Decimal | None = None


def _pinball(actual: Decimal, quantile: Decimal, q: Decimal) -> Decimal:
    error = actual - quantile
    return q * error if error >= 0 else (q - Decimal("1")) * error


def evaluate_v42_return_metrics(
    observations: Sequence[V42ReturnObservation],
) -> V42ReturnMetrics:
    if not observations:
        return V42ReturnMetrics(n=0)
    n = Decimal(len(observations))
    mae = Decimal("0")
    q10_loss = Decimal("0")
    q50_loss = Decimal("0")
    q90_loss = Decimal("0")
    gt_brier = Decimal("0")
    lt_brier = Decimal("0")
    breaches = 0
    predicted = Decimal("0")
    realized = Decimal("0")
    for row in observations:
        d = row.forecast.return_distribution
        actual = row.realized_return
        mae += abs(d.expected_return - actual)
        q10_loss += _pinball(actual, d.q10, Decimal("0.10"))
        q50_loss += _pinball(actual, d.q50, Decimal("0.50"))
        q90_loss += _pinball(actual, d.q90, Decimal("0.90"))
        y_gt = Decimal("1") if actual > Decimal("0.02") else Decimal("0")
        y_lt = Decimal("1") if actual < Decimal("-0.05") else Decimal("0")
        gt_brier += (d.p_return_gt_2pct - y_gt) ** 2
        lt_brier += (d.p_return_lt_minus_5pct - y_lt) ** 2
        breaches += int(actual < d.q10)
        predicted += d.expected_return
        realized += actual
    return V42ReturnMetrics(
        n=len(observations),
        expected_return_mae=mae / n,
        q10_pinball_loss=q10_loss / n,
        q50_pinball_loss=q50_loss / n,
        q90_pinball_loss=q90_loss / n,
        p_gt_2_brier=gt_brier / n,
        p_lt_minus_5_brier=lt_brier / n,
        q10_breach_rate=Decimal(breaches) / n,
        mean_predicted_expected_return=predicted / n,
        mean_realized_return=realized / n,
    )


__all__ = [
    "DEFAULT_V42_SPEC",
    "V42_ACTIVATION_STATE",
    "V42_DESIGN_EVIDENCE_SESSIONS",
    "V42_FEATURE_SCHEMA_VERSION",
    "V42_FIRST_ELIGIBLE_FORWARD_SESSION",
    "V42_PREDICTOR_VERSION",
    "V42_REQUIRED_DEMAND_FEATURES",
    "V42FeatureBundle",
    "V42Forecast",
    "V42ForecastAttempt",
    "V42MechanismHeads",
    "V42ModelSpec",
    "V42ReturnDistribution",
    "V42ReturnMetrics",
    "V42ReturnObservation",
    "V42RiskInteractions",
    "build_v42_feature_bundle",
    "build_v42_return_distribution",
    "demand_evidence_missing",
    "evaluate_v42_return_metrics",
    "freeze_v42_forecast",
    "score_v42_probability",
    "session_eligible_for_v42_forward_validation",
]
