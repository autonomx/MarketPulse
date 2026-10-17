"""Session-scoped persistent PCM websocket transport for live voice."""
from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.conversation.performance_contract import apply_performance_plan_to_provider
from app.platform.live_voice.capacity import live_call_capacity
from app.conversation.text import remove_emojis
from app.platform.voice.contracts import TTSProviderResolver
from app.platform.live_voice.speech.pcm_diagnostics import (
    measured_pcm_block_streamer,
    measured_pcm_converter,
)
from app.platform.live_voice.speech.startup_frame_policy import (
    TTS_LIVE_CALL_STARTUP_FRAME_SAMPLES,
    stream_live_call_pcm16_blocks,
)

from app.platform.live_voice.speech.speculative_tts import resolve_live_call_tts_provider
from app.platform.live_voice.speech.tts_lane import live_voice_tts_lane_is_warm
from app.observability.tts_stream_diagnostics import (
    begin_stream,
    diagnostics_log_path,
    end_stream,
    normalize_stream_id,
    stream_log,
)
from app.conversation.tts_stream_contract import (
    DEFAULT_SAMPLE_RATE,
    TtsStreamRequest,
    audio_chunk_to_pcm16_bytes as _audio_chunk_to_pcm16_bytes,
    stream_pcm16_blocks as _stream_pcm16_blocks,
)

_ROUTE_SENTINEL = "_omnix_tts_live_call_ws_registered"
TTS_LIVE_CALL_WEBSOCKET_PATH = "/api/tts/live-call/websocket"
TTS_PCM_FRAME_SAMPLES = TTS_LIVE_CALL_STARTUP_FRAME_SAMPLES
FRAME_HANDOFF_TIMEOUT_SECONDS = 0.250
SPECULATIVE_STARTUP_BURST_FRAMES = 2
_measured_audio_converter = measured_pcm_converter(_audio_chunk_to_pcm16_bytes)
_measured_pcm_block_streamer = measured_pcm_block_streamer(_stream_pcm16_blocks)

@dataclass(frozen=True)
class FrameMessage:
    message_type: str
    pcm_bytes: bytes | None
    sample_rate: int
    metadata: dict[str, Any]
    queued_at: float | None = None
    sent_ack: threading.Event | None = None


@dataclass
class ActiveOutput:
    output_id: str
    generation_epoch: int
    segment_id: str
    phrase_index: int | None
    stop_event: threading.Event
    sent_frames: int = 0


@dataclass
class ConnectionState:
    stop_event: threading.Event
    requests: asyncio.Queue[dict[str, Any] | None]
    active_frames: asyncio.Queue[FrameMessage] | None = None
    active_output: ActiveOutput | None = None
    cancelled_outputs: set[tuple[str, int]] = field(default_factory=set)


def _connection_details(websocket: WebSocket) -> dict[str, Any]:
    client = websocket.client
    return {
        "client_host": client.host if client else None,
        "client_port": client.port if client else None,
        "origin": websocket.headers.get("origin"),
        "user_agent": websocket.headers.get("user-agent"),
        "websocket_path": websocket.url.path,
        "persistent_connection": True,
        "websocket_scope": "live_session",
    }


def _phrase_index(payload: dict[str, Any]) -> int | None:
    value = payload.get("phrase_index")
    return value if isinstance(value, int) and value >= 0 else None


def _output_identity(payload: dict[str, Any]) -> tuple[str, int, str]:
    output_id = str(payload.get("output_id") or payload.get("request_id") or "legacy-output")[:160]
    try:
        generation_epoch = max(0, int(payload.get("generation_epoch") or 0))
    except (TypeError, ValueError):
        generation_epoch = 0
    segment_id = str(payload.get("segment_id") or f"{output_id}:segment")[:160]
    return output_id, generation_epoch, segment_id


_FIRST_RAW_TIMING_FIELDS = {
    "provider_setup_ms": "provider_setup_ms",
    "provider_entry_to_first_audio_ms": "provider_entry_to_first_audio_ms",
    "prepare_total_ms": "provider_prepare_ms",
    "input_tokenization_ms": "provider_input_tokenization_ms",
    "voice_prompt_prepare_ms": "provider_voice_prompt_ms",
    "talker_input_construction_ms": "provider_talker_input_ms",
    "graph_warmup_ms": "provider_graph_warmup_ms",
    "prefill_ms": "provider_talker_prefill_ms",
    "decode_ms": "provider_first_codec_ms",
    "model_to_first_codec_ms": "provider_model_to_first_codec_ms",
    "speech_tokenizer_decode_call_ms": "provider_speech_decode_call_ms",
    "tensor_to_numpy_ms": "provider_tensor_to_numpy_ms",
    "speech_tokenizer_decode_ms": "provider_speech_decode_ms",
    "codec_to_audio_ms": "provider_codec_to_audio_ms",
    "model_to_first_audio_ms": "provider_model_to_first_audio_ms",
}


