"""User experience settings profile models."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ResearchMode = Literal["disabled", "quick", "deep"]
ResearchProvider = Literal["duckduckgo", "brave", "tavily", "playwright"]


class AppearanceSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    mode: str = "system"
    theme: str = "aurora"
    density: str = "comfortable"
    text_scale: int = Field(100, ge=80, le=140, alias="textScale")
    reduce_motion: bool = Field(False, alias="reduceMotion")
    live_captions: bool = Field(True, alias="liveCaptions")


class AssistantSettingsProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    personality_id: str = Field(default="omnix-default", alias="personalityId")
    custom_personality: str = Field(default="", alias="customPersonality")
    voice_id: str = Field(default="", alias="voiceId")
    auto_speak_replies: bool = Field(default=False, alias="autoSpeakReplies")
    speech_language: str = Field(default="en-US", alias="speechLanguage")
    streaming_audio: bool = Field(default=True, alias="streamingAudio")

    research_default_mode: ResearchMode = Field(default="disabled", alias="researchDefaultMode")
    research_provider: ResearchProvider = Field(
        default="brave",
        alias="researchProvider",
    )
    research_provider_fallbacks: list[ResearchProvider] = Field(
        default_factory=lambda: ["playwright", "duckduckgo"],
        max_length=3,
        alias="researchProviderFallbacks",
    )
    research_max_results: int = Field(default=5, ge=1, le=8, alias="researchMaxResults")
    research_max_steps: int = Field(default=6, ge=1, le=12, alias="researchMaxSteps")
    research_max_queries: int = Field(default=5, ge=1, le=10, alias="researchMaxQueries")
    research_max_sources: int = Field(default=12, ge=1, le=100, alias="researchMaxSources")
    research_max_extracts: int = Field(default=8, ge=0, le=20, alias="researchMaxExtracts")
    research_search_cache_ttl_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        alias="researchSearchCacheTtlSeconds",
    )
    research_extraction_cache_ttl_seconds: int = Field(
        default=3600,
        ge=1,
        le=604800,
        alias="researchExtractionCacheTtlSeconds",
    )
    research_raw_retention_days: int = Field(
        default=7,
        ge=0,
        le=365,
        alias="researchRawRetentionDays",
    )
    research_manifest_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        alias="researchManifestRetentionDays",
    )
    research_show_diagnostics: bool = Field(default=True, alias="researchShowDiagnostics")
    research_deep_enabled: bool = Field(default=False, alias="researchDeepEnabled")
    research_hermes_planner_enabled: bool = Field(
        default=False,
        alias="researchHermesPlannerEnabled",
    )

    desktop_companion_enabled: bool = Field(default=False, alias="desktopCompanionEnabled")
    desktop_companion_rollout_stage: Literal["disabled", "shadow", "text", "speech"] = Field(
        default="disabled",
        alias="desktopCompanionRolloutStage",
    )
    desktop_companion_vision_model_id: str = Field(
        default="",
        max_length=240,
        alias="desktopCompanionVisionModelId",
    )
    desktop_companion_remote_vision_allowed: bool = Field(
        default=False,
        alias="desktopCompanionRemoteVisionAllowed",
    )
    desktop_companion_show_diagnostics: bool = Field(
        default=False,
        alias="desktopCompanionShowDiagnostics",
    )
    desktop_companion_background_calls_per_minute: int = Field(
        default=6,
        ge=1,
        le=30,
        alias="desktopCompanionBackgroundCallsPerMinute",
    )
    desktop_companion_minimum_observation_interval_ms: int = Field(
        default=8_000,
        ge=2_000,
        le=120_000,
        alias="desktopCompanionMinimumObservationIntervalMs",
    )
    desktop_companion_observation_timeout_ms: int = Field(
        default=10_000,
        ge=1_000,
        le=60_000,
        alias="desktopCompanionObservationTimeoutMs",
    )
    desktop_companion_observation_ttl_ms: int = Field(
        default=12_000,
        ge=2_000,
        le=120_000,
        alias="desktopCompanionObservationTtlMs",
    )
    desktop_companion_commentary_cooldown_ms: int = Field(
        default=25_000,
        ge=5_000,
        le=300_000,
        alias="desktopCompanionCommentaryCooldownMs",
    )
    desktop_companion_minimum_change_confidence: float = Field(
        default=0.55,
        ge=0,
        le=1,
        alias="desktopCompanionMinimumChangeConfidence",
    )


class AgentProviderPrice(BaseModel):
    """What one provider charges, so an agent run's cost can be metered."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    input_usd_per_million: float = Field(default=0.0, ge=0, le=10_000, alias="inputUsdPerMillion")
    output_usd_per_million: float = Field(default=0.0, ge=0, le=10_000, alias="outputUsdPerMillion")


class AgentRunSettingsProfile(BaseModel):
    """Default limits for agent runs started without their own (unset: unlimited)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    default_max_output_tokens: int | None = Field(default=None, ge=1, le=100_000_000, alias="defaultMaxOutputTokens")
    default_max_cost_usd: float | None = Field(default=None, ge=0, le=100_000, alias="defaultMaxCostUsd")
    provider_prices: dict[str, AgentProviderPrice] = Field(default_factory=dict, alias="providerPrices")

