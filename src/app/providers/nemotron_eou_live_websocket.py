"""Segmented WebSocket transport for Nemotron ASR + Parakeet Realtime EOU."""
from __future__ import annotations
import logging
from app.config.env import env_str as _env_str

import asyncio
import json
import math
import struct
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.providers.live_stt_contracts import (
    CAP_AUTHORITATIVE_EOU,
    CAP_AUTHORITATIVE_FINAL,
    CAP_AUTHORITATIVE_PREVIEW,
    CAP_BINARY_AUDIO_FRAMES,
    CAP_PARTIAL_TRANSCRIPTS,
    CAP_RESULT_REPLAY,
    CAP_SEGMENTED_AUDIO,
    LiveSttNegotiation,
    audio_message_pcm,
    receive_client_message,
)
from app.providers.nemotron_eou_streaming import (
    SAMPLE_RATE,
    NemotronEouModelManager,
    model_manager,
)

logger = logging.getLogger(__name__)

SEGMENTED_PROTOCOL = "segmented-v1"
PROVIDER_NAME = "nemotron_parakeet_eou"
FRAME_SAMPLES = 320
MAX_SEGMENT_AUDIO_MS = 15_000
MAX_SEGMENT_BYTES = int(SAMPLE_RATE * 2 * MAX_SEGMENT_AUDIO_MS / 1_000)
MAX_REPLAY_RESULTS = 64
SESSION_TTL_SECONDS = 600.0
MAX_SESSION_STATES = 64

HYBRID_NEGOTIATION = LiveSttNegotiation(
    provider=PROVIDER_NAME,
    protocol=SEGMENTED_PROTOCOL,
    sample_rate=SAMPLE_RATE,
    frame_samples=FRAME_SAMPLES,
    capabilities=frozenset(
        {
            CAP_SEGMENTED_AUDIO,
            CAP_AUTHORITATIVE_FINAL,
            CAP_RESULT_REPLAY,
            CAP_PARTIAL_TRANSCRIPTS,
            CAP_AUTHORITATIVE_EOU,
            CAP_AUTHORITATIVE_PREVIEW,
            CAP_BINARY_AUDIO_FRAMES,
        }
    ),
)


def _metric(event: str, **fields: Any) -> None:
    logger.info("[STT_METRIC] "
        + json.dumps(
            {
                "event": event,
                "source": "nemotron-parakeet-eou-runtime",
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **fields,
            },
            sort_keys=True,
            default=str,
        ))


