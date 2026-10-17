"""Lossless span-to-chapter timeline with per-speaker loudness mastering."""
from __future__ import annotations

import io
import math
import wave
from array import array
from dataclasses import asdict, dataclass
from typing import Sequence

from .hashing import bytes_hash, object_hash


ASSEMBLY_VERSION = "audiobook-assembly-v3"
_ACTIVE_SAMPLE_GATE = 33  # ~-60 dBFS: excludes digital/near-digital silence.
_SPEAKER_GAIN_LIMIT_DB = 9.0
_SPEAKER_HEADROOM_DBFS = -3.0
_OUTPUT_PEAK_CEILING_DBFS = -1.0


@dataclass(frozen=True, slots=True)
class PausePolicy:
    same_speaker_ms: int = 250
    speaker_change_ms: int = 500
    paragraph_ms: int = 700


@dataclass(frozen=True, slots=True)
class AudioSpan:
    render_id: str
    render_key: str
    speaker_id: str
    source_text: str
    wav_bytes: bytes
    source_span_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    render_id: str
    start_seconds: float
    end_seconds: float
    pause_before_seconds: float


@dataclass(frozen=True, slots=True)
class AssembledChapter:
    wav_bytes: bytes
    duration_seconds: float
    sample_rate: int
    timeline: tuple[TimelineEntry, ...]
    assembly_key: str
    measured_rms_dbfs: float
    normalized_rms_dbfs: float
    applied_gain_db: float
    speaker_gain_db: dict[str, float]
    peak_dbfs: float


def _pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2 or reader.getcomptype() != "NONE":
                raise ValueError("assembly requires mono PCM16 WAV renders")
            frames = reader.readframes(reader.getnframes())
            if not frames:
                raise ValueError("assembly cannot use empty audio")
            return frames, reader.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError("render is not valid WAV audio") from exc


def _active_energy(samples: array) -> tuple[int, int, int]:
    """Return gated speech energy, active sample count, and absolute peak.

    Qwen renders include appended digital silence. Counting that silence in RMS
    makes short dialogue lines appear artificially quieter than long narration,
    so the gate excludes only near-digital silence while preserving voiced
    dynamics.
    """
    squared = 0
    count = 0
    peak = 0
    for sample in samples:
        absolute = abs(sample)
        peak = max(peak, absolute)
        if absolute >= _ACTIVE_SAMPLE_GATE:
            squared += sample * sample
            count += 1
    return squared, count, peak


def _normalization_gains(
    speaker_stats: dict[str, list[int]], *, target_rms_dbfs: float,
) -> tuple[dict[str, float], float, float, float, float]:
    """Compute one bounded gain per speaker plus a final chapter trim.

    A single gain per speaker preserves that speaker's intentional line-to-line
    dynamics. It only corrects systematic level differences between voices.
    """
    if not speaker_stats:
        raise ValueError("chapter speech audio is silent")
    total_squared = sum(values[0] for values in speaker_stats.values())
    total_samples = sum(values[1] for values in speaker_stats.values())
    if total_samples <= 0 or total_squared <= 0:
        raise ValueError("chapter speech audio is silent")
    measured_rms = math.sqrt(total_squared / total_samples)
    measured_dbfs = 20 * math.log10(measured_rms / 32768)

    speaker_linear: dict[str, float] = {}
    adjusted_squared = 0.0
    adjusted_samples = 0
    adjusted_peak = 0.0
    speaker_headroom = 32768 * 10 ** (_SPEAKER_HEADROOM_DBFS / 20)
    for speaker_id, (squared, count, peak) in speaker_stats.items():
        if count <= 0 or squared <= 0:
            speaker_linear[speaker_id] = 1.0
            continue
        rms = math.sqrt(squared / count)
        current_dbfs = 20 * math.log10(rms / 32768)
        requested_db = target_rms_dbfs - current_dbfs
        bounded_db = max(-_SPEAKER_GAIN_LIMIT_DB,
                         min(_SPEAKER_GAIN_LIMIT_DB, requested_db))
        gain = 10 ** (bounded_db / 20)
        if peak > 0:
            gain = min(gain, speaker_headroom / peak)
        speaker_linear[speaker_id] = gain
        adjusted_squared += squared * gain * gain
        adjusted_samples += count
        adjusted_peak = max(adjusted_peak, peak * gain)

    if adjusted_samples <= 0 or adjusted_squared <= 0:
        raise ValueError("chapter speech audio is silent")
    adjusted_rms = math.sqrt(adjusted_squared / adjusted_samples)
    adjusted_dbfs = 20 * math.log10(adjusted_rms / 32768)
    chapter_gain = 10 ** ((target_rms_dbfs - adjusted_dbfs) / 20)
    if adjusted_peak > 0:
        output_ceiling = 32768 * 10 ** (_OUTPUT_PEAK_CEILING_DBFS / 20)
        chapter_gain = min(chapter_gain, output_ceiling / adjusted_peak)

    total_gains = {
        speaker_id: gain * chapter_gain
        for speaker_id, gain in speaker_linear.items()
    }
    normalized_squared = adjusted_squared * chapter_gain * chapter_gain
    normalized_rms = math.sqrt(normalized_squared / adjusted_samples)
    normalized_dbfs = 20 * math.log10(normalized_rms / 32768)
    final_peak = adjusted_peak * chapter_gain
    peak_dbfs = 20 * math.log10(
        max(1.0, min(32767.0, final_peak)) / 32768
    )
    effective_gain_db = normalized_dbfs - measured_dbfs
    return total_gains, measured_dbfs, normalized_dbfs, effective_gain_db, peak_dbfs