def _first_raw_timing_details(timing: Any) -> dict[str, Any]:
    if not isinstance(timing, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in timing.items():
        if isinstance(value, bool):
            sanitized[str(key)] = value
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            sanitized[str(key)] = round(float(value), 3)
        elif isinstance(value, str):
            sanitized[str(key)] = value[:120]
    details: dict[str, Any] = {"provider_timing": sanitized}
    for source, target in _FIRST_RAW_TIMING_FIELDS.items():
        value = sanitized.get(source)
        if isinstance(value, (int, float)):
            details[target] = value
    if isinstance(sanitized.get("voice_prompt_cache_hit"), bool):
        details["provider_voice_prompt_cache_hit"] = sanitized["voice_prompt_cache_hit"]
    if isinstance(sanitized.get("prompt_tokens"), (int, float)):
        details["provider_prompt_tokens"] = int(sanitized["prompt_tokens"])
    return details


def _is_speculative_cache_frame(timing: Any) -> bool:
    return isinstance(timing, dict) and timing.get("speculative_tts_cache") is True


def _ownership_fields(
    payload: dict[str, Any],
    output_id: str,
    generation_epoch: int,
    segment_id: str,
    output_order: Any = None,
) -> dict[str, Any]:
    """Expose ownership fields only to item-aware clients, preserving legacy controls."""
    if not str(payload.get("output_id") or "").strip():
        return {}
    return {
        "output_id": output_id,
        "generation_epoch": generation_epoch,
        "segment_id": segment_id,
        "output_order": output_order,
    }


def _stop_connection(state: ConnectionState, reason: str) -> None:
    if state.stop_event.is_set():
        return
    state.stop_event.set()
    if state.active_output is not None:
        state.active_output.stop_event.set()
    if state.active_frames is not None:
        state.active_frames.put_nowait(
            FrameMessage(
                "client_disconnect",
                None,
                DEFAULT_SAMPLE_RATE,
                {"reason": reason},
            )
        )
    state.requests.put_nowait(None)


async def _receive_messages(websocket: WebSocket, state: ConnectionState) -> None:
    """Receive synthesis, cancellation, diagnostics, and close messages concurrently."""
    try:
        while not state.stop_event.is_set():
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError as exc:
                await websocket.send_json({"type": "error", "message": f"Invalid JSON: {exc}"})
                continue
            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "message": "TTS message must be an object."})
                continue
            message_type = payload.get("type")
            if message_type == "close":
                _stop_connection(state, str(payload.get("reason") or "client-close"))
                return
            if message_type == "diagnostic":
                stream_id = normalize_stream_id(payload.get("stream_id"))
                event = str(payload.get("event") or "diagnostic")[:120]
                details = payload.get("details")
                stream_log(
                    stream_id,
                    "client",
                    event,
                    persistent_connection=True,
                    **(details if isinstance(details, dict) else {}),
                )
                continue
            if message_type == "cancel":
                output_id, generation_epoch, segment_id = _output_identity(payload)
                key = (output_id, generation_epoch)
                state.cancelled_outputs.add(key)
                active = state.active_output
                generated_through_frame = -1
                if active and (active.output_id, active.generation_epoch) == key:
                    generated_through_frame = active.sent_frames - 1
                    active.stop_event.set()
                    if state.active_frames is not None:
                        state.active_frames.put_nowait(
                            FrameMessage(
                                "cancelled",
                                None,
                                DEFAULT_SAMPLE_RATE,
                                {
                                    "output_id": output_id,
                                    "generation_epoch": generation_epoch,
                                    "segment_id": active.segment_id,
                                    "reason": str(payload.get("reason") or "cancelled"),
                                },
                            )
                        )
                await websocket.send_json(
                    {
                        "type": "cancel_accepted",
                        "output_id": output_id,
                        "generation_epoch": generation_epoch,
                        "segment_id": segment_id,
                        "generated_through_frame": generated_through_frame,
                    }
                )
                continue
            if message_type == "synthesize":
                await state.requests.put(payload)
                continue
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "unsupported_message_type",
                    "received_type": message_type,
                }
            )
    except WebSocketDisconnect:
        _stop_connection(state, "websocket-disconnected")
    except Exception as exc:  # pragma: no cover
        _stop_connection(state, f"receiver-failed:{type(exc).__name__}")


