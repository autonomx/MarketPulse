"""Phase 9.0 — Save migration v3 to v4.

Adds inventory_state to player_state for Phase 9.0 compatibility.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def migrate_v3_to_v4(package: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate a Phase 8.5 (schema v3) save to Phase 9.0 (schema v4).

    Adds the new ``inventory_state`` subtree under ``player_state``.
    """
    package = dict(package or {})
    state = _safe_dict(package.get("state"))
    simulation_state = _safe_dict(state.get("simulation_state"))
    player_state = _safe_dict(simulation_state.get("player_state"))

    player_state.setdefault("inventory_state", {
        "items": [],
        "equipment": {},
        "capacity": 50,
        "currency": {},
        "last_loot": [],
    })

    simulation_state["player_state"] = player_state
    state["simulation_state"] = simulation_state
    package["state"] = state
    package["schema_version"] = 4
    return package