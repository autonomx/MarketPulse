"""Typed live-speech performance intent and provider capability declarations."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SpeechAct = Literal[
    "acknowledgement",
    "answer",
    "question",
    "reassurance",
    "reflection",
    "instruction",
]
DeliveryLevel = Literal["low", "moderate", "high"]
DeliveryPace = Literal["slightly_slow", "natural", "slightly_fast"]
ClausePause = Literal["short", "medium", "long"]


class SpeechOnsetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    desired_perceived_onset_ms: int = Field(default=450, ge=0, le=2_000)
    maximum_additional_delay_ms: int = Field(default=350, ge=0, le=1_000)


class NonverbalEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    breath: bool = False
    acknowledgement: bool = False
    amused_exhale: bool = False
    sigh: bool = False


class SpeechPerformancePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    speech_act: SpeechAct = "answer"
    energy: DeliveryLevel = "moderate"
    warmth: DeliveryLevel = "moderate"
    certainty: DeliveryLevel = "moderate"
    pace: DeliveryPace = "natural"
    clause_pause: ClausePause = "medium"
    emphasis: list[str] = Field(default_factory=list, max_length=6)
    onset_policy: SpeechOnsetPolicy = Field(default_factory=SpeechOnsetPolicy)
    nonverbal_eligibility: NonverbalEligibility = Field(
        default_factory=NonverbalEligibility
    )


class TtsProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    supports_streaming: bool
    supports_concurrent_generation: bool
    supports_emotion: bool
    supports_speaking_rate: bool
    supports_word_emphasis: bool
    supports_ssml: bool
    supports_word_timestamps: bool


class PerformanceControlApplication(BaseModel):
    """Provider controls that survived explicit capability enforcement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capabilities: TtsProviderCapabilities
    provider_kwargs: dict[str, Any] = Field(default_factory=dict)
    applied_controls: tuple[str, ...] = ()
    ignored_controls: tuple[str, ...] = ()


FASTER_QWEN3_TTS_CAPABILITIES = TtsProviderCapabilities(
    provider="faster_qwen3_tts",
    supports_streaming=True,
    supports_concurrent_generation=False,
    supports_emotion=False,
    supports_speaking_rate=False,
    supports_word_emphasis=False,
    supports_ssml=False,
    supports_word_timestamps=False,
)


def resolve_tts_provider_capabilities(provider: Any) -> TtsProviderCapabilities:
    """Resolve declared capabilities, falling back to a conservative profile."""

    declared = getattr(provider, "tts_capabilities", None)
    if callable(declared):
        declared = declared()
    if declared is not None:
        try:
            return TtsProviderCapabilities.model_validate(declared)
        except (TypeError, ValueError):
            pass

    provider_name = str(getattr(provider, "provider_name", "") or "").strip()
    class_name = f"{type(provider).__module__}.{type(provider).__qualname__}"
    normalized = f"{provider_name} {class_name}".casefold()
    if "qwen" in normalized:
        return FASTER_QWEN3_TTS_CAPABILITIES.model_copy(
            update={"provider": provider_name or "faster_qwen3_tts"}
        )
    return TtsProviderCapabilities(
        provider=provider_name or class_name,
        supports_streaming=callable(getattr(provider, "generate_audio_stream", None)),
        supports_concurrent_generation=False,
        supports_emotion=False,
        supports_speaking_rate=False,
        supports_word_emphasis=False,
        supports_ssml=False,
        supports_word_timestamps=False,
    )


def apply_performance_plan_to_provider(
    provider: Any,
    plan: SpeechPerformancePlan | None,
    capabilities: TtsProviderCapabilities | None = None,
) -> PerformanceControlApplication:
    """Apply only provider-declared and mapper-produced performance controls.

    Onset and clause timing remain browser-scheduler responsibilities. This
    function never converts sampling parameters into emotion or speaking style.
    """

    resolved = capabilities or resolve_tts_provider_capabilities(provider)
    mapper = getattr(provider, "build_performance_kwargs", None)
    raw: dict[str, Any] = {}
    if plan is not None and callable(mapper):
        candidate = mapper(plan)
        if isinstance(candidate, dict):
            raw = candidate

    allowed = {
        "emotion": resolved.supports_emotion,
        "speaking_rate": resolved.supports_speaking_rate,
        "emphasis": resolved.supports_word_emphasis,
        "ssml": resolved.supports_ssml,
    }
    provider_kwargs = {
        key: value
        for key, value in raw.items()
        if key in allowed and allowed[key]
    }
    applied = tuple(sorted(provider_kwargs))
    ignored: list[str] = []
    if plan is not None:
        if "speaking_rate" not in provider_kwargs:
            ignored.append("pace")
        if "emotion" not in provider_kwargs:
            ignored.extend(("energy", "warmth", "certainty"))
        if plan.emphasis and "emphasis" not in provider_kwargs:
            ignored.append("emphasis")
        if "ssml" in raw and "ssml" not in provider_kwargs:
            ignored.append("ssml")

    return PerformanceControlApplication(
        capabilities=resolved,
        provider_kwargs=provider_kwargs,
        applied_controls=applied,
        ignored_controls=tuple(dict.fromkeys(ignored)),
    )


__all__ = [
    "FASTER_QWEN3_TTS_CAPABILITIES",
    "NonverbalEligibility",
    "PerformanceControlApplication",
    "SpeechOnsetPolicy",
    "SpeechPerformancePlan",
    "TtsProviderCapabilities",
    "apply_performance_plan_to_provider",
    "resolve_tts_provider_capabilities",
]
