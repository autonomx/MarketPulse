"""Accepted-first speculative TTS cache for the dedicated live execution lane."""
from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.env import env_str as _env_str
from app.conversation.performance_contract import (
    apply_performance_plan_to_provider,
    resolve_tts_provider_capabilities,
)
from app.conversation.text import remove_emojis
from app.platform.live_voice.hardware_policy import should_defer_speculative_tts

from .tts_lane import (
    TtsLanePriority,
    live_voice_tts_scheduler,
    resolve_live_voice_tts_provider,
)
from app.runtime.live_voice_config import live_voice_execution_lane_config
from .runtime_offload import get_cached_live_tts_provider
from app.conversation.tts_stream_contract import TtsStreamRequest, audio_chunk_to_pcm16_bytes
from app.observability.tts_stream_diagnostics import stream_log

_ROUTE_SENTINEL = "_omnix_live_voice_speculative_tts_routes_registered"
_CACHE_TTL_SECONDS = 45.0
# Accepted speculative PCM is useful only for the immediately following
# authoritative TTS request. Keeping an unclaimed accepted entry around for the
# full cache TTL can let a later turn with the same text prefix replay stale
# audio. Five seconds leaves ample contention headroom while failing safely to
# fresh TTS if the originating turn never claims its cache.
_ACCEPTED_UNCLAIMED_TTL_SECONDS = 5.0
_MAX_CACHE_ENTRIES = 16
_WAIT_SLICE_SECONDS = 0.025
# An accepted claim can race the provider's first decoder step. Abandoning the
# cache at that instant starts a second CUDA generation while the promoted one
# is about to produce PCM, making the cold turn slower and wasting GPU work.
# Wait only through the measured first-step envelope plus steady decoder work,
# then fail safely to a fresh authoritative stream if the promoted producer is
# genuinely stuck. Promotion is based on playable duration rather than provider
# chunk count: one accepted two-step Qwen chunk already contains the complete
# 160 ms startup frame, while two hidden one-step chunks are still required to
# provide the same runway. The websocket sender continues to deliver later
# frames between decoder steps.
_COLD_CLAIM_WAIT_SECONDS = 0.55
_COLD_CLAIM_MIN_AUDIO_MS = 160.0
# Provider chunk_size controls streaming/cancellation cadence, not synthesis
# identity. Accepted first-phrase playback may intentionally request a smaller
# chunk than hidden speculation, and cached PCM is reblocked by the live TTS
# transport before playback. Keep only synthesis-affecting controls in the key.
_CACHE_KEY_KWARGS = frozenset(
    {
        "temperature",
        "top_k",
        "top_p",
        "repetition_penalty",
        "append_silence",
        "non_streaming_mode",
        "parity_mode",
    }
)
_ONLY_PUNCTUATION = re.compile(r"^[\W_]+$", re.UNICODE)
_ALLOW_SERIAL_TTS_SPECULATION_ENV = "OMNIX_LIVE_TTS_ALLOW_SERIAL_SPECULATION"
_DEFERRED_TTS_ERROR = "speculative_tts_deferred_nonconcurrent_provider"


class SpeculativeTtsPrefetchRequest(BaseModel):
    generation_id: str = Field(min_length=1, max_length=160)
    request: TtsStreamRequest


@dataclass
class _CachedPcmChunk:
    pcm_bytes: bytes
    sample_rate: int
    timing: Any


@dataclass
class _SpeculativeTtsEntry:
    generation_id: str
    created_at: float
    text: str
    speaker: str
    language: str
    stable_kwargs: dict[str, Any]
    lane: str
    chunks: list[_CachedPcmChunk] = field(default_factory=list)
    accepted: bool = False
    accepted_at: float | None = None
    claimed: bool = False
    completed: bool = False
    cancelled: bool = False
    error: str | None = None
    promotion_event: threading.Event = field(default_factory=threading.Event)
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock())
    )


@dataclass(frozen=True)
class _CacheClaim:
    entry: _SpeculativeTtsEntry
    remainder_text: str


