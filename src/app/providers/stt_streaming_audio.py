"""Fast preparation helpers for live-call PCM16 speech transcription."""
from __future__ import annotations

import math
import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

PCM16_BYTES_PER_SAMPLE = 2
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_SILENCE_THRESHOLD_DBFS = -40.0
DEFAULT_EDGE_PADDING_MS = 100


@dataclass(frozen=True)
class PcmTrimResult:
    original_samples: int
    trimmed_samples: int
    start_sample: int
    end_sample: int
    threshold_amplitude: int
    speech_detected: bool

    @property
    def removed_samples(self) -> int:
        return max(0, self.original_samples - self.trimmed_samples)


def pcm16_duration_ms(pcm: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE) -> float:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if len(pcm) % PCM16_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must contain whole samples")
    return len(pcm) / PCM16_BYTES_PER_SAMPLE / sample_rate * 1000.0


def trim_pcm16_edge_silence(
    pcm: bytes,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    silence_threshold_dbfs: float = DEFAULT_SILENCE_THRESHOLD_DBFS,
    edge_padding_ms: int = DEFAULT_EDGE_PADDING_MS,
) -> tuple[bytes, PcmTrimResult]:
    """Trim quiet leading/trailing PCM while retaining a speech margin."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if edge_padding_ms < 0:
        raise ValueError("edge_padding_ms must be non-negative")
    if len(pcm) % PCM16_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must contain whole samples")

    original_samples = len(pcm) // PCM16_BYTES_PER_SAMPLE
    threshold = max(1, min(32_767, round(32_767 * math.pow(10.0, silence_threshold_dbfs / 20.0))))
    if original_samples == 0:
        return pcm, PcmTrimResult(0, 0, 0, 0, threshold, False)

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    first_active = next((index for index, sample in enumerate(samples) if abs(int(sample)) >= threshold), -1)
    if first_active < 0:
        return pcm, PcmTrimResult(original_samples, original_samples, 0, original_samples, threshold, False)

    last_active = next(
        index
        for index in range(original_samples - 1, first_active - 1, -1)
        if abs(int(samples[index])) >= threshold
    )
    padding_samples = round(sample_rate * edge_padding_ms / 1000.0)
    start_sample = max(0, first_active - padding_samples)
    end_sample = min(original_samples, last_active + padding_samples + 1)
    trimmed = pcm[start_sample * PCM16_BYTES_PER_SAMPLE : end_sample * PCM16_BYTES_PER_SAMPLE]
    return trimmed, PcmTrimResult(
        original_samples,
        end_sample - start_sample,
        start_sample,
        end_sample,
        threshold,
        True,
    )


def write_pcm16_wav(path: str | Path, pcm: bytes, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> Path:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if len(pcm) % PCM16_BYTES_PER_SAMPLE:
        raise ValueError("PCM16 payload must contain whole samples")
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(PCM16_BYTES_PER_SAMPLE)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output_path
