"""Bounded-memory chapter mastering from immutable render files."""
from __future__ import annotations

import math
import wave
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


from .assembly import (ASSEMBLY_VERSION, PausePolicy, TimelineEntry,
                       _active_energy, _normalization_gains)
from .hashing import object_hash
from app.persistence.contracts import BlobStore


_FRAMES_PER_CHUNK = 65536
_MAX_WAV_DATA_BYTES = (1 << 32) - 44


@dataclass(frozen=True, slots=True)
class AudioFileSpan:
    render_id: str
    render_key: str
    speaker_id: str
    source_text: str
    storage_key: str
    audio_checksum: str
    source_span_id: str


@dataclass(frozen=True, slots=True)
class AssembledFileChapter:
    duration_seconds: float
    sample_rate: int
    timeline: tuple[TimelineEntry, ...]
    assembly_key: str
    measured_rms_dbfs: float
    normalized_rms_dbfs: float
    applied_gain_db: float
    speaker_gain_db: dict[str, float]
    peak_dbfs: float


def _pause_frames(previous: AudioFileSpan | None, span: AudioFileSpan,
                  rate: int, policy: PausePolicy) -> int:
    if previous is None:
        return 0
    if previous.source_span_id == span.source_span_id:
        pause_ms = 0
    else:
        pause_ms = (policy.speaker_change_ms if previous.speaker_id != span.speaker_id
                    else policy.same_speaker_ms)
    if previous.source_text.endswith("\n\n") or span.source_text.startswith("\n\n"):
        pause_ms = max(pause_ms, policy.paragraph_ms)
    return round(rate * pause_ms / 1000)


def _reader(blobs: BlobStore, span: AudioFileSpan):
    handle = blobs.open_verified(span.storage_key, expected_checksum=span.audio_checksum)
    try:
        reader = wave.open(handle, "rb")
        if (reader.getnchannels() != 1 or reader.getsampwidth() != 2
                or reader.getcomptype() != "NONE" or reader.getframerate() <= 0
                or reader.getnframes() <= 0):
            raise ValueError("assembly requires non-empty mono PCM16 WAV renders")
        return handle, reader
    except (wave.Error, EOFError) as exc:
        handle.close()
        raise ValueError("render is not valid WAV audio") from exc
    except Exception:
        handle.close()
        raise


def assembly_key_for(
    spans: list[AudioFileSpan], *, policy: PausePolicy = PausePolicy(),
    target_rms_dbfs: float = -20.0,
) -> str:
    return object_hash({
        "version": ASSEMBLY_VERSION,
        "render_keys": [span.render_key for span in spans],
        "audio_checksums": [span.audio_checksum for span in spans],
        "same_source_as_previous": [
            bool(
                index > 0
                and span.source_span_id == spans[index - 1].source_span_id
            )
            for index, span in enumerate(spans)
        ],
        "pause_policy": asdict(policy), "target_rms_dbfs": target_rms_dbfs,
    })


def assemble_chapter_file(
    blobs: BlobStore, spans: list[AudioFileSpan], output_path: Path, *,
    policy: PausePolicy = PausePolicy(), target_rms_dbfs: float = -20.0,
    on_chunk: Callable[[], None] | None = None,
) -> AssembledFileChapter:
    if not spans:
        raise ValueError("chapter has no rendered audio")
    if not -35 <= target_rms_dbfs <= -10:
        raise ValueError("chapter loudness target is out of range")
    if min(asdict(policy).values()) < 0:
        raise ValueError("pause durations must be non-negative")

    sample_rate: int | None = None
    speaker_stats: dict[str, list[int]] = {}
    total_frames = 0
    previous: AudioFileSpan | None = None
    timeline: list[TimelineEntry] = []
    for span in spans:
        handle, reader = _reader(blobs, span)
        try:
            rate = reader.getframerate()
            if sample_rate is None:
                sample_rate = rate
            elif rate != sample_rate:
                raise ValueError("chapter renders use different sample rates")
            pause_frames = _pause_frames(previous, span, rate, policy)
            total_frames += pause_frames
            start = total_frames / rate
            frames_read = 0
            stats = speaker_stats.setdefault(span.speaker_id, [0, 0, 0])
            while frames := reader.readframes(_FRAMES_PER_CHUNK):
                if on_chunk is not None:
                    on_chunk()
                samples = array("h")
                samples.frombytes(frames)
                squared, count, peak = _active_energy(samples)
                stats[0] += squared
                stats[1] += count
                stats[2] = max(stats[2], peak)
                frames_read += len(samples)
            if frames_read != reader.getnframes():
                raise ValueError("render WAV is truncated")
            total_frames += frames_read
            timeline.append(TimelineEntry(span.render_id, start,
                                          total_frames / rate, pause_frames / rate))
            previous = span
        finally:
            reader.close()
            handle.close()
    if total_frames * 2 > _MAX_WAV_DATA_BYTES:
        raise ValueError("chapter audio exceeds the WAV size limit")

    gains, measured_dbfs, normalized_dbfs, effective_gain_db, peak_dbfs = _normalization_gains(
        speaker_stats, target_rms_dbfs=target_rms_dbfs,
    )
    with wave.open(str(output_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        previous = None
        for span in spans:
            handle, reader = _reader(blobs, span)
            try:
                pause = _pause_frames(previous, span, sample_rate, policy)
                while pause:
                    count = min(pause, _FRAMES_PER_CHUNK)
                    writer.writeframesraw(b"\x00\x00" * count)
                    pause -= count
                gain = gains[span.speaker_id]
                while frames := reader.readframes(_FRAMES_PER_CHUNK):
                    if on_chunk is not None:
                        on_chunk()
                    samples = array("h")
                    samples.frombytes(frames)
                    mastered = array("h", (max(-32768, min(32767, round(sample * gain)))
                                           for sample in samples))
                    writer.writeframesraw(mastered.tobytes())
                previous = span
            finally:
                reader.close()
                handle.close()
    key = assembly_key_for(spans, policy=policy, target_rms_dbfs=target_rms_dbfs)
    return AssembledFileChapter(
        total_frames / sample_rate, sample_rate, tuple(timeline), key,
        measured_dbfs, normalized_dbfs, effective_gain_db,
        {speaker_id: 20 * math.log10(max(gain, 1e-12))
         for speaker_id, gain in gains.items()},
        peak_dbfs,
    )
