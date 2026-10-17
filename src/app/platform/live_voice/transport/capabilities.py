"""Capability contract for incremental live-call TTS."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.providers.service import get_tts_provider

_ROUTE_SENTINEL = "_omnix_tts_live_capabilities_registered"


def live_tts_capabilities_payload() -> dict[str, Any]:
    provider = get_tts_provider()
    provider_available = provider is not None and hasattr(provider, "generate_audio_stream")
    incremental_factory = None if provider is None else next(
        (
            getattr(provider, name)
            for name in (
                "create_incremental_tts_session",
                "create_streaming_tts_session",
                "create_text_append_session",
            )
            if callable(getattr(provider, name, None))
        ),
        None,
    )
    native_text_append = incremental_factory is not None
    return {
        "ok": provider_available,
        "protocol": "live-tts-v2",
        # The browser keeps one websocket/audio worklet for the complete live session.
        "persistent_websocket": True,
        # LLM text is committed into streaming synthesis incrementally rather than
        # waiting for the complete assistant response.
        "incremental_text_ingest": True,
        "text_commit_deadline_ms": 140,
        "text_commit_minimum_characters": 12,
        "streaming_audio_chunks": provider_available,
        # Native decoder continuation is a separate provider capability. The current
        # Qwen provider streams each committed clause but does not expose a decoder
        # session that accepts more text after generation has started.
        "native_decoder_text_append": native_text_append,
        "stateful_text_append": native_text_append,
        "prosody_continuous_decoder": native_text_append,
        "cancellation_generations": True,
        "adaptive_playback_buffer": True,
        "fallback_mode": "persistent_incremental_clause_stream",
        "provider_available": provider_available,
        "provider_name": getattr(provider, "provider_name", None) if provider else None,
    }


def register_tts_live_capability_routes(router: APIRouter, state: Any) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get("/api/tts/live-call/capabilities")
    def tts_live_call_capabilities() -> dict[str, Any]:
        return live_tts_capabilities_payload()
