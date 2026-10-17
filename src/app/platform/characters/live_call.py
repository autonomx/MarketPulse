"""Server-owned live-call runtime resolution for System Assistant and Character Mode."""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.contracts import ChatSession
from app.providers.voice_debug import voice_debug_log

from .avatar_models import CharacterAvatarPack
from .avatar_service import CharacterAvatarService, default_character_avatar_service
from .interaction import resolve_system_session_identity
from .models import SYSTEM_ASSISTANT_NAME, CharacterProfileSnapshot
from .service import (
    CharacterService,
    CharacterVoiceAssetError,
    default_character_service,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _voice_speaker_id(asset_id: str | None, service: CharacterService) -> str | None:
    """Resolve a governed voice asset into the exact speaker ID expected by TTS."""

    normalized_asset_id = str(asset_id or "").strip()
    if not normalized_asset_id:
        return None
    asset = service.resolve_voice_asset(normalized_asset_id)
    if asset is not None:
        metadata = dict(asset.metadata or {})
        for key in ("voice_clone_id", "voice_id", "speaker"):
            value = str(metadata.get(key) or "").strip()
            if value and value.lower() != "default":
                return value
    fallback = normalized_asset_id.removeprefix("voice-cloning:").strip()
    return fallback or None


class LiveCallSpeechStyle(BaseModel):
    """Validated delivery controls kept separate from language identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    temperature: float = Field(default=0.6, ge=0.1, le=1.5)
    top_k: int = Field(default=20, ge=1, le=100)
    top_p: float = Field(default=0.85, ge=0.1, le=1.0)
    repetition_penalty: float = Field(default=1.0, ge=0.5, le=2.0)
    expressiveness: str = Field(default="neutral", max_length=80)
    emotion: str = Field(default="neutral", max_length=80)
    interruption_style: str = Field(default="balanced", max_length=80)


class LiveCallPreloadState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_loaded: bool
    voice_resolved: bool
    voice_error: str | None = None
    avatar_pack_loaded: bool = False
    memory_snapshot_loaded: bool
    memory_record_count: int = Field(default=0, ge=0)
    preload_ms: float = Field(ge=0)
    resolved_at: str


class CharacterLiveCallRuntime(BaseModel):
    """Trusted browser-safe runtime used to start and render a live call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    interaction_mode: Literal["system", "character"]
    display_name: str
    character_id: str | None = None
    character_profile_version: int | None = Field(default=None, ge=1)
    effective_identity_hash: str | None = Field(default=None, min_length=64, max_length=64)
    voice_asset_id: str | None = None
    voice_speaker_id: str | None = None
    greeting: str = Field(default="", max_length=2_000)
    avatar_pack: CharacterAvatarPack | None = None
    speech_style: LiveCallSpeechStyle
    read_memory: bool = False
    write_memory: bool = False
    shared_memory_access: Literal["none", "read_only"] = "none"
    memory_snapshot_id: str | None = None
    preload: LiveCallPreloadState


def normalize_speech_style(raw: dict[str, Any] | None) -> LiveCallSpeechStyle:
    payload = dict(raw or {})
    return LiveCallSpeechStyle(
        speed=_bounded_float(payload.get("speed"), 1.0, 0.5, 2.0),
        temperature=_bounded_float(payload.get("temperature"), 0.6, 0.1, 1.5),
        top_k=_bounded_int(payload.get("top_k"), 20, 1, 100),
        top_p=_bounded_float(payload.get("top_p"), 0.85, 0.1, 1.0),
        repetition_penalty=_bounded_float(
            payload.get("repetition_penalty"), 1.0, 0.5, 2.0
        ),
        expressiveness=str(payload.get("expressiveness") or "neutral")[:80],
        emotion=str(payload.get("default_emotion") or payload.get("emotion") or "neutral")[:80],
        interruption_style=str(payload.get("interruption_style") or "balanced")[:80],
    )


def resolve_live_call_runtime(
    session: ChatSession,
    *,
    character_service_factory: Callable[[], CharacterService] = default_character_service,
    avatar_service_factory: Callable[[], CharacterAvatarService] = default_character_avatar_service,
) -> CharacterLiveCallRuntime:
    """Resolve one session without accepting prompt, namespace, or profile data from the browser."""

    started = time.perf_counter()
    interaction = resolve_system_session_identity(session)
    character_service = character_service_factory()
    character: CharacterProfileSnapshot | None = None
    avatar_pack: CharacterAvatarPack | None = None
    if interaction.interaction_mode == "character":
        character = character_service.resolve_snapshot(interaction.character_id or "")
        avatar_pack = avatar_service_factory().resolve(interaction.character_id)

    # The character profile's governed voice is authoritative for live calls,
    # even when an older session still carries a previous voice override.
    voice_asset_id = (
        character.default_voice_asset_id
        if character and character.default_voice_asset_id
        else interaction.voice_asset_id
    )
    voice_error: str | None = None

    # Older profiles can preserve speaker casing in the governed ID, for example
    # voice-cloning:Jinx, while the current library record is voice-cloning:jinx.
    # Canonicalize the asset identifier before validation; speaker casing remains
    # independent and is read from the asset metadata below.
    if voice_asset_id:
        resolved_voice_asset = character_service.resolve_voice_asset(voice_asset_id)
        if resolved_voice_asset is not None:
            voice_asset_id = resolved_voice_asset.id
        elif character is not None:
            voice_error = f"voice asset not found: {voice_asset_id}"
            voice_asset_id = None

    if character is not None and voice_asset_id:
        try:
            character_service.validate_voice_for_use(voice_asset_id, "live_call")
        except CharacterVoiceAssetError as exc:
            # Voice availability must not erase a valid Character Mode identity.
            # Keep the character active and let the caller fall back to its default
            # system voice while surfacing the exact asset problem for diagnostics.
            voice_error = str(exc)
            voice_asset_id = None
    voice_speaker_id = _voice_speaker_id(voice_asset_id, character_service)
    # Live-call greetings are generated ephemerally by the active LLM. Keep the
    # legacy field empty so old browser playback code cannot race that stream.
    greeting = ""
    speech_style = normalize_speech_style(character.speech_style if character else None)
    resolved_at = _utcnow()
    preload_ms = round((time.perf_counter() - started) * 1000, 3)
    memory_loaded = bool(session.read_memory and session.memory_snapshot_id)
    runtime = CharacterLiveCallRuntime(
        session_id=session.id,
        interaction_mode=interaction.interaction_mode,
        display_name=character.display_name if character else SYSTEM_ASSISTANT_NAME,
        character_id=interaction.character_id,
        character_profile_version=interaction.character_profile_version,
        effective_identity_hash=interaction.effective_identity_hash,
        voice_asset_id=voice_asset_id,
        voice_speaker_id=voice_speaker_id,
        greeting=greeting,
        avatar_pack=avatar_pack,
        speech_style=speech_style,
        read_memory=interaction.read_memory,
        write_memory=interaction.write_memory,
        shared_memory_access=interaction.shared_memory_access,
        memory_snapshot_id=session.memory_snapshot_id if memory_loaded else None,
        preload=LiveCallPreloadState(
            profile_loaded=character is not None,
            voice_resolved=bool(voice_speaker_id),
            voice_error=voice_error,
            avatar_pack_loaded=avatar_pack is not None,
            memory_snapshot_loaded=memory_loaded,
            memory_record_count=session.memory_record_count if memory_loaded else 0,
            preload_ms=preload_ms,
            resolved_at=resolved_at,
        ),
    )
    voice_debug_log(
        "backend",
        "live_call_runtime_resolved",
        trace_id=f"live-call-runtime:{session.id}",
        session_id=session.id,
        interaction_mode=runtime.interaction_mode,
        character_id=runtime.character_id,
        character_profile_version=runtime.character_profile_version,
        display_name=runtime.display_name,
        voice_asset_id=runtime.voice_asset_id,
        voice_speaker_id=runtime.voice_speaker_id,
        voice_resolved=runtime.preload.voice_resolved,
        voice_error=runtime.preload.voice_error,
        profile_loaded=runtime.preload.profile_loaded,
        avatar_pack_loaded=runtime.preload.avatar_pack_loaded,
        preload_ms=runtime.preload.preload_ms,
    )
    return runtime


__all__ = [
    "CharacterLiveCallRuntime",
    "LiveCallPreloadState",
    "LiveCallSpeechStyle",
    "normalize_speech_style",
    "resolve_live_call_runtime",
]