def _field(data: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    return data.get(camel, data.get(snake, default))


def primary_pcm_slice(sample_start: int, primary_start_sample: int, payload: bytes) -> bytes:
    """Remove the browser's cross-segment overlap before model inference."""
    if len(payload) % 2:
        raise ValueError("audio_frame_partial_sample")
    skip_samples = max(0, primary_start_sample - sample_start)
    if skip_samples >= len(payload) // 2:
        return b""
    return payload[skip_samples * 2 :]


def pcm16le_rms(payload: bytes) -> float:
    if not payload:
        return 0.0
    sample_count = len(payload) // 2
    if sample_count <= 0:
        return 0.0
    squared = sum(sample * sample for (sample,) in struct.iter_unpack("<h", payload))
    return math.sqrt(squared / sample_count) / 32768.0


def preview_tail_rms_threshold() -> float:
    try:
        value = float(_env_str("OMNIX_STT_PREVIEW_TAIL_RMS_THRESHOLD", "0.012"))
    except (TypeError, ValueError):
        return 0.012
    return min(0.05, max(0.001, value))


@dataclass
class HybridSegment:
    segment_id: str
    sequence: int
    capture_start_sample: int
    primary_start_sample: int
    capture_epoch: str = ""
    accepted_through_sample: int = 0
    primary_audio: bytearray = field(default_factory=bytearray)
    stream_pending: bytearray = field(default_factory=bytearray)
    stream_task: asyncio.Task[None] | None = None
    last_partial: str = ""
    eou_emitted: bool = False
    eou_candidate_count: int = 0
    finalize_request_id: str = ""
    end_sample: int = 0
    finalized: bool = False
    preview_request_id: str = ""
    preview_text: str = ""
    preview_decode_ms: float = 0.0
    preview_end_sample: int = 0
    preview_tail_max_rms: float = 0.0

    def append(self, sample_start: int, payload: bytes) -> int:
        if len(payload) % 2:
            raise ValueError("audio_frame_partial_sample")
        if self.accepted_through_sample == 0:
            self.accepted_through_sample = self.capture_start_sample
        sample_count = len(payload) // 2
        frame_end = sample_start + sample_count
        if frame_end <= self.accepted_through_sample:
            return self.accepted_through_sample
        if sample_start < self.accepted_through_sample:
            duplicate_samples = self.accepted_through_sample - sample_start
            payload = payload[duplicate_samples * 2 :]
            sample_start = self.accepted_through_sample
        if sample_start != self.accepted_through_sample:
            raise ValueError("audio_frame_gap")
        if len(self.primary_audio) + len(payload) > MAX_SEGMENT_BYTES + (SAMPLE_RATE * 2):
            raise ValueError("segment_audio_limit")
        primary = primary_pcm_slice(sample_start, self.primary_start_sample, payload)
        if primary:
            if len(self.primary_audio) + len(primary) > MAX_SEGMENT_BYTES:
                raise ValueError("segment_audio_limit")
            self.primary_audio.extend(primary)
            self.stream_pending.extend(primary)
            if self.preview_text and sample_start >= self.preview_end_sample:
                self.preview_tail_max_rms = max(
                    self.preview_tail_max_rms,
                    pcm16le_rms(primary),
                )
        self.accepted_through_sample = sample_start + len(payload) // 2
        return self.accepted_through_sample

    def remember_preview(
        self,
        *,
        request_id: str,
        text: str,
        decode_ms: float,
        end_sample: int,
    ) -> None:
        self.preview_request_id = request_id
        self.preview_text = text.strip()
        self.preview_decode_ms = decode_ms
        self.preview_end_sample = end_sample
        self.preview_tail_max_rms = 0.0

    def can_reuse_preview(self) -> bool:
        return bool(
            self.preview_text
            and self.preview_end_sample > self.primary_start_sample
            and self.preview_end_sample <= self.accepted_through_sample
            and self.preview_tail_max_rms < preview_tail_rms_threshold()
        )


@dataclass
class HybridSessionState:
    session_id: str
    segments: dict[str, HybridSegment] = field(default_factory=dict)
    results: dict[int, dict[str, Any]] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)

    def remember_result(self, payload: dict[str, Any]) -> None:
        self.results[int(payload["sequence"])] = payload
        while len(self.results) > MAX_REPLAY_RESULTS:
            self.results.pop(min(self.results))
        self.last_seen = time.monotonic()


_SESSION_STATES: OrderedDict[str, HybridSessionState] = OrderedDict()


def _session_state(session_id: str) -> HybridSessionState:
    now = time.monotonic()
    _prune_session_states(now)
    state = _SESSION_STATES.get(session_id)
    if state is None:
        if len(_SESSION_STATES) >= MAX_SESSION_STATES:
            idle = next(
                (key for key, value in _SESSION_STATES.items() if not value.segments),
                None,
            )
            if idle is None:
                raise RuntimeError("nemotron_live_session_capacity")
            _SESSION_STATES.pop(idle, None)
        state = HybridSessionState(session_id=session_id)
        _SESSION_STATES[session_id] = state
    _SESSION_STATES.move_to_end(session_id)
    state.last_seen = now
    return state


def clear_nemotron_session_states() -> None:
    """Invalidate transient segmented-audio replay and assembly state."""

    _SESSION_STATES.clear()


def _prune_session_states(now: float) -> None:
    stale = [
        key
        for key, value in _SESSION_STATES.items()
        if now - value.last_seen > SESSION_TTL_SECONDS and not value.segments
    ]
    for key in stale:
        _SESSION_STATES.pop(key, None)


async def _safe_send(websocket: WebSocket, lock: asyncio.Lock, payload: dict[str, Any]) -> bool:
    try:
        async with lock:
            await websocket.send_json(payload)
        return True
    except Exception:  # noqa: BLE001 - best-effort send during websocket teardown
        return False


