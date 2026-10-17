"""Phase 10 — Personality style system.

Provides deterministic personality style tags and prompt hints
for NPCs and companions based on their current state.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.prompts import prompt_template
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

_PROMPT_1 = prompt_template('rpg.presentation_personality.build_personality_prompt_hints', "1", "Use concise, character-consistent phrasing grounded in current scene context.")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def build_personality_style_tags(actor: Dict[str, Any]) -> List[str]:
    """Build deterministic style tags from actor state.

    Uses loyalty, morale, and role to produce consistent tags.
    """
    actor = _safe_dict(actor)
    loyalty = _safe_float(actor.get("loyalty"), 0.0)
    morale = _safe_float(actor.get("morale"), 0.5)
    role = _safe_str(actor.get("role") or "ally")

    tags: List[str] = []

    if loyalty < -0.3:
        tags.append("cold")
    elif loyalty > 0.6:
        tags.append("supportive")
    else:
        tags.append("neutral")

    if morale < 0.3:
        tags.append("shaken")
    elif morale > 0.7:
        tags.append("confident")

    if role == "support":
        tags.append("careful")
    elif role == "guard":
        tags.append("protective")
    elif role == "scout":
        tags.append("alert")

    return tags[:6]


def build_personality_prompt_hints(actor: Dict[str, Any]) -> Dict[str, Any]:
    """Build personality prompt hints for LLM-guided dialogue."""
    actor = _safe_dict(actor)
    name = _safe_str(actor.get("name") or actor.get("npc_id") or "Unknown")
    tags = build_personality_style_tags(actor)

    return {
        "name": name,
        "style_tags": tags,
        "speech_guidance": _PROMPT_1.text,
    }