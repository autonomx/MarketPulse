"""Phase 10 — Scene presentation builder.

Builds a presentation-ready payload for a scene, including
speaker cards, companion interjections, and reactions.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.rules.party import (
    build_companion_presence_summary,
    build_companion_scene_context,
    build_companion_scene_reactions,
    choose_scene_interjections,
)

from .dialogue_fallbacks import build_deterministic_scene_fallback
from .speaker_cards import build_speaker_cards
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def build_scene_presentation_payload(simulation_state: Dict[str, Any], scene_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a presentation-ready payload for a scene.

    This is a pure builder: it does not mutate simulation_state.
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    scene_state = _safe_dict(scene_state)

    scene_context = build_companion_scene_context(simulation_state, scene_state)
    interjections = choose_scene_interjections(simulation_state, scene_state)
    reactions = build_companion_scene_reactions(player_state, scene_state)
    presence_summary = build_companion_presence_summary(player_state)
    speaker_cards = build_speaker_cards(simulation_state, scene_state)
    present_companions = _safe_list(presence_summary.get("present_companions"))
    lead_companion_name = ""
    if present_companions:
        lead_companion_name = str((present_companions[0] or {}).get("name") or "")
    fallback = build_deterministic_scene_fallback({
        **scene_state,
        "lead_companion_name": lead_companion_name,
    })

    return {
        "scene_id": scene_state.get("scene_id"),
        "tone": scene_state.get("tone"),
        "location_id": scene_state.get("location_id"),
        "scene_context": scene_context,
        "speaker_cards": speaker_cards,
        "companion_interjections": _safe_list(interjections)[:3],
        "companion_reactions": _safe_list(reactions)[:4],
        "presence_summary": presence_summary,
        "fallback_text": fallback.get("text"),
    }