def assemble_chapter(
    spans: Sequence[AudioSpan], *, policy: PausePolicy = PausePolicy(),
    target_rms_dbfs: float = -20.0,
) -> AssembledChapter:
    if not spans:
        raise ValueError("chapter has no rendered audio")
    if not -35 <= target_rms_dbfs <= -10:
        raise ValueError("chapter loudness target is out of range")
    if min(asdict(policy).values()) < 0:
        raise ValueError("pause durations must be non-negative")
    sample_rate: int | None = None
    raw_parts: list[tuple[bytes, str | None]] = []
    timeline: list[TimelineEntry] = []
    speaker_stats: dict[str, list[int]] = {}
    total_frames = 0
    previous: AudioSpan | None = None
    for span in spans:
        frames, rate = _pcm16(span.wav_bytes)
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise ValueError("chapter renders use different sample rates")
        if rate <= 0:
            raise ValueError("invalid audio sample rate")
        pause_ms = 0
        if previous is not None:
            same_source_span = (
                previous.source_span_id is not None
                and span.source_span_id is not None
                and previous.source_span_id == span.source_span_id
            )
            if not same_source_span:
                pause_ms = (
                    policy.speaker_change_ms
                    if previous.speaker_id != span.speaker_id
                    else policy.same_speaker_ms
                )
            if previous.source_text.endswith("\n\n") or span.source_text.startswith("\n\n"):
                pause_ms = max(pause_ms, policy.paragraph_ms)
        pause_frames = round(rate * pause_ms / 1000)
        raw_parts.append((b"\x00\x00" * pause_frames, None))
        total_frames += pause_frames
        start = total_frames / rate
        samples = array("h")
        samples.frombytes(frames)
        squared, count, peak = _active_energy(samples)
        stats = speaker_stats.setdefault(span.speaker_id, [0, 0, 0])
        stats[0] += squared
        stats[1] += count
        stats[2] = max(stats[2], peak)
        raw_parts.append((frames, span.speaker_id))
        total_frames += len(samples)
        timeline.append(TimelineEntry(span.render_id, start, total_frames / rate, pause_frames / rate))
        previous = span

    gains, measured_dbfs, normalized_dbfs, effective_gain_db, peak_dbfs = _normalization_gains(
        speaker_stats, target_rms_dbfs=target_rms_dbfs,
    )
    mastered = array("h")
    for part, speaker_id in raw_parts:
        samples = array("h")
        samples.frombytes(part)
        gain = 1.0 if speaker_id is None else gains[speaker_id]
        mastered.extend(max(-32768, min(32767, round(sample * gain))) for sample in samples)
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(mastered.tobytes())
    key = object_hash({
        "version": ASSEMBLY_VERSION,
        "render_keys": [span.render_key for span in spans],
        "audio_checksums": [bytes_hash(span.wav_bytes) for span in spans],
        "same_source_as_previous": [
            bool(
                index > 0
                and span.source_span_id is not None
                and spans[index - 1].source_span_id is not None
                and span.source_span_id == spans[index - 1].source_span_id
            )
            for index, span in enumerate(spans)
        ],
        "pause_policy": asdict(policy), "target_rms_dbfs": target_rms_dbfs,
    })
    return AssembledChapter(
        output.getvalue(), total_frames / sample_rate, sample_rate,
        tuple(timeline), key, measured_dbfs, normalized_dbfs, effective_gain_db,
        {speaker_id: 20 * math.log10(max(gain, 1e-12))
         for speaker_id, gain in gains.items()},
        peak_dbfs,
    )
