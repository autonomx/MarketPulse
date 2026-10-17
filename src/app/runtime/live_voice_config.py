"""Process configuration for the dedicated live-voice execution lane."""
from __future__ import annotations

from dataclasses import dataclass

from app.config.env import environment


@dataclass(frozen=True, slots=True)
class LiveVoiceExecutionLaneConfig:
    mode: str
    provider_id: str | None
    model_id: str | None
    dedicated_tts: bool
    tts_provider_name: str | None

    @property
    def dedicated_chat_enabled(self) -> bool:
        return self.mode == "dedicated" and bool(self.provider_id or self.model_id)


def _normalized(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _boolean_setting(name: str, fallback: bool = False) -> bool:
    raw = environment().get(name)
    if raw is None:
        return fallback
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def live_voice_execution_lane_config() -> LiveVoiceExecutionLaneConfig:
    mode = (environment().get("OMNIX_LIVE_VOICE_EXECUTION_MODE") or "session").strip().lower()
    if mode not in {"session", "dedicated"}:
        mode = "session"
    return LiveVoiceExecutionLaneConfig(
        mode=mode,
        provider_id=_normalized(environment().get("OMNIX_LIVE_VOICE_PROVIDER_ID")),
        model_id=_normalized(environment().get("OMNIX_LIVE_VOICE_MODEL_ID")),
        dedicated_tts=_boolean_setting("OMNIX_LIVE_TTS_DEDICATED", False),
        tts_provider_name=_normalized(environment().get("OMNIX_LIVE_TTS_PROVIDER_NAME")),
    )


def resolve_live_voice_chat_route(
    provider_id: str | None,
    model_id: str | None,
) -> tuple[str | None, str | None, str]:
    config = live_voice_execution_lane_config()
    if not config.dedicated_chat_enabled:
        return provider_id, model_id, "session"
    return (
        config.provider_id or provider_id,
        config.model_id or model_id,
        "dedicated",
    )


__all__ = [
    "LiveVoiceExecutionLaneConfig",
    "live_voice_execution_lane_config",
    "resolve_live_voice_chat_route",
]
