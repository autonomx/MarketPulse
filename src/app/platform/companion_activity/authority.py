"""Predicate-specific authority policy for Companion Activity state."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .contracts import EvidenceProposition, FrozenContract

ActivityAuthoritySource = Literal[
    "user_explicit",
    "runtime_state",
    "trusted_process_integration",
    "deterministic_telemetry",
    "repeated_perception",
    "single_perception",
    "language_inference",
    "imported_unverified",
]
ActivityFieldFamily = Literal[
    "semantic_intent",
    "runtime_fact",
    "counted_event",
    "perceptual_state",
]
ActivityTransitionRule = Literal["immediate", "hysteresis", "monotonic_count"]
ActivityConflictPolicy = Literal[
    "authority_then_confidence",
    "newest_within_authority",
    "highest_count",
]
ActivityStalenessPolicy = Literal["persistent", "session", "short_lived"]


class ActivityFieldPolicy(FrozenContract):
    field_name: str = Field(min_length=1, max_length=160)
    family: ActivityFieldFamily
    allowed_sources: tuple[ActivityAuthoritySource, ...]
    authority_order: tuple[ActivityAuthoritySource, ...]
    transition_rule: ActivityTransitionRule
    confirmation_requirement: int = Field(default=1, ge=1, le=20)
    staleness_policy: ActivityStalenessPolicy = "session"
    stale_after_seconds: float | None = Field(default=None, ge=0.0, le=86_400.0)
    conflict_policy: ActivityConflictPolicy = "authority_then_confidence"

    def authority_rank(self, source: ActivityAuthoritySource) -> int:
        try:
            return self.authority_order.index(source)
        except ValueError:
            return len(self.authority_order) + 100

    def source_allowed(self, source: ActivityAuthoritySource) -> bool:
        return source in self.allowed_sources


SEMANTIC_INTENT_SOURCES: tuple[ActivityAuthoritySource, ...] = (
    "user_explicit",
    "deterministic_telemetry",
    "repeated_perception",
    "single_perception",
    "language_inference",
    "imported_unverified",
)
PERCEPTUAL_STATE_SOURCES: tuple[ActivityAuthoritySource, ...] = (
    "trusted_process_integration",
    "deterministic_telemetry",
    "user_explicit",
    "repeated_perception",
    "single_perception",
    "language_inference",
    "imported_unverified",
)
COUNTED_EVENT_SOURCES: tuple[ActivityAuthoritySource, ...] = (
    "deterministic_telemetry",
    "repeated_perception",
    "single_perception",
    "language_inference",
    "imported_unverified",
)


DEFAULT_ACTIVITY_FIELD_POLICIES: dict[str, ActivityFieldPolicy] = {
    "current_objective": ActivityFieldPolicy(
        field_name="current_objective",
        family="semantic_intent",
        allowed_sources=SEMANTIC_INTENT_SOURCES,
        authority_order=(
            "user_explicit",
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=2,
        staleness_policy="persistent",
    ),
    "current_subtask": ActivityFieldPolicy(
        field_name="current_subtask",
        family="semantic_intent",
        allowed_sources=SEMANTIC_INTENT_SOURCES,
        authority_order=(
            "user_explicit",
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=2,
        staleness_policy="session",
    ),
    "strategy": ActivityFieldPolicy(
        field_name="strategy",
        family="semantic_intent",
        allowed_sources=SEMANTIC_INTENT_SOURCES,
        authority_order=(
            "user_explicit",
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=3,
        staleness_policy="session",
    ),
    "foreground_application": ActivityFieldPolicy(
        field_name="foreground_application",
        family="runtime_fact",
        allowed_sources=(
            "runtime_state",
            "trusted_process_integration",
            "repeated_perception",
            "single_perception",
        ),
        authority_order=(
            "runtime_state",
            "trusted_process_integration",
            "repeated_perception",
            "single_perception",
        ),
        transition_rule="immediate",
        confirmation_requirement=1,
        staleness_policy="short_lived",
        stale_after_seconds=30.0,
    ),
    "application_or_game": ActivityFieldPolicy(
        field_name="application_or_game",
        family="perceptual_state",
        allowed_sources=PERCEPTUAL_STATE_SOURCES,
        authority_order=(
            "trusted_process_integration",
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "user_explicit",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=2,
        staleness_policy="session",
    ),
    "activity_type": ActivityFieldPolicy(
        field_name="activity_type",
        family="perceptual_state",
        allowed_sources=PERCEPTUAL_STATE_SOURCES,
        authority_order=(
            "trusted_process_integration",
            "deterministic_telemetry",
            "user_explicit",
            "repeated_perception",
            "single_perception",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=2,
        staleness_policy="session",
    ),
    "current_phase": ActivityFieldPolicy(
        field_name="current_phase",
        family="perceptual_state",
        allowed_sources=PERCEPTUAL_STATE_SOURCES,
        authority_order=(
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "user_explicit",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="hysteresis",
        confirmation_requirement=2,
        staleness_policy="session",
    ),
    "attempt_count": ActivityFieldPolicy(
        field_name="attempt_count",
        family="counted_event",
        allowed_sources=COUNTED_EVENT_SOURCES,
        authority_order=(
            "deterministic_telemetry",
            "repeated_perception",
            "single_perception",
            "language_inference",
            "imported_unverified",
        ),
        transition_rule="monotonic_count",
        confirmation_requirement=1,
        staleness_policy="session",
        conflict_policy="highest_count",
    ),
    "voice_call_connected": ActivityFieldPolicy(
        field_name="voice_call_connected",
        family="runtime_fact",
        allowed_sources=("runtime_state",),
        authority_order=("runtime_state",),
        transition_rule="immediate",
        confirmation_requirement=1,
        staleness_policy="short_lived",
        stale_after_seconds=10.0,
    ),
}


def authority_source_for(
    proposition: EvidenceProposition,
    *,
    repeated_perception: bool = False,
) -> ActivityAuthoritySource:
    """Map evidence producer identity to activity authority without changing trust."""

    if proposition.source_kind == "user":
        return "user_explicit"
    if proposition.source_kind == "runtime":
        return "runtime_state"
    if proposition.source_kind == "telemetry":
        return "deterministic_telemetry"
    if proposition.source_kind == "system":
        return "trusted_process_integration"
    if proposition.source_kind == "external":
        return "repeated_perception" if repeated_perception else "single_perception"
    if proposition.source_kind == "import":
        return "imported_unverified"
    return "language_inference"


def activity_field_policy(field_name: str) -> ActivityFieldPolicy | None:
    return DEFAULT_ACTIVITY_FIELD_POLICIES.get(field_name)


__all__ = [
    "DEFAULT_ACTIVITY_FIELD_POLICIES",
    "ActivityAuthoritySource",
    "ActivityConflictPolicy",
    "ActivityFieldFamily",
    "ActivityFieldPolicy",
    "ActivityStalenessPolicy",
    "ActivityTransitionRule",
    "activity_field_policy",
    "authority_source_for",
]
