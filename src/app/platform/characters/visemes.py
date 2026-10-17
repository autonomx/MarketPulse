"""Deterministic text-to-viseme planning for live Character Mode speech."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Viseme = Literal["silence", "A", "E", "O", "U", "MBP", "FV", "L", "WQ", "other"]


class TimedViseme(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    viseme: Viseme
    start_ms: float = Field(ge=0)
    duration_ms: float = Field(gt=0)


_TOKEN_PATTERN = re.compile(r"[a-z]+|[^a-z\s]", re.IGNORECASE)


def viseme_sequence(text: str) -> list[Viseme]:
    """Return a compact visual mouth sequence without claiming phoneme accuracy."""

    sequence: list[Viseme] = []
    for token in _TOKEN_PATTERN.findall(str(text or "")):
        lowered = token.lower()
        if not lowered.isalpha():
            _append_unique(sequence, "silence")
            continue
        index = 0
        while index < len(lowered):
            pair = lowered[index : index + 2]
            if pair in {"qu", "wh"}:
                _append_unique(sequence, "WQ")
                index += 2
                continue
            character = lowered[index]
            if character in "mbp":
                viseme: Viseme = "MBP"
            elif character in "fv":
                viseme = "FV"
            elif character == "l":
                viseme = "L"
            elif character in "wq":
                viseme = "WQ"
            elif character == "a":
                viseme = "A"
            elif character in "eiy":
                viseme = "E"
            elif character == "o":
                viseme = "O"
            elif character == "u":
                viseme = "U"
            else:
                viseme = "other"
            _append_unique(sequence, viseme)
            index += 1
        _append_unique(sequence, "silence")
    if not sequence:
        return ["silence"]
    if sequence[-1] != "silence":
        sequence.append("silence")
    return sequence


def fit_visemes(text: str, duration_ms: float) -> list[TimedViseme]:
    """Fit deterministic visual cues to an actual audio duration."""

    duration = max(1.0, float(duration_ms))
    sequence = viseme_sequence(text)
    weights = [0.45 if viseme == "silence" else 1.0 for viseme in sequence]
    total_weight = sum(weights) or 1.0
    cursor = 0.0
    result: list[TimedViseme] = []
    for index, (viseme, weight) in enumerate(zip(sequence, weights, strict=True)):
        cue_duration = duration - cursor if index == len(sequence) - 1 else duration * weight / total_weight
        result.append(TimedViseme(viseme=viseme, start_ms=cursor, duration_ms=max(1.0, cue_duration)))
        cursor += cue_duration
    return result


def _append_unique(sequence: list[Viseme], viseme: Viseme) -> None:
    if not sequence or sequence[-1] != viseme:
        sequence.append(viseme)


__all__ = ["TimedViseme", "Viseme", "fit_visemes", "viseme_sequence"]
