"""Phase 8 — Player-facing dialogue mode transitions.

Controls entering and exiting dialogue mode, updating the lightweight
player-facing state without mutating the authoritative simulation.
"""
from __future__ import annotations

from typing import Any, Dict

from .player_scene_state import ensure_player_state
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


def enter_dialogue_mode(
    simulation_state: Dict[str, Any],
    npc_id: str,
    scene_id: str = "",
) -> Dict[str, Any]:
    """Mark the player as being in dialogue with *npc_id*.

    If *scene_id* is provided it becomes the current scene context.
    """
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    dialogue_state = player_state.setdefault("dialogue_state", {})
    dialogue_state.setdefault("active", False)
    dialogue_state.setdefault("npc_id", "")
    dialogue_state.setdefault("scene_id", "")
    dialogue_state.setdefault("turn_index", 0)
    dialogue_state.setdefault("history", [])
    dialogue_state.setdefault("suggested_replies", [])
    player_state["current_mode"] = "dialogue"
    player_state["active_npc_id"] = _safe_str(npc_id)
    if scene_id:
        player_state["current_scene_id"] = _safe_str(scene_id)
    dialogue_state["active"] = True
    dialogue_state["npc_id"] = _safe_str(npc_id)
    dialogue_state["scene_id"] = _safe_str(scene_id)
    return simulation_state


def exit_dialogue_mode(
    simulation_state: Dict[str, Any],
    fallback_mode: str = "scene",
) -> Dict[str, Any]:
    """Leave dialogue mode and return to *fallback_mode* (default: "scene")."""
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    dialogue_state = player_state.setdefault("dialogue_state", {})
    player_state["current_mode"] = _safe_str(fallback_mode) or "scene"
    player_state["active_npc_id"] = ""
    dialogue_state["active"] = False
    dialogue_state["npc_id"] = ""
    dialogue_state["scene_id"] = ""
    dialogue_state["suggested_replies"] = []
    return simulation_state