_ENTRIES: dict[str, _SpeculativeTtsEntry] = {}
_CACHE_LOCK = threading.RLock()


class _LiveLaneProviderProxy:
    """Replay accepted PCM and schedule all provider work by turn priority."""

    def __init__(self, provider: Any, lane: str) -> None:
        self._provider = provider
        self.execution_lane = lane

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def generate_audio_stream(
        self,
        *,
        text: str,
        speaker: str | None = None,
        language: str = "en",
        **kwargs: Any,
    ) -> Iterator[tuple[bytes, int, Any]]:
        claim = _claim_entry(text, speaker, language, kwargs)
        if claim is None:
            yield from _scheduled_provider_stream(
                self._provider,
                text=text,
                speaker=speaker,
                language=language,
                kwargs=kwargs,
                priority=TtsLanePriority.ACCEPTED,
            )
            return

        entry = claim.entry
        stream_log(
            "gateway-live-speculative-tts",
            "provider",
            "speculative_tts_cache_claimed",
            generation_id=entry.generation_id,
            buffered_chunk_count=len(entry.chunks),
            completed=entry.completed,
            prefix_match=bool(claim.remainder_text),
            remainder_text_length=len(claim.remainder_text),
            execution_lane=entry.lane,
        )
        index = 0
        emitted = 0
        replay_completed = False
        try:
            while True:
                with entry.condition:
                    while (
                        index >= len(entry.chunks)
                        and not entry.completed
                        and not entry.cancelled
                        and entry.error is None
                    ):
                        entry.condition.wait(_WAIT_SLICE_SECONDS)
                    available = list(entry.chunks[index:])
                    index += len(available)
                    terminal = (
                        entry.completed
                        or entry.cancelled
                        or entry.error is not None
                    )
                    error = entry.error
                for chunk in available:
                    emitted += 1
                    yield chunk.pcm_bytes, chunk.sample_rate, {
                        **(
                            chunk.timing
                            if isinstance(chunk.timing, dict)
                            else {}
                        ),
                        "speculative_tts_cache": True,
                        "speculation_generation_id": entry.generation_id,
                        "live_execution_lane": entry.lane,
                    }
                if terminal:
                    break

            if emitted == 0 and error:
                stream_log(
                    "gateway-live-speculative-tts",
                    "provider",
                    "speculative_tts_cache_fallback",
                    generation_id=entry.generation_id,
                    error=error,
                    execution_lane=self.execution_lane,
                )
                yield from _scheduled_provider_stream(
                    self._provider,
                    text=text,
                    speaker=speaker,
                    language=language,
                    kwargs=kwargs,
                    priority=TtsLanePriority.ACCEPTED,
                )
                replay_completed = True
                return

            if claim.remainder_text:
                yield from _scheduled_provider_stream(
                    self._provider,
                    text=claim.remainder_text,
                    speaker=speaker,
                    language=language,
                    kwargs=kwargs,
                    priority=TtsLanePriority.CONTINUATION,
                )

            replay_completed = True
            stream_log(
                "gateway-live-speculative-tts",
                "provider",
                "speculative_tts_cache_replayed",
                generation_id=entry.generation_id,
                emitted_chunk_count=emitted,
                completed=entry.completed,
                cancelled=entry.cancelled,
                error=entry.error,
                prefix_match=bool(claim.remainder_text),
                remainder_text_length=len(claim.remainder_text),
                execution_lane=entry.lane,
            )
        finally:
            if not replay_completed:
                with entry.condition:
                    background_active = not entry.completed and not entry.cancelled
                    if background_active:
                        entry.cancelled = True
                        entry.condition.notify_all()
                if background_active:
                    stream_log(
                        "gateway-live-speculative-tts",
                        "provider",
                        "speculative_tts_replay_consumer_cancelled",
                        generation_id=entry.generation_id,
                        emitted_chunk_count=emitted,
                    )


