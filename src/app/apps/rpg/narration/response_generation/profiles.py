from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from app.apps.rpg.narration.prompt_profiles import (
    RpgPromptProfile,
    default_rpg_prompt_profile_registry,
    resolve_rpg_prompt_profile,
)

from .contracts import ResponseMode


class DeliveryMode(str, Enum):
    COMPLETE = "complete"
    SENTENCE = "sentence"
    AUDIO_PHRASE = "audio_phrase"


@dataclass(frozen=True)
class ResponseGenerationProfile:
    profile_id: str
    mode: ResponseMode
    task: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    retry_count: int
    execution_mode: str
    delivery_mode: DeliveryMode
    use_provider: bool
    allow_hermes: bool
    blocking_budget_ms: int

    def debug_payload(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "mode": self.mode.value,
            "task": self.task,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "execution_mode": self.execution_mode,
            "delivery_mode": self.delivery_mode.value,
            "use_provider": self.use_provider,
            "allow_hermes": self.allow_hermes,
            "blocking_budget_ms": self.blocking_budget_ms,
        }


_MODE_TASK = {
    ResponseMode.UTILITY: "intent_classification",
    ResponseMode.DIALOGUE: "npc_dialogue",
    ResponseMode.OBSERVATION: "narration",
    ResponseMode.ACTION: "narration",
    ResponseMode.TRANSACTION: "narration",
    ResponseMode.TRAVEL: "narration",
    ResponseMode.COMBAT: "combat_narration",
    ResponseMode.INVESTIGATION: "narration",
    ResponseMode.RECOVERY: "narration",
    ResponseMode.FAILURE: "narration",
    ResponseMode.MAJOR_BEAT: "narration",
}


class ResponseProfileRegistry:
    """Single authority for model, timeout, retry, blocking, and delivery policy."""

    def __init__(
        self,
        prompt_profiles: Mapping[str, RpgPromptProfile] | None = None,
    ) -> None:
        self._prompt_profiles = dict(
            prompt_profiles or default_rpg_prompt_profile_registry()
        )

    def resolve(
        self,
        mode: ResponseMode,
        *,
        high_value: bool = False,
        recovery_needed: bool = False,
    ) -> ResponseGenerationProfile:
        task = _MODE_TASK[mode]
        prompt = resolve_rpg_prompt_profile(
            task, registry=self._prompt_profiles  # type: ignore[arg-type]
        )
        deterministic = mode is ResponseMode.UTILITY
        delivery = self._delivery_mode(mode)
        timeout = min(
            float(prompt.timeout_seconds),
            6.0 if recovery_needed else 12.0 if high_value else 8.0,
        )
        tokens = min(
            int(prompt.max_tokens),
            220 if mode is ResponseMode.UTILITY else 520 if high_value else 360,
        )
        retry_count = min(int(prompt.retry_count), 1)
        use_provider = not deterministic
        allow_hermes = bool(
            recovery_needed
            and mode in {ResponseMode.INVESTIGATION, ResponseMode.RECOVERY}
        )
        budget = (
            250
            if deterministic
            else 5000
            if recovery_needed
            else 3500
            if high_value
            else 2200
        )
        return ResponseGenerationProfile(
            profile_id=f"response-{mode.value}-{prompt.profile_id}",
            mode=mode,
            task=task,
            provider="deterministic" if deterministic else prompt.provider,
            model="none" if deterministic else prompt.model,
            temperature=0.0 if deterministic else float(prompt.temperature),
            max_tokens=tokens,
            timeout_seconds=timeout,
            retry_count=retry_count,
            execution_mode="blocking",
            delivery_mode=delivery,
            use_provider=use_provider,
            allow_hermes=allow_hermes,
            blocking_budget_ms=budget,
        )

    def resolve_from_request(
        self,
        mode: ResponseMode,
        request_policy: Mapping[str, Any] | None,
        *,
        recovery_needed: bool = False,
    ) -> tuple[ResponseGenerationProfile, tuple[str, ...]]:
        policy = dict(request_policy or {})
        high_value = bool(policy.get("high_value"))
        ignored = tuple(
            sorted(
                key
                for key in policy
                if key
                in {
                    "provider",
                    "model",
                    "temperature",
                    "max_tokens",
                    "timeout_seconds",
                    "retry_count",
                    "delivery_mode",
                    "execution_mode",
                }
            )
        )
        return (
            self.resolve(
                mode,
                high_value=high_value,
                recovery_needed=recovery_needed,
            ),
            ignored,
        )

    @staticmethod
    def _delivery_mode(mode: ResponseMode) -> DeliveryMode:
        if mode is ResponseMode.UTILITY:
            return DeliveryMode.COMPLETE
        if mode in {ResponseMode.DIALOGUE, ResponseMode.COMBAT}:
            return DeliveryMode.AUDIO_PHRASE
        return DeliveryMode.SENTENCE


def validate_response_profile(profile: ResponseGenerationProfile) -> tuple[str, ...]:
    issues: list[str] = []
    if profile.max_tokens <= 0 or profile.max_tokens > 1200:
        issues.append("invalid_max_tokens")
    if profile.timeout_seconds <= 0 or profile.timeout_seconds > 12:
        issues.append("invalid_timeout")
    if profile.retry_count < 0 or profile.retry_count > 1:
        issues.append("invalid_retry_count")
    if profile.blocking_budget_ms <= 0 or profile.blocking_budget_ms > 6000:
        issues.append("invalid_blocking_budget")
    if not profile.use_provider and profile.provider != "deterministic":
        issues.append("deterministic_profile_has_provider")
    if profile.allow_hermes and profile.mode not in {
        ResponseMode.INVESTIGATION,
        ResponseMode.RECOVERY,
    }:
        issues.append("hermes_not_allowed_for_mode")
    return tuple(issues)