async def _resolve_phrase_tts_provider(provider_resolver: TTSProviderResolver) -> Any:
    """Warm lookups stay on the loop; a cold one runs on a worker thread (WP-7.1).

    A cold lookup starts a provider or loads settings, which can take seconds
    and would stall every other call's audio frames.
    """
    is_warm = getattr(provider_resolver, "is_warm", None)
    if callable(is_warm) and is_warm() and live_voice_tts_lane_is_warm():
        return resolve_live_call_tts_provider(provider_resolver.get())
    return await asyncio.to_thread(
        lambda: resolve_live_call_tts_provider(provider_resolver.get())
    )


async def _stream_phrase(
    websocket: WebSocket,
    payload: dict[str, Any],
    state: ConnectionState,
    *,
    provider_resolver: TTSProviderResolver,
) -> None:
    """Generate and send one output item without closing the session websocket."""
    phrase_index = _phrase_index(payload)
    stream_id = normalize_stream_id(payload.get("diagnostics_stream_id"))
    output_id, generation_epoch, segment_id = _output_identity(payload)
    output_order = payload.get("output_order")
    ownership_fields = _ownership_fields(
        payload,
        output_id,
        generation_epoch,
        segment_id,
        output_order,
    )
    route_started_at = time.perf_counter()
    registered = False
    producer: threading.Thread | None = None
    phrase_stop = threading.Event()
    sent_frames = 0
    sent_samples = 0
    key = (output_id, generation_epoch)

    if key in state.cancelled_outputs:
        await websocket.send_json(
            {
                "type": "cancelled",
                "stream_id": stream_id,
                **ownership_fields,
                "generated_through_frame": -1,
            }
        )
        return

    active_output = ActiveOutput(
        output_id=output_id,
        generation_epoch=generation_epoch,
        segment_id=segment_id,
        phrase_index=phrase_index,
        stop_event=phrase_stop,
    )
    state.active_output = active_output

    try:
        try:
            request = TtsStreamRequest.model_validate(payload)
        except ValidationError as exc:
            stream_log(stream_id, "server", "request_invalid", error=repr(exc))
            await websocket.send_json(
                {
                    "type": "error",
                    "stream_id": stream_id,
                    **ownership_fields,
                    "phrase_index": phrase_index,
                    "message": f"Invalid TTS request: {exc}",
                }
            )
            return

        begin_stream(stream_id, **_connection_details(websocket))
        registered = True
        raw_text = request.text or ""
        text = remove_emojis(raw_text).strip()
        stream_log(
            stream_id,
            "server",
            "request_received",
            text=raw_text,
            sanitized_text=text,
            text_length=len(raw_text),
            sanitized_text_length=len(text),
            phrase_index=phrase_index,
            request_id=request.request_id,
            output_id=output_id,
            generation_epoch=generation_epoch,
            segment_id=segment_id,
            output_order=output_order,
            speaker=request.speaker,
            language=request.language,
            chunk_size=request.chunk_size,
            temperature=request.temperature,
            top_k=request.top_k,
            top_p=request.top_p,
            repetition_penalty=request.repetition_penalty,
            append_silence=request.append_silence,
            max_new_tokens=request.max_new_tokens,
            parity_mode=request.parity_mode,
            performance_schema_version=(
                request.delivery_plan.schema_version if request.delivery_plan else None
            ),
            log_path=diagnostics_log_path(),
            persistent_connection=True,
        )
        if not text:
            await websocket.send_json(
                {
                    "type": "error",
                    "stream_id": stream_id,
                    **ownership_fields,
                    "phrase_index": phrase_index,
                    "message": "text_required",
                }
            )
            return

        provider = await _resolve_phrase_tts_provider(provider_resolver)
        if provider is None or not hasattr(provider, "generate_audio_stream"):
            message = "tts_provider_unavailable" if provider is None else "tts_provider_streaming_unavailable"
            stream_log(stream_id, "server", "request_rejected", reason=message)
            await websocket.send_json(
                {
                    "type": "error",
                    "stream_id": stream_id,
                    **ownership_fields,
                    "phrase_index": phrase_index,
                    "message": message,
                }
            )
            return

        performance_application = apply_performance_plan_to_provider(provider, request.delivery_plan)
        capability_payload = performance_application.capabilities.model_dump(mode="json")
        applied_controls = list(performance_application.applied_controls)
        ignored_controls = list(performance_application.ignored_controls)
        provider_resolved_at = time.perf_counter()
        stream_log(
            stream_id,
            "server",
            "provider_resolved",
            provider_class=f"{type(provider).__module__}.{type(provider).__qualname__}",
            provider_name=getattr(provider, "provider_name", None),
            provider_object_id=id(provider),
            live_execution_lane=getattr(provider, "execution_lane", "shared"),
            provider_capabilities=capability_payload,
            performance_controls_applied=applied_controls,
            performance_controls_ignored=ignored_controls,
            request_to_provider_ms=round((provider_resolved_at - route_started_at) * 1000, 3),
            output_id=output_id,
            generation_epoch=generation_epoch,
        )
        loop = asyncio.get_running_loop()
        frames: asyncio.Queue[FrameMessage] = asyncio.Queue()
        state.active_frames = frames

        def stopped() -> bool:
            return state.stop_event.is_set() or phrase_stop.is_set() or key in state.cancelled_outputs

        def emit(message: FrameMessage) -> None:
            if not stopped() or message.message_type in {"cancelled", "client_disconnect"}:
                loop.call_soon_threadsafe(frames.put_nowait, message)

        def produce() -> None:
            producer_started_at = time.perf_counter()
            last_raw_chunk_at = producer_started_at
            raw_chunk_count = 0
            queued_frame_count = 0
            produced_samples = 0
            frame_handoff_count = 0
            frame_handoff_timeout_count = 0
            max_frame_handoff_wait_ms = 0.0
            speculative_startup_burst_frames = 0
            stream_log(
                stream_id,
                "provider",
                "producer_thread_started",
                phrase_index=phrase_index,
                output_id=output_id,
                generation_epoch=generation_epoch,
                request_to_producer_ms=round((producer_started_at - route_started_at) * 1000, 3),
            )
            try:
                stream_kwargs: dict[str, Any] = {
                    "chunk_size": request.chunk_size,
                    "temperature": request.temperature,
                    "top_k": request.top_k,
                    "top_p": request.top_p,
                    "repetition_penalty": request.repetition_penalty,
                    "append_silence": request.append_silence,
                    "non_streaming_mode": False,
                }
                if request.max_new_tokens is not None:
                    stream_kwargs["max_new_tokens"] = request.max_new_tokens
                if request.parity_mode is not None:
                    stream_kwargs["parity_mode"] = request.parity_mode
                stream_kwargs.update(performance_application.provider_kwargs)

                def raw_chunks():
                    nonlocal last_raw_chunk_at, produced_samples, raw_chunk_count
                    provider_stream = provider.generate_audio_stream(
                        text=text,
                        speaker=request.speaker,
                        language=request.language or "en",
                        **stream_kwargs,
                    )
                    for audio_chunk, sample_rate, timing in provider_stream:
                        if stopped():
                            return
                        raw_chunk_ready_at = time.perf_counter()
                        raw_chunk_interarrival_ms = (
                            raw_chunk_ready_at - last_raw_chunk_at
                        ) * 1000
                        pcm_bytes = _measured_audio_converter(audio_chunk)
                        resolved_rate = int(sample_rate or DEFAULT_SAMPLE_RATE)
                        sample_count = len(pcm_bytes) // 2
                        raw_chunk_count += 1
                        produced_samples += sample_count
                        last_raw_chunk_at = raw_chunk_ready_at
                        stream_log(
                            stream_id,
                            "provider",
                            "raw_chunk_ready",
                            raw_chunk_index=raw_chunk_count - 1,
                            interarrival_ms=round(raw_chunk_interarrival_ms, 3),
                            raw_chunk_samples=sample_count,
                            raw_chunk_audio_ms=round(
                                sample_count * 1000 / max(1, resolved_rate),
                                3,
                            ),
                            producer_elapsed_ms=round(
                                (raw_chunk_ready_at - producer_started_at) * 1000,
                                3,
                            ),
                            provider_timing=timing if isinstance(timing, dict) else {},
                            output_id=output_id,
                            generation_epoch=generation_epoch,
                        )
                        if raw_chunk_count == 1:
                            first_raw_timing = _first_raw_timing_details(timing)
                            stream_log(
                                stream_id,
                                "provider",
                                "first_raw_chunk_ready",
                                provider_to_first_raw_ms=round(
                                    (raw_chunk_ready_at - provider_resolved_at) * 1000,
                                    3,
                                ),
                                route_to_first_raw_ms=round(
                                    (raw_chunk_ready_at - route_started_at) * 1000,
                                    3,
                                ),
                                raw_chunk_samples=sample_count,
                                sample_rate=resolved_rate,
                                output_id=output_id,
                                generation_epoch=generation_epoch,
                                **first_raw_timing,
                            )
                        yield pcm_bytes, resolved_rate, timing

                def handoff_frame(
                    pcm_bytes: bytes,
                    sample_rate: int,
                    timing: Any,
                    *,
                    bundled_frame_count: int = 1,
                ) -> bool:
                    nonlocal queued_frame_count
                    nonlocal frame_handoff_count
                    nonlocal frame_handoff_timeout_count
                    nonlocal max_frame_handoff_wait_ms
                    if stopped():
                        return False
                    is_first_frame = queued_frame_count == 0
                    # Synchronize every websocket handoff with the asyncio
                    # sender. Qwen's CUDA decode loop can retain the GIL long
                    # enough for faster-than-real-time chunks to accumulate in
                    # the callback queue. Waiting releases the GIL before the
                    # next decoder step begins.
                    frame_sent_ack = threading.Event()
                    queued_at = time.perf_counter()
                    metadata = {
                        "frame_index": queued_frame_count,
                        "bundled_frame_count": bundled_frame_count,
                        "provider_timing": timing,
                        "output_id": output_id,
                        "generation_epoch": generation_epoch,
                        "segment_id": segment_id,
                    }
                    queued_frame_count += 1
                    emit(
                        FrameMessage(
                            "chunk",
                            pcm_bytes,
                            sample_rate,
                            metadata,
                            queued_at=queued_at,
                            sent_ack=frame_sent_ack,
                        )
                    )
                    wait_started_at = time.perf_counter()
                    frame_sent_ack.wait(timeout=FRAME_HANDOFF_TIMEOUT_SECONDS)
                    handoff_wait_ms = (time.perf_counter() - wait_started_at) * 1000
                    frame_handoff_count += 1
                    max_frame_handoff_wait_ms = max(
                        max_frame_handoff_wait_ms,
                        handoff_wait_ms,
                    )
                    if not frame_sent_ack.is_set():
                        frame_handoff_timeout_count += 1
                    if is_first_frame:
                        stream_log(
                            stream_id,
                            "provider",
                            "first_frame_handoff_completed",
                            acknowledged=frame_sent_ack.is_set(),
                            wait_ms=round(handoff_wait_ms, 3),
                            bundled_frame_count=bundled_frame_count,
                            output_id=output_id,
                            generation_epoch=generation_epoch,
                        )
                    elif not frame_sent_ack.is_set():
                        stream_log(
                            stream_id,
                            "provider",
                            "frame_handoff_timeout",
                            frame_index=queued_frame_count - 1,
                            wait_ms=round(handoff_wait_ms, 3),
                            output_id=output_id,
                            generation_epoch=generation_epoch,
                        )
                    return not stopped()

                transport_frames = stream_live_call_pcm16_blocks(
                    raw_chunks(),
                    streamer=_measured_pcm_block_streamer,
                    block_samples=TTS_PCM_FRAME_SAMPLES,
                )
                deferred_frame: tuple[bytes, int, Any] | None = None
                first_frame = next(transport_frames, None)
                if first_frame is not None:
                    startup_frames = [first_frame]
                    if _is_speculative_cache_frame(first_frame[2]):
                        # A promoted cache already owns a two-frame runway.
                        # Deliver it in one browser message so a busy main
                        # thread cannot start playback after frame one and then
                        # delay frame two long enough to underrun.
                        while len(startup_frames) < SPECULATIVE_STARTUP_BURST_FRAMES:
                            next_frame = next(transport_frames, None)
                            if next_frame is None:
                                break
                            if next_frame[1] != first_frame[1]:
                                deferred_frame = next_frame
                                break
                            startup_frames.append(next_frame)
                        speculative_startup_burst_frames = len(startup_frames)
                        startup_pcm = b"".join(frame[0] for frame in startup_frames)
                        if not handoff_frame(
                            startup_pcm,
                            first_frame[1],
                            first_frame[2],
                            bundled_frame_count=len(startup_frames),
                        ):
                            return
                    else:
                        if not handoff_frame(*first_frame):
                            return

                if deferred_frame is not None and not handoff_frame(*deferred_frame):
                    return
                for pcm_bytes, sample_rate, timing in transport_frames:
                    if not handoff_frame(pcm_bytes, sample_rate, timing):
                        return
                if stopped():
                    return
                emit(FrameMessage("done", None, DEFAULT_SAMPLE_RATE, {}))
            except Exception as exc:  # pragma: no cover
                retry_after = getattr(exc, "retry_after", None)
                emit(
                    FrameMessage(
                        "error",
                        None,
                        DEFAULT_SAMPLE_RATE,
                        {
                            "message": (
                                "Speech capacity is busy; this phrase was not generated."
                                if retry_after is not None
                                else str(exc) or "TTS stream failed."
                            ),
                            "retry_after": retry_after,
                            "capacity_saturated": retry_after is not None,
                        },
                    )
                )
            finally:
                stream_log(
                    stream_id,
                    "provider",
                    "producer_thread_finished",
                    elapsed_ms=round((time.perf_counter() - producer_started_at) * 1000, 3),
                    raw_chunk_count=raw_chunk_count,
                    queued_frame_count=queued_frame_count,
                    produced_samples=produced_samples,
                    frame_handoff_count=frame_handoff_count,
                    frame_handoff_timeout_count=frame_handoff_timeout_count,
                    max_frame_handoff_wait_ms=round(max_frame_handoff_wait_ms, 3),
                    speculative_startup_burst_frames=speculative_startup_burst_frames,
                    last_raw_chunk_age_ms=round((time.perf_counter() - last_raw_chunk_at) * 1000, 3),
                    stop_requested=stopped(),
                    output_id=output_id,
                    generation_epoch=generation_epoch,
                )

        producer = threading.Thread(
            target=produce,
            name=f"omnix-tts-live-{stream_id[:19]}",
            daemon=True,
        )
        producer.start()

        started = False
        current_sample_rate = DEFAULT_SAMPLE_RATE
        while True:
            message = await frames.get()
            message_type = message.message_type
            pcm_bytes = message.pcm_bytes
            sample_rate = message.sample_rate
            metadata = message.metadata
            if message_type == "client_disconnect":
                return
            if message_type == "cancelled":
                await websocket.send_json(
                    {
                        "type": "cancelled",
                        "stream_id": stream_id,
                        **ownership_fields,
                        "generated_through_frame": sent_frames - 1,
                        "reason": metadata.get("reason") or "cancelled",
                    }
                )
                return
            if message_type == "chunk" and pcm_bytes is not None:
                try:
                    if stopped():
                        continue
                    if not started or sample_rate != current_sample_rate:
                        first_start_control = not started
                        current_sample_rate = sample_rate
                        await websocket.send_json(
                            {
                                "type": "start" if first_start_control else "format",
                                "stream_id": stream_id,
                                **ownership_fields,
                                "phrase_index": phrase_index,
                                "sample_rate": current_sample_rate,
                                "sample_format": "pcm_s16le",
                                "channels": 1,
                                "frame_samples": TTS_PCM_FRAME_SAMPLES,
                                "diagnostics_log": diagnostics_log_path(),
                                "provider_capabilities": capability_payload,
                                "performance_controls_applied": applied_controls,
                                "performance_controls_ignored": ignored_controls,
                            }
                        )
                        if first_start_control:
                            stream_log(
                                stream_id,
                                "server",
                                "first_start_control_sent",
                                route_to_start_control_ms=round(
                                    (time.perf_counter() - route_started_at) * 1000,
                                    3,
                                ),
                                output_id=output_id,
                                generation_epoch=generation_epoch,
                            )
                        started = True
                    first_pcm_frame = sent_frames == 0
                    await websocket.send_bytes(pcm_bytes)
                    if first_pcm_frame:
                        now = time.perf_counter()
                        stream_log(
                            stream_id,
                            "server",
                            "first_pcm_frame_sent",
                            frame_queue_wait_ms=(
                                round((now - message.queued_at) * 1000, 3)
                                if message.queued_at is not None
                                else None
                            ),
                            route_to_first_frame_ms=round((now - route_started_at) * 1000, 3),
                            frame_samples=len(pcm_bytes) // 2,
                            bundled_frame_count=int(metadata.get("bundled_frame_count") or 1),
                            sample_rate=sample_rate,
                            output_id=output_id,
                            generation_epoch=generation_epoch,
                        )
                finally:
                    if message.sent_ack is not None:
                        message.sent_ack.set()
                frame_samples = len(pcm_bytes) // 2
                sent_frames += 1
                active_output.sent_frames = sent_frames
                sent_samples += frame_samples
                continue
            if message_type == "done":
                await websocket.send_json(
                    {
                        "type": "done",
                        "stream_id": stream_id,
                        **ownership_fields,
                        "phrase_index": phrase_index,
                        "last_frame_index": sent_frames - 1,
                        "partial": False,
                    }
                )
                stream_log(
                    stream_id,
                    "server",
                    "done_control_sent",
                    route_to_done_control_ms=round(
                        (time.perf_counter() - route_started_at) * 1000,
                        3,
                    ),
                    sent_frames=sent_frames,
                    sent_samples=sent_samples,
                    last_frame_index=sent_frames - 1,
                    output_id=output_id,
                    generation_epoch=generation_epoch,
                )
                return
            await websocket.send_json(
                {
                    "type": "error",
                    "stream_id": stream_id,
                    **ownership_fields,
                    "phrase_index": phrase_index,
                    "message": metadata.get("message") or "TTS stream failed.",
                    "retry_after": metadata.get("retry_after"),
                    "capacity_saturated": metadata.get("capacity_saturated", False),
                }
            )
            return
    finally:
        phrase_stop.set()
        if state.active_frames is not None:
            state.active_frames = None
        if state.active_output is active_output:
            state.active_output = None
        producer_alive_before_join = bool(producer and producer.is_alive())
        if producer is not None and producer_alive_before_join:
            await asyncio.to_thread(producer.join, 0.25)
        producer_alive_after_join = bool(producer and producer.is_alive())
        stream_log(
            stream_id,
            "server",
            "phrase_route_cleanup",
            phrase_index=phrase_index,
            output_id=output_id,
            generation_epoch=generation_epoch,
            route_elapsed_ms=round((time.perf_counter() - route_started_at) * 1000, 3),
            sent_frames=sent_frames,
            sent_samples=sent_samples,
            producer_alive_before_join=producer_alive_before_join,
            producer_alive_after_join=producer_alive_after_join,
            persistent_connection=True,
        )
        if registered:
            end_stream(
                stream_id,
                producer_alive_after_join=producer_alive_after_join,
                sent_frames=sent_frames,
                sent_samples=sent_samples,
                output_id=output_id,
                generation_epoch=generation_epoch,
            )


