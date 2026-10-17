"""Persistent acknowledged WebSocket route for segmented Parakeet transcription."""
from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.routing import APIWebSocketRoute

from app.providers.live_stt_contracts import (
    CAP_AUTHORITATIVE_FINAL,
    CAP_BINARY_AUDIO_FRAMES,
    CAP_RESULT_REPLAY,
    CAP_SEGMENTED_AUDIO,
    audio_message_has_audio,
    audio_message_pcm,
    receive_client_message,
    LiveSttNegotiation,
)
from app.providers.stt_live_runtime_support import (
    env_float,
    env_int,
    metric,
    transcribe_path,
    warmed,
)
from app.providers.stt_segment_scheduler import (
    ProviderSegmentScheduler,
    SegmentQueueFullError,
)
from app.providers.stt_streaming_audio import (
    DEFAULT_SAMPLE_RATE,
    pcm16_duration_ms,
    trim_pcm16_edge_silence,
    write_pcm16_wav,
)

SEGMENTED_PROTOCOL = "segmented-v1"
PARAKEET_FRAME_SAMPLES = 320
PARAKEET_NEGOTIATION = LiveSttNegotiation(
    provider="parakeet",
    protocol=SEGMENTED_PROTOCOL,
    sample_rate=DEFAULT_SAMPLE_RATE,
    frame_samples=PARAKEET_FRAME_SAMPLES,
    capabilities=frozenset(
        {
            CAP_SEGMENTED_AUDIO,
            CAP_AUTHORITATIVE_FINAL,
            CAP_RESULT_REPLAY,
            CAP_BINARY_AUDIO_FRAMES,
        }
    ),
)
SESSION_TTL_SECONDS = 600.0
MAX_SESSION_STATES = 64
MAX_PROVIDER_SCHEDULERS = 4
PROVIDER_SCHEDULER_TTL_SECONDS = 60 * 60.0
MAX_OPEN_SEGMENTS = 16
MAX_REPLAY_RESULTS = 64
MAX_SEGMENT_AUDIO_MS = 15_000
MAX_SEGMENT_BYTES = int(DEFAULT_SAMPLE_RATE * 2 * MAX_SEGMENT_AUDIO_MS / 1_000)
_PROVIDER_SCHEDULERS: OrderedDict[
    int,
    tuple[ProviderSegmentScheduler[tuple[str, dict[str, float | int | bool]]], float],
] = OrderedDict()
_SESSION_STATES: dict[str, "SegmentSessionState"] = {}


@dataclass
class SegmentBuffer:
    segment_id: str
    sequence: int
    capture_start_sample: int
    primary_start_sample: int
    sample_rate: int = DEFAULT_SAMPLE_RATE
    accepted_through_sample: int = 0
    audio: bytearray = field(default_factory=bytearray)
    finalize_queued: bool = False
    capture_epoch: str = ""
    finalize_request_id: str = ""
    end_sample: int = 0

    def append(self, sample_start: int, payload: bytes) -> int:
        sample_count = len(payload) // 2
        if len(payload) % 2:
            raise ValueError("audio_frame_partial_sample")
        if self.accepted_through_sample == 0:
            self.accepted_through_sample = self.capture_start_sample
        frame_end = sample_start + sample_count
        if frame_end <= self.accepted_through_sample:
            return self.accepted_through_sample
        if sample_start < self.accepted_through_sample:
            duplicate_samples = self.accepted_through_sample - sample_start
            payload = payload[duplicate_samples * 2 :]
            sample_start = self.accepted_through_sample
        if sample_start != self.accepted_through_sample:
            raise ValueError("audio_frame_gap")
        if len(self.audio) + len(payload) > MAX_SEGMENT_BYTES:
            raise ValueError("segment_audio_limit")
        self.audio.extend(payload)
        self.accepted_through_sample += len(payload) // 2
        return self.accepted_through_sample


@dataclass
class SegmentSessionState:
    session_id: str
    segments: dict[str, SegmentBuffer] = field(default_factory=dict)
    results: dict[int, dict[str, Any]] = field(default_factory=dict)
    inflight: dict[str, asyncio.Future[tuple[str, dict[str, float | int | bool]]]] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)

    def remember_result(self, payload: dict[str, Any]) -> None:
        sequence = int(payload["sequence"])
        self.results[sequence] = payload
        while len(self.results) > MAX_REPLAY_RESULTS:
            self.results.pop(min(self.results))
        self.last_seen = time.monotonic()

    def release_segment(self, segment_id: str) -> None:
        self.inflight.pop(segment_id, None)
        self.segments.pop(segment_id, None)
        self.last_seen = time.monotonic()


