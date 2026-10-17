"""Runtime startup policy for low-latency live-call TTS playback."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from itertools import chain
from typing import Any

from app.conversation.tts_stream_contract import (
    DEFAULT_SAMPLE_RATE,
    STREAM_INITIAL_FALLBACK_THRESHOLD,
    STREAM_MAX_INITIAL_SILENCE_MS,
)

# The first accepted response phrase uses a two-step Qwen decoder chunk, which
# materializes 3,840 samples (160 ms at 24 kHz). Transfer that cadence reserve
# atomically. The producer and speculative-cache loops explicitly yield between
# decoder chunks so this frame reaches the event loop before the next CUDA step.
# Four-step later phrases become two steady transport frames.
TTS_LIVE_CALL_STARTUP_FRAME_SAMPLES = 3_840

# Generated TTS output is not microphone input. The transport-neutral scanner
# intentionally starts at a stricter 1% amplitude threshold, then falls back to
# this quiet-speech threshold after 400 ms. Live-call traces show that Qwen's
# first chunk often already contains quiet speech, but the stricter gate can
# wait for a later decoder chunk. Using the existing fallback threshold
# immediately for this route preserves silence rejection while removing that
# avoidable wait when signal is already present.
TTS_LIVE_CALL_INITIAL_SILENCE_THRESHOLD = STREAM_INITIAL_FALLBACK_THRESHOLD

# The two-step first-phrase fast path produces exactly one 3,840-sample / 160 ms
# chunk at 24 kHz. Arm the non-zero fallback after that first chunk so quiet
# speech can be handed off immediately. Exact digital silence is still rejected
# by the transport-neutral scanner, and ordinary later phrases retain the
# established 400 ms window.
TTS_LIVE_CALL_FIRST_CHUNK_MAX_INITIAL_SILENCE_MS = 160.0

def live_call_max_initial_silence_ms_for_first_chunk(
    pcm_bytes: bytes,
    sample_rate: int,
) -> float:
    """Return the onset scan window for the first raw live-call TTS chunk."""
    resolved_rate = int(sample_rate or DEFAULT_SAMPLE_RATE)
    first_chunk_samples = len(pcm_bytes) // 2
    if (
        resolved_rate == DEFAULT_SAMPLE_RATE
        and first_chunk_samples == TTS_LIVE_CALL_STARTUP_FRAME_SAMPLES
    ):
        return TTS_LIVE_CALL_FIRST_CHUNK_MAX_INITIAL_SILENCE_MS
    return STREAM_MAX_INITIAL_SILENCE_MS


def stream_live_call_pcm16_blocks(
    chunks: Iterator[tuple[bytes, int, Any]],
    *,
    streamer: Callable[..., Iterator[tuple[bytes, int, Any]]],
    block_samples: int,
) -> Iterator[tuple[bytes, int, Any]]:
    """Apply the first-chunk onset window before packing transport frames."""
    chunk_iter = iter(chunks)
    try:
        first_chunk = next(chunk_iter)
    except StopIteration:
        return iter(())

    first_pcm, first_rate, _first_timing = first_chunk
    return streamer(
        chain((first_chunk,), chunk_iter),
        block_samples=block_samples,
        silence_threshold=TTS_LIVE_CALL_INITIAL_SILENCE_THRESHOLD,
        max_initial_silence_ms=live_call_max_initial_silence_ms_for_first_chunk(
            first_pcm,
            first_rate,
        ),
    )
