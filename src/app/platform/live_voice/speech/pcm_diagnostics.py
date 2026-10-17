"""Stage-level diagnostics for live-call audio conversion and PCM packing."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from functools import wraps
from typing import Any

from app.observability.tts_stream_diagnostics import stream_log

_DIAGNOSTIC_STREAM_ID = "gateway-live-tts-pcm-conversion"
_SLOW_CONVERSION_MS = 25.0
_THREAD_STATE = threading.local()


def _audio_descriptor(audio_chunk: Any) -> dict[str, Any]:
    shape = getattr(audio_chunk, "shape", None)
    if shape is not None:
        try:
            shape = [int(value) for value in shape]
        except (TypeError, ValueError):
            shape = str(shape)
    device = getattr(audio_chunk, "device", None)
    return {
        "input_type": f"{type(audio_chunk).__module__}.{type(audio_chunk).__qualname__}",
        "input_shape": shape,
        "input_dtype": str(getattr(audio_chunk, "dtype", "")) or None,
        "input_device": str(device) if device is not None else None,
    }


def measured_pcm_converter(
    converter: Callable[[Any], bytes],
) -> Callable[[Any], bytes]:
    """Wrap a PCM converter with one normal event per producer thread.

    Each live TTS phrase has its own producer thread, so the first conversion
    event identifies tensor synchronization and quantization without logging
    every subsequent codec chunk. Slow conversions are always emitted.
    """

    @wraps(converter)
    def measured(audio_chunk: Any) -> bytes:
        conversion_index = int(getattr(_THREAD_STATE, "conversion_index", 0))
        started = time.perf_counter()
        pcm_bytes = converter(audio_chunk)
        conversion_ms = (time.perf_counter() - started) * 1000.0
        _THREAD_STATE.conversion_index = conversion_index + 1
        slow = conversion_ms >= _SLOW_CONVERSION_MS
        if conversion_index == 0 or slow:
            stream_log(
                _DIAGNOSTIC_STREAM_ID,
                "provider",
                "first_pcm_conversion_completed"
                if conversion_index == 0
                else "slow_pcm_conversion_completed",
                conversion_index=conversion_index,
                conversion_ms=round(conversion_ms, 3),
                pcm_samples=len(pcm_bytes) // 2,
                slow=slow,
                slow_threshold_ms=_SLOW_CONVERSION_MS,
                **_audio_descriptor(audio_chunk),
            )
        return pcm_bytes

    return measured


def measured_pcm_block_streamer(
    streamer: Callable[..., Iterator[tuple[bytes, int, Any]]],
) -> Callable[..., Iterator[tuple[bytes, int, Any]]]:
    """Measure conversion-to-first-audible-block packing delay.

    The existing ``first_raw_chunk_ready`` event occurs before leading-silence
    filtering. This wrapper records when the first transportable speech block
    actually emerges, separating silence filtering/provider cadence from
    event-loop queue and WebSocket send time.
    """

    @wraps(streamer)
    def measured(
        chunks: Iterator[tuple[bytes, int, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[tuple[bytes, int, Any]]:
        first_raw_at: float | None = None
        raw_chunks_seen = 0

        def observed_chunks() -> Iterator[tuple[bytes, int, Any]]:
            nonlocal first_raw_at, raw_chunks_seen
            for chunk in chunks:
                raw_chunks_seen += 1
                if first_raw_at is None:
                    first_raw_at = time.perf_counter()
                yield chunk

        for block_index, block in enumerate(streamer(observed_chunks(), *args, **kwargs)):
            if block_index == 0:
                ready_at = time.perf_counter()
                pcm_bytes, sample_rate, _timing = block
                stream_log(
                    _DIAGNOSTIC_STREAM_ID,
                    "provider",
                    "first_audible_pcm_block_ready",
                    raw_to_audible_block_ms=(
                        round((ready_at - first_raw_at) * 1000.0, 3)
                        if first_raw_at is not None
                        else None
                    ),
                    raw_chunks_before_audible_block=raw_chunks_seen,
                    block_samples=len(pcm_bytes) // 2,
                    sample_rate=sample_rate,
                )
            yield block

    return measured


__all__ = [
    "measured_pcm_block_streamer",
    "measured_pcm_converter",
]