async def _drain_stream_audio(
    segment: HybridSegment,
    manager: NemotronEouModelManager,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    *,
    flush: bool = False,
) -> None:
    chunk_bytes = manager.feed_chunk_samples * 2
    while len(segment.stream_pending) >= chunk_bytes or (flush and segment.stream_pending):
        take = min(chunk_bytes, len(segment.stream_pending))
        payload = bytes(segment.stream_pending[:take])
        del segment.stream_pending[:take]
        update = await asyncio.to_thread(manager.feed, segment.segment_id, payload)
        model_time_ms = round(
            len(segment.primary_audio) / 2 / SAMPLE_RATE * 1000.0,
            3,
        )
        if update.transcript_changed and update.transcript != segment.last_partial:
            segment.last_partial = update.transcript
            await _safe_send(
                websocket,
                send_lock,
                {
                    "type": "partial",
                    "provider": PROVIDER_NAME,
                    "segmentId": segment.segment_id,
                    "sequence": segment.sequence,
                    "text": update.transcript,
                },
            )
            _metric(
                "stt_streaming_partial",
                segment_sequence=segment.sequence,
                transcript_chars=len(update.transcript),
                nemotron_ms=round(update.nemotron_ms, 3),
                eou_ms=round(update.eou_ms, 3),
                model_time_ms=model_time_ms,
            )
        if update.eou:
            # EOU state is re-armed inside the model manager after every token.
            # Do not permanently latch the first candidate for this segment: if
            # the browser rejects a rare mid-turn candidate because speech is
            # still active, a later true endpoint must still be deliverable.
            segment.eou_emitted = True
            segment.eou_candidate_count += 1
            endpoint = {
                "provider": PROVIDER_NAME,
                "segmentId": segment.segment_id,
                "sequence": segment.sequence,
                "probability": 1.0,
                "modelTimeMs": model_time_ms,
                "signal": "eou",
            }
            await _safe_send(websocket, send_lock, {"type": "endpoint_score", **endpoint})
            await _safe_send(websocket, send_lock, {"type": "endpoint_candidate", **endpoint})
            _metric(
                "stt_eou_emitted",
                segment_sequence=segment.sequence,
                candidate_count=segment.eou_candidate_count,
                transcript_chars=len(segment.last_partial),
                model_time_ms=model_time_ms,
                nemotron_ms=round(update.nemotron_ms, 3),
                eou_ms=round(update.eou_ms, 3),
            )


