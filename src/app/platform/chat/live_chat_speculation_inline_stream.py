"""Single-request eager SSE transport for live LLM speculation.

This route allocates the speculative generation and attaches its only consumer
before the worker begins. It removes the second browser request from the hot
path while retaining the existing JSON handshake routes as a compatibility
fallback.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.platform.chat import ChatSessionStore, default_chat_store
from app.platform.chat.store import _provider_key

from . import live_chat_speculation as speculation_runtime
from . import live_chat_speculation_handshake as handshake_runtime
from .live_call_prewarm import live_call_provider_affinity
from .provider_routing import resolve_effective_provider_id
from app.runtime.live_voice_config import resolve_live_voice_chat_route
from app.observability.tts_stream_diagnostics import stream_log

_CLIENT_GENERATION_ID = re.compile(r"^spec-client-[A-Za-z0-9_-]{8,80}$")
_SPECULATION_BLOCKED_PROVIDER_IDS = frozenset({"cerebras"})


def _effective_speculation_route(
    session_id: str,
    request: speculation_runtime.LiveSpeculationRequest,
) -> tuple[str | None, str | None, str]:
    """Resolve speculation against current Settings/prewarm, not stale session state."""

    requested_provider_id = str(request.provider_id or "").strip() or None
    requested_model_id = str(request.model_id or "").strip() or None
    if requested_provider_id is not None:
        # Explicit provider selection wins and must not inherit a model from the
        # provider previously persisted on the chat session.
        return resolve_live_voice_chat_route(
            requested_provider_id,
            requested_model_id,
        )

    configured_provider_id = resolve_effective_provider_id(None)
    affinity = live_call_provider_affinity(session_id)
    if affinity is not None:
        affinity_provider_id, affinity_model_id = affinity
        if _provider_key(affinity_provider_id) == _provider_key(configured_provider_id):
            return resolve_live_voice_chat_route(
                affinity_provider_id or configured_provider_id,
                requested_model_id or affinity_model_id,
            )

    return resolve_live_voice_chat_route(
        configured_provider_id,
        requested_model_id,
    )


def register_live_chat_speculation_inline_stream_routes(router: APIRouter,
    *,
    chat_store_factory: Callable[[], ChatSessionStore] = default_chat_store,
) -> None:
    """Register the one-request eager speculation stream."""


    @router.post(
        "/api/live/speculation/sessions/{session_id}/start-stream",
        response_model=None,
        responses={
            200: {
                "description": "Speculation start and generation events as Server-Sent Events.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
    )
    async def start_and_stream_live_speculation(
        session_id: str,
        request: speculation_runtime.LiveSpeculationRequest,
        raw_request: Request,
    ) -> StreamingResponse:
        started = time.perf_counter()
        if not speculation_runtime.transcript_is_speculation_safe(request.content):
            raise HTTPException(
                status_code=409,
                detail="speculation_transcript_not_stable",
            )

        store = chat_store_factory()
        session, cache_hit, session_load_ms = (
            await speculation_runtime._resolve_speculation_session(store, session_id)
        )
        if session is None:
            raise HTTPException(status_code=404, detail="chat session not found")

        provider_id, model_id, execution_lane = _effective_speculation_route(
            session_id,
            request,
        )
        if str(provider_id or "").strip().casefold() in _SPECULATION_BLOCKED_PROVIDER_IDS:
            stream_log(
                "gateway-live-chat-speculation",
                "runtime",
                "live_chat_speculation_provider_suppressed",
                provider_id=provider_id,
                model_id=model_id,
                execution_lane=execution_lane,
                reason="rate_limited_remote_provider",
                session_id=session_id,
                segment_id=request.segment_id,
                source_sequence=request.source_sequence,
            )
            raise HTTPException(
                status_code=409,
                detail="speculation_provider_suppressed",
            )
        generation_id = _requested_generation_id(await _request_payload(raw_request))
        if generation_id is None:
            generation_id = f"spec-{uuid.uuid4().hex}"
        pending = speculation_runtime._Speculation(
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
        generation = handshake_runtime._HandshakeGeneration(
            store=store,
            session=session,
        )
        worker = threading.Thread(
            target=handshake_runtime._run_generation,
            args=(pending, generation),
            name=f"omnix-live-speculation-{generation_id[-8:]}",
            daemon=True,
        )
        generation.worker = worker

        superseded_generation_ids: list[str] = []
        with speculation_runtime._SPECULATION_LOCK:
            speculation_runtime._prune_speculations()
            handshake_runtime._prune_handshake_state_locked()
            if (
                generation_id in speculation_runtime._SPECULATIONS
                or generation_id in handshake_runtime._HANDSHAKE_GENERATIONS
            ):
                raise HTTPException(
                    status_code=409,
                    detail="speculation_generation_id_conflict",
                )
            superseded_generation_ids = _supersede_prior_generations_locked(pending)
            speculation_runtime._SPECULATIONS[generation_id] = pending
            handshake_runtime._HANDSHAKE_GENERATIONS[generation_id] = generation
            generation.stream_started = True

        if superseded_generation_ids:
            stream_log(
                "gateway-live-chat-speculation",
                "runtime",
                "live_chat_speculation_superseded",
                generation_id=generation_id,
                superseded_generation_ids=superseded_generation_ids,
                superseded_count=len(superseded_generation_ids),
                segment_id=request.segment_id,
                source_sequence=request.source_sequence,
                execution_lane=execution_lane,
            )

        allocation_ms = (time.perf_counter() - started) * 1000.0
        stream_log(
            "gateway-live-chat-speculation",
            "runtime",
            "live_chat_speculation_handshake_ready",
            generation_id=generation_id,
            client_allocated=generation_id.startswith("spec-client-"),
            cache_hit=cache_hit,
            session_load_ms=round(session_load_ms, 3),
            total_ms=round(allocation_ms, 3),
            generation_started=False,
            inline_stream=True,
            execution_lane=execution_lane,
            provider_id=provider_id,
            model_id=model_id,
            max_buffered_events=handshake_runtime._MAX_BUFFERED_EVENTS,
            max_buffered_bytes=handshake_runtime._MAX_BUFFERED_BYTES,
        )
        stream_log(
            "gateway-live-chat-speculation",
            "runtime",
            "live_chat_speculation_stream_attached",
            generation_id=generation_id,
            buffered_event_count=0,
            buffered_bytes=0,
            generation_completed=False,
            attach_delay_ms=round(allocation_ms, 3),
            inline_stream=True,
            execution_lane=execution_lane,
        )

        def generate() -> Iterator[str]:
            yield speculation_runtime._speculation_started_sse(
                {
                    "type": "speculation_started",
                    "generation_id": generation_id,
                    "segment_id": request.segment_id,
                    "source_sequence": request.source_sequence,
                    "provider_id": pending.provider_id,
                    "model_id": pending.model_id,
                    "execution_lane": pending.execution_lane,
                    "inline_stream": True,
                    "client_allocated": generation_id.startswith("spec-client-"),
                }
            )
            worker.start()
            yield from handshake_runtime._subscribe_generation(
                pending,
                generation,
            )

        headers = {
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Omnix-Speculation-Generation-Id": generation_id,
            "X-Omnix-Speculation-Transport": "inline-v2-client-id",
            "X-Omnix-Live-Execution-Lane": pending.execution_lane,
        }
        if pending.provider_id:
            headers["X-Omnix-Speculation-Provider-Id"] = pending.provider_id
        if pending.model_id:
            headers["X-Omnix-Speculation-Model-Id"] = pending.model_id
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=headers,
        )


def _supersede_prior_generations_locked(pending: Any) -> list[str]:
    """Cancel older inline hypotheses for the exact same utterance identity."""

    superseded: list[str] = []
    for generation_id, existing in tuple(speculation_runtime._SPECULATIONS.items()):
        if generation_id == pending.generation_id or existing.completed:
            continue
        if (
            existing.session_id != pending.session_id
            or existing.segment_id != pending.segment_id
            or existing.source_sequence != pending.source_sequence
        ):
            continue
        generation = handshake_runtime._HANDSHAKE_GENERATIONS.get(generation_id)
        if generation is None:
            continue
        generation.cancel_event.set()
        handshake_runtime._finish_with_error(
            existing,
            generation,
            code="speculation_superseded",
            message="A newer speculative hypothesis superseded this generation.",
        )
        superseded.append(generation_id)
    return superseded


async def _request_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - invalid optional hint is ignored.
        return {}
    return payload if isinstance(payload, dict) else {}


def _requested_generation_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("generation_id")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not _CLIENT_GENERATION_ID.fullmatch(normalized):
        raise HTTPException(
            status_code=422,
            detail="invalid_speculation_generation_id",
        )
    return normalized


__all__ = ["register_live_chat_speculation_inline_stream_routes"]