def resolve_live_call_tts_provider(default_provider: Any) -> Any:
    """Resolve the explicit live-call TTS lane without patching provider lookup."""
    if default_provider is None or isinstance(default_provider, _LiveLaneProviderProxy):
        return default_provider
    provider, lane = resolve_live_voice_tts_provider(default_provider)
    if provider is None:
        return None
    return _LiveLaneProviderProxy(provider, lane)


def _scheduled_provider_stream(
    provider: Any,
    *,
    text: str,
    speaker: str | None,
    language: str,
    kwargs: dict[str, Any],
    priority: TtsLanePriority,
    should_stop: Callable[[], bool] | None = None,
    promotion_event: threading.Event | None = None,
) -> Iterator[tuple[Any, int, Any]]:
    yield from live_voice_tts_scheduler().stream(
        provider,
        text=text,
        speaker=speaker,
        language=language,
        kwargs=kwargs,
        priority=priority,
        should_stop=should_stop,
        promotion_event=promotion_event,
    )


def clear_speculative_tts_cache() -> None:
    with _CACHE_LOCK:
        entries = list(_ENTRIES.values())
        _ENTRIES.clear()
    for entry in entries:
        with entry.condition:
            entry.cancelled = True
            entry.condition.notify_all()


def speculative_tts_cache_snapshot() -> list[dict[str, Any]]:
    with _CACHE_LOCK:
        _prune_entries_locked()
        return [
            {
                "generation_id": entry.generation_id,
                "accepted": entry.accepted,
                "claimed": entry.claimed,
                "completed": entry.completed,
                "cancelled": entry.cancelled,
                "error": entry.error,
                "chunk_count": len(entry.chunks),
                "text_length": len(entry.text),
                "execution_lane": entry.lane,
            }
            for entry in _ENTRIES.values()
        ]


def _stream_kwargs(request: TtsStreamRequest, provider: Any) -> dict[str, Any]:
    performance = apply_performance_plan_to_provider(provider, request.delivery_plan)
    kwargs: dict[str, Any] = {
        "chunk_size": request.chunk_size,
        "temperature": request.temperature,
        "top_k": request.top_k,
        "top_p": request.top_p,
        "repetition_penalty": request.repetition_penalty,
        "append_silence": request.append_silence,
        "non_streaming_mode": False,
    }
    if request.max_new_tokens is not None:
        kwargs["max_new_tokens"] = request.max_new_tokens
    if request.parity_mode is not None:
        kwargs["parity_mode"] = request.parity_mode
    kwargs.update(performance.provider_kwargs)
    return kwargs