def _remove_existing_route(app: Any) -> None:
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (isinstance(route, APIWebSocketRoute) and route.path == "/ws/transcribe")
    ]


def _scheduler_for(legacy: Any) -> ProviderSegmentScheduler[tuple[str, dict[str, float | int | bool]]]:
    key = id(legacy.model) if legacy.model is not None else id(legacy)
    now = time.monotonic()
    loop = asyncio.get_running_loop()
    for scheduler_key, (cached_scheduler, expires_at) in list(_PROVIDER_SCHEDULERS.items()):
        if expires_at <= now and cached_scheduler.is_idle:
            _PROVIDER_SCHEDULERS.pop(scheduler_key, None)
            loop.create_task(cached_scheduler.close())
    cached = _PROVIDER_SCHEDULERS.get(key)
    if cached is not None:
        scheduler = cached[0]
        _PROVIDER_SCHEDULERS.move_to_end(key)
        _PROVIDER_SCHEDULERS[key] = (scheduler, now + PROVIDER_SCHEDULER_TTL_SECONDS)
    else:
        while len(_PROVIDER_SCHEDULERS) >= MAX_PROVIDER_SCHEDULERS:
            idle_key = next(
                (
                    scheduler_key
                    for scheduler_key, (candidate, _expires_at) in _PROVIDER_SCHEDULERS.items()
                    if candidate.is_idle
                ),
                None,
            )
            if idle_key is None:
                raise SegmentQueueFullError("provider_scheduler_capacity")
            evicted = _PROVIDER_SCHEDULERS.pop(idle_key)[0]
            loop.create_task(evicted.close())
        scheduler = ProviderSegmentScheduler(
            max_queued_jobs=env_int("PARAKEET_LIVE_MAX_QUEUED_SEGMENTS", 32),
            max_session_jobs=env_int("PARAKEET_LIVE_MAX_SESSION_SEGMENTS", 8),
        )
        _PROVIDER_SCHEDULERS[key] = (scheduler, now + PROVIDER_SCHEDULER_TTL_SECONDS)
    return scheduler


async def clear_provider_schedulers() -> None:
    """Close and clear provider-local segment schedulers during shutdown/tests."""

    schedulers = [scheduler for scheduler, _expires_at in _PROVIDER_SCHEDULERS.values()]
    _PROVIDER_SCHEDULERS.clear()
    for scheduler in schedulers:
        await scheduler.close()


def _prune_sessions() -> None:
    now = time.monotonic()
    stale = [
        session_id
        for session_id, state in _SESSION_STATES.items()
        if now - state.last_seen > SESSION_TTL_SECONDS and not state.inflight
    ]
    for session_id in stale:
        _SESSION_STATES.pop(session_id, None)
    if len(_SESSION_STATES) <= MAX_SESSION_STATES:
        return
    oldest = sorted(
        (state for state in _SESSION_STATES.values() if not state.inflight),
        key=lambda state: state.last_seen,
    )
    for state in oldest[: max(0, len(_SESSION_STATES) - MAX_SESSION_STATES)]:
        _SESSION_STATES.pop(state.session_id, None)


def clear_stt_session_states() -> None:
    """Invalidate segmented-transcription replay state."""

    _SESSION_STATES.clear()


def _session_state(session_id: str) -> SegmentSessionState:
    _prune_sessions()
    state = _SESSION_STATES.get(session_id)
    if state is None:
        state = SegmentSessionState(session_id=session_id)
        _SESSION_STATES[session_id] = state
    state.last_seen = time.monotonic()
    return state


