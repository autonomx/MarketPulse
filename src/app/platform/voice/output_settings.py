"""Voice Studio's output controls: what each slider changes on each TTS engine.

Stability, Style Exaggeration and Similarity steer the engine's sampling, so
their effect depends on the engine (and an engine without a matching knob
ignores the slider). Speed, Pitch and Volume are applied to the generated
audio, the same way on every engine. At the default positions nothing changes:
the engine keeps its configured sampling and the audio is untouched.
"""
from __future__ import annotations

import io
import math
import wave
from typing import Any

import numpy as np

# The default slider positions (Voice Studio's shipped defaults).
NEUTRAL: dict[str, float] = {
    "stability": 0.75, "similarity": 0.8, "style": 0.35, "speed": 1.0, "pitch": 0.0, "volume": 0.0,
}
AUDIO_CONTROLS = ("speed", "pitch", "volume")

# slider -> (engine parameter, value at slider 0, at the default position, at slider 1).
# The default-position values are each engine's own defaults.
_SAMPLING: dict[str, dict[str, tuple[str, float, float, float]]] = {
    "faster-qwen3-tts": {
        # Lower temperature: steadier, more uniform delivery.
        "stability": ("temperature", 1.1, 0.9, 0.6),
        # More candidate tokens: more varied, expressive prosody.
        "style": ("top_k", 20, 50, 100),
    },
    "fish-s2-pro": {
        "stability": ("temperature", 1.0, 0.7, 0.45),
        "style": ("top_p", 0.5, 0.7, 0.95),
    },
}
# Qwen clones from the sample and its transcript (copies delivery closely) or,
# below this similarity, from the voice's timbre alone.
_QWEN_TIMBRE_ONLY_BELOW = 0.5

_SPEED_RANGE = (0.5, 2.0)
_PITCH_RANGE = (-12.0, 12.0)
_VOLUME_RANGE = (-12.0, 12.0)


def engine_controls(provider_id: str) -> list[str]:
    """The sliders that change this engine's output."""
    controls = list(_SAMPLING.get(provider_id, {}))
    if provider_id == "faster-qwen3-tts":
        controls.append("similarity")
    return [name for name in NEUTRAL if name in controls or name in AUDIO_CONTROLS]


def _value(settings: dict[str, Any], name: str) -> float:
    try:
        return float(settings.get(name, NEUTRAL[name]))
    except (TypeError, ValueError):
        return NEUTRAL[name]


def _at_default(settings: dict[str, Any], name: str) -> bool:
    return abs(_value(settings, name) - NEUTRAL[name]) < 0.005


def _interpolate(position: float, neutral: float, low: float, mid: float, high: float) -> float:
    position = min(1.0, max(0.0, position))
    if position <= neutral:
        return low + (mid - low) * (position / neutral if neutral else 1.0)
    return mid + (high - mid) * ((position - neutral) / (1.0 - neutral))


def sampling_overrides(provider_id: str, settings: dict[str, Any] | None) -> dict[str, Any]:
    """Engine generation parameters for the sliders that are off their defaults."""
    settings = settings or {}
    overrides: dict[str, Any] = {}
    for name, (parameter, low, mid, high) in _SAMPLING.get(provider_id, {}).items():
        if _at_default(settings, name):
            continue
        value = _interpolate(_value(settings, name), NEUTRAL[name], low, mid, high)
        overrides[parameter] = int(round(value)) if parameter == "top_k" else round(value, 3)
    if provider_id == "faster-qwen3-tts" and _value(settings, "similarity") < _QWEN_TIMBRE_ONLY_BELOW:
        overrides["xvec_only"] = True
    return overrides


def audio_adjustments(settings: dict[str, Any] | None) -> dict[str, float]:
    """Speed, pitch (semitones) and volume (dB) to apply, clamped; empty at the defaults."""
    settings = settings or {}
    ranges = {"speed": _SPEED_RANGE, "pitch": _PITCH_RANGE, "volume": _VOLUME_RANGE}
    return {name: min(ranges[name][1], max(ranges[name][0], _value(settings, name)))
            for name in AUDIO_CONTROLS if not _at_default(settings, name)}