def register_tts_live_call_websocket(
    router: APIRouter,
    state: Any,
    *,
    provider_resolver: TTSProviderResolver,
) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)
    """Register one reusable websocket per Live session."""

    @router.websocket(TTS_LIVE_CALL_WEBSOCKET_PATH)
    async def tts_live_call_websocket(websocket: WebSocket) -> None:
        capacity = live_call_capacity()
        if not capacity.try_acquire():
            await websocket.send_denial_response(
                JSONResponse(
                    {"error": "live_call_capacity_saturated"},
                    status_code=429,
                    headers={"Retry-After": "1"},
                )
            )
            return

        try:
            await websocket.accept()
            state = ConnectionState(
                stop_event=threading.Event(),
                requests=asyncio.Queue(),
            )
            receiver = asyncio.create_task(_receive_messages(websocket, state))
            try:
                while not state.stop_event.is_set():
                    payload = await state.requests.get()
                    if payload is None:
                        return
                    output_id, generation_epoch, segment_id = _output_identity(payload)
                    if (output_id, generation_epoch) in state.cancelled_outputs:
                        ownership_fields = _ownership_fields(
                            payload,
                            output_id,
                            generation_epoch,
                            segment_id,
                            payload.get("output_order"),
                        )
                        await websocket.send_json(
                            {
                                "type": "cancelled",
                                **ownership_fields,
                                "generated_through_frame": -1,
                            }
                        )
                        continue
                    await _stream_phrase(
                        websocket,
                        payload,
                        state,
                        provider_resolver=provider_resolver,
                    )
            finally:
                _stop_connection(state, "route-cleanup")
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
                with suppress(Exception):
                    await websocket.close()
        finally:
            capacity.release()
