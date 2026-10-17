"""Transport-neutral request policy and PCM helpers for TTS streaming."""
from __future__ import annotations

import importlib
import math
import re
import sys
from types import ModuleType
from typing import Any, Iterator

from pydantic import BaseModel, Field, model_validator

from app.conversation.performance_contract import SpeechPerformancePlan
from app.conversation.text import remove_emojis


def _numpy() -> ModuleType | None:
    """NumPy, imported with the first audio chunk rather than with the gateway (it is about 80 modules)."""
    try:
        return importlib.import_module("numpy")
    except ImportError:  # pragma: no cover - exercised in minimal dependency environments.
        return None


DEFAULT_SAMPLE_RATE = 24_000
STREAM_OUTPUT_BLOCK_SAMPLES = 2_048
STREAM_INITIAL_SILENCE_THRESHOLD = 0.01
STREAM_INITIAL_FALLBACK_THRESHOLD = 0.0025
STREAM_INITIAL_PREROLL_MS = 40.0
STREAM_MAX_INITIAL_SILENCE_MS = 400.0
# Qwen3-TTS emits roughly 80 ms of audio per codec step. A 96-step minimum
# allowed a 14-character tail that missed EOS to produce about 7 seconds of
# speech. Keep only enough floor for very short acknowledgements; ordinary
# clauses continue to receive per-character headroom from the estimator below.
CHAT_STREAM_MIN_NEW_TOKENS = 32
CHAT_STREAM_MAX_NEW_TOKENS = 1_024
CHAT_STREAM_TOKEN_NUMERATOR = 9
CHAT_STREAM_TOKEN_DENOMINATOR = 8
CHAT_STREAM_TOKEN_OVERHEAD = 24
CHAT_STREAM_MIN_REPETITION_PENALTY = 1.05
# Qwen3-TTS emits roughly 80 ms of 24 kHz audio per codec step. Keep ordinary
# clauses capped at four steps for decoder throughput, but let the first spoken
# phrase of each accepted response use two steps so decoder overhead is
# amortized across 160 ms of speech. A one-step / 80 ms chunk has essentially
# no cadence reserve and starves playback under ordinary scheduling jitter.
# Conversation output IDs encode their response-local phrase index as ``-pN``.
CHAT_STREAM_FIRST_PHRASE_CODEC_CHUNK_STEPS = 2
CHAT_STREAM_MAX_CODEC_CHUNK_STEPS = 4
_FIRST_CONVERSATION_PHRASE_PATTERN = re.compile(r"-g\d+-p0$")


class TtsPronunciationEntry(BaseModel):
    phrase: str = Field(min_length=1, max_length=120)
    pronunciation: str = Field(min_length=1, max_length=160)
    locale: str | None = Field(default=None, max_length=20)


def apply_pronunciation_lexicon(text: str, entries: list[TtsPronunciationEntry]) -> str:
    """Apply bounded, case-insensitive whole-phrase rendering hints to synthesized text only."""
    result = text
    ordered = sorted(entries[:32], key=lambda entry: len(entry.phrase), reverse=True)
    for entry in ordered:
        phrase = entry.phrase.strip()
        pronunciation = entry.pronunciation.strip()
        if not phrase or not pronunciation or phrase == pronunciation:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)
        result = pattern.sub(pronunciation, result)
    return result


def estimate_chat_stream_max_new_tokens(text: str) -> int:
    """Bound chat speech while leaving headroom above normal English speech duration."""
    normalized = remove_emojis(text or "").strip()
    estimated = (
        (len(normalized) * CHAT_STREAM_TOKEN_NUMERATOR + CHAT_STREAM_TOKEN_DENOMINATOR - 1)
        // CHAT_STREAM_TOKEN_DENOMINATOR
        + CHAT_STREAM_TOKEN_OVERHEAD
    )
    return max(CHAT_STREAM_MIN_NEW_TOKENS, min(CHAT_STREAM_MAX_NEW_TOKENS, estimated))


def chat_stream_codec_chunk_cap(output_id: str | None) -> int:
    """Use the two-step startup path only for the first canonical response phrase."""
    normalized_output_id = (output_id or "").strip()
    if _FIRST_CONVERSATION_PHRASE_PATTERN.search(normalized_output_id):
        return CHAT_STREAM_FIRST_PHRASE_CODEC_CHUNK_STEPS
    return CHAT_STREAM_MAX_CODEC_CHUNK_STEPS