def adjust_wav(wav_bytes: bytes, adjustments: dict[str, float]) -> bytes:
    """Apply speed, pitch and volume to 16-bit PCM WAV audio."""
    if not adjustments:
        return wav_bytes
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise ValueError("speed, pitch and volume need 16-bit PCM audio")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    speed = adjustments.get("speed", 1.0)
    semitones = adjustments.get("pitch", 0.0)
    if speed != 1.0 or semitones != 0.0:
        samples = change_speed_and_pitch(samples, rate, speed, semitones)
    if "volume" in adjustments:
        samples = samples * 10 ** (adjustments["volume"] / 20)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.99:  # Louder never clips: the gain stops at full scale.
        samples = samples * (0.99 / peak)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def change_speed_and_pitch(samples: np.ndarray, rate: int, speed: float, semitones: float) -> np.ndarray:
    """``speed`` times faster and ``semitones`` higher, each without changing the other."""
    ratio = 2 ** (semitones / 12)
    target_length = max(1, int(round(len(samples) / speed)))
    # Resampling by ``ratio`` raises the pitch and shortens the audio by the same
    # factor, so stretch first to the length that leaves ``target_length``.
    stretched = time_stretch(samples, rate, speed / ratio)
    if ratio == 1.0:
        return stretched
    return _resample(stretched, target_length)


def time_stretch(samples: np.ndarray, rate: int, factor: float) -> np.ndarray:
    """WSOLA: ``factor`` > 1 plays faster, at the same pitch.

    Overlap-adds 30 ms Hann frames, each shifted by up to 10 ms to line up with
    the waveform already written, which keeps speech free of phasing artifacts.
    """
    if abs(factor - 1.0) < 1e-6 or len(samples) == 0:
        return samples.copy()
    frame = max(16, int(rate * 0.03) // 2 * 2)
    hop = frame // 2
    tolerance = max(1, int(rate * 0.01))
    window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(frame) / frame)  # sums to 1 at 50% overlap
    output_length = max(1, int(round(len(samples) / factor)))
    padded = np.concatenate([np.zeros(tolerance + frame), samples, np.zeros(tolerance + 2 * frame)])
    offset = tolerance + frame
    output = np.zeros(output_length + frame)
    weight = np.zeros(output_length + frame)
    previous = None
    for index in range(math.ceil(output_length / hop) + 1):
        nominal = offset + int(round(index * hop * factor))
        if previous is None:
            start = nominal
        else:
            template = padded[previous + hop: previous + hop + frame]
            region = padded[nominal - tolerance: nominal + tolerance + frame]
            start = nominal - tolerance + int(np.argmax(np.correlate(region, template, mode="valid")))
        position = index * hop
        if position >= len(output):
            break
        piece = padded[start: start + frame] * window
        end = min(len(output), position + frame)
        output[position:end] += piece[: end - position]
        weight[position:end] += window[: end - position]
        previous = start
    nonzero = weight > 1e-3
    output[nonzero] /= weight[nonzero]
    return output[:output_length]


def _resample(samples: np.ndarray, length: int) -> np.ndarray:
    """Band-limited resampling to ``length`` samples (FFT method)."""
    spectrum = np.fft.rfft(samples)
    resized = np.zeros(length // 2 + 1, dtype=complex)
    keep = min(len(spectrum), len(resized))
    resized[:keep] = spectrum[:keep]
    return np.fft.irfft(resized, length) * (length / len(samples))


__all__ = [
    "AUDIO_CONTROLS",
    "NEUTRAL",
    "adjust_wav",
    "audio_adjustments",
    "change_speed_and_pitch",
    "engine_controls",
    "sampling_overrides",
    "time_stretch",
]
