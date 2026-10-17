"""Phase 10 — Deterministic fallback builders.

Provides fallback dialogue/scene text when LLM is unavailable.
"""
from __future__ import annotations

from typing import Any, Dict

from .personality import build_personality_style_tags
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str


def build_deterministic_dialogue_fallback(actor_profile: Dict[str, Any], dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a deterministic dialogue fallback response.

    Used when LLM is unavailable or fails.
    """
    actor_profile = _safe_dict(actor_profile)
    dialogue_state = _safe_dict(dialogue_state)
    name = _safe_str(actor_profile.get("display_name") or actor_profile.get("actor_id") or "Speaker")
    tone = _safe_str(actor_profile.get("tone") or "")
    topic = _safe_str(dialogue_state.get("topic") or "the situation")

    if not tone or tone == "neutral":
        style_tags = build_personality_style_tags(actor_profile)
        if "cold" in style_tags:
            tone = "wary"
        elif "supportive" in style_tags:
            tone = "warm"
        elif "shaken" in style_tags:
            tone = "wary"
        else:
            tone = "neutral"

    if tone == "warm":
        text = f"{name} responds calmly about {topic}."
    elif tone == "stern":
        text = f"{name} answers in a firm, restrained way about {topic}."
    elif tone == "wary":
        text = f"{name} responds cautiously, measuring every word about {topic}."
    else:
        text = f"{name} responds about {topic}."

    return {
        "ok": True,
        "source": "deterministic_fallback",
        "text": text,
    }


def build_deterministic_scene_fallback(scene_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a deterministic scene fallback response.

    Used when LLM is unavailable or fails.
    """
    scene_state = _safe_dict(scene_state)
    tone = _safe_str(scene_state.get("tone") or "neutral")
    location_id = _safe_str(scene_state.get("location_id") or "the area")
    companion_name = _safe_str(scene_state.get("lead_companion_name") or "")

    if tone == "tense":
        text = f"Tension lingers over {location_id} as everyone measures the next move."
    elif tone == "calm":
        text = f"The scene at {location_id} settles into a quieter rhythm."
    else:
        text = f"The scene continues at {location_id}."

    if companion_name:
        text = f"{text} {companion_name} watches closely."

    return {
        "ok": True,
        "source": "deterministic_fallback",
        "text": text,
    }