async def _transcribe_raw_pcm(
    legacy: Any,
    combined_audio: bytes,
    session_dir: Path,
) -> tuple[str, dict[str, float | int | bool]]:
    prepare_started_at = time.perf_counter()
    trimmed_audio, trim = trim_pcm16_edge_silence(
        combined_audio,
        silence_threshold_dbfs=env_float("PARAKEET_LIVE_TRIM_DBFS", -40.0),
        edge_padding_ms=env_int("PARAKEET_LIVE_EDGE_PADDING_MS", 100),
    )
    audio_path = write_pcm16_wav(session_dir / "audio.wav", trimmed_audio)
    prepare_ms = (time.perf_counter() - prepare_started_at) * 1000.0
    text, inference_ms = await asyncio.to_thread(transcribe_path, legacy.model, audio_path)
    return text, {
        "prepare_ms": round(prepare_ms, 3),
        "inference_ms": round(inference_ms, 3),
        "original_audio_ms": round(pcm16_duration_ms(combined_audio), 3),
        "transcribed_audio_ms": round(pcm16_duration_ms(trimmed_audio), 3),
        "removed_audio_ms": round(trim.removed_samples / DEFAULT_SAMPLE_RATE * 1000.0, 3),
        "speech_detected": trim.speech_detected,
        "original_bytes": len(combined_audio),
        "transcribed_bytes": len(trimmed_audio),
    }


async def _transcribe_compatible_audio(
    legacy: Any,
    combined_audio: bytes,
    session_dir: Path,
) -> tuple[str, dict[str, float | int | bool]]:
    prepare_started_at = time.perf_counter()
    if len(combined_audio) > 44 and combined_audio[:4] == b"RIFF":
        audio_path = session_dir / "audio.wav"
        audio_path.write_bytes(combined_audio)
    else:
        input_path = session_dir / "audio.webm"
        input_path.write_bytes(combined_audio)
        audio = legacy.AudioSegment.from_file(input_path)
        audio = audio.set_frame_rate(DEFAULT_SAMPLE_RATE).set_channels(1)
        audio_path = session_dir / "audio.wav"
        audio.export(audio_path, format="wav")
    prepare_ms = (time.perf_counter() - prepare_started_at) * 1000.0
    text, inference_ms = await asyncio.to_thread(transcribe_path, legacy.model, audio_path)
    return text, {
        "prepare_ms": round(prepare_ms, 3),
        "inference_ms": round(inference_ms, 3),
        "original_audio_ms": 0.0,
        "transcribed_audio_ms": 0.0,
        "removed_audio_ms": 0.0,
        "speech_detected": True,
        "original_bytes": len(combined_audio),
        "transcribed_bytes": audio_path.stat().st_size,
    }


async def _run_transcription(
    legacy: Any,
    combined_audio: bytes,
) -> tuple[str, dict[str, float | int | bool]]:
    session_dir = Path(tempfile.gettempdir()) / f"ws_transcription_{uuid.uuid4()}"
    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        is_wav = len(combined_audio) > 44 and combined_audio[:4] == b"RIFF"
        is_webm = len(combined_audio) > 4 and combined_audio[:4] in {b"\x1a\x45\xdf\xa3", b"ftyp"}
        if not is_wav and not is_webm:
            return await _transcribe_raw_pcm(legacy, combined_audio, session_dir)
        return await _transcribe_compatible_audio(legacy, combined_audio, session_dir)
    finally:
        legacy.cleanup_session_dir(session_dir)


