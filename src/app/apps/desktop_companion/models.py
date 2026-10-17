"""Versioned contracts for the local-first desktop companion runtime."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DESKTOP_COMPANION_SCHEMA_VERSION = 1

DesktopActivity = Literal[
    "static",
    "micro_change",
    "translation_like",
    "localized_change",
    "continuous_motion",
    "full_scene_change",
    "unknown",
]
DesktopActivityHypothesis = Literal[
    "none",
    "likely_scroll",
    "likely_typing",
    "likely_navigation",
    "likely_app_switch",
    "likely_media",
]
DesktopChangeKind = Literal["none", "delta", "scene_change"]
CompanionReaction = Literal["ignore", "observe_silently", "glance", "deep"]
CompanionRuntimePhase = Literal[
    "off",
    "sharing",
    "watching_idle",
    "change_pending",
    "analyzing",
    "observation_ready",
    "paused",
    "backing_off",
    "error",
]
CompanionDeliverySource = Literal["desktop_companion", "desktop_critical"]
CompanionCommentaryAction = Literal["speak", "display", "skip"]
CompanionDeliveryStatus = Literal["generated", "displayed", "completed", "interrupted", "discarded"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DesktopActivitySignal(BaseModel):
    """Conservative browser-side visual activity classification."""

    model_config = ConfigDict(extra="forbid")

    activity: DesktopActivity = "unknown"
    hypothesis: DesktopActivityHypothesis = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    changed_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    mean_difference: float = Field(default=0.0, ge=0.0, le=1.0)
    horizontal_shift: float = 0.0
    vertical_shift: float = 0.0
    focus: float = Field(default=0.0, ge=0.0, le=1.0)
    source_width: int | None = Field(default=None, ge=1)
    source_height: int | None = Field(default=None, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)


class DesktopBehaviorState(BaseModel):
    """Bounded temporal interpretation of recent activity signals."""

    model_config = ConfigDict(extra="forbid")

    current_pattern: Literal[
        "starting",
        "settled",
        "browsing",
        "rapid_switching",
        "exploring",
        "watching",
        "typing",
        "mixed",
    ] = "starting"
    settled_seconds: float = Field(default=0.0, ge=0.0)
    browsing_pace: float = Field(default=0.0, ge=0.0, le=1.0)
    rapid_browsing: bool = False
    likely_typing: bool = False
    likely_media: bool = False
    transition: str | None = Field(default=None, max_length=80)
    sample_count: int = Field(default=0, ge=0)


class DesktopObservedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(default="", max_length=500)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DesktopObservedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    between: tuple[float, float] | None = None
    fingerprint: str | None = Field(default=None, max_length=160)


class DesktopObservation(BaseModel):
    """A factual, uncertain and revisable vision result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = DESKTOP_COMPANION_SCHEMA_VERSION
    observation_id: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1, max_length=160)
    character_id: str | None = Field(default=None, max_length=160)
    capture_generation: str = Field(min_length=1, max_length=160)
    source_fingerprint: str = Field(min_length=1, max_length=240)
    client_sequence: int = Field(ge=0)
    captured_at: datetime
    observed_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    activity: DesktopActivitySignal = Field(default_factory=DesktopActivitySignal)
    behavior: DesktopBehaviorState = Field(default_factory=DesktopBehaviorState)
    change_kind: DesktopChangeKind = "none"
    current_scene: DesktopObservedValue = Field(default_factory=DesktopObservedValue)
    visible_changes: list[DesktopObservedChange] = Field(default_factory=list, max_length=16)
    visible_text: list[str] = Field(default_factory=list, max_length=24)
    possible_events: list[DesktopObservedChange] = Field(default_factory=list, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=16)
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    plain_text_fallback: str | None = Field(default=None, max_length=3000)
    diagnostics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "DesktopObservation":
        if self.expires_at <= self.captured_at:
            raise ValueError("expires_at must be after captured_at")
        return self

    def is_stale(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at


class CompanionAttentionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reaction: CompanionReaction
    should_generate: bool
    should_deliver: bool
    target_sentences: int = Field(default=0, ge=0, le=4)
    priority: Literal["background", "normal", "critical"] = "background"
    rationale: str = Field(min_length=1, max_length=240)
    scores: dict[CompanionReaction, float] = Field(default_factory=dict)
    policy_version: int = Field(default=1, ge=1)
    eligible_in_ms: int | None = Field(default=None, ge=0)


class CompanionCommentaryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=120)
    observation_id: str = Field(min_length=1, max_length=120)
    session_id: str = Field(min_length=1, max_length=160)
    source: CompanionDeliverySource = "desktop_companion"
    action: CompanionCommentaryAction
    text: str = Field(default="", max_length=500)
    emotion: str | None = Field(default=None, max_length=80)
    grounding_ids: list[str] = Field(default_factory=list, max_length=16)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    generated_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    duplicate_of: str | None = Field(default=None, max_length=120)
    skip_reason: str | None = Field(default=None, max_length=160)


class CompanionLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CompanionCommentaryCandidate
    status: CompanionDeliveryStatus
    delivered_at: datetime | None = None
    interrupted_at_phrase: int | None = Field(default=None, ge=0)


class CompanionRuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: CompanionRuntimePhase = "off"
    session_id: str | None = None
    character_id: str | None = None
    capture_generation: str | None = None
    source_fingerprint: str | None = None
    watch_enabled: bool = False
    speech_muted: bool = False
    shadow_mode: bool = True
    last_activity: DesktopActivitySignal | None = None
    last_behavior: DesktopBehaviorState | None = None
    last_observation_id: str | None = None
    last_attention: CompanionAttentionDecision | None = None
    last_error: str | None = None
    active_requests: int = Field(default=0, ge=0)
    pending_requests: int = Field(default=0, ge=0, le=1)
    dropped_requests: int = Field(default=0, ge=0)
    coalesced_requests: int = Field(default=0, ge=0)


class DesktopCompanionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    shadow_mode: bool = True
    speech_enabled: bool = False
    visible_comments: bool = True
    background_calls_per_minute: int = Field(default=6, ge=1, le=30)
    minimum_observation_interval_ms: int = Field(default=8_000, ge=2_000, le=120_000)
    observation_timeout_ms: int = Field(default=10_000, ge=1_000, le=60_000)
    observation_ttl_ms: int = Field(default=12_000, ge=2_000, le=120_000)
    commentary_cooldown_ms: int = Field(default=25_000, ge=5_000, le=300_000)
    minimum_change_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    attention_seed: int = 0
    attention_policy_version: int = Field(default=1, ge=1)


__all__ = [
    "CompanionAttentionDecision",
    "CompanionCommentaryCandidate",
    "CompanionDeliverySource",
    "CompanionLedgerEntry",
    "CompanionReaction",
    "CompanionRuntimeStatus",
    "DESKTOP_COMPANION_SCHEMA_VERSION",
    "DesktopActivitySignal",
    "DesktopBehaviorState",
    "DesktopCompanionPolicy",
    "DesktopObservation",
    "DesktopObservedChange",
    "DesktopObservedValue",
    "utcnow",
]