def _start_prefetch(
    generation_id: str,
    request: TtsStreamRequest,
    provider: Any,
    lane: str,
) -> _SpeculativeTtsEntry:
    text = _normalized_text(remove_emojis(request.text or ""))
    if not text:
        raise HTTPException(status_code=422, detail="text_required")
    speaker = _normalized_speaker(request.speaker)
    language = _normalized_language(request.language or "en")
    kwargs = _stream_kwargs(request, provider)
    if should_defer_speculative_tts(
        provider,
        _env_str(_ALLOW_SERIAL_TTS_SPECULATION_ENV),
    ):
        entry = _SpeculativeTtsEntry(
            generation_id=generation_id,
            created_at=time.time(),
            text=text,
            speaker=speaker,
            language=language,
            stable_kwargs=_stable_kwargs(kwargs),
            lane=lane,
            completed=True,
            error=_DEFERRED_TTS_ERROR,
        )
        with _CACHE_LOCK:
            _prune_entries_locked()
            previous = _ENTRIES.pop(generation_id, None)
            _ENTRIES[generation_id] = entry
            _prune_entries_locked()
        if previous is not None:
            with previous.condition:
                previous.cancelled = True
                previous.condition.notify_all()
        capabilities = resolve_tts_provider_capabilities(provider)
        stream_log(
            "gateway-live-speculative-tts",
            "scheduler",
            "speculative_tts_prefetch_deferred_nonconcurrent",
            generation_id=generation_id,
            provider_name=getattr(provider, "provider_name", None),
            supports_concurrent_generation=capabilities.supports_concurrent_generation,
            execution_lane=lane,
            text_length=len(text),
        )
        return entry

    entry = _SpeculativeTtsEntry(
        generation_id=generation_id,
        created_at=time.time(),
        text=text,
        speaker=speaker,
        language=language,
        stable_kwargs=_stable_kwargs(kwargs),
        lane=lane,
    )
    with _CACHE_LOCK:
        _prune_entries_locked()
        previous = _ENTRIES.pop(generation_id, None)
        _ENTRIES[generation_id] = entry
        _prune_entries_locked()
    if previous is not None:
        with previous.condition:
            previous.cancelled = True
            previous.condition.notify_all()

    def stopped() -> bool:
        with entry.condition:
            return entry.cancelled

    def produce() -> None:
        started = time.perf_counter()
        try:
            for audio_chunk, sample_rate, timing in _scheduled_provider_stream(
                provider,
                text=text,
                speaker=request.speaker,
                language=request.language or "en",
                kwargs=kwargs,
                priority=TtsLanePriority.SPECULATIVE,
                should_stop=stopped,
                promotion_event=entry.promotion_event,
            ):
                pcm_bytes = audio_chunk_to_pcm16_bytes(audio_chunk)
                if not pcm_bytes:
                    continue
                with entry.condition:
                    if entry.cancelled:
                        break
                    entry.chunks.append(
                        _CachedPcmChunk(
                            pcm_bytes=pcm_bytes,
                            sample_rate=int(sample_rate or 24_000),
                            timing=timing,
                        )
                    )
                    entry.condition.notify_all()
                # Once accepted playback is consuming this cache, the gateway
                # event loop must get a scheduling window between CUDA decoder
                # steps. Without an explicit yield, produced-real-time cadence
                # can still arrive at the browser as a >300 ms burst.
                time.sleep(0.001)
        except Exception as exc:  # noqa: BLE001 - private speculative work
            with entry.condition:
                if not entry.cancelled:
                    entry.error = str(exc) or type(exc).__name__
                entry.condition.notify_all()
        finally:
            with entry.condition:
                entry.completed = True
                entry.condition.notify_all()
            stream_log(
                "gateway-live-speculative-tts",
                "provider",
                "speculative_tts_prefetch_finished",
                generation_id=generation_id,
                chunk_count=len(entry.chunks),
                accepted=entry.accepted,
                claimed=entry.claimed,
                cancelled=entry.cancelled,
                error=entry.error,
                execution_lane=lane,
                elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
            )

    threading.Thread(
        target=produce,
        name=f"omnix-live-lane-tts-{generation_id[-20:]}",
        daemon=True,
    ).start()
    return entry


def _accept_entry(generation_id: str) -> _SpeculativeTtsEntry:
    with _CACHE_LOCK:
        _prune_entries_locked()
        entry = _ENTRIES.get(generation_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="speculative_tts_not_found")
        if not entry.accepted:
            entry.accepted_at = time.time()
        entry.accepted = True
        entry.promotion_event.set()
    live_voice_tts_scheduler().notify_priority_change()
    return entry


def _cancel_entry(generation_id: str) -> bool:
    with _CACHE_LOCK:
        entry = _ENTRIES.pop(generation_id, None)
    if entry is None:
        return False
    with entry.condition:
        entry.cancelled = True
        entry.condition.notify_all()
    return True