def _field(data: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    return data.get(camel, data.get(snake, default))


async def _safe_send(websocket: WebSocket, lock: asyncio.Lock, payload: dict[str, Any]) -> bool:
    try:
        async with lock:
            await websocket.send_json(payload)
        return True
    except Exception:
        return False


def install_live_stt_websocket(legacy: Any) -> None:
    app = legacy.app
    _remove_existing_route(app)

    @app.websocket("/ws/transcribe")
    async def websocket_transcribe(websocket: WebSocket) -> None:
        await websocket.accept()
        send_lock = asyncio.Lock()
        connection_id = uuid.uuid4().hex
        default_session_id = f"connection-{connection_id}"
        active_session_id = default_session_id
        legacy_segment_sequence = 0
        await _safe_send(
            websocket,
            send_lock,
            PARAKEET_NEGOTIATION.ready_payload(
                connection_id=connection_id,
                maxSegmentAudioMs=MAX_SEGMENT_AUDIO_MS,
            ),
        )

        async def deliver_result(
            state: SegmentSessionState,
            segment: SegmentBuffer,
            future: asyncio.Future[tuple[str, dict[str, float | int | bool]]],
            *,
            legacy_mode: bool,
            final_started_at: float,
        ) -> None:
            try:
                text, metrics = await future
                payload = {
                    "type": "result_available",
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
                }
                state.remember_result(payload)
                if legacy_mode:
                    await _safe_send(websocket, send_lock, {**payload, "type": "done"})
                else:
                    await _safe_send(websocket, send_lock, payload)
                metric(
                    "stt_websocket_final_completed",
                    total_ms=round((time.perf_counter() - final_started_at) * 1000.0, 3),
                    warmed=warmed(),
                    device=str(legacy.device),
                    transcript_chars=len(text),
                    segment_sequence=segment.sequence,
                    queued_segments=_scheduler_for(legacy).queued_jobs,
                    open_segments=max(0, len(state.segments) - 1),
                    replay_results=len(state.results),
                    **metrics,
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                metric(
                    "stt_websocket_final_failed",
                    total_ms=round((time.perf_counter() - final_started_at) * 1000.0, 3),
                    error_type=type(exc).__name__,
                    segment_sequence=segment.sequence,
                )
                await _safe_send(
                    websocket,
                    send_lock,
                    {
                        "type": "segment_error" if not legacy_mode else "error",
                        "segmentId": segment.segment_id,
                        "sequence": segment.sequence,
                        "retryable": False,
                        "errorCode": type(exc).__name__,
                        "error": str(exc),
                    },
                )
            finally:
                state.release_segment(segment.segment_id)

        async def finalize_segment(
            state: SegmentSessionState,
            segment_id: str,
            sequence: int,
            *,
            legacy_mode: bool,
        ) -> None:
            cached = state.results.get(sequence)
            if cached is not None:
                await _safe_send(
                    websocket,
                    send_lock,
                    cached if not legacy_mode else {**cached, "type": "done"},
                )
                return
            segment = state.segments.get(segment_id)
            if segment is None:
                await _safe_send(
                    websocket,
                    send_lock,
                    {
                        "type": "segment_error" if not legacy_mode else "error",
                        "segmentId": segment_id,
                        "sequence": sequence,
                        "retryable": True,
                        "errorCode": "segment_missing",
                        "error": "segment_missing",
                    },
                )
                return
            existing = state.inflight.get(segment_id)
            if existing is not None:
                await _safe_send(
                    websocket,
                    send_lock,
                    {"type": "finalize_queued", "segmentId": segment_id, "sequence": sequence},
                )
                return
            if not segment.audio:
                payload = {
                    "type": "result_available",
                    "sessionId": state.session_id,
                    "captureEpoch": segment.capture_epoch,
                    "segmentId": segment_id,
                    "sequence": sequence,
                    "resultId": uuid.uuid4().hex,
                    "finalizeRequestId": segment.finalize_request_id,
                    "startSample": segment.primary_start_sample,
                    "endSample": segment.end_sample or segment.accepted_through_sample,
                    "text": "",
                    "acceptedThroughSample": segment.accepted_through_sample,
                }
                state.remember_result(payload)
                state.release_segment(segment_id)
                await _safe_send(
                    websocket,
                    send_lock,
                    payload if not legacy_mode else {**payload, "type": "done"},
                )
                return
            if legacy.model is None:
                state.release_segment(segment_id)
                await _safe_send(
                    websocket,
                    send_lock,
                    {"type": "error", "error": "ASR model not loaded"},
                )
                return
            final_started_at = time.perf_counter()
            try:
                future = await _scheduler_for(legacy).submit(
                    session_id=state.session_id,
                    segment_id=segment.segment_id,
                    sequence=segment.sequence,
                    run=lambda audio=bytes(segment.audio): _run_transcription(legacy, audio),
                )
            except SegmentQueueFullError as exc:
                await _safe_send(
                    websocket,
                    send_lock,
                    {
                        "type": "segment_error" if not legacy_mode else "error",
                        "segmentId": segment.segment_id,
                        "sequence": segment.sequence,
                        "retryable": True,
                        "errorCode": str(exc),
                        "error": str(exc),
                    },
                )
                return
            segment.finalize_queued = True
            state.inflight[segment.segment_id] = future
            await _safe_send(
                websocket,
                send_lock,
                {
                    "type": "finalize_queued",
                    "segmentId": segment.segment_id,
                    "sequence": segment.sequence,
                    "queuedSegments": _scheduler_for(legacy).queued_jobs,
                },
            )
            asyncio.create_task(
                deliver_result(
                    state,
                    segment,
                    future,
                    legacy_mode=legacy_mode,
                    final_started_at=final_started_at,
                )
            )

        try:
            while True:
                data = await receive_client_message(websocket)
                message_type = str(data.get("type", ""))
                if message_type == "hello":
                    active_session_id = str(_field(data, "sessionId", "session_id", default_session_id))[:120]
                    state = _session_state(active_session_id)
                    await _safe_send(
                        websocket,
                        send_lock,
                        {
                            "type": "session_ready",
                            "sessionId": active_session_id,
                            "provider": "parakeet",
                            "results": [state.results[key] for key in sorted(state.results)],
                        },
                    )
                    continue
                state = _session_state(str(_field(data, "sessionId", "session_id", active_session_id))[:120])
                if message_type == "audio":
                    if not audio_message_has_audio(data):
                        continue
                    segmented = _field(data, "segmentId", "segment_id") is not None
                    sample_rate = int(_field(data, "sampleRate", "sample_rate", DEFAULT_SAMPLE_RATE))
                    if sample_rate != DEFAULT_SAMPLE_RATE:
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error" if segmented else "error",
                                "segmentId": _field(data, "segmentId", "segment_id"),
                                "sequence": int(data.get("sequence", 0)),
                                "retryable": False,
                                "errorCode": "sample_rate_mismatch",
                                "error": f"Parakeet requires {DEFAULT_SAMPLE_RATE} Hz PCM",
                            },
                        )
                        continue
                    if segmented:
                        segment_id = str(_field(data, "segmentId", "segment_id"))[:120]
                        sequence = int(data.get("sequence", 0))
                        capture_start = int(_field(data, "captureStartSample", "capture_start_sample", 0))
                        primary_start = int(_field(data, "primaryStartSample", "primary_start_sample", capture_start))
                        sample_start = int(_field(data, "sampleStart", "sample_start", capture_start))
                    else:
                        segment_id = f"legacy-{legacy_segment_sequence}"
                        sequence = legacy_segment_sequence
                        capture_start = 0
                        primary_start = 0
                        sample_start = state.segments.get(
                            segment_id,
                            SegmentBuffer(segment_id, sequence, 0, 0),
                        ).accepted_through_sample
                    if segment_id not in state.segments:
                        if len(state.segments) >= MAX_OPEN_SEGMENTS:
                            await _safe_send(
                                websocket,
                                send_lock,
                                {
                                    "type": "segment_error" if segmented else "error",
                                    "segmentId": segment_id,
                                    "sequence": sequence,
                                    "retryable": True,
                                    "errorCode": "open_segment_limit",
                                    "error": "open_segment_limit",
                                },
                            )
                            continue
                        state.segments[segment_id] = SegmentBuffer(
                            segment_id=segment_id,
                            sequence=sequence,
                            capture_start_sample=capture_start,
                            primary_start_sample=primary_start,
                            sample_rate=sample_rate,
                            capture_epoch=str(_field(data, "captureEpoch", "capture_epoch", ""))[:160],
                        )
                    segment = state.segments[segment_id]
                    try:
                        accepted = segment.append(sample_start, audio_message_pcm(data))
                    except Exception as exc:
                        state.release_segment(segment_id)
                        await _safe_send(
                            websocket,
                            send_lock,
                            {
                                "type": "segment_error" if segmented else "error",
                                "segmentId": segment_id,
                                "sequence": sequence,
                                "retryable": False,
                                "errorCode": str(exc),
                                "error": str(exc),
                            },
                        )
                        continue
                    if segmented:
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
                    continue
                if message_type in {"final", "finalize"}:
                    segmented = message_type == "finalize" or _field(data, "segmentId", "segment_id") is not None
                    if segmented:
                        segment_id = str(_field(data, "segmentId", "segment_id", ""))[:120]
                        sequence = int(data.get("sequence", 0))
                    else:
                        segment_id = f"legacy-{legacy_segment_sequence}"
                        sequence = legacy_segment_sequence
                        legacy_segment_sequence += 1
                    segment = state.segments.get(segment_id)
                    if segment is not None:
                        segment.capture_epoch = str(
                            _field(data, "captureEpoch", "capture_epoch", segment.capture_epoch)
                        )[:160]
                        segment.finalize_request_id = str(
                            _field(data, "finalizeRequestId", "finalize_request_id", "")
                        )[:160]
                        segment.end_sample = int(
                            _field(data, "endSample", "end_sample", segment.accepted_through_sample)
                        )
                    await finalize_segment(state, segment_id, sequence, legacy_mode=not segmented)
                    continue
        except WebSocketDisconnect:
            return
        except Exception as exc:
            metric("stt_websocket_failed", error_type=type(exc).__name__)
            await _safe_send(websocket, send_lock, {"type": "error", "error": str(exc)})
