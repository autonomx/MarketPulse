from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .bounce_model import BounceFeatureVector
from .bounce_training import BounceModelArtifact, score_fitted_bounce_model


class BounceValidationExample(BaseModel):
    """Out-of-sample labeled example tied to an immutable trading session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_date: date
    features: BounceFeatureVector
    label: int = Field(ge=0, le=1)


class CalibrationBin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lower: Decimal
    upper: Decimal
    count: int
    mean_probability: Decimal
    observed_rate: Decimal


class BounceValidationMetrics(BaseModel):
    """Transparent OOS metrics; this is research evidence, never an execution gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    model_version: str
    artifact_fingerprint: str
    examples: int
    sessions: int
    positives: int
    positive_rate: Decimal
    mean_probability: Decimal
    log_loss: Decimal
    baseline_log_loss: Decimal
    log_loss_improvement: Decimal
    brier_score: Decimal
    expected_calibration_error: Decimal
    calibration_bins: tuple[CalibrationBin, ...]
    minimum_examples_required: int
    minimum_sessions_required: int
    evidence_volume_sufficient: bool
    shadow_only: bool = True


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def validate_bounce_artifact(
    artifact: BounceModelArtifact,
    examples: list[BounceValidationExample] | tuple[BounceValidationExample, ...],
    *,
    observed_at: datetime | None = None,
    minimum_examples: int = 100,
    minimum_sessions: int = 20,
) -> BounceValidationMetrics:
    frozen = tuple(examples)
    if not frozen:
        raise ValueError("bounce validation requires examples")
    if minimum_examples < 1 or minimum_sessions < 1:
        raise ValueError("bounce validation evidence thresholds must be positive")
    labels = [int(item.label) for item in frozen]
    positives = sum(labels)
    if positives == 0 or positives == len(labels):
        raise ValueError("bounce validation requires both outcome classes")
    timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    probabilities = [
        float(
            score_fitted_bounce_model(
                artifact,
                item.features,
                observed_at=timestamp,
            ).probability
        )
        for item in frozen
    ]
    epsilon = 1e-12
    log_loss = -sum(
        label * math.log(max(epsilon, probability))
        + (1 - label) * math.log(max(epsilon, 1 - probability))
        for probability, label in zip(probabilities, labels)
    ) / len(labels)
    prevalence = positives / len(labels)
    baseline_log_loss = -sum(
        label * math.log(max(epsilon, prevalence))
        + (1 - label) * math.log(max(epsilon, 1 - prevalence))
        for label in labels
    ) / len(labels)
    brier = sum(
        (probability - label) ** 2
        for probability, label in zip(probabilities, labels)
    ) / len(labels)

    calibration: list[CalibrationBin] = []
    weighted_error = 0.0
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        members = [
            (probability, label)
            for probability, label in zip(probabilities, labels)
            if lower <= probability < upper or (index == 9 and probability == 1.0)
        ]
        if not members:
            continue
        mean_probability = sum(item[0] for item in members) / len(members)
        observed_rate = sum(item[1] for item in members) / len(members)
        weighted_error += abs(mean_probability - observed_rate) * len(members) / len(labels)
        calibration.append(
            CalibrationBin(
                lower=_decimal(lower),
                upper=_decimal(upper),
                count=len(members),
                mean_probability=_decimal(mean_probability),
                observed_rate=_decimal(observed_rate),
            )
        )

    session_count = len({item.session_date for item in frozen})
    return BounceValidationMetrics(
        model_id=artifact.model_id,
        model_version=artifact.model_version,
        artifact_fingerprint=artifact.fingerprint,
        examples=len(frozen),
        sessions=session_count,
        positives=positives,
        positive_rate=_decimal(prevalence),
        mean_probability=_decimal(sum(probabilities) / len(probabilities)),
        log_loss=_decimal(log_loss),
        baseline_log_loss=_decimal(baseline_log_loss),
        log_loss_improvement=_decimal(baseline_log_loss - log_loss),
        brier_score=_decimal(brier),
        expected_calibration_error=_decimal(weighted_error),
        calibration_bins=tuple(calibration),
        minimum_examples_required=minimum_examples,
        minimum_sessions_required=minimum_sessions,
        evidence_volume_sufficient=(
            len(frozen) >= minimum_examples and session_count >= minimum_sessions
        ),
        shadow_only=True,
    )
