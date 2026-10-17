"""Side-effect-free LLM speculation for stable live-STT partials."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.providers.service import provider_lease
from app.platform.chat import (
    ChatMessage,
    ChatSessionStore,
    SendChatMessageRequest,
    default_chat_store,
)
from app.platform.chat.store import _model_key, _provider_key
from app.providers import ChatMessage as ProviderMessage
from app.providers.catalog import CANCELLATION, RUNTIME_STATS, THINKING_TOGGLE, provider_supports

from app.runtime.live_voice_config import resolve_live_voice_chat_route
from app.observability.tts_stream_diagnostics import stream_log
from app.runtime.cancellation import CancellationToken

_SPECULATION_TTL_SECONDS = 90.0
_SESSION_CACHE_TTL_SECONDS = 120.0
_SESSION_LOAD_WAIT_SECONDS = 5.0
_MAX_SPECULATIONS = 64
_MAX_PRIMED_SESSIONS = 64
_MAX_SESSION_LOADS = 64
_SESSION_LOAD_TTL_SECONDS = 30.0
_MIN_SINGLE_WORD_CHARS = 4
_SPECULATION_PREAMBLE_CHARS = 2_048
_WORD_PATTERN = re.compile(r"[\w]+(?:['’][\w]+)?", re.UNICODE)
_UNRESOLVED_CORRECTION_PATTERN = re.compile(
    r"(?:^|\s)(?:uh+|um+|erm+|wait|sorry|actually|correction|no[,. ]+i mean)(?:\s|$)",
    re.IGNORECASE,
)


class LiveSpeculationRequest(BaseModel):
    content: str = Field(min_length=2, max_length=8_000)
    provider_id: str | None = None
    model_id: str | None = None
    segment_id: str = Field(min_length=1, max_length=180)
    source_sequence: int = Field(ge=0)


class LiveSpeculationAcceptRequest(BaseModel):
    final_text: str = Field(min_length=1, max_length=8_000)
    user_turn_id: str | None = Field(default=None, min_length=1, max_length=160)
    speech_segment_id: str | None = Field(default=None, min_length=1, max_length=160)
    live_voice_turn_id: str | None = Field(default=None, min_length=1, max_length=120)


@dataclass
class _Speculation:
    generation_id: str
    session_id: str
    candidate_text: str
    provider_id: str | None
    model_id: str | None
    segment_id: str
    source_sequence: int
    created_at: float
    execution_lane: str = "session"
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    error: str | None = None
    accepted_payload: dict[str, Any] | None = None


@dataclass
class _PrimedSession:
    session: Any
    primed_at: float


@dataclass
class _SessionLoad:
    event: threading.Event
    started_at: float


_SPECULATIONS: dict[str, _Speculation] = {}
_PRIMED_SESSIONS: dict[str, _PrimedSession] = {}
_SESSION_LOADS: OrderedDict[str, _SessionLoad] = OrderedDict()
_SPECULATION_LOCK = threading.RLock()


def normalized_transcript_words(text: str) -> tuple[str, ...]:
    return tuple(token.casefold().replace("’", "'") for token in _WORD_PATTERN.findall(text))


def transcript_is_speculation_safe(text: str) -> bool:
    words = normalized_transcript_words(text)
    has_stable_length = len(words) >= 2 or (
        len(words) == 1 and len(words[0]) >= _MIN_SINGLE_WORD_CHARS
    )
    return has_stable_length and _UNRESOLVED_CORRECTION_PATTERN.search(text) is None


def transcripts_are_compatible(candidate: str, final: str) -> bool:
    return (
        transcript_is_speculation_safe(candidate)
        and normalized_transcript_words(candidate) == normalized_transcript_words(final)
    )


def prime_live_speculation_session(session: Any) -> None:
    """Cache one read-only session snapshot for the next live speculation."""

    session_id = str(getattr(session, "id", "") or "").strip()
    if not session_id:
        return
    snapshot = _copy_session(session)
    with _SPECULATION_LOCK:
        _prune_speculations()
        _PRIMED_SESSIONS[session_id] = _PrimedSession(
            session=snapshot,
            primed_at=time.time(),
        )
        _prune_primed_sessions()


def clear_live_speculation_session_cache() -> None:
    """Compatibility alias for clearing all in-process speculation caches."""

    clear_live_speculation_cache()


def clear_live_speculation_cache() -> None:
    """Invalidate speculative generations, session snapshots, and single-flight state."""

    with _SPECULATION_LOCK:
        _SPECULATIONS.clear()
        _PRIMED_SESSIONS.clear()
        waiting = [load.event for load in _SESSION_LOADS.values()]
        _SESSION_LOADS.clear()
    for event in waiting:
        event.set()


def register_live_chat_speculation_routes(router: APIRouter,
    *,
    chat_store_factory: Callable[[], ChatSessionStore] = default_chat_store,
) -> None:

    @router.post(
        "/api/live/speculation/sessions/{session_id}/stream",
        response_model=None,
        responses={
            200: {
                "description": "Speculative generation events as Server-Sent Events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def stream_live_speculation(
        session_id: str,
        request: LiveSpeculationRequest,
    ) -> StreamingResponse:
        if not transcript_is_speculation_safe(request.content):
            raise HTTPException(status_code=409, detail="speculation_transcript_not_stable")
        store = chat_store_factory()
        session, cache_hit, session_load_ms = await _resolve_speculation_session(
            store,
            session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="chat session not found")
        provider_id, model_id, execution_lane = resolve_live_voice_chat_route(
            request.provider_id or session.provider_id,
            request.model_id or session.model_id,
        )
        stream_log(
            "gateway-live-chat-speculation",
            "runtime",
            "live_chat_speculation_session_resolved",
            cache_hit=cache_hit,
            session_load_ms=round(session_load_ms, 3),
            execution_lane=execution_lane,
            provider_id=provider_id,
            model_id=model_id,
        )
        generation_id = f"spec-{uuid.uuid4().hex}"
        speculation = _Speculation(
            generation_id=generation_id,
            session_id=session_id,
            candidate_text=request.content.strip(),
            provider_id=provider_id,
            model_id=model_id,
            segment_id=request.segment_id,
            source_sequence=request.source_sequence,
            created_at=time.time(),
            execution_lane=execution_lane,
        )
        with _SPECULATION_LOCK:
            _prune_speculations()
            _SPECULATIONS[generation_id] = speculation

        def generate() -> Iterator[str]:
            yield _speculation_started_sse(
                {
                    "type": "speculation_started",
                    "generation_id": generation_id,
                    "segment_id": request.segment_id,
                    "source_sequence": request.source_sequence,
                    "provider_id": speculation.provider_id,
                    "model_id": speculation.model_id,
                    "execution_lane": speculation.execution_lane,
                }
            )
            try:
                yield from _generate_side_effect_free(store, session, speculation)
            except Exception as exc:  # noqa: BLE001 - normalized into private speculative stream
                message = str(exc) or "Speculative generation failed."
                with _SPECULATION_LOCK:
                    speculation.error = message
                    speculation.completed = True
                yield _sse({"type": "error", "generation_id": generation_id, "message": message})
            finally:
                yield _sse({"type": "done", "generation_id": generation_id})

        headers = {
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Omnix-Speculation-Generation-Id": generation_id,
            "X-Omnix-Live-Execution-Lane": speculation.execution_lane,
        }
        if speculation.provider_id:
            headers["X-Omnix-Speculation-Provider-Id"] = speculation.provider_id
        if speculation.model_id:
            headers["X-Omnix-Speculation-Model-Id"] = speculation.model_id
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=headers,
        )

    @router.post(
        "/api/live/speculation/sessions/{session_id}/{generation_id}/accept",
    )
    async def accept_live_speculation(
        session_id: str,
        generation_id: str,
        request: LiveSpeculationAcceptRequest,
    ) -> dict[str, Any]:
        with _SPECULATION_LOCK:
            _prune_speculations()
            speculation = _SPECULATIONS.get(generation_id)
            if speculation is None or speculation.session_id != session_id:
                raise HTTPException(status_code=404, detail="speculation_not_found")
            if speculation.accepted_payload is not None:
                return dict(speculation.accepted_payload)
            if not speculation.completed:
                raise HTTPException(status_code=409, detail="speculation_not_complete")
            if speculation.error:
                raise HTTPException(status_code=409, detail="speculation_failed")
            if not transcripts_are_compatible(speculation.candidate_text, request.final_text):
                raise HTTPException(status_code=409, detail="speculation_transcript_mismatch")

        store = chat_store_factory()
        persist_started = time.perf_counter()
        begin_started = time.perf_counter()
        appended = await asyncio.to_thread(
            store.begin_user_message,
            session_id,
            SendChatMessageRequest(
                content=request.final_text.strip(),
                provider_id=speculation.provider_id,
                model_id=speculation.model_id,
                user_turn_id=request.user_turn_id
                or f"speculation-user:{generation_id}"[:160],
                speech_segment_id=request.speech_segment_id
                or f"speculation-segment:{speculation.segment_id}"[:160],
            ),
        )
        begin_ms = (time.perf_counter() - begin_started) * 1000.0
        if appended is None:
            raise HTTPException(status_code=404, detail="chat session not found")
        _, user_message = appended
        complete_started = time.perf_counter()
        completed = await asyncio.to_thread(
            store.complete_streamed_reply,
            session_id,
            user_message.id,
            speculation.content,
            {
                **speculation.metadata,
                "generation_status": "completed",
                "speculative_generation": True,
                "speculation_generation_id": generation_id,
                "speculation_candidate_words": len(
                    normalized_transcript_words(speculation.candidate_text)
                ),
                "live_voice_turn_id": request.live_voice_turn_id,
                "live_execution_lane": speculation.execution_lane,
            },
        )
        complete_ms = (time.perf_counter() - complete_started) * 1000.0
        if completed is None:
            raise HTTPException(status_code=409, detail="speculation_accept_failed")
        prime_live_speculation_session(completed)
        payload = {
            "ok": True,
            "generation_id": generation_id,
            "content": speculation.content,
            "execution_lane": speculation.execution_lane,
            "user_message": user_message.model_dump(mode="json"),
            "session": completed.model_dump(mode="json"),
        }
        with _SPECULATION_LOCK:
            speculation.accepted_payload = payload
        stream_log(
            "gateway-live-chat-speculation",
            "runtime",
            "live_chat_speculation_accept_persisted",
            begin_ms=round(begin_ms, 3),
            complete_ms=round(complete_ms, 3),
            total_ms=round((time.perf_counter() - persist_started) * 1000.0, 3),
            event_loop_offloaded=True,
            execution_lane=speculation.execution_lane,
        )
        return payload


async def _resolve_speculation_session(
    store: ChatSessionStore,
    session_id: str,
) -> tuple[Any | None, bool, float]:
    started = time.perf_counter()
    cached = _get_primed_session(session_id)
    if cached is not None:
        return cached, True, (time.perf_counter() - started) * 1000.0

    with _SPECULATION_LOCK:
        cached = _get_primed_session_locked(session_id)
        if cached is not None:
            return cached, True, (time.perf_counter() - started) * 1000.0
        load, owns_load = _session_load_locked(session_id, time.monotonic())

    if not owns_load:
        await asyncio.to_thread(load.event.wait, _SESSION_LOAD_WAIT_SECONDS)
        cached = _get_primed_session(session_id)
        if cached is not None:
            return cached, True, (time.perf_counter() - started) * 1000.0

    try:
        session = await asyncio.to_thread(store.get_session, session_id)
        if session is not None:
            prime_live_speculation_session(session)
            session = _get_primed_session(session_id) or session
        return session, False, (time.perf_counter() - started) * 1000.0
    finally:
        if owns_load:
            with _SPECULATION_LOCK:
                if _SESSION_LOADS.get(session_id) is load:
                    _SESSION_LOADS.pop(session_id, None)
            load.event.set()


def _get_primed_session(session_id: str) -> Any | None:
    with _SPECULATION_LOCK:
        return _get_primed_session_locked(session_id)


def _get_primed_session_locked(session_id: str) -> Any | None:
    primed = _PRIMED_SESSIONS.get(session_id)
    if primed is None:
        return None
    if time.time() - primed.primed_at > _SESSION_CACHE_TTL_SECONDS:
        _PRIMED_SESSIONS.pop(session_id, None)
        return None
    return _copy_session(primed.session)


def _copy_session(session: Any) -> Any:
    model_copy = getattr(session, "model_copy", None)
    if callable(model_copy):
        return model_copy(deep=True)
    return copy.deepcopy(session)


def _generate_side_effect_free(
    store: ChatSessionStore,
    session: Any,
    speculation: _Speculation,
    *,
    cancel_event: CancellationToken | None = None,
) -> Iterator[str]:
    live_voice = getattr(store, "live_voice_chat_port", None)
    if live_voice is None:
        raise RuntimeError("live_voice_chat_port_required")
    with provider_lease(_provider_key(speculation.provider_id)) as provider:
        if provider is None:
            raise RuntimeError("Chat provider is not available")
        user_message = ChatMessage(
            id=f"speculation-user-{speculation.generation_id}",
            role="user",
            content=speculation.candidate_text,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata={
                "speculative": True,
                "side_effects_allowed": False,
                "tools_allowed": False,
                "memory_writes_allowed": False,
                "user_turn_id": f"voice-user-turn:{speculation.generation_id}"[:160],
                "speech_segment_id": f"voice-segment:{speculation.segment_id}"[:160],
                "live_execution_lane": speculation.execution_lane,
            },
        )
        assembly, rendered = store.build_provider_prompt(session, user_message, [])
        messages = [
            ProviderMessage(role=item.role, content=item.content)
            for item in rendered.messages
        ]
        provider_kwargs: dict[str, Any] = {}
        if cancel_event is not None and provider_supports(provider, CANCELLATION):
            provider_kwargs["cancel"] = cancel_event
        if provider_supports(provider, THINKING_TOGGLE):
            provider_kwargs.update(live_voice.lmstudio_live_voice_options(user_message))
        if provider_supports(provider, RUNTIME_STATS):
            provider_kwargs["include_metrics"] = True
        raw_response = provider.chat_completion(
            messages=messages,
            model=_model_key(speculation.model_id),
            stream=True,
            **provider_kwargs,
        )
        response = live_voice.observe_live_voice_provider_stream(raw_response)
        chunker = live_voice.new_text_chunker()
        full_text = ""
        for chunk in response:
            if cancel_event is not None and cancel_event.is_set():
                return
            text = getattr(chunk, "content", "") or ""
            if not text:
                continue
            full_text += text
            for ready in chunker.push(text):
                yield _sse({
                    "type": "text_chunk",
                    "generation_id": speculation.generation_id,
                    "text": ready,
                })
        if cancel_event is not None and cancel_event.is_set():
            return
        remaining = chunker.flush()
        if remaining:
            yield _sse({
                "type": "text_chunk",
                "generation_id": speculation.generation_id,
                "text": remaining,
            })
        with _SPECULATION_LOCK:
            speculation.content = full_text.strip()
            speculation.metadata = {
                "provider_id": speculation.provider_id,
                "model_id": speculation.model_id,
                "resolved_model": speculation.model_id,
                "live_execution_lane": speculation.execution_lane,
                "speculation_side_effects": "disabled",
                "speculation_tools": "disabled",
                "speculation_memory_writes": "disabled",
                "prompt_source_count": len(getattr(assembly, "sources", []) or []),
            }
            speculation.completed = True
        yield _sse({
            "type": "complete",
            "generation_id": speculation.generation_id,
            "content": speculation.content,
            "metadata": speculation.metadata,
        })


def _prune_speculations() -> None:
    cutoff = time.time() - _SPECULATION_TTL_SECONDS
    expired = [key for key, value in _SPECULATIONS.items() if value.created_at < cutoff]
    for key in expired:
        _SPECULATIONS.pop(key, None)
    if len(_SPECULATIONS) > _MAX_SPECULATIONS:
        oldest = sorted(_SPECULATIONS.values(), key=lambda item: item.created_at)
        for item in oldest[: len(_SPECULATIONS) - _MAX_SPECULATIONS]:
            _SPECULATIONS.pop(item.generation_id, None)
    _prune_primed_sessions()


def _prune_primed_sessions() -> None:
    cutoff = time.time() - _SESSION_CACHE_TTL_SECONDS
    expired = [
        key
        for key, value in _PRIMED_SESSIONS.items()
        if value.primed_at < cutoff
    ]
    for key in expired:
        _PRIMED_SESSIONS.pop(key, None)
    if len(_PRIMED_SESSIONS) <= _MAX_PRIMED_SESSIONS:
        return
    oldest = sorted(_PRIMED_SESSIONS.items(), key=lambda item: item[1].primed_at)
    for key, _ in oldest[: len(_PRIMED_SESSIONS) - _MAX_PRIMED_SESSIONS]:
        _PRIMED_SESSIONS.pop(key, None)


def _prune_session_loads_locked(now: float) -> None:
    expired = [
        session_id
        for session_id, load in _SESSION_LOADS.items()
        if now - load.started_at > _SESSION_LOAD_TTL_SECONDS
    ]
    for session_id in expired:
        load = _SESSION_LOADS.pop(session_id, None)
        if load is not None:
            load.event.set()


def _session_load_locked(session_id: str, now: float) -> tuple[_SessionLoad, bool]:
    _prune_session_loads_locked(now)
    existing = _SESSION_LOADS.get(session_id)
    if existing is not None:
        _SESSION_LOADS.move_to_end(session_id)
        return existing, False
    while len(_SESSION_LOADS) >= _MAX_SESSION_LOADS:
        _old_session_id, oldest = _SESSION_LOADS.popitem(last=False)
        oldest.event.set()
    created = _SessionLoad(threading.Event(), now)
    _SESSION_LOADS[session_id] = created
    return created, True


def _speculation_started_sse(payload: dict[str, Any]) -> str:
    """Send enough initial SSE bytes to defeat small-chunk buffering."""

    preamble = f": omnix-speculation-open {' ' * _SPECULATION_PREAMBLE_CHARS}\n\n"
    return preamble + _sse(payload)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, sort_keys=True)}\n\n"


__all__ = [
    "clear_live_speculation_session_cache",
    "clear_live_speculation_cache",
    "normalized_transcript_words",
    "prime_live_speculation_session",
    "register_live_chat_speculation_routes",
    "transcript_is_speculation_safe",
    "transcripts_are_compatible",
]
