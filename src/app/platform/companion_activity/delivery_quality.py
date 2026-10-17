"""Diagnostic delivery scoring and content-free quality metrics.

The scorer ranks already-authorized intents. It is not an authority boundary and must not
bypass presence or initiative decisions. Component scores remain visible for diagnosis.
"""
from __future__ import annotations

import threading
from datetime import datetime

from pydantic import Field

from .cognition import DeliveryIntent
from .contracts import FrozenContract


class DeliveryQualityFeatures(FrozenContract):
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    open_loop_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    progress_significance: float = Field(default=0.0, ge=0.0, le=1.0)
    interruption_cost: float = Field(default=0.0, ge=0.0, le=1.0)
    repetition_debt: float = Field(default=0.0, ge=0.0, le=1.0)
    freshness_window_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)


class DeliveryQualityScore(FrozenContract):
    intent_id: str = Field(min_length=1, max_length=240)
    salience: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    relevance: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    open_loop_relevance: float = Field(ge=0.0, le=1.0)
    progress_significance: float = Field(ge=0.0, le=1.0)
    interruption_cost: float = Field(ge=0.0, le=1.0)
    repetition_debt: float = Field(ge=0.0, le=1.0)
    composite: float = Field(ge=0.0, le=1.0)


class CompanionDeliveryQualityScorer:
    """Score delivery value while retaining all diagnostic dimensions."""

    def score(
        self,
        intent: DeliveryIntent,
        features: DeliveryQualityFeatures,
        *,
        now: datetime,
    ) -> DeliveryQualityScore:
        age_seconds = max(0.0, (now - intent.created_at).total_seconds())
        freshness = max(0.0, 1.0 - age_seconds / features.freshness_window_seconds)
        positive = (
            0.22 * intent.salience
            + 0.14 * features.novelty
            + 0.16 * features.relevance
            + 0.12 * freshness
            + 0.12 * intent.confidence
            + 0.10 * features.open_loop_relevance
            + 0.08 * features.progress_significance
        )
        penalty = 0.12 * features.interruption_cost + 0.10 * features.repetition_debt
        composite = min(1.0, max(0.0, positive - penalty))
        return DeliveryQualityScore(
            intent_id=intent.intent_id,
            salience=intent.salience,
            novelty=features.novelty,
            relevance=features.relevance,
            freshness=freshness,
            confidence=intent.confidence,
            open_loop_relevance=features.open_loop_relevance,
            progress_significance=features.progress_significance,
            interruption_cost=features.interruption_cost,
            repetition_debt=features.repetition_debt,
            composite=composite,
        )


class DeliveryQualityOutcome(FrozenContract):
    delivered: bool = False
    stale_suppressed: bool = False
    repeated_suppressed: bool = False
    interrupted: bool = False
    ignored_by_user: bool = False
    activity_state_corrected: bool = False
    false_objective_transition: bool = False
    open_loop_resolution_predicted: bool = False
    open_loop_resolution_correct: bool = False
    memory_promotion_predicted: bool = False
    memory_promotion_correct: bool = False


class DeliveryQualityMetricsSnapshot(FrozenContract):
    samples: int = Field(ge=0)
    delivered: int = Field(ge=0)
    stale_comment_rate: float = Field(ge=0.0, le=1.0)
    repeated_comment_rate: float = Field(ge=0.0, le=1.0)
    interruption_rate: float = Field(ge=0.0, le=1.0)
    ignored_initiative_rate: float = Field(ge=0.0, le=1.0)
    activity_state_correction_rate: float = Field(ge=0.0, le=1.0)
    false_objective_transition_rate: float = Field(ge=0.0, le=1.0)
    open_loop_completion_precision: float = Field(ge=0.0, le=1.0)
    memory_promotion_precision: float = Field(ge=0.0, le=1.0)


class CompanionDeliveryQualityMetrics:
    """In-memory content-free counters suitable for release evaluation aggregation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples = 0
        self._delivered = 0
        self._stale = 0
        self._repeated = 0
        self._interrupted = 0
        self._ignored = 0
        self._corrections = 0
        self._false_objective = 0
        self._loop_predicted = 0
        self._loop_correct = 0
        self._memory_predicted = 0
        self._memory_correct = 0

    def record(self, outcome: DeliveryQualityOutcome) -> None:
        with self._lock:
            self._samples += 1
            self._delivered += int(outcome.delivered)
            self._stale += int(outcome.stale_suppressed)
            self._repeated += int(outcome.repeated_suppressed)
            self._interrupted += int(outcome.interrupted)
            self._ignored += int(outcome.ignored_by_user)
            self._corrections += int(outcome.activity_state_corrected)
            self._false_objective += int(outcome.false_objective_transition)
            self._loop_predicted += int(outcome.open_loop_resolution_predicted)
            self._loop_correct += int(
                outcome.open_loop_resolution_predicted and outcome.open_loop_resolution_correct
            )
            self._memory_predicted += int(outcome.memory_promotion_predicted)
            self._memory_correct += int(
                outcome.memory_promotion_predicted and outcome.memory_promotion_correct
            )

    def snapshot(self) -> DeliveryQualityMetricsSnapshot:
        with self._lock:
            samples = self._samples
            delivered = self._delivered
            return DeliveryQualityMetricsSnapshot(
                samples=samples,
                delivered=delivered,
                stale_comment_rate=_ratio(self._stale, samples),
                repeated_comment_rate=_ratio(self._repeated, samples),
                interruption_rate=_ratio(self._interrupted, delivered),
                ignored_initiative_rate=_ratio(self._ignored, delivered),
                activity_state_correction_rate=_ratio(self._corrections, samples),
                false_objective_transition_rate=_ratio(self._false_objective, samples),
                open_loop_completion_precision=_ratio(self._loop_correct, self._loop_predicted),
                memory_promotion_precision=_ratio(self._memory_correct, self._memory_predicted),
            )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return min(1.0, max(0.0, numerator / denominator))


__all__ = [
    "CompanionDeliveryQualityMetrics",
    "CompanionDeliveryQualityScorer",
    "DeliveryQualityFeatures",
    "DeliveryQualityMetricsSnapshot",
    "DeliveryQualityOutcome",
    "DeliveryQualityScore",
]
