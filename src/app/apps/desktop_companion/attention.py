"""Explainable Wallie-inspired attention policy for desktop observations."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Literal

from .models import (
    CompanionAttentionDecision,
    CompanionReaction,
    DesktopCompanionPolicy,
    DesktopObservation,
)

AttentionSelectionMode = Literal["deterministic", "organic"]
_REACTION_ORDER: tuple[CompanionReaction, ...] = (
    "ignore",
    "observe_silently",
    "glance",
    "deep",
)


@dataclass(frozen=True, slots=True)
class DesktopAttentionContext:
    scene_age_seconds: float = 0.0
    seconds_since_comment: float | None = None
    visual_reaction_streak: int = 0
    ignored_streak: int = 0
    user_floor_active: bool = False
    assistant_busy: bool = False
    request_in_flight: bool = False
    commentary_enabled: bool = True
    speech_muted: bool = False
    selection_mode: AttentionSelectionMode = "deterministic"


def decide_desktop_attention(
    observation: DesktopObservation,
    *,
    policy: DesktopCompanionPolicy,
    context: DesktopAttentionContext = DesktopAttentionContext(),
) -> CompanionAttentionDecision:
    """Choose ignore, silent observation, glance, or deep commentary.

    The default path selects the highest score deterministically. Organic mode
    uses a stable observation/session seed and therefore remains replayable.
    """

    scores: dict[CompanionReaction, float] = {
        "ignore": 0.34,
        "observe_silently": 0.30,
        "glance": 0.24,
        "deep": 0.12,
    }
    rationale: list[str] = []
    activity = observation.activity
    behavior = observation.behavior
    scene_confidence = observation.current_scene.confidence
    confidence = max(
        scene_confidence,
        max((item.confidence for item in observation.visible_changes), default=0.0),
        max((item.confidence for item in observation.possible_events), default=0.0),
    )

    if observation.is_stale():
        return _decision(
            reaction="ignore",
            scores=scores,
            rationale="observation_stale",
            policy=policy,
            context=context,
            should_generate=False,
            should_deliver=False,
        )

    if activity.activity in {"static", "micro_change", "unknown"}:
        scores["ignore"] += 0.75
        scores["observe_silently"] += 0.25
        scores["glance"] *= 0.35
        scores["deep"] *= 0.15
        rationale.append("low_value_activity")
    if activity.hypothesis == "likely_typing" and behavior.likely_typing:
        scores["ignore"] += 1.0
        scores["observe_silently"] += 0.5
        scores["glance"] *= 0.2
        scores["deep"] *= 0.1
        rationale.append("user_likely_typing")
    if behavior.rapid_browsing:
        scores["ignore"] += 0.75
        scores["observe_silently"] += 0.4
        scores["glance"] *= 0.45
        scores["deep"] *= 0.2
        rationale.append("rapid_browsing")
    if behavior.current_pattern == "settled" and activity.hypothesis != "likely_typing":
        scores["glance"] += 0.2
        scores["deep"] += 0.15
        scores["ignore"] *= 0.75
        rationale.append("user_settled")

    if observation.change_kind == "scene_change":
        scores["deep"] += 0.45
        scores["glance"] += 0.2
        scores["ignore"] *= 0.65
        rationale.append("scene_change")
    elif observation.change_kind == "delta":
        scores["glance"] += 0.12
        scores["ignore"] += 0.08
        rationale.append("scene_delta")

    importance = observation.importance
    scores["deep"] += importance * 0.55
    scores["glance"] += importance * 0.30
    if importance >= 0.75:
        scores["ignore"] *= 0.55
        rationale.append("high_importance")
    elif importance < 0.3:
        scores["ignore"] += 0.35
        scores["observe_silently"] += 0.2
        rationale.append("low_importance")

    if confidence < 0.45:
        scores["ignore"] += 0.45
        scores["observe_silently"] += 0.35
        scores["deep"] *= 0.3
        rationale.append("low_confidence")
    elif confidence >= 0.8:
        scores["glance"] += 0.15
        scores["deep"] += 0.15
        rationale.append("strong_visual_support")

    if context.scene_age_seconds > 45:
        scores["ignore"] += 0.3
        scores["observe_silently"] += 0.15
        scores["deep"] *= 0.55
        rationale.append("old_scene")
    elif context.scene_age_seconds < 6 and observation.change_kind == "scene_change":
        scores["deep"] += 0.1
        rationale.append("new_scene")

    if context.seconds_since_comment is not None:
        cooldown_seconds = policy.commentary_cooldown_ms / 1000
        if context.seconds_since_comment < cooldown_seconds:
            ratio = 1 - max(0.0, context.seconds_since_comment) / cooldown_seconds
            scores["ignore"] += 0.65 * ratio
            scores["observe_silently"] += 0.55 * ratio
            scores["glance"] *= max(0.15, 1 - ratio * 0.8)
            scores["deep"] *= max(0.08, 1 - ratio * 0.9)
            rationale.append("commentary_cooldown")

    if context.visual_reaction_streak >= 2:
        pressure = min(1.0, context.visual_reaction_streak / 5)
        scores["ignore"] += 0.5 * pressure
        scores["observe_silently"] += 0.45 * pressure
        scores["glance"] *= max(0.35, 1 - pressure * 0.55)
        scores["deep"] *= max(0.2, 1 - pressure * 0.7)
        rationale.append("reaction_streak")
    if context.ignored_streak >= 3 and importance >= 0.5:
        scores["ignore"] *= 0.45
        scores["glance"] += 0.25
        scores["deep"] += 0.2
        rationale.append("ignored_streak_relief")

    floor_blocked = context.user_floor_active or context.assistant_busy or context.request_in_flight
    if floor_blocked:
        scores["observe_silently"] += 2.0
        scores["glance"] *= 0.05
        scores["deep"] *= 0.05
        rationale.append("conversation_floor_busy")

    normalized = _normalize_scores(scores)
    reaction = _select_reaction(
        normalized,
        observation=observation,
        policy=policy,
        mode=context.selection_mode,
    )
    should_generate = reaction in {"glance", "deep"}
    should_deliver = should_generate and not floor_blocked
    if policy.shadow_mode:
        should_generate = False
        should_deliver = False
        rationale.append("shadow_mode")
    if not context.commentary_enabled:
        should_generate = False
        should_deliver = False
        rationale.append("commentary_disabled")
    if context.speech_muted:
        should_deliver = False
        rationale.append("speech_muted")

    target_sentences = 1 if reaction == "glance" else 3 if reaction == "deep" else 0
    priority = "critical" if observation.importance >= 0.9 and confidence >= 0.85 else "normal" if should_generate else "background"
    return CompanionAttentionDecision(
        reaction=reaction,
        should_generate=should_generate,
        should_deliver=should_deliver,
        target_sentences=target_sentences,
        priority=priority,
        rationale=",".join(rationale)[:240] or "base_attention_weights",
        scores=normalized,
        policy_version=policy.attention_policy_version,
        eligible_in_ms=0,
    )


def _select_reaction(
    scores: dict[CompanionReaction, float],
    *,
    observation: DesktopObservation,
    policy: DesktopCompanionPolicy,
    mode: AttentionSelectionMode,
) -> CompanionReaction:
    if mode == "deterministic":
        return max(_REACTION_ORDER, key=lambda reaction: (scores[reaction], -_REACTION_ORDER.index(reaction)))
    material = f"{policy.attention_seed}:{observation.session_id}:{observation.observation_id}"
    seed = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    threshold = rng.random()
    cumulative = 0.0
    for reaction in _REACTION_ORDER:
        cumulative += scores[reaction]
        if threshold <= cumulative:
            return reaction
    return _REACTION_ORDER[-1]


def _normalize_scores(scores: dict[CompanionReaction, float]) -> dict[CompanionReaction, float]:
    bounded = {key: max(0.001, value) for key, value in scores.items()}
    total = sum(bounded.values())
    return {key: round(value / total, 6) for key, value in bounded.items()}


def _decision(
    *,
    reaction: CompanionReaction,
    scores: dict[CompanionReaction, float],
    rationale: str,
    policy: DesktopCompanionPolicy,
    context: DesktopAttentionContext,
    should_generate: bool,
    should_deliver: bool,
) -> CompanionAttentionDecision:
    return CompanionAttentionDecision(
        reaction=reaction,
        should_generate=should_generate,
        should_deliver=should_deliver and not context.speech_muted,
        target_sentences=0,
        priority="background",
        rationale=rationale,
        scores=_normalize_scores(scores),
        policy_version=policy.attention_policy_version,
    )


__all__ = ["DesktopAttentionContext", "decide_desktop_attention"]
