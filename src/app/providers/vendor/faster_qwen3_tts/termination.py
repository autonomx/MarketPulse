"""Deterministic end-of-sequence policy for streaming codec generation."""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal

TerminationReason = Literal[
    "natural_eos",
    "forced_eos",
    "token_limit",
    "sequence_limit",
    "model_stopped",
]


@dataclass(frozen=True)
class StreamingEosPolicy:
    """Control how streaming generation approaches the phrase token budget."""

    bias_start_fraction: float = 0.70
    force_fraction: float = 0.90
    min_post_text_bias_steps: int = 8
    min_post_text_force_steps: int = 16
    bias_per_step: float = 0.5
    max_bias: float = 8.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.bias_start_fraction <= 1.0:
            raise ValueError("bias_start_fraction must be between 0 and 1")
        if not 0.0 < self.force_fraction <= 1.0:
            raise ValueError("force_fraction must be between 0 and 1")
        if self.force_fraction < self.bias_start_fraction:
            raise ValueError("force_fraction must be >= bias_start_fraction")
        if self.min_post_text_bias_steps < 0:
            raise ValueError("min_post_text_bias_steps must be non-negative")
        if self.min_post_text_force_steps < self.min_post_text_bias_steps:
            raise ValueError(
                "min_post_text_force_steps must be >= min_post_text_bias_steps"
            )
        if self.bias_per_step < 0:
            raise ValueError("bias_per_step must be non-negative")
        if self.max_bias < 0:
            raise ValueError("max_bias must be non-negative")


@dataclass(frozen=True)
class StreamingEosDeadlines:
    """Resolved absolute codec-step deadlines for one phrase."""

    bias_start_step: int
    force_step: int
    hard_limit_step: int


def post_text_steps(generation_step: int, text_context_steps: int) -> int:
    """Return how many codec steps have advanced beyond text alignment context."""

    return max(0, int(generation_step) - max(0, int(text_context_steps)))


def resolve_eos_deadlines(
    *,
    max_new_tokens: int,
    text_context_steps: int,
    policy: StreamingEosPolicy,
) -> StreamingEosDeadlines:
    """Resolve safe EOS deadlines from the text-relative phrase token budget."""

    hard_limit = max(1, int(max_new_tokens))
    text_steps = max(0, int(text_context_steps))

    bias_start = max(
        text_steps + policy.min_post_text_bias_steps,
        ceil(hard_limit * policy.bias_start_fraction),
    )
    force_step = max(
        text_steps + policy.min_post_text_force_steps,
        ceil(hard_limit * policy.force_fraction),
        bias_start + 1,
    )

    bias_start = min(max(0, hard_limit - 1), bias_start)
    force_step = min(hard_limit, max(bias_start + 1, force_step))
    return StreamingEosDeadlines(
        bias_start_step=bias_start,
        force_step=force_step,
        hard_limit_step=hard_limit,
    )


def eos_logit_bias(
    generation_step: int,
    deadlines: StreamingEosDeadlines,
    policy: StreamingEosPolicy,
) -> float:
    """Return the progressive EOS logit bias for the current generation step."""

    step = max(0, int(generation_step))
    if step < deadlines.bias_start_step:
        return 0.0
    raw_bias = (step - deadlines.bias_start_step + 1) * policy.bias_per_step
    return min(policy.max_bias, raw_bias)


def classify_after_sample(
    *,
    sampled_token_id: int,
    eos_token_id: int,
    generation_step: int,
    deadlines: StreamingEosDeadlines,
) -> TerminationReason | None:
    """Classify termination after sampling the next codec token."""

    if int(sampled_token_id) == int(eos_token_id):
        return "natural_eos"

    step = max(0, int(generation_step))
    if deadlines.force_step < deadlines.hard_limit_step and step >= deadlines.force_step:
        return "forced_eos"
    if step >= deadlines.hard_limit_step:
        return "token_limit"
    return None


def termination_metadata(
    *,
    reason: TerminationReason,
    generated_steps: int,
    generation_step: int,
    text_context_steps: int,
    eos_bias_applied: float,
    deadlines: StreamingEosDeadlines,
    policy: StreamingEosPolicy,
) -> dict[str, object]:
    """Build consistent final-chunk diagnostics for both decoder paths."""

    return {
        "termination_reason": reason,
        "generated_steps": max(0, int(generated_steps)),
        "generation_step": max(0, int(generation_step)),
        "text_context_steps": max(0, int(text_context_steps)),
        "post_text_steps": post_text_steps(generation_step, text_context_steps),
        "eos_bias_applied": float(eos_bias_applied),
        "eos_bias_start_step": deadlines.bias_start_step,
        "eos_force_step": deadlines.force_step,
        "hard_token_limit_step": deadlines.hard_limit_step,
        "eos_bias_start_fraction": policy.bias_start_fraction,
        "eos_force_fraction": policy.force_fraction,
    }