def _schedule_stream_drain(
    segment: HybridSegment,
    manager: NemotronEouModelManager,
    websocket: WebSocket,
    send_lock: asyncio.Lock,
) -> None:
    chunk_bytes = manager.feed_chunk_samples * 2
    if len(segment.stream_pending) < chunk_bytes:
        return
    if segment.stream_task is not None and not segment.stream_task.done():
        return

    async def run() -> None:
        try:
            await _drain_stream_audio(segment, manager, websocket, send_lock)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider failures become segment errors
            _metric(
                "stt_streaming_feed_failed",
                segment_sequence=segment.sequence,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            await _safe_send(
                websocket,
                send_lock,
                {
                    "type": "segment_error",
                    "segmentId": segment.segment_id,
                    "sequence": segment.sequence,
                    "retryable": False,
                    "errorCode": "model_service_error",
                    "error": "model_service_error",
                    "request_id": websocket.scope.get("state", {}).get("request_id") or uuid.uuid4().hex,
                },
            )
        finally:
            segment.stream_task = None
            if len(segment.stream_pending) >= chunk_bytes and not segment.finalized:
                _schedule_stream_drain(segment, manager, websocket, send_lock)

    segment.stream_task = asyncio.create_task(run())


async def _settle_stream_for_final(
    segment: HybridSegment,
) -> int:
    """Drop obsolete draft work and wait for at most one in-flight feed.

    The authoritative final is decoded from ``primary_audio`` immediately
    afterward. Processing the rest of ``stream_pending`` first only repeats
    inference over audio already present in that complete buffer and delays the
    correctness-owning decode by several hundred milliseconds under load.
    """

    discarded_samples = len(segment.stream_pending) // 2
    segment.stream_pending.clear()
    existing = segment.stream_task
    if existing is not None:
        await existing
    return discarded_samples


def install_nemotron_eou_websocket(app: Any, manager: NemotronEouModelManager = model_manager) -> None:
    @app.websocket("/ws/transcribe")
    async def websocket_transcribe(websocket: WebSocket) -> None:
        await websocket.accept()
        send_lock = asyncio.Lock()
        connection_id = uuid.uuid4().hex
        active_session_id = f"connection-{connection_id}"
        owned_segments: dict[str, HybridSessionState] = {}
        await _safe_send(
            websocket,
            send_lock,
            HYBRID_NEGOTIATION.ready_payload(
                connection_id=connection_id,
                maxSegmentAudioMs=MAX_SEGMENT_AUDIO_MS,
                language="en-US",
            ),
        )
        try:
            while True:
                data = await receive_client_message(websocket)
                message_type = str(data.get("type", ""))
                if message_type == "hello":
                    active_session_id = str(_field(data, "sessionId", "session_id", active_session_id))[:120]
                    state = _session_state(active_session_id)
                    await _safe_send(
                        websocket,
                        send_lock,
                        {
                            "type": "session_ready",
                            "sessionId": state.session_id,
                            "provider": PROVIDER_NAME,
                            "results": [state.results[key] for key in sorted(state.results)],
                        },
                    )
                    continue
                state = _session_state(str(_field(data, "sessionId", "session_id", active_session_id))[:120])
                if message_type == "audio":
                    segment_id = str(_field(data, "segmentId", "segment_id", ""))[:120]
                    if not segment_id:
                        await _safe_send(websocket, send_lock, {"type": "error", "error": "segment_id_required"})
                        continue
                    sequence = int(data.get("sequence", 0))
                    sample_rate = int(_field(data, "sampleRate", "sample_rate", SAMPLE_RATE))
                    if sample_rate != SAMPLE_RATE:
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error",
                                "segmentId": segment_id,
                                "sequence": sequence,
                                "retryable": False,
                                "errorCode": "sample_rate_mismatch",
                                "error": f"Hybrid STT requires {SAMPLE_RATE} Hz PCM",
                            },
                        )
                        continue
                    capture_start = int(_field(data, "captureStartSample", "capture_start_sample", 0))
                    primary_start = int(_field(data, "primaryStartSample", "primary_start_sample", capture_start))
                    sample_start = int(_field(data, "sampleStart", "sample_start", capture_start))
                    segment = state.segments.get(segment_id)
                    if segment is None:
                        segment = HybridSegment(
                            segment_id=segment_id,
                            sequence=sequence,
                            capture_start_sample=capture_start,
                            primary_start_sample=primary_start,
                            capture_epoch=str(_field(data, "captureEpoch", "capture_epoch", ""))[:160],
                        )
                        state.segments[segment_id] = segment
                        owned_segments[segment_id] = state
                    try:
                        payload = audio_message_pcm(data, validate=True)
                        accepted = segment.append(sample_start, payload)
                    except Exception:  # noqa: BLE001 - malformed frames become protocol errors
                        manager.release(segment_id)
                        state.segments.pop(segment_id, None)
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error",
                                "segmentId": segment_id,
                                "sequence": sequence,
                                "retryable": False,
                                "errorCode": "model_service_error",
                                "error": "model_service_error",
                                "request_id": websocket.scope.get("state", {}).get("request_id", connection_id),
                            },
                        )
                        continue
                    await _safe_send(
                        websocket,
                        send_lock,
                        {
                            "type": "audio_buffered",
                            "segmentId": segment_id,
                            "sequence": sequence,
                            "acceptedThroughSample": accepted,
                        },
                    )
                    _schedule_stream_drain(segment, manager, websocket, send_lock)
                    continue
                if message_type == "preview":
                    segment_id = str(_field(data, "segmentId", "segment_id", ""))[:120]
                    sequence = int(data.get("sequence", 0))
                    request_id = str(
                        _field(data, "previewRequestId", "preview_request_id", "")
                    )[:160]
                    segment = state.segments.get(segment_id)
                    if segment is None or segment.finalized or not request_id:
                        continue
                    snapshot_end_sample = segment.accepted_through_sample
                    snapshot = bytes(segment.primary_audio)
                    preview_started = time.perf_counter()
                    preview_method = getattr(manager, "preview", None)
                    if not callable(preview_method):
                        continue
                    try:
                        text, preview_metrics = await asyncio.to_thread(
                            preview_method,
                            snapshot,
                        )
                    except Exception as exc:  # noqa: BLE001 - preview failure is non-fatal
                        _metric(
                            "stt_authoritative_preview_failed",
                            segment_sequence=sequence,
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
                        continue
                    preview_ms = (time.perf_counter() - preview_started) * 1000.0
                    preview_decode_ms = float(
                        preview_metrics.get("preview_decode_ms", preview_ms)
                    )
                    segment.remember_preview(
                        request_id=request_id,
                        text=text,
                        decode_ms=preview_decode_ms,
                        end_sample=snapshot_end_sample,
                    )
                    await _safe_send(
                        websocket,
                        send_lock,
                        {
                            "type": "preview_result",
                            "provider": PROVIDER_NAME,
                            "segmentId": segment_id,
                            "sequence": sequence,
                            "previewRequestId": request_id,
                            "snapshotEndSample": snapshot_end_sample,
                            "text": text,
                            "providerMetrics": preview_metrics,
                        },
                    )
                    _metric(
                        "stt_authoritative_preview_completed",
                        segment_sequence=sequence,
                        transcript_chars=len(text),
                        snapshot_end_sample=snapshot_end_sample,
                        preview_ms=round(preview_ms, 3),
                        preview_decode_ms=round(preview_decode_ms, 3),
                    )
                    continue
                if message_type in {"final", "finalize"}:
                    segment_id = str(_field(data, "segmentId", "segment_id", ""))[:120]
                    sequence = int(data.get("sequence", 0))
                    cached = state.results.get(sequence)
                    if cached is not None:
                        await _safe_send(websocket, send_lock, cached)
                        continue
                    segment = state.segments.get(segment_id)
                    if segment is None:
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error",
                                "segmentId": segment_id,
                                "sequence": sequence,
                                "retryable": True,
                                "errorCode": "segment_missing",
                                "error": "segment_missing",
                            },
                        )
                        continue
                    if segment.finalized:
                        await _safe_send(
                            websocket,
                            send_lock,
                            {"type": "finalize_queued", "segmentId": segment_id, "sequence": sequence},
                        )
                        continue
                    segment.finalized = True
                    segment.capture_epoch = str(
                        _field(data, "captureEpoch", "capture_epoch", segment.capture_epoch)
                    )[:160]
                    segment.finalize_request_id = str(
                        _field(data, "finalizeRequestId", "finalize_request_id", "")
                    )[:160]
                    segment.end_sample = int(
                        _field(data, "endSample", "end_sample", segment.accepted_through_sample)
                    )
                    final_started = time.perf_counter()
                    stream_pending_samples_at_request = len(segment.stream_pending) // 2
                    stream_task_active_at_request = (
                        segment.stream_task is not None and not segment.stream_task.done()
                    )
                    await _safe_send(
                        websocket,
                        send_lock,
                        {"type": "finalize_queued", "segmentId": segment_id, "sequence": sequence, "queuedSegments": 0},
                    )
                    try:
                        stream_flush_started = time.perf_counter()
                        stream_discarded_samples_at_final = await _settle_stream_for_final(
                            segment,
                        )
                        stream_flush_ms = (time.perf_counter() - stream_flush_started) * 1000.0
                        provider_finalize_started = time.perf_counter()
                        finalize_from_preview = getattr(manager, "finalize_from_preview", None)
                        preview_reused = segment.can_reuse_preview() and callable(
                            finalize_from_preview
                        )
                        if preview_reused:
                            text, provider_metrics = finalize_from_preview(
                                segment_id,
                                segment.preview_text,
                                segment.preview_decode_ms,
                            )
                        else:
                            text, provider_metrics = await asyncio.to_thread(
                                manager.finalize,
                                segment_id,
                                bytes(segment.primary_audio),
                            )
                        provider_finalize_ms = (
                            time.perf_counter() - provider_finalize_started
                        ) * 1000.0
                        finalize_ms = (time.perf_counter() - final_started) * 1000.0
                        provider_metrics = {
                            **provider_metrics,
                            "finalize_ms": round(finalize_ms, 3),
                            "stream_flush_ms": round(stream_flush_ms, 3),
                            "provider_finalize_ms": round(provider_finalize_ms, 3),
                            "stream_pending_samples_at_request": float(
                                stream_pending_samples_at_request
                            ),
                            "stream_pending_ms_at_request": round(
                                stream_pending_samples_at_request * 1000.0 / SAMPLE_RATE,
                                3,
                            ),
                            "stream_task_active_at_request": (
                                1.0 if stream_task_active_at_request else 0.0
                            ),
                            "stream_discarded_samples_at_final": float(
                                stream_discarded_samples_at_final
                            ),
                            "stream_discarded_ms_at_final": round(
                                stream_discarded_samples_at_final * 1000.0 / SAMPLE_RATE,
                                3,
                            ),
                            "authoritative_preview_reused": 1.0 if preview_reused else 0.0,
                            "preview_tail_max_rms": round(segment.preview_tail_max_rms, 6),
                            "eou_triggered": 1.0 if segment.eou_emitted else 0.0,
                            "eou_candidate_count": float(segment.eou_candidate_count),
                        }
                        result = {
                            "type": "result_available",
                            "provider": PROVIDER_NAME,
                            "sessionId": state.session_id,
                            "captureEpoch": segment.capture_epoch,
                            "segmentId": segment.segment_id,
                            "sequence": segment.sequence,
                            "resultId": uuid.uuid4().hex,
                            "finalizeRequestId": segment.finalize_request_id,
                            "startSample": segment.primary_start_sample,
                            "endSample": segment.end_sample or segment.accepted_through_sample,
                            "text": text,
                            "acceptedThroughSample": segment.accepted_through_sample,
                            "providerMetrics": provider_metrics,
                        }
                        state.remember_result(result)
                        await _safe_send(websocket, send_lock, result)
                        _metric(
                            "stt_hybrid_final_completed",
                            segment_sequence=segment.sequence,
                            transcript_chars=len(text),
                            finalize_ms=round(finalize_ms, 3),
                            stream_flush_ms=round(stream_flush_ms, 3),
                            provider_finalize_ms=round(provider_finalize_ms, 3),
                            stream_pending_samples_at_request=stream_pending_samples_at_request,
                            stream_discarded_samples_at_final=stream_discarded_samples_at_final,
                            stream_task_active_at_request=stream_task_active_at_request,
                            eou_triggered=segment.eou_emitted,
                            eou_candidate_count=segment.eou_candidate_count,
                            streaming_final=provider_metrics.get("streaming_final", 0.0),
                            offline_fallback=provider_metrics.get("offline_fallback", 0.0),
                            authoritative_full_decode=provider_metrics.get("authoritative_full_decode", 0.0),
                            full_decode_ms=provider_metrics.get("full_decode_ms", 0.0),
                            streaming_chars=provider_metrics.get("streaming_chars", 0.0),
                            authoritative_chars=provider_metrics.get("authoritative_chars", 0.0),
                            authoritative_changed=provider_metrics.get("authoritative_changed", 0.0),
                            final_right_context=provider_metrics.get("final_right_context", 0.0),
                        )
                    except Exception as exc:  # noqa: BLE001 - provider failures become segment errors
                        _metric(
                            "stt_hybrid_final_failed",
                            segment_sequence=segment.sequence,
                            error_type=type(exc).__name__,
                            error=str(exc),
                        )
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error",
                                "segmentId": segment.segment_id,
                                "sequence": segment.sequence,
                                "retryable": False,
                                "errorCode": "model_service_error",
                                "error": "model_service_error",
                                "request_id": websocket.scope.get("state", {}).get("request_id", connection_id),
                            },
                        )
                    finally:
                        manager.release(segment_id)
                        state.segments.pop(segment_id, None)
                        owned_segments.pop(segment_id, None)
                    continue
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 - top-level websocket fault containment
            _metric("stt_hybrid_websocket_failed", error_type=type(exc).__name__, error=str(exc))
            await _safe_send(websocket, send_lock, {
                "type": "error", "error": "model_service_error",
                "request_id": websocket.scope.get("state", {}).get("request_id", connection_id),
            })
        finally:
            released = 0
            for segment_id, state in tuple(owned_segments.items()):
                segment = state.segments.pop(segment_id, None)
                if segment is not None and segment.stream_task is not None:
                    segment.stream_task.cancel()
                manager.release(segment_id)
                released += 1
            if released:
                _metric(
                    "stt_hybrid_websocket_segments_released",
                    connection_id=connection_id,
                    released_segments=released,
                )