def _claim_entry(
    text: str,
    speaker: str | None,
    language: str,
    kwargs: dict[str, Any],
) -> _CacheClaim | None:
    actual_text = _normalized_text(text)
    actual_speaker = _normalized_speaker(speaker)
    actual_language = _normalized_language(language)
    actual_kwargs = _stable_kwargs(kwargs)
    with _CACHE_LOCK:
        _prune_entries_locked()
        matches: list[tuple[int, float, _SpeculativeTtsEntry, str]] = []
        for entry in _ENTRIES.values():
            if (
                not entry.accepted
                or entry.claimed
                or entry.cancelled
                or entry.error is not None
                or entry.speaker != actual_speaker
                or entry.language != actual_language
                or entry.stable_kwargs != actual_kwargs
            ):
                continue
            remainder = _prefix_remainder(entry.text, actual_text)
            if remainder is None:
                continue
            matches.append((len(entry.text), entry.created_at, entry, remainder))
        if not matches:
            return None
        _, _, entry, remainder = max(matches, key=lambda item: (item[0], item[1]))
        with entry.condition:
            entry.claimed = True
            initial_buffered_chunk_count = len(entry.chunks)
            initial_buffered_audio_ms = _buffered_audio_ms(entry)
            if (
                entry.completed
                or initial_buffered_audio_ms >= _COLD_CLAIM_MIN_AUDIO_MS
            ):
                return _CacheClaim(entry=entry, remainder_text=remainder)

    wait_started = time.perf_counter()
    with entry.condition:
        deadline = time.monotonic() + _COLD_CLAIM_WAIT_SECONDS
        while (
            _buffered_audio_ms(entry) < _COLD_CLAIM_MIN_AUDIO_MS
            and not entry.completed
            and not entry.cancelled
            and entry.error is None
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            entry.condition.wait(min(_WAIT_SLICE_SECONDS, remaining))
        if entry.chunks:
            wait_ms = round((time.perf_counter() - wait_started) * 1000.0, 3)
            stream_log(
                "gateway-live-speculative-tts",
                "provider",
                "speculative_tts_cache_cold_claim_promoted",
                generation_id=entry.generation_id,
                buffered_chunk_count=len(entry.chunks),
                initial_buffered_chunk_count=initial_buffered_chunk_count,
                buffered_audio_ms=round(_buffered_audio_ms(entry), 3),
                initial_buffered_audio_ms=round(initial_buffered_audio_ms, 3),
                completed=entry.completed,
                wait_ms=wait_ms,
                prefix_match=bool(remainder),
                remainder_text_length=len(remainder),
                execution_lane=entry.lane,
            )
            return _CacheClaim(entry=entry, remainder_text=remainder)
        entry.cancelled = True
        entry.condition.notify_all()
        completed = entry.completed
        error = entry.error

    with _CACHE_LOCK:
        if _ENTRIES.get(entry.generation_id) is entry:
            _ENTRIES.pop(entry.generation_id, None)
    stream_log(
        "gateway-live-speculative-tts",
        "provider",
        "speculative_tts_cache_cold_claim_abandoned",
        generation_id=entry.generation_id,
        buffered_chunk_count=0,
        completed=completed,
        error=error,
        wait_ms=round((time.perf_counter() - wait_started) * 1000.0, 3),
        prefix_match=bool(remainder),
        remainder_text_length=len(remainder),
        execution_lane=entry.lane,
    )
    live_voice_tts_scheduler().notify_priority_change()
    return None


def _buffered_audio_ms(entry: _SpeculativeTtsEntry) -> float:
    return sum(
        (len(chunk.pcm_bytes) // 2) * 1000.0 / max(1, chunk.sample_rate)
        for chunk in entry.chunks
    )


def _prefix_remainder(cached_text: str, actual_text: str) -> str | None:
    if actual_text == cached_text:
        return ""
    if not actual_text.startswith(cached_text):
        return None
    boundary = actual_text[len(cached_text) : len(cached_text) + 1]
    if cached_text[-1:].isalnum() and boundary.isalnum():
        return None
    remainder = actual_text[len(cached_text) :].strip()
    if not remainder or _ONLY_PUNCTUATION.fullmatch(remainder):
        return ""
    return remainder.lstrip(" ,;:—–-").strip()


def _normalized_text(text: str) -> str:
    return " ".join((text or "").split())


def _normalized_speaker(speaker: str | None) -> str:
    return (speaker or "").strip()


def _normalized_language(language: str | None) -> str:
    return (language or "en").strip().casefold()


def _stable_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json_safe(value)
        for key, value in kwargs.items()
        if key in _CACHE_KEY_KWARGS
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    return repr(value)


def _prune_entries_locked() -> None:
    now = time.time()
    cutoff = now - _CACHE_TTL_SECONDS
    accepted_cutoff = now - _ACCEPTED_UNCLAIMED_TTL_SECONDS
    expired = [
        generation_id
        for generation_id, entry in _ENTRIES.items()
        if entry.created_at < cutoff
        or (
            entry.accepted
            and not entry.claimed
            and entry.accepted_at is not None
            and entry.accepted_at < accepted_cutoff
        )
    ]
    for generation_id in expired:
        entry = _ENTRIES.pop(generation_id, None)
        if entry is not None:
            with entry.condition:
                entry.cancelled = True
                entry.condition.notify_all()
    if len(_ENTRIES) <= _MAX_CACHE_ENTRIES:
        return
    oldest = sorted(_ENTRIES.values(), key=lambda item: item.created_at)
    for entry in oldest[: len(_ENTRIES) - _MAX_CACHE_ENTRIES]:
        _ENTRIES.pop(entry.generation_id, None)
        with entry.condition:
            entry.cancelled = True
            entry.condition.notify_all()


def register_live_voice_execution_lane_routes(router: APIRouter, state: Any) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)

    @router.get("/api/live/voice/execution-lane")
    def live_voice_execution_lane_status() -> dict[str, Any]:
        config = live_voice_execution_lane_config()
        return {
            "ok": True,
            "mode": config.mode,
            "provider_id": config.provider_id,
            "model_id": config.model_id,
            "dedicated_chat_enabled": config.dedicated_chat_enabled,
            "dedicated_tts": config.dedicated_tts,
            "tts_provider_name": config.tts_provider_name,
            "scheduler": live_voice_tts_scheduler().snapshot(),
        }

    @router.post("/api/live/speculation/tts-prefetch")
    def prefetch_speculative_tts(
        payload: SpeculativeTtsPrefetchRequest,
    ) -> dict[str, Any]:
        route_started = time.perf_counter()
        default_provider = get_cached_live_tts_provider()
        cached_provider_resolved = time.perf_counter()
        provider, lane = resolve_live_voice_tts_provider(default_provider)
        lane_provider_resolved = time.perf_counter()
        if provider is None or not hasattr(provider, "generate_audio_stream"):
            raise HTTPException(status_code=503, detail="tts_provider_unavailable")
        entry = _start_prefetch(
            payload.generation_id,
            payload.request,
            provider,
            lane,
        )
        prefetch_started = time.perf_counter()
        stream_log(
            "gateway-live-speculative-tts",
            "runtime",
            "speculative_tts_prefetch_started",
            generation_id=payload.generation_id,
            text_length=len(payload.request.text or ""),
            speaker=payload.request.speaker,
            execution_lane=lane,
            cached_provider_lookup_ms=round(
                (cached_provider_resolved - route_started) * 1000.0,
                3,
            ),
            lane_provider_resolution_ms=round(
                (lane_provider_resolved - cached_provider_resolved) * 1000.0,
                3,
            ),
            route_to_prefetch_thread_ms=round(
                (prefetch_started - route_started) * 1000.0,
                3,
            ),
        )
        return {
            "ok": True,
            "generation_id": entry.generation_id,
            "status": "generating",
            "execution_lane": lane,
        }

    @router.post(
        "/api/live/speculation/tts-prefetch/{generation_id}/accept",
    )
    def accept_speculative_tts(generation_id: str) -> dict[str, Any]:
        entry = _accept_entry(generation_id)
        with entry.condition:
            buffered = len(entry.chunks)
            completed = entry.completed
        return {
            "ok": True,
            "generation_id": generation_id,
            "buffered_chunk_count": buffered,
            "completed": completed,
            "execution_lane": entry.lane,
        }

    @router.post(
        "/api/live/speculation/tts-prefetch/{generation_id}/cancel",
    )
    def cancel_speculative_tts(generation_id: str) -> dict[str, Any]:
        return {
            "ok": True,
            "generation_id": generation_id,
            "cancelled": _cancel_entry(generation_id),
        }
