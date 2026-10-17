from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bounce_model import LABEL_DEFINITION, BounceFeatureVector, BounceModelScore


FEATURE_NAMES: tuple[str, ...] = (
    "gap_pct",
    "premarket_dollar_volume_log10",
    "tod_rvol",
    "float_shares_log10",
    "market_cap_log10",
    "spread_bps",
    "opening_impulse_pct",
    "hod_distance_pct",
    "pullback_depth_pct",
    "pullback_volume_ratio",
    "l2_over_l1",
    "vwap_distance_pct",
    "vwap_slope_pct",
    "breakout_volume_ratio",
    "atr_pct",
    "minutes_since_open",
    "catalyst_positive",
    "catalyst_negative",
    "dilution_flag",
)


class BounceTrainingExample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    features: BounceFeatureVector
    label: Literal[0, 1]


class BounceModelArtifact(BaseModel):
    """Versioned, auditable logistic-regression artifact kept shadow-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: Literal["gap_pullback_logistic"] = "gap_pullback_logistic"
    model_version: str = Field(min_length=1, max_length=200)
    label_definition: Literal["P(+2R before -1R within 90 minutes)"] = LABEL_DEFINITION
    trained_at: datetime
    feature_names: tuple[str, ...] = FEATURE_NAMES
    coefficients: dict[str, Decimal]
    intercept: Decimal
    feature_means: dict[str, Decimal]
    feature_scales: dict[str, Decimal]
    training_examples: int = Field(gt=0)
    positive_examples: int = Field(ge=0)
    iterations: int = Field(gt=0)
    learning_rate: Decimal = Field(gt=0)
    l2_penalty: Decimal = Field(ge=0)
    training_log_loss: Decimal = Field(ge=0)
    shadow_only: Literal[True] = True
    fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("trained_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("trained_at must be timezone-aware")
        return value.astimezone(timezone.utc)


def _raw_features(vector: BounceFeatureVector) -> list[float]:
    payload = vector.model_dump()
    return [float(payload.get(name) or 0) for name in FEATURE_NAMES]


def _sigmoid(value: float) -> float:
    clipped = max(-60.0, min(60.0, value))
    return 1.0 / (1.0 + math.exp(-clipped))


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _artifact_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def fit_bounce_logistic(
    examples: list[BounceTrainingExample] | tuple[BounceTrainingExample, ...],
    *,
    model_version: str,
    trained_at: datetime | None = None,
    iterations: int = 800,
    learning_rate: Decimal = Decimal("0.05"),
    l2_penalty: Decimal = Decimal("0.001"),
) -> BounceModelArtifact:
    """Fit a deterministic standardized logistic baseline with batch gradient descent.

    This deliberately avoids opaque AutoML. The fitted artifact stores every
    coefficient and normalization statistic and remains shadow-only.
    """
    frozen = tuple(examples)
    if len(frozen) < 20:
        raise ValueError("bounce model training requires at least 20 examples")
    positives = sum(item.label for item in frozen)
    if positives == 0 or positives == len(frozen):
        raise ValueError("bounce model training requires both outcome classes")
    if iterations < 1 or iterations > 20_000:
        raise ValueError("iterations must be between 1 and 20000")
    learning = float(learning_rate)
    penalty = float(l2_penalty)
    if learning <= 0 or learning > 2:
        raise ValueError("learning_rate must be in (0, 2]")
    if penalty < 0 or penalty > 10:
        raise ValueError("l2_penalty must be in [0, 10]")

    matrix = [_raw_features(item.features) for item in frozen]
    labels = [float(item.label) for item in frozen]
    columns = len(FEATURE_NAMES)
    count = len(matrix)
    means = [sum(row[column] for row in matrix) / count for column in range(columns)]
    scales: list[float] = []
    for column, mean in enumerate(means):
        variance = sum((row[column] - mean) ** 2 for row in matrix) / count
        scale = math.sqrt(variance)
        scales.append(scale if scale > 1e-12 else 1.0)
    normalized = [
        [(row[column] - means[column]) / scales[column] for column in range(columns)]
        for row in matrix
    ]

    weights = [0.0] * columns
    prevalence = min(1 - 1e-6, max(1e-6, positives / count))
    intercept = math.log(prevalence / (1 - prevalence))
    for _ in range(iterations):
        probabilities = [
            _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, row)))
            for row in normalized
        ]
        errors = [probability - label for probability, label in zip(probabilities, labels)]
        intercept_gradient = sum(errors) / count
        gradients = [
            sum(error * row[column] for error, row in zip(errors, normalized)) / count
            + penalty * weights[column]
            for column in range(columns)
        ]
        intercept -= learning * intercept_gradient
        weights = [weight - learning * gradient for weight, gradient in zip(weights, gradients)]

    probabilities = [
        _sigmoid(intercept + sum(weight * value for weight, value in zip(weights, row)))
        for row in normalized
    ]
    epsilon = 1e-12
    log_loss = -sum(
        label * math.log(max(epsilon, probability))
        + (1 - label) * math.log(max(epsilon, 1 - probability))
        for probability, label in zip(probabilities, labels)
    ) / count
    fitted_at = (trained_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    coefficients = {
        name: _decimal(weights[index]) for index, name in enumerate(FEATURE_NAMES)
    }
    feature_means = {
        name: _decimal(means[index]) for index, name in enumerate(FEATURE_NAMES)
    }
    feature_scales = {
        name: _decimal(scales[index]) for index, name in enumerate(FEATURE_NAMES)
    }
    payload: dict[str, Any] = {
        "model_id": "gap_pullback_logistic",
        "model_version": model_version,
        "label_definition": LABEL_DEFINITION,
        "trained_at": fitted_at.isoformat(),
        "feature_names": FEATURE_NAMES,
        "coefficients": coefficients,
        "intercept": _decimal(intercept),
        "feature_means": feature_means,
        "feature_scales": feature_scales,
        "training_examples": count,
        "positive_examples": positives,
        "iterations": iterations,
        "learning_rate": learning_rate,
        "l2_penalty": l2_penalty,
        "training_log_loss": _decimal(log_loss),
        "shadow_only": True,
    }
    return BounceModelArtifact(
        **payload,
        fingerprint=_artifact_fingerprint(payload),
    )


def score_fitted_bounce_model(
    artifact: BounceModelArtifact,
    features: BounceFeatureVector,
    *,
    observed_at: datetime,
) -> BounceModelScore:
    raw = _raw_features(features)
    z = float(artifact.intercept)
    for index, name in enumerate(FEATURE_NAMES):
        mean = float(artifact.feature_means[name])
        scale = float(artifact.feature_scales[name])
        normalized = (raw[index] - mean) / (scale if abs(scale) > 1e-12 else 1.0)
        z += float(artifact.coefficients[name]) * normalized
    probability = Decimal(str(_sigmoid(z)))
    payload = {
        "model_id": artifact.model_id,
        "model_version": artifact.model_version,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "probability": str(probability),
        "features": features.model_dump(mode="json"),
        "label_definition": artifact.label_definition,
        "artifact_fingerprint": artifact.fingerprint,
        "shadow_only": True,
    }
    fingerprint = _artifact_fingerprint(payload)
    return BounceModelScore(
        model_id=artifact.model_id,
        model_version=artifact.model_version,
        observed_at=observed_at,
        probability=probability,
        label_definition=artifact.label_definition,
        features=features,
        shadow_only=True,
        fingerprint=fingerprint,
    )
