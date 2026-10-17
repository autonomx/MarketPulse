"""Typed Live Conversation behavior contracts shared across features."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PresencePreset = Literal["quiet", "natural", "engaged", "listener"]
ConversationStance = Literal["automatic", "listen", "discuss", "advise", "brainstorm", "teach"]
ConversationPace = Literal["quick", "balanced", "reflective"]
InterruptionPreference = Literal["easy", "balanced", "finish_more"]
AssistantBackchannelMode = Literal["off", "minimal", "natural"]
InitiativeMode = Literal["off", "gentle", "active"]
LongPauseBehavior = Literal["wait", "reassure", "ask_to_continue"]
ResponseLength = Literal["brief", "conversational", "detailed"]
ResponseOnsetStyle = Literal["adaptive", "immediate", "natural", "reflective"]
EmotionalAttunement = Literal["off", "subtle", "expressive"]
TopicContinuity = Literal["focused", "natural", "exploratory"]
DuplexMode = Literal["automatic", "half_duplex", "echo_aware"]
PronunciationSavePolicy = Literal["ask", "session_only", "allow"]


class LiveConversationProfile(BaseModel):
    """Validated behavior used by live conversation policy and generation."""

    model_config = ConfigDict(extra="forbid")

    presence_preset: PresencePreset = "natural"
    talkativeness: int = Field(default=50, ge=0, le=100)
    conversation_stance: ConversationStance = "automatic"
    conversation_pace: ConversationPace = "balanced"
    interruption_preference: InterruptionPreference = "balanced"
    assistant_backchannel_mode: AssistantBackchannelMode = "off"
    initiative_mode: InitiativeMode = "gentle"
    idle_threshold_ms: int = Field(default=15_000, ge=5_000, le=120_000)
    long_pause_behavior: LongPauseBehavior = "wait"
    response_length: ResponseLength = "conversational"
    response_onset_style: ResponseOnsetStyle = "adaptive"
    emotional_attunement: EmotionalAttunement = "subtle"
    topic_continuity: TopicContinuity = "natural"
    max_idle_prompts: int = Field(default=1, ge=0, le=3)
    duplex_mode: DuplexMode = "automatic"
    pronunciation_save_policy: PronunciationSavePolicy = "ask"
    profile_version: int = Field(default=1, ge=1)


class LiveConversationProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presence_preset: PresencePreset | None = None
    talkativeness: int | None = Field(default=None, ge=0, le=100)
    conversation_stance: ConversationStance | None = None
    conversation_pace: ConversationPace | None = None
    interruption_preference: InterruptionPreference | None = None
    assistant_backchannel_mode: AssistantBackchannelMode | None = None
    initiative_mode: InitiativeMode | None = None
    idle_threshold_ms: int | None = Field(default=None, ge=5_000, le=120_000)
    long_pause_behavior: LongPauseBehavior | None = None
    response_length: ResponseLength | None = None
    response_onset_style: ResponseOnsetStyle | None = None
    emotional_attunement: EmotionalAttunement | None = None
    topic_continuity: TopicContinuity | None = None
    max_idle_prompts: int | None = Field(default=None, ge=0, le=3)
    duplex_mode: DuplexMode | None = None
    pronunciation_save_policy: PronunciationSavePolicy | None = None


class LiveConversationProfileEnvelope(BaseModel):
    user_defaults: LiveConversationProfile
    session_override: LiveConversationProfile | None = None
    effective: LiveConversationProfile
    source: Literal["user_defaults", "session_override"]
