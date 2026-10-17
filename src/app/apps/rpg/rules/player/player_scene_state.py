"""Phase 8 — Player-facing scene state management.

Provides deterministic, serialisable player-facing state that sits on top
of the authoritative simulation state.

Key guarantees:
    - scene_history is bounded to 50 entries
    - all values are safe (str / dict / list) even if upstream data is dirty
    - state updates are pure functions returning a new simulation_state dict
"""
from __future__ import annotations

from typing import Any, Dict

from .player_progression_state import ensure_player_progression_state
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_SCENE_HISTORY = 50


# ---------------------------------------------------------------------------
# Safe-cast helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure *simulation_state* has a well-formed ``player_state`` subtree.

    Idempotent — safe to call multiple times.
    """
    simulation_state = dict(simulation_state or {})
    player_state = simulation_state.setdefault("player_state", {})
    player_state.setdefault("current_scene_id", "")
    player_state.setdefault("current_mode", "scene")
    player_state.setdefault("active_npc_id", "")
    player_state.setdefault("scene_history", [])
    player_state.setdefault("journal_entries", [])
    player_state.setdefault("codex", {
        "npcs": {},
        "factions": {},
        "locations": {},
        "threads": {},
    })
    player_state.setdefault("active_objectives", [])
    player_state.setdefault("last_player_view", {})
    player_state.setdefault("inventory_state", {
        "items": [],
        "equipment": {},
        "capacity": 50,
        "currency": {},
        "last_loot": [],
    })

    player_state.setdefault("party_state", {
        "companions": [],
        "max_size": 3,
    })

    # Phase 18.3A — progression, equipment, and action fields
    player_state = ensure_player_progression_state(player_state)
    player_state.setdefault("nearby_npc_ids", [])
    player_state.setdefault("equipped_weapon_slot", "")
    player_state.setdefault("equipped_armor_slots", [])
    player_state.setdefault("available_checks", [])
    simulation_state["player_state"] = player_state
    return simulation_state


def push_scene_history(simulation_state: Dict[str, Any], scene: Dict[str, Any]) -> Dict[str, Any]:
    """Append a compact record to the player's scene history, trimming to cap."""
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    scene = _safe_dict(scene)

    record = {
        "scene_id": _safe_str(scene.get("scene_id")),
        "title": _safe_str(scene.get("title")),
        "type": _safe_str(scene.get("scene_type") or scene.get("type")),
        "tick": int(simulation_state.get("tick", 0) or 0),
    }

    history = _safe_list(player_state.get("scene_history"))
    history.append(record)
    history = history[-_MAX_SCENE_HISTORY:]
    player_state["scene_history"] = history
    return simulation_state


def set_current_scene(
    simulation_state: Dict[str, Any],
    scene: Dict[str, Any],
    mode: str = "scene",
    active_npc_id: str = "",
) -> Dict[str, Any]:
    """Update the player's current scene and snapshot a player-facing view."""
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    scene = _safe_dict(scene)

    player_state["current_scene_id"] = _safe_str(scene.get("scene_id"))
    player_state["current_mode"] = _safe_str(mode) or "scene"
    player_state["active_npc_id"] = _safe_str(active_npc_id)
    player_state["last_player_view"] = {
        "scene_id": _safe_str(scene.get("scene_id")),
        "scene_title": _safe_str(scene.get("title")),
        "scene_type": _safe_str(scene.get("scene_type") or scene.get("type")),
        "actors": [
            actor if isinstance(actor, dict) else {"id": _safe_str(actor)}
            for actor in _safe_list(scene.get("actors"))[:8]
        ],
        "choices": [
            dict(choice)
            for choice in _safe_list(scene.get("choices"))[:8]
            if isinstance(choice, dict)
        ],
    }
    return push_scene_history(simulation_state, scene)