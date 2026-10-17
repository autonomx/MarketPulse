"""Raw PCM websocket transport for browser TTS streaming."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from contextlib import suppress
from functools import wraps
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.providers.service import get_tts_provider
from app.conversation.text import remove_emojis

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

_ROUTE_SENTINEL = "_omnix_tts_pcm_ws_registered"
TTS_PCM_WEBSOCKET_PATH = "/api/tts/stream/websocket"
TTS_PCM_FRAME_SAMPLES = 2_400  # 100 ms at 24 kHz.
CLIENT_DRAIN_TIMEOUT_SECONDS = 30.0

FrameMessage = tuple[str, bytes | None, int, dict[str, Any]]


def _connection_details(websocket: WebSocket) -> dict[str, Any]:
    client = websocket.client
    return {
        "client_host": client.host if client else None,
        "client_port": client.port if client else None,
        "origin": websocket.headers.get("origin"),
        "user_agent": websocket.headers.get("user-agent"),
        "websocket_path": websocket.url.path,
    }


async def _receive_client_diagnostics(
    websocket: WebSocket,
    stream_id: str,
    stop_event: threading.Event,
    client_finished: asyncio.Event,
    frames: asyncio.Queue[FrameMessage],
) -> None:
    """Record browser/worklet events while the server streams audio."""
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                stream_log(stream_id, "client", "diagnostic_json_invalid", raw_length=len(raw))
                continue
            if message.get("type") != "diagnostic":
                stream_log(
                    stream_id,
                    "client",
                    "unexpected_client_message",
                    message_type=message.get("type"),
                )
                continue
            client_stream_id = normalize_stream_id(message.get("stream_id"))
            event = str(message.get("event") or "diagnostic")[:120]
            details = message.get("details") if isinstance(message.get("details"), dict) else {}
            stream_log(
                stream_id,
                "client",
                event,
                client_stream_id=client_stream_id,
                stream_id_matches=client_stream_id == stream_id,
                **details,
            )
            if event in {"playback_finished", "playback_failed", "playback_stopped"}:
                client_finished.set()
    except WebSocketDisconnect:
        stream_log(stream_id, "client", "websocket_disconnected")
        stop_event.set()
        client_finished.set()
        frames.put_nowait(("client_disconnect", None, DEFAULT_SAMPLE_RATE, {}))
    except Exception as exc:  # pragma: no cover - defensive diagnostics receiver.
        stream_log(stream_id, "client", "diagnostic_receiver_failed", error=repr(exc))
        stop_event.set()
        client_finished.set()
        frames.put_nowait(("client_disconnect", None, DEFAULT_SAMPLE_RATE, {"error": str(exc)}))


def register_tts_pcm_websocket(router: APIRouter, state: Any) -> None:
    if getattr(state, _ROUTE_SENTINEL, False):
        return
    setattr(state, _ROUTE_SENTINEL, True)
    """Register the raw PCM websocket route once."""

    @router.websocket(TTS_PCM_WEBSOCKET_PATH)
    async def tts_pcm_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        stop_event = threading.Event()
        client_finished = asyncio.Event()
        producer: threading.Thread | None = None
        diagnostics_receiver: asyncio.Task[None] | None = None
        stream_id = normalize_stream_id()
        registered = False
        route_started_at = time.perf_counter()
        sent_frames = 0
        sent_samples = 0

        try:
            raw_message = await websocket.receive_text()
            try:
                payload = json.loads(raw_message)
                if not isinstance(payload, dict):
                    raise ValueError("TTS request must be a JSON object.")
                stream_id = normalize_stream_id(payload.get("diagnostics_stream_id"))
                request = TtsStreamRequest.model_validate(payload)
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                stream_log(stream_id, "server", "request_invalid", error=repr(exc), raw_length=len(raw_message))
                await websocket.send_json({"type": "error", "message": f"Invalid TTS request: {exc}"})
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
                text_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
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
                log_path=diagnostics_log_path(),
            )
            if not text:
                stream_log(stream_id, "server", "request_rejected", reason="text_required")
                await websocket.send_json({"type": "error", "message": "text_required"})
                return

            provider = await asyncio.to_thread(get_tts_provider)
            if provider is None:
                stream_log(stream_id, "server", "request_rejected", reason="tts_provider_unavailable")
                await websocket.send_json({"type": "error", "message": "tts_provider_unavailable"})
                return
            if not hasattr(provider, "generate_audio_stream"):
                stream_log(stream_id, "server", "request_rejected", reason="tts_provider_streaming_unavailable")
                await websocket.send_json({"type": "error", "message": "tts_provider_streaming_unavailable"})
                return

            stream_log(
                stream_id,
                "server",
                "provider_resolved",
                provider_class=f"{type(provider).__module__}.{type(provider).__qualname__}",
                provider_name=getattr(provider, "provider_name", None),
                provider_object_id=id(provider),
            )
            loop = asyncio.get_running_loop()
            frames: asyncio.Queue[FrameMessage] = asyncio.Queue()
            diagnostics_receiver = asyncio.create_task(
                _receive_client_diagnostics(websocket, stream_id, stop_event, client_finished, frames)
            )

            def emit(message: FrameMessage) -> None:
                if not stop_event.is_set():
                    loop.call_soon_threadsafe(frames.put_nowait, message)

            def produce() -> None:
                producer_started_at = time.perf_counter()
                last_raw_chunk_at = producer_started_at
                raw_chunk_count = 0
                queued_frame_count = 0
                produced_samples = 0
                stream_log(stream_id, "provider", "producer_thread_started")
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
                    stream_log(stream_id, "provider", "generation_started", stream_kwargs=stream_kwargs)

                    def raw_chunks():
                        nonlocal last_raw_chunk_at, raw_chunk_count, produced_samples
                        provider_stream = provider.generate_audio_stream(
                            text=text,
                            speaker=request.speaker,
                            language=request.language or "en",
                            **stream_kwargs,
                        )
                        for audio_chunk, sample_rate, timing in provider_stream:
                            now = time.perf_counter()
                            pcm_bytes = _audio_chunk_to_pcm16_bytes(audio_chunk)
                            resolved_rate = int(sample_rate or DEFAULT_SAMPLE_RATE)
                            sample_count = len(pcm_bytes) // 2
                            raw_chunk_count += 1
                            produced_samples += sample_count
                            elapsed_ms = (now - producer_started_at) * 1000
                            audio_ms = produced_samples * 1000 / max(1, resolved_rate)
                            stream_log(
                                stream_id,
                                "provider",
                                "raw_chunk_received",
                                raw_chunk_index=raw_chunk_count - 1,
                                sample_rate=resolved_rate,
                                samples=sample_count,
                                bytes=len(pcm_bytes),
                                interval_ms=round((now - last_raw_chunk_at) * 1000, 3),
                                generation_elapsed_ms=round(elapsed_ms, 3),
                                cumulative_audio_ms=round(audio_ms, 3),
                                generation_rtf=round(elapsed_ms / audio_ms, 5) if audio_ms else None,
                                provider_timing=timing,
                                stop_requested=stop_event.is_set(),
                            )
                            last_raw_chunk_at = now
                            yield pcm_bytes, resolved_rate, timing

                    for pcm_bytes, sample_rate, timing in _stream_pcm16_blocks(
                        raw_chunks(),
                        block_samples=TTS_PCM_FRAME_SAMPLES,
                    ):
                        if stop_event.is_set():
                            stream_log(
                                stream_id,
                                "provider",
                                "stop_observed_before_frame_queue",
                                queued_frame_count=queued_frame_count,
                            )
                            return
                        queued_frame_count += 1
                        emit(
                            (
                                "chunk",
                                pcm_bytes,
                                sample_rate,
                                {
                                    "frame_index": queued_frame_count - 1,
                                    "provider_timing": timing,
                                    "producer_elapsed_ms": round(
                                        (time.perf_counter() - producer_started_at) * 1000,
                                        3,
                                    ),
                                },
                            )
                        )
                        stream_log(
                            stream_id,
                            "provider",
                            "network_frame_queued",
                            frame_index=queued_frame_count - 1,
                            sample_rate=sample_rate,
                            samples=len(pcm_bytes) // 2,
                            bytes=len(pcm_bytes),
                        )
                    emit(("done", None, DEFAULT_SAMPLE_RATE, {}))
                except Exception as exc:  # pragma: no cover - provider failures are surfaced to the client.
                    stream_log(
                        stream_id,
                        "provider",
                        "generation_failed",
                        error=repr(exc),
                        raw_chunk_count=raw_chunk_count,
                        queued_frame_count=queued_frame_count,
                    )
                    event = "partial_done" if queued_frame_count > 0 else "error"
                    emit((event, None, DEFAULT_SAMPLE_RATE, {"message": str(exc) or "TTS stream failed."}))
                finally:
                    stream_log(
                        stream_id,
                        "provider",
                        "producer_thread_finished",
                        elapsed_ms=round((time.perf_counter() - producer_started_at) * 1000, 3),
                        raw_chunk_count=raw_chunk_count,
                        queued_frame_count=queued_frame_count,
                        produced_samples=produced_samples,
                        stop_requested=stop_event.is_set(),
                    )

            producer = threading.Thread(
                target=produce,
                name=f"omnix-tts-{stream_id[:24]}",
                daemon=True,
            )
            producer.start()
            stream_log(stream_id, "server", "producer_thread_launched", producer_thread_name=producer.name)

            started = False
            current_sample_rate = DEFAULT_SAMPLE_RATE
            last_send_at = route_started_at
            while True:
                message_type, pcm_bytes, sample_rate, metadata = await frames.get()
                if message_type == "client_disconnect":
                    stream_log(stream_id, "server", "client_disconnect_observed", metadata=metadata)
                    return
                if message_type == "chunk" and pcm_bytes is not None:
                    if not started or sample_rate != current_sample_rate:
                        current_sample_rate = sample_rate
                        control_type = "start" if not started else "format"
                        await websocket.send_json(
                            {
                                "type": control_type,
                                "stream_id": stream_id,
                                "sample_rate": current_sample_rate,
                                "sample_format": "pcm_s16le",
                                "channels": 1,
                                "frame_samples": TTS_PCM_FRAME_SAMPLES,
                                "diagnostics_log": diagnostics_log_path(),
                            }
                        )
                        stream_log(
                            stream_id,
                            "server",
                            "format_control_sent",
                            control_type=control_type,
                            sample_rate=current_sample_rate,
                            frame_samples=TTS_PCM_FRAME_SAMPLES,
                        )
                        started = True
                    send_started_at = time.perf_counter()
                    await websocket.send_bytes(pcm_bytes)
                    sent_at = time.perf_counter()
                    frame_samples = len(pcm_bytes) // 2
                    sent_frames += 1
                    sent_samples += frame_samples
                    stream_log(
                        stream_id,
                        "server",
                        "network_frame_sent",
                        frame_index=sent_frames - 1,
                        bytes=len(pcm_bytes),
                        samples=frame_samples,
                        sample_rate=current_sample_rate,
                        send_duration_ms=round((sent_at - send_started_at) * 1000, 3),
                        interval_since_previous_send_ms=round((sent_at - last_send_at) * 1000, 3),
                        cumulative_audio_ms=round(sent_samples * 1000 / max(1, current_sample_rate), 3),
                        pending_server_frames=frames.qsize(),
                        producer_metadata=metadata,
                    )
                    last_send_at = sent_at
                    continue
                if message_type in {"done", "partial_done"}:
                    partial = message_type == "partial_done"
                    await websocket.send_json(
                        {
                            "type": "done",
                            "stream_id": stream_id,
                            "partial": partial,
                            **({"message": metadata.get("message")} if metadata.get("message") else {}),
                        }
                    )
                    stream_log(
                        stream_id,
                        "server",
                        "done_control_sent",
                        partial=partial,
                        sent_frames=sent_frames,
                        sent_samples=sent_samples,
                        sent_audio_ms=round(sent_samples * 1000 / max(1, current_sample_rate), 3),
                    )
                    try:
                        await asyncio.wait_for(client_finished.wait(), timeout=CLIENT_DRAIN_TIMEOUT_SECONDS)
                        stream_log(stream_id, "server", "client_drain_acknowledged")
                    except TimeoutError:
                        stream_log(
                            stream_id,
                            "server",
                            "client_drain_timeout",
                            timeout_seconds=CLIENT_DRAIN_TIMEOUT_SECONDS,
                        )
                    return
                message = metadata.get("message") or "TTS stream failed."
                stream_log(stream_id, "server", "error_control_sent", message=message)
                await websocket.send_json({"type": "error", "stream_id": stream_id, "message": message})
                return
        except WebSocketDisconnect:
            stream_log(stream_id, "server", "route_websocket_disconnected")
        except Exception as exc:  # pragma: no cover - defensive route diagnostics.
            stream_log(stream_id, "server", "route_failed", error=repr(exc))
            with suppress(Exception):
                await websocket.send_json({"type": "error", "stream_id": stream_id, "message": str(exc)})
        finally:
            stop_event.set()
            client_finished.set()
            if diagnostics_receiver is not None:
                diagnostics_receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await diagnostics_receiver
            producer_alive_before_join = bool(producer and producer.is_alive())
            if producer is not None and producer_alive_before_join:
                await asyncio.to_thread(producer.join, 0.25)
            producer_alive_after_join = bool(producer and producer.is_alive())
            stream_log(
                stream_id,
                "server",
                "route_cleanup",
                route_elapsed_ms=round((time.perf_counter() - route_started_at) * 1000, 3),
                sent_frames=sent_frames,
                sent_samples=sent_samples,
                producer_alive_before_join=producer_alive_before_join,
                producer_alive_after_join=producer_alive_after_join,
            )
            if producer_alive_after_join:
                stream_log(
                    stream_id,
                    "server",
                    "producer_thread_lingering_after_route",
                    producer_thread_name=producer.name if producer else None,
                )
            if registered:
                end_stream(
                    stream_id,
                    producer_alive_after_join=producer_alive_after_join,
                    sent_frames=sent_frames,
                    sent_samples=sent_samples,
                )
