"""Book and character performance: the Stability and Style controls for audiobook renders.

A project stores them in ``settings.performance``::

    {"stability": 0.6, "style": 0.5,
     "speakers": {"<speaker uuid>": {"stability": 0.3}}}

A character's own values override the book's. They map onto the TTS engine's
sampling exactly as Voice Studio's sliders do (``platform.voice.output_settings``),
and at the default positions they add nothing, so existing renders keep their
cache keys. Render and preview jobs pin the resulting generation parameters in
their payload, so a run renders with the values it started with.
"""
from __future__ import annotations

from typing import Any

from app.platform.voice.output_settings import NEUTRAL, sampling_overrides

CONTROLS = ("stability", "style")


def _level(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number between 0 and 1") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return round(number, 3)


def normalize_performance(value: Any) -> dict[str, Any]:
    """A validated performance setting; book values default to the neutral positions."""
    value = value if isinstance(value, dict) else {}
    book = {name: _level(value.get(name, NEUTRAL[name]), name) for name in CONTROLS}
    speakers: dict[str, dict[str, float]] = {}
    raw_speakers = value.get("speakers") if isinstance(value.get("speakers"), dict) else {}
    for speaker_id, raw in raw_speakers.items():
        if not isinstance(raw, dict):
            raise ValueError("a character's performance must be an object")
        own = {name: _level(raw[name], name) for name in CONTROLS if raw.get(name) is not None}
        if own:
            speakers[str(speaker_id)] = own
    return {**book, "speakers": speakers}


def speaker_levels(performance: dict[str, Any], speaker_id: str) -> dict[str, float]:
    """A character's effective Stability and Style: its own values, else the book's."""
    performance = normalize_performance(performance)
    return {**{name: performance[name] for name in CONTROLS},
            **performance["speakers"].get(speaker_id, {})}


def render_generation_parameters(
    provider_id: str, performance: Any, base: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The job's generation parameters and those of each character with its own values.

    Each character's parameters are complete (``base`` plus its own effective
    levels), not a delta on the book's, so a character set back to a default
    position does not inherit the book's change.
    """
    performance = normalize_performance(performance)
    base = dict(base or {})
    book = {**base, **sampling_overrides(provider_id, performance)}
    speakers = {
        speaker_id: {**base, **sampling_overrides(provider_id, speaker_levels(performance, speaker_id))}
        for speaker_id in performance["speakers"]
    }
    return book, {speaker_id: params for speaker_id, params in speakers.items() if params != book}


__all__ = [
    "CONTROLS",
    "normalize_performance",
    "render_generation_parameters",
    "speaker_levels",
]
