"""How much audio a passage may take, so a speech model that fails to stop is caught.

Qwen3-TTS sometimes never emits its end-of-speech token and generates low-level noise
until ``max_new_tokens`` runs out: about 163 seconds at the default 2048 steps. Text with
nothing to pronounce ("—", "“…”") does this far more often. Each request therefore gets a
token budget sized to its text, and audio that used up its budget is treated as a model
failure rather than speech.
"""
from __future__ import annotations

import math

# The Qwen3-TTS 12 Hz tokenizer emits one codec step per 80 ms of audio. Another
# engine reports its own rate as ``codec_steps_per_second`` in its resolved
# generation parameters (Fish S2 Pro: about 21.5).
CODEC_STEPS_PER_SECOND = 12.5
DEFAULT_MAX_NEW_TOKENS = 2048
# Rendered books narrate at a median of 15 characters a second, and no finished
# passage used more than 61% of this allowance; runaways overshoot it many times over.
_SLACK_SECONDS = 4.0
_SLOWEST_CHARACTERS_PER_SECOND = 6.0
# Audio this close to its budget ran into the cap instead of ending on its own.
_EXHAUSTED_FRACTION = 0.95


def has_speakable_text(text: str) -> bool:
    """Whether the text has anything to pronounce: a letter or a digit in any script."""
    return any(character.isalnum() for character in text)


def speech_token_budget(text: str, max_new_tokens: int | None = None,
                        steps_per_second: float = CODEC_STEPS_PER_SECOND) -> int:
    """Codec steps a passage may use: generous for slow delivery, never above the configured cap."""
    ceiling = int(max_new_tokens or DEFAULT_MAX_NEW_TOKENS)
    characters = len(" ".join(text.split()))
    seconds = _SLACK_SECONDS + characters / _SLOWEST_CHARACTERS_PER_SECOND
    return max(1, min(ceiling, math.ceil(seconds * steps_per_second)))


def exhausted_speech_budget(duration_seconds: float, token_budget: int,
                            steps_per_second: float = CODEC_STEPS_PER_SECOND) -> bool:
    """Whether audio of this length ran out its budget instead of ending on its own."""
    return duration_seconds >= _EXHAUSTED_FRACTION * token_budget / steps_per_second
