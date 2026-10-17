"""Phase 9.3 — Migration v6 to v7.

Adds party narrative state (history, last_interjection, last_scene_reactions)
without breaking existing party data.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def _safe_str(v: Any, default: str = "") -> str:
    return default if v is None else str(v)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def migrate_v6_to_v7(package: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate package from schema v6 to v7.

    Adds narrative_state to party_state while preserving all existing data.
    """
    package = dict(package or {})
    state = _safe_dict(package.get("state"))
    simulation_state = _safe_dict(state.get("simulation_state"))
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_state = _safe_dict(player_state.get("party_state"))

    # Preserve existing companions
    companions = []
    for comp in _safe_list(party_state.get("companions")):
        if not isinstance(comp, dict):
            continue
        companions.append({
            "npc_id": _safe_str(comp.get("npc_id")),
            "name": _safe_str(comp.get("name") or comp.get("npc_id") or "Companion"),
            "hp": _safe_int(comp.get("hp"), 100),
            "max_hp": _safe_int(comp.get("max_hp"), 100),
            "loyalty": float(comp.get("loyalty", 0.5)),
            "morale": float(comp.get("morale", 0.5)),
            "role": _safe_str(comp.get("role") or "ally"),
            "status": _safe_str(comp.get("status") or "active"),
            "equipment": _safe_dict(comp.get("equipment")),
        })

    party_state["companions"] = companions[:6]
    party_state.setdefault("max_size", 3)

    # Add narrative_state
    narrative_state = _safe_dict(party_state.get("narrative_state"))
    narrative_state.setdefault("history", [])
    narrative_state.setdefault("last_interjection", {})
    narrative_state.setdefault("last_scene_reactions", [])
    party_state["narrative_state"] = narrative_state

    player_state["party_state"] = party_state
    simulation_state["player_state"] = player_state
    state["simulation_state"] = simulation_state
    package["state"] = state
    package["schema_version"] = 7
    return package