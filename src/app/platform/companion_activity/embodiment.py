"""Renderer-independent semantic embodiment planning."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .cognition import DeliveryIntent, DeliveryIntentKind
from .contracts import FrozenContract

SemanticEmbodimentState = Literal[
    "neutral",
    "engaged",
    "curious",
    "focused",
    "celebrating",
    "alert",
]


class RendererCapabilities(FrozenContract):
    expressions: tuple[str, ...] = ()
    motions: tuple[str, ...] = ()
    supports_intensity: bool = False


class SemanticEmbodimentCue(FrozenContract):
    semantic_state: SemanticEmbodimentState
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)


class EmbodimentPlan(FrozenContract):
    semantic_state: SemanticEmbodimentState
    intensity: float = Field(ge=0.0, le=1.0)
    expression: str | None = Field(default=None, max_length=120)
    motion: str | None = Field(default=None, max_length=120)
    expose_intensity: bool = False


_DEFAULT_EXPRESSION_PREFERENCES: dict[SemanticEmbodimentState, tuple[str, ...]] = {
    "neutral": ("neutral", "idle"),
    "engaged": ("engaged", "smile", "happy"),
    "curious": ("curious", "question", "interested"),
    "focused": ("focused", "serious", "thinking"),
    "celebrating": ("celebrating", "happy", "smile", "excited"),
    "alert": ("alert", "concerned", "serious"),
}
_DEFAULT_MOTION_PREFERENCES: dict[SemanticEmbodimentState, tuple[str, ...]] = {
    "neutral": ("idle",),
    "engaged": ("nod", "idle"),
    "curious": ("tilt", "look", "nod"),
    "focused": ("focus", "nod"),
    "celebrating": ("cheer", "celebrate", "wave"),
    "alert": ("alert", "warn", "focus"),
}


class CompanionEmbodimentMapper:
    """Map semantic companion behavior onto whatever the installed renderer supports."""

    def cue_for_intent(self, intent: DeliveryIntent) -> SemanticEmbodimentCue:
        state = _semantic_state(intent.kind)
        base = {
            "IGNORE": 0.0,
            "REACT": 0.55,
            "ASK": 0.55,
            "ADVISE": 0.65,
            "CELEBRATE": 0.9,
            "WARN": 1.0,
            "RESUME": 0.5,
        }[intent.kind]
        intensity = min(1.0, max(base, intent.salience)) if intent.kind != "IGNORE" else 0.0
        return SemanticEmbodimentCue(semantic_state=state, intensity=intensity)

    def map(
        self,
        cue: SemanticEmbodimentCue,
        capabilities: RendererCapabilities,
    ) -> EmbodimentPlan:
        return EmbodimentPlan(
            semantic_state=cue.semantic_state,
            intensity=cue.intensity,
            expression=_first_supported(
                _DEFAULT_EXPRESSION_PREFERENCES[cue.semantic_state],
                capabilities.expressions,
            ),
            motion=_first_supported(
                _DEFAULT_MOTION_PREFERENCES[cue.semantic_state],
                capabilities.motions,
            ),
            expose_intensity=capabilities.supports_intensity,
        )


def _semantic_state(kind: DeliveryIntentKind) -> SemanticEmbodimentState:
    states: dict[str, SemanticEmbodimentState] = {
        "IGNORE": "neutral",
        "REACT": "engaged",
        "ASK": "curious",
        "ADVISE": "focused",
        "CELEBRATE": "celebrating",
        "WARN": "alert",
        "RESUME": "engaged",
    }
    return states[kind]


def _first_supported(preferred: tuple[str, ...], supported: tuple[str, ...]) -> str | None:
    supported_index = {value.casefold(): value for value in supported}
    for candidate in preferred:
        match = supported_index.get(candidate.casefold())
        if match is not None:
            return match
    return None


__all__ = [
    "CompanionEmbodimentMapper",
    "EmbodimentPlan",
    "RendererCapabilities",
    "SemanticEmbodimentCue",
    "SemanticEmbodimentState",
]
