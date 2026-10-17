"""Phase 10 — Speaker card builders.

Provides deterministic speaker card building for scene and dialogue presentation.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.party import build_companion_presence_summary

from .personality import build_personality_style_tags
from .personality_state import get_actor_personality_profile
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_KIND_ORDER = {
    "player": 0,
    "companion": 1,
}


def build_speaker_cards(simulation_state: Dict[str, Any], scene_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build speaker cards for a scene, including player and present companions.

    Returns a sorted list of cards by kind then speaker_id.
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    scene_state = _safe_dict(scene_state)

    cards: List[Dict[str, Any]] = []

    # Always include the player card
    cards.append({
        "speaker_id": "player",
        "name": "Player",
        "kind": "player",
        "portrait_key": "player",
        "style_tags": ["player"],
        "location_id": _safe_str(scene_state.get("location_id")),
    })

    # Build cards for present companions
    presence = build_companion_presence_summary(player_state)
    for comp in _safe_list(presence.get("present_companions")):
        if not isinstance(comp, dict):
            continue
        profile = get_actor_personality_profile(
            simulation_state,
            _safe_str(comp.get("npc_id")),
            default_name=_safe_str(comp.get("name")),
        )
        style_tags = _safe_list(profile.get("style_tags")) or build_personality_style_tags(comp)
        cards.append({
            "speaker_id": _safe_str(comp.get("npc_id")),
            "name": _safe_str(profile.get("display_name") or comp.get("name")),
            "kind": "companion",
            "portrait_key": _safe_str(comp.get("npc_id")),
            "style_tags": style_tags,
            "location_id": _safe_str(scene_state.get("location_id")),
        })

    return sorted(
        cards,
        key=lambda c: (
            _KIND_ORDER.get(_safe_str(c.get("kind")), 99),
            _safe_str(c.get("speaker_id")),
        ),
    )


def build_party_speaker_cards(simulation_state: Dict[str, Any], companions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build minimal speaker cards for a list of companion dicts.

    Used when you need only companion cards, not the full speaker set.
    """
    simulation_state = _safe_dict(simulation_state)
    cards: List[Dict[str, Any]] = []
    for comp in _safe_list(companions):
        if not isinstance(comp, dict):
            continue
        if _safe_str(comp.get("status")) != "active":
            continue
        profile = get_actor_personality_profile(
            simulation_state,
            _safe_str(comp.get("npc_id")),
            default_name=_safe_str(comp.get("name")),
        )
        style_tags = _safe_list(profile.get("style_tags")) or build_personality_style_tags(comp)
        cards.append({
            "speaker_id": _safe_str(comp.get("npc_id")),
            "name": _safe_str(profile.get("display_name") or comp.get("name")),
            "kind": "companion",
            "portrait_key": _safe_str(comp.get("npc_id")),
            "style_tags": style_tags,
            "location_id": _safe_str(comp.get("location_id")),
        })
    cards = sorted(
        cards,
        key=lambda c: (
            _KIND_ORDER.get(_safe_str(c.get("kind")), 99),
            _safe_str(c.get("speaker_id")),
        ),
    )
    return cards[:6]


def build_nearby_npc_cards(simulation_state: dict, scene: dict) -> list:
    """Build dynamic NPC presence cards from scene and simulation state."""
    simulation_state = dict(simulation_state or {})
    scene = dict(scene or {})
    cards = []

    # Gather present NPC IDs from scene
    present_ids = list(scene.get("present_npc_ids", []))
    player_state = dict(simulation_state.get("player_state") or {})
    nearby_ids = list(player_state.get("nearby_npc_ids", []))

    # Merge both lists, dedupe
    all_ids = list(dict.fromkeys(present_ids + nearby_ids))

    # Build NPC index from simulation state
    npcs = simulation_state.get("npcs", [])
    if isinstance(npcs, dict):
        npc_index = npcs
    else:
        npc_index = {}
        for npc in (npcs if isinstance(npcs, list) else []):
            if isinstance(npc, dict):
                npc_id = str(npc.get("npc_id") or npc.get("id") or "")
                if npc_id:
                    npc_index[npc_id] = npc

    # Also check npc_seeds
    for npc in (simulation_state.get("npc_seeds") or []):
        if isinstance(npc, dict):
            npc_id = str(npc.get("npc_id") or npc.get("id") or "")
            if npc_id and npc_id not in npc_index:
                npc_index[npc_id] = npc

    for npc_id in all_ids[:12]:
        npc_id = str(npc_id)
        npc_data = dict(npc_index.get(npc_id, {}))
        card = {
            "npc_id": npc_id,
            "name": str(npc_data.get("name") or npc_id),
            "role": str(npc_data.get("role") or npc_data.get("archetype") or ""),
            "faction": str(npc_data.get("faction") or npc_data.get("faction_id") or ""),
            "portrait": str(npc_data.get("portrait") or ""),
            "attitude_to_player": str(npc_data.get("attitude_to_player") or npc_data.get("disposition") or "neutral"),
            "status_summary": str(npc_data.get("status_summary") or npc_data.get("status") or ""),
            "location_name": str(npc_data.get("location_name") or npc_data.get("location") or ""),
            "is_present": npc_id in present_ids,
            "relationship_tags": list(npc_data.get("relationship_tags") or [])[:5],
        }
        cards.append(card)

    return cards
