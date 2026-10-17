from __future__ import annotations

"""Pre-registered prospective-gap v4.1 challenger contract.

This file is intentionally inactive. It freezes the next research hypothesis
before any session after 2026-09-21 can be used to evaluate it. Nothing here
mutates prospective-gap-v3 or prospective-gap-v4-shadow.
"""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


V41_PREDICTOR_VERSION: Final = "prospective-gap-v4.1-shadow"
V41_FEATURE_SCHEMA_VERSION: Final = "prospective-gap-features-v4.1"
V41_SPEC_VERSION: Final = "prospective-gap-v4.1-preregistered-spec-v1"
V41_ACTIVATION_STATE: Final = "PRE_REGISTERED_NOT_ACTIVE"

V41_DESIGN_EVIDENCE_SESSIONS = (
    date(2026, 9, 15),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 9, 18),
    date(2026, 9, 21),
)


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _clamp01(value: Decimal) -> Decimal:
    return min(Decimal("1"), max(Decimal("0"), value))


class V41MechanismHeads(BaseModel):
    """Independent mechanism heads, not calibrated probabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fundamental_reprice_score: Decimal = Field(ge=0, le=1)
    theme_squeeze_score: Decimal = Field(ge=0, le=1)
    low_information_technical_score: Decimal = Field(ge=0, le=1)
    continuation_demand_score: Decimal = Field(ge=0, le=1)
    opening_exhaustion_score: Decimal = Field(ge=0, le=1)
    supply_fade_score: Decimal = Field(ge=0, le=1)


class V41ReturnDistribution(BaseModel):
    """Economic outputs required before v4.1 can authorize Portfolio E capital."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    q10: Decimal
    q50: Decimal
    q90: Decimal
    expected_return: Decimal
    expected_shortfall_10pct: Decimal
    p_return_gt_2pct: Decimal = Field(ge=0, le=1)
    p_return_lt_minus_5pct: Decimal = Field(ge=0, le=1)
    expected_mae: Decimal | None = None
    expected_mfe: Decimal | None = None

    @model_validator(mode="after")
    def ordered(self):
        if not self.q10 <= self.q50 <= self.q90:
            raise ValueError("v41_return_quantiles_must_be_monotonic")
        return self


class V41ModelSpec(BaseModel):
    """Frozen hypothesis weights for future-only shadow validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_version: Literal[
        "prospective-gap-v4.1-preregistered-spec-v1"
    ] = V41_SPEC_VERSION
    predictor_version: Literal[
        "prospective-gap-v4.1-shadow"
    ] = V41_PREDICTOR_VERSION
    feature_schema_version: Literal[
        "prospective-gap-features-v4.1"
    ] = V41_FEATURE_SCHEMA_VERSION
    activation_state: Literal[
        "PRE_REGISTERED_NOT_ACTIVE"
    ] = V41_ACTIVATION_STATE

    intercept: Decimal = Decimal("0.34")
    catalyst_strength_weight: Decimal = Decimal("0.07")
    catalyst_finality_weight: Decimal = Decimal("0.05")
    catalyst_freshness_weight: Decimal = Decimal("0.04")
    catalyst_materiality_weight: Decimal = Decimal("0.07")
    fundamental_reprice_weight: Decimal = Decimal("0.08")
    theme_squeeze_weight: Decimal = Decimal("0.07")
    low_information_technical_weight: Decimal = Decimal("0.06")
    continuation_demand_weight: Decimal = Decimal("0.14")
    opening_exhaustion_weight: Decimal = Decimal("-0.12")
    extension_exhaustion_weight: Decimal = Decimal("-0.16")
    supply_fade_weight: Decimal = Decimal("-0.08")

    design_evidence_sessions: tuple[date, ...] = V41_DESIGN_EVIDENCE_SESSIONS
    first_eligible_forward_session: date = date(2026, 9, 22)
    minimum_forward_sessions_before_review: int = 10
    minimum_forward_observations_before_review: int = 100
    return_distribution_required_for_action: Literal[True] = True
    confirmation_required_for_action: Literal[True] = True
    positive_net_alpha_required_for_action: Literal[True] = True

    @property
    def implementation_fingerprint(self) -> str:
        return _hash(self.model_dump(mode="json"))


class V41ScoreInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalyst_strength: Decimal = Field(ge=0, le=1)
    catalyst_finality: Decimal = Field(ge=0, le=1)
    catalyst_freshness: Decimal = Field(ge=0, le=1)
    catalyst_materiality: Decimal = Field(ge=0, le=1)
    extension_exhaustion_score: Decimal = Field(ge=0, le=1)
    mechanisms: V41MechanismHeads


DEFAULT_V41_SPEC = V41ModelSpec()


def score_v41_raw_probability(
    inputs: V41ScoreInputs,
    *,
    spec: V41ModelSpec = DEFAULT_V41_SPEC,
) -> Decimal:
    """Deterministic frozen v4.1 score, available for future shadow activation only."""

    m = inputs.mechanisms
    value = (
        spec.intercept
        + inputs.catalyst_strength * spec.catalyst_strength_weight
        + inputs.catalyst_finality * spec.catalyst_finality_weight
        + inputs.catalyst_freshness * spec.catalyst_freshness_weight
        + inputs.catalyst_materiality * spec.catalyst_materiality_weight
        + m.fundamental_reprice_score * spec.fundamental_reprice_weight
        + m.theme_squeeze_score * spec.theme_squeeze_weight
        + m.low_information_technical_score * spec.low_information_technical_weight
        + m.continuation_demand_score * spec.continuation_demand_weight
        + m.opening_exhaustion_score * spec.opening_exhaustion_weight
        + inputs.extension_exhaustion_score * spec.extension_exhaustion_weight
        + m.supply_fade_score * spec.supply_fade_weight
    )
    return min(Decimal("0.95"), max(Decimal("0.05"), value))


def session_eligible_for_v41_forward_validation(
    session_date: date,
    *,
    spec: V41ModelSpec = DEFAULT_V41_SPEC,
) -> bool:
    return (
        session_date >= spec.first_eligible_forward_session
        and session_date not in set(spec.design_evidence_sessions)
    )


__all__ = [
    "DEFAULT_V41_SPEC",
    "V41_ACTIVATION_STATE",
    "V41_DESIGN_EVIDENCE_SESSIONS",
    "V41_FEATURE_SCHEMA_VERSION",
    "V41_PREDICTOR_VERSION",
    "V41_SPEC_VERSION",
    "V41MechanismHeads",
    "V41ModelSpec",
    "V41ReturnDistribution",
    "V41ScoreInputs",
    "score_v41_raw_probability",
    "session_eligible_for_v41_forward_validation",
]