class TtsStreamRequest(BaseModel):
    """Browser-facing TTS streaming request shared by binary transports."""

    text: str = ""
    speaker: str | None = None
    language: str | None = "en"
    chunk_size: int = Field(default=12, ge=1, le=256)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    top_k: int = Field(default=50, ge=0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    repetition_penalty: float = Field(default=1.05, ge=0.0)
    append_silence: bool = True
    max_new_tokens: int | None = Field(default=None, ge=1)
    non_streaming_mode: bool | None = None
    parity_mode: bool | None = None
    request_id: str | None = None
    diagnostics_stream_id: str | None = None
    output_id: str | None = Field(default=None, max_length=160)
    generation_epoch: int = Field(default=0, ge=0)
    output_order: int | None = Field(default=None, ge=0)
    segment_id: str | None = Field(default=None, max_length=160)
    delivery_plan: SpeechPerformancePlan | None = None
    pronunciation_lexicon: list[TtsPronunciationEntry] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def apply_live_rendering_policy(self) -> "TtsStreamRequest":
        if self.pronunciation_lexicon:
            self.text = apply_pronunciation_lexicon(self.text, self.pronunciation_lexicon)
        stream_id = (self.diagnostics_stream_id or "").strip()
        if not stream_id.startswith("chat-"):
            return self
        self.parity_mode = False
        self.chunk_size = min(self.chunk_size, chat_stream_codec_chunk_cap(self.output_id))
        token_budget = estimate_chat_stream_max_new_tokens(self.text)
        if self.max_new_tokens is None or self.max_new_tokens > token_budget:
            self.max_new_tokens = token_budget
        self.repetition_penalty = max(
            self.repetition_penalty,
            CHAT_STREAM_MIN_REPETITION_PENALTY,
        )
        return self


def stream_pcm16_blocks(
    chunks: Iterator[tuple[bytes, int, Any]],
    *,
    block_samples: int = STREAM_OUTPUT_BLOCK_SAMPLES,
    silence_threshold: float = STREAM_INITIAL_SILENCE_THRESHOLD,
    preroll_ms: float = STREAM_INITIAL_PREROLL_MS,
    max_initial_silence_ms: float = STREAM_MAX_INITIAL_SILENCE_MS,
    fallback_threshold: float = STREAM_INITIAL_FALLBACK_THRESHOLD,
) -> Iterator[tuple[bytes, int, Any]]:
    """Repack provider chunks into steady PCM16 blocks with bounded startup scanning.

    Initial speech detection first uses the normal amplitude threshold. After
    ``max_initial_silence_ms`` of generated audio, each new chunk is rescanned
    at a quiet-speech threshold and then for any nonzero signal. Until genuine
    signal appears, only a small preroll tail is retained; all-zero PCM is not
    mislabeled as audible output.
    """
    block_bytes = max(1, int(block_samples)) * 2
    leftover = b""
    leftover_rate = DEFAULT_SAMPLE_RATE
    leftover_timing: Any = {}
    initial_pcm = b""
    initial_rate = DEFAULT_SAMPLE_RATE
    initial_timing: Any = {}
    found_speech = False
    fallback_armed = False

    for pcm_bytes, sample_rate, timing in chunks:
        sample_rate = int(sample_rate or DEFAULT_SAMPLE_RATE)
        pcm_bytes = even_pcm16_bytes(pcm_bytes)
        if not pcm_bytes:
            continue

        if leftover and sample_rate != leftover_rate:
            yield pad_pcm16_block(leftover, block_bytes), leftover_rate, leftover_timing
            leftover = b""

        if not found_speech:
            if initial_pcm and sample_rate != initial_rate:
                initial_pcm = b""
                fallback_armed = False
            initial_pcm += pcm_bytes
            initial_rate = sample_rate
            initial_timing = timing
            start_byte = initial_speech_start_byte(
                initial_pcm,
                sample_rate,
                silence_threshold,
                preroll_ms,
            )
            if start_byte is None:
                max_initial_samples = max(
                    0,
                    int(sample_rate * max(0.0, max_initial_silence_ms) / 1000.0),
                )
                if not fallback_armed and len(initial_pcm) // 2 < max_initial_samples:
                    continue
                fallback_armed = True
                quiet_threshold = min(
                    max(0.0, silence_threshold),
                    max(0.0, fallback_threshold),
                )
                start_byte = initial_speech_start_byte(
                    initial_pcm,
                    sample_rate,
                    quiet_threshold,
                    preroll_ms,
                )
                if start_byte is None:
                    start_byte = initial_speech_start_byte(
                        initial_pcm,
                        sample_rate,
                        0.0,
                        preroll_ms,
                    )
                if start_byte is None:
                    preroll_samples = max(
                        0,
                        int(sample_rate * max(0.0, preroll_ms) / 1000.0),
                    )
                    retained_bytes = preroll_samples * 2
                    initial_pcm = (
                        initial_pcm[-retained_bytes:]
                        if retained_bytes > 0
                        else b""
                    )
                    continue
            pcm_bytes = initial_pcm[start_byte:]
            sample_rate = initial_rate
            timing = initial_timing
            initial_pcm = b""
            found_speech = True

        audio = leftover + pcm_bytes
        full_bytes = (len(audio) // block_bytes) * block_bytes
        for offset in range(0, full_bytes, block_bytes):
            yield audio[offset : offset + block_bytes], sample_rate, timing

        leftover = audio[full_bytes:]
        leftover_rate = sample_rate
        leftover_timing = timing

    if leftover:
        yield pad_pcm16_block(leftover, block_bytes), leftover_rate, leftover_timing


def audio_chunk_to_pcm16_bytes(audio_chunk: Any) -> bytes:
    """Convert float-like mono audio to little-endian PCM16 using vectorized fast paths."""
    if audio_chunk is None:
        return b""
    if isinstance(audio_chunk, bytes):
        return audio_chunk
    if isinstance(audio_chunk, (bytearray, memoryview)):
        return bytes(audio_chunk)

    # Qwen commonly yields torch tensors, including CUDA tensors. NumPy cannot
    # directly coerce a CUDA tensor, and the old fallback called ``tolist()``,
    # forcing a device sync plus thousands of Python scalar conversions. Keep
    # clipping and int16 quantization on the tensor device, then transfer only
    # the compact int16 buffer to CPU. Use the already-loaded torch module so
    # lightweight gateway imports do not eagerly initialize torch.
    torch_module = sys.modules.get("torch")
    if torch_module is not None and torch_module.is_tensor(audio_chunk):
        return _torch_audio_chunk_to_pcm16_bytes(audio_chunk, torch_module)

    np = _numpy()
    if np is None:
        return _audio_chunk_to_pcm16_bytes_fallback(audio_chunk)

    try:
        values = np.asarray(audio_chunk, dtype=np.float32)
    except (TypeError, ValueError):
        return _audio_chunk_to_pcm16_bytes_fallback(audio_chunk)

    if values.size == 0:
        return b""
    if values.ndim > 1:
        values = values[..., 0]
    values = np.ascontiguousarray(values.reshape(-1), dtype=np.float32)
    np.nan_to_num(values, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    np.clip(values, -1.0, 1.0, out=values)
    return (values * 32767.0).astype("<i2", copy=False).tobytes()


def _torch_audio_chunk_to_pcm16_bytes(audio_chunk: Any, torch_module: Any) -> bytes:
    values = audio_chunk.detach()
    if int(values.numel()) == 0:
        return b""
    if int(values.ndim) > 1:
        values = values[..., 0]
    values = values.reshape(-1).to(dtype=torch_module.float32)
    values = torch_module.nan_to_num(
        values,
        nan=0.0,
        posinf=1.0,
        neginf=-1.0,
    )
    pcm = values.clamp(-1.0, 1.0).mul(32767.0).to(dtype=torch_module.int16)
    if getattr(pcm.device, "type", "cpu") != "cpu":
        pcm = pcm.to(device="cpu", non_blocking=False)
    pcm = pcm.contiguous()
    array = pcm.numpy()
    if _numpy() is not None:
        array = array.astype("<i2", copy=False)
    return array.tobytes()


def _audio_chunk_to_pcm16_bytes_fallback(audio_chunk: Any) -> bytes:
    """Compatibility path for irregular iterables that NumPy cannot coerce."""
    values = audio_chunk.tolist() if hasattr(audio_chunk, "tolist") else audio_chunk
    if not isinstance(values, (list, tuple)):
        values = [values]

    pcm = bytearray()
    for value in values:
        if isinstance(value, (list, tuple)):
            value = value[0] if value else 0.0
        try:
            sample = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(sample):
            sample = 0.0
        elif math.isinf(sample):
            sample = 1.0 if sample > 0 else -1.0
        sample = max(-1.0, min(1.0, sample))
        pcm.extend(int(sample * 32767).to_bytes(2, byteorder="little", signed=True))
    return bytes(pcm)


def even_pcm16_bytes(pcm_bytes: bytes) -> bytes:
    return pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)]


def pad_pcm16_block(pcm_bytes: bytes, block_bytes: int) -> bytes:
    return pcm_bytes + (b"\x00" * max(0, block_bytes - len(pcm_bytes)))


def initial_speech_start_byte(
    pcm_bytes: bytes,
    sample_rate: int,
    threshold: float,
    preroll_ms: float,
) -> int | None:
    even_bytes = even_pcm16_bytes(pcm_bytes)
    if not even_bytes:
        return None
    threshold_int = int(max(0.0, min(1.0, threshold)) * 32767)
    preroll_samples = max(0, int(sample_rate * max(0.0, preroll_ms) / 1000.0))
    np = _numpy()
    if np is not None:
        samples = np.frombuffer(even_bytes, dtype="<i2").astype(np.int32, copy=False)
        speech_indices = np.flatnonzero(np.abs(samples) > threshold_int)
        if not speech_indices.size:
            return None
        start_sample = max(0, int(speech_indices[0]) - preroll_samples)
        return start_sample * 2

    for index in range(0, len(even_bytes), 2):
        sample = int.from_bytes(even_bytes[index : index + 2], byteorder="little", signed=True)
        if abs(sample) > threshold_int:
            start_sample = max(0, (index // 2) - preroll_samples)
            return start_sample * 2
    return None
