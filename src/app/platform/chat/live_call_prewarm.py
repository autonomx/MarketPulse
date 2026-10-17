"""Best-effort live-call cold-path warm-up for local LLM and TTS providers."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.settings.access import load_settings
from app.providers import service as provider_service
from app.platform.chat import ChatMessage, ChatSessionStore, default_chat_store
from app.platform.chat.store import _model_key, _provider_key
from app.providers import ChatMessage as ProviderMessage
from app.providers.lmstudio_provider import LMStudioProvider
from app.providers.service import get_tts_provider
from app.providers.catalog import LOADED_MODEL, provider_supports

from app.runtime.live_voice_config import resolve_live_voice_chat_route
from app.conversation.tts_stream_contract import TtsStreamRequest, audio_chunk_to_pcm16_bytes
from app.observability.tts_stream_diagnostics import stream_log

_PREWARM_TTL_SECONDS = 300.0
_PREWARM_WAIT_SECONDS = 8.0
_MAX_PREWARM_ENTRIES = 256
_PREWARM_INFLIGHT_TTL_SECONDS = 300.0
_PREWARM_LOCK = threading.RLock()
_PREWARMED_AT: dict[str, float] = {}
_PREWARM_INFLIGHT: dict[str, tuple[threading.Event, float]] = {}
_PROVIDER_AFFINITY: dict[str, tuple[str | None, str | None, float]] = {}
_WARM_USER_CONTENT = "Reply with one short acknowledgement."
_WARM_TTS_TEXT = "Ready to answer."


def _prewarm_now() -> float:
    return time.monotonic()


class LiveCallPrewarmRequest(BaseModel):
    speaker: str | None = Field(default=None, max_length=160)
    language: str = Field(default="English", min_length=1, max_length=40)


@dataclass(frozen=True)
class _WarmResult:
    status: str
    elapsed_ms: float
    detail: str | None = None
    prompt_message_count: int | None = None
    prompt_chars: int | None = None

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "detail": self.detail,
        }
        if self.prompt_message_count is not None:
            payload["prompt_message_count"] = self.prompt_message_count
        if self.prompt_chars is not None:
            payload["prompt_chars"] = self.prompt_chars
        return payload


def remember_live_call_provider_affinity(
    session_id: str,
    provider_id: str | None,
    model_id: str | None,
) -> None:
    """Remember the provider/model that was warmed for an active live call."""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return
    provider = str(provider_id or "").strip() or None
    model = str(model_id or "").strip() or None
    with _PREWARM_LOCK:
        _prune_prewarm_locked()
        _PROVIDER_AFFINITY[normalized_session_id] = (provider, model, _prewarm_now())
        _trim_oldest(_PROVIDER_AFFINITY, _MAX_PREWARM_ENTRIES, lambda value: value[2])


def live_call_provider_affinity(
    session_id: str,
) -> tuple[str | None, str | None] | None:
    """Return a recent prewarmed provider/model affinity for one live session."""

    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    with _PREWARM_LOCK:
        entry = _PROVIDER_AFFINITY.get(normalized_session_id)
        if entry is None:
            return None
        provider_id, model_id, recorded_at = entry
        if _prewarm_now() - recorded_at > _PREWARM_TTL_SECONDS:
            _PROVIDER_AFFINITY.pop(normalized_session_id, None)
            return None
        return provider_id, model_id


def clear_live_call_prewarm_state() -> None:
    """Clear warm-up deduplication state for focused tests."""

    with _PREWARM_LOCK:
        waiting = [event for event, _created_at in _PREWARM_INFLIGHT.values()]
        _PREWARM_INFLIGHT.clear()
        _PREWARMED_AT.clear()
        _PROVIDER_AFFINITY.clear()
    for event in waiting:
        event.set()


def _configured_live_route() -> tuple[str | None, str | None, str]:
    """Resolve the live route from current Settings, not stale session metadata."""

    settings = load_settings()
    provider_id = str(settings.get("provider") or "lmstudio").strip() or "lmstudio"
    provider_name = _provider_key(provider_id)
    model_id = None
    if not provider_supports(provider_name, LOADED_MODEL):
        provider_settings = settings.get(provider_name) if provider_name else None
        if isinstance(provider_settings, dict):
            model_id = _model_key(provider_settings.get("model"))
    return resolve_live_voice_chat_route(provider_id, model_id)


def register_live_call_prewarm_routes(router: APIRouter,
    *,
    chat_store_factory: Callable[[], ChatSessionStore] = default_chat_store,
) -> None:
    """Register a side-effect-free warm-up endpoint used when a call opens."""


    @router.post(
        "/api/live-call/sessions/{session_id}/prewarm",
    )
    async def prewarm_live_call(
        session_id: str,
        request: LiveCallPrewarmRequest,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        store = chat_store_factory()
        session = await asyncio.to_thread(store.get_session, session_id)
        if session is None:
            return {
                "ok": False,
                "fully_warmed": False,
                "status": "session_not_found",
                "cached": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        provider_id, model_id, execution_lane = _configured_live_route()
        remember_live_call_provider_affinity(
            session_id,
            provider_id,
            model_id,
        )
        key = _prewarm_key(
            session=session,
            provider_id=provider_id,
            model_id=model_id,
            speaker=request.speaker,
            language=request.language,
        )
        owner, cached, wait_event = _claim_prewarm(key)
        if not owner and not cached and wait_event is None:
            return {
                "ok": False,
                "fully_warmed": False,
                "status": "capacity_limited",
                "cached": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        if cached:
            return {
                "ok": True,
                "fully_warmed": True,
                "status": "cached",
                "cached": True,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        if not owner and wait_event is not None:
            await asyncio.to_thread(wait_event.wait, _PREWARM_WAIT_SECONDS)
            with _PREWARM_LOCK:
                completed = _recently_warmed_locked(key)
            return {
                "ok": completed,
                "fully_warmed": completed,
                "status": "shared" if completed else "wait_timeout",
                "cached": completed,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

        stream_log(
            "gateway-live-call-prewarm",
            "runtime",
            "live_call_prewarm_started",
            session_id=session_id,
            provider_id=provider_id,
            model_id=model_id,
            execution_lane=execution_lane,
            session_provider_id=getattr(session, "provider_id", None),
            session_model_id=getattr(session, "model_id", None),
            interaction_mode=getattr(session, "interaction_mode", None),
            character_id=getattr(session, "character_id", None),
            character_profile_version=getattr(
                session,
                "character_profile_version",
                None,
            ),
            session_message_count=len(getattr(session, "messages", []) or []),
            speaker=request.speaker,
        )
        try:
            llm_result, tts_result = await asyncio.gather(
                asyncio.to_thread(
                    _warm_llm,
                    store,
                    session,
                    provider_id=provider_id,
                    model_id=model_id,
                ),
                asyncio.to_thread(
                    _warm_tts,
                    request.speaker,
                    request.language,
                ),
            )
            results = (llm_result, tts_result)
            fully_warmed = all(result.status == "warmed" for result in results)
            no_failures = all(
                result.status in {"warmed", "unavailable"}
                for result in results
            )
            if fully_warmed:
                with _PREWARM_LOCK:
                    _PREWARMED_AT[key] = _prewarm_now()
                    _trim_oldest(_PREWARMED_AT, _MAX_PREWARM_ENTRIES, lambda value: value)
            status = _combined_status(results, fully_warmed=fully_warmed)
            payload = {
                "ok": no_failures,
                "fully_warmed": fully_warmed,
                "status": status,
                "cached": False,
                "llm": llm_result.payload(),
                "tts": tts_result.payload(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
            stream_log(
                "gateway-live-call-prewarm",
                "runtime",
                "live_call_prewarm_completed",
                session_id=session_id,
                provider_id=provider_id,
                model_id=model_id,
                execution_lane=execution_lane,
                **payload,
            )
            return payload
        finally:
            _release_prewarm(key)


def _combined_status(
    results: tuple[_WarmResult, _WarmResult],
    *,
    fully_warmed: bool,
) -> str:
    if fully_warmed:
        return "completed"
    statuses = {result.status for result in results}
    if "warmed" in statuses:
        return "partial"
    if statuses == {"unavailable"}:
        return "unavailable"
    return "failed"


def _claim_prewarm(key: str) -> tuple[bool, bool, threading.Event | None]:
    with _PREWARM_LOCK:
        _prune_prewarm_locked()
        if _recently_warmed_locked(key):
            return False, True, None
        existing = _PREWARM_INFLIGHT.get(key)
        if existing is not None:
            return False, False, existing[0]
        if len(_PREWARM_INFLIGHT) >= _MAX_PREWARM_ENTRIES:
            return False, False, None
        event = threading.Event()
        _PREWARM_INFLIGHT[key] = (event, _prewarm_now())
        return True, False, event


def _release_prewarm(key: str) -> None:
    with _PREWARM_LOCK:
        entry = _PREWARM_INFLIGHT.pop(key, None)
    if entry is not None:
        entry[0].set()


def _recently_warmed_locked(key: str) -> bool:
    warmed_at = _PREWARMED_AT.get(key)
    return warmed_at is not None and _prewarm_now() - warmed_at <= _PREWARM_TTL_SECONDS


def _prune_prewarm_locked() -> None:
    now = _prewarm_now()
    cutoff = now - _PREWARM_TTL_SECONDS
    for key, warmed_at in list(_PREWARMED_AT.items()):
        if warmed_at < cutoff:
            _PREWARMED_AT.pop(key, None)
    for session_id, (_, _, recorded_at) in list(_PROVIDER_AFFINITY.items()):
        if recorded_at < cutoff:
            _PROVIDER_AFFINITY.pop(session_id, None)
    inflight_cutoff = now - _PREWARM_INFLIGHT_TTL_SECONDS
    for key, (event, created_at) in list(_PREWARM_INFLIGHT.items()):
        if created_at < inflight_cutoff:
            _PREWARM_INFLIGHT.pop(key, None)
            event.set()


def _trim_oldest(mapping: dict, capacity: int, timestamp) -> None:
    while len(mapping) > capacity:
        oldest_key = min(mapping, key=lambda key: timestamp(mapping[key]))
        mapping.pop(oldest_key, None)


def _prewarm_key(
    *,
    session: Any,
    provider_id: str | None,
    model_id: str | None,
    speaker: str | None,
    language: str,
) -> str:
    return "|".join(
        (
            str(getattr(session, "id", "") or ""),
            str(provider_id or ""),
            str(model_id or ""),
            str(getattr(session, "interaction_mode", "") or ""),
            str(getattr(session, "character_id", "") or ""),
            str(getattr(session, "character_profile_version", "") or ""),
            str(getattr(session, "effective_identity_hash", "") or ""),
            str(getattr(session, "active_segment_id", "") or ""),
            str(getattr(session, "message_count", "") or ""),
            str(getattr(session, "updated_at", "") or ""),
            str(speaker or ""),
            language.casefold(),
        )
    )


def _warm_llm(
    store: Any,
    session: Any,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> _WarmResult:
    started = time.perf_counter()
    selected_provider_id = (
        provider_id
        if provider_id is not None
        else getattr(session, "provider_id", None)
    )
    selected_model_id = (
        model_id
        if provider_id is not None
        else getattr(session, "model_id", None)
    )
    provider = provider_service.get_provider(_provider_key(selected_provider_id))
    if provider is None or not hasattr(provider, "chat_completion"):
        return _WarmResult(
            status="unavailable",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail="chat_provider_unavailable",
        )

    response: Iterator[Any] | None = None
    prompt_messages: list[ProviderMessage] = []
    try:
        prompt_messages = _live_voice_warm_messages(store, session)
        # LM Studio should warm whichever model is actually resident. Cloud and
        # other explicit providers use the current model from Settings/live lane.
        model = (
            None
            if isinstance(provider, LMStudioProvider)
            else _model_key(selected_model_id)
        )
        response = provider.chat_completion(
            messages=prompt_messages,
            model=model,
            stream=True,
            chat_template_kwargs={"enable_thinking": False},
        )
        for chunk in response:
            if str(getattr(chunk, "content", "") or "").strip():
                break
        return _WarmResult(
            status="warmed",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            prompt_message_count=len(prompt_messages),
            prompt_chars=sum(len(message.content) for message in prompt_messages),
        )
    except Exception as exc:  # noqa: BLE001 - warm-up must never block live calls.
        return _WarmResult(
            status="failed",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail=f"{type(exc).__name__}:{exc}",
            prompt_message_count=len(prompt_messages) or None,
            prompt_chars=(
                sum(len(message.content) for message in prompt_messages)
                if prompt_messages
                else None
            ),
        )
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            with suppress(Exception):
                close()


def _live_voice_warm_messages(store: Any, session: Any) -> list[ProviderMessage]:
    warm_message = ChatMessage(
        id=f"live-call-prewarm:{getattr(session, 'id', 'session')}",
        role="user",
        content=_WARM_USER_CONTENT,
        created_at=datetime.now(timezone.utc).isoformat(),
        metadata={
            "prewarm": True,
            "side_effects_allowed": False,
            "tools_allowed": False,
            "memory_writes_allowed": False,
            "user_turn_id": "voice-user-turn:live-call-prewarm",
            "speech_segment_id": "voice-segment:live-call-prewarm",
        },
    )
    builder = getattr(store, "build_provider_prompt", None)
    if not callable(builder):
        return [ProviderMessage(role="user", content=_WARM_USER_CONTENT)]
    _, rendered = builder(session, warm_message, [])
    return [
        ProviderMessage(role=message.role, content=message.content)
        for message in rendered.messages
    ]


def _warm_tts(speaker: str | None, language: str) -> _WarmResult:
    started = time.perf_counter()
    provider = get_tts_provider()
    if provider is None or not hasattr(provider, "generate_audio_stream"):
        return _WarmResult(
            status="unavailable",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail="tts_provider_unavailable",
        )

    # Build the warm-up through the same chat-stream policy used by live voice.
    # Faster Qwen's first-token path is sensitive to decoder/chunk shapes; the
    # old six-character, one-step warm-up did not prepare the 4-step / ~42-token
    # shape actually used by the first spoken live-chat clause.
    warm_request = TtsStreamRequest(
        text=_WARM_TTS_TEXT,
        speaker=speaker,
        language=language,
        diagnostics_stream_id="chat-live-call-prewarm",
        temperature=0.6,
        top_k=20,
        top_p=0.85,
        repetition_penalty=1.05,
        append_silence=False,
        parity_mode=False,
    )
    provider_stream: Iterator[Any] | None = None
    try:
        provider_stream = provider.generate_audio_stream(
            text=warm_request.text,
            speaker=warm_request.speaker,
            language=warm_request.language,
            chunk_size=warm_request.chunk_size,
            temperature=warm_request.temperature,
            top_k=warm_request.top_k,
            top_p=warm_request.top_p,
            repetition_penalty=warm_request.repetition_penalty,
            append_silence=warm_request.append_silence,
            max_new_tokens=warm_request.max_new_tokens,
            parity_mode=warm_request.parity_mode,
            non_streaming_mode=False,
        )
        first = next(provider_stream, None)
        if first is not None:
            audio_chunk = first[0] if isinstance(first, tuple) else first
            audio_chunk_to_pcm16_bytes(audio_chunk)
        return _WarmResult(
            status="warmed",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
    except Exception as exc:  # noqa: BLE001 - warm-up must never block live calls.
        return _WarmResult(
            status="failed",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            detail=f"{type(exc).__name__}:{exc}",
        )
    finally:
        close = getattr(provider_stream, "close", None)
        if callable(close):
            with suppress(Exception):
                close()


__all__ = [
    "LiveCallPrewarmRequest",
    "clear_live_call_prewarm_state",
    "live_call_provider_affinity",
    "register_live_call_prewarm_routes",
    "remember_live_call_provider_affinity",
]
