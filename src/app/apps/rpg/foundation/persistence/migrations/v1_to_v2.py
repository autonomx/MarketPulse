from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def migrate_v1_to_v2(package: Dict[str, Any]) -> Dict[str, Any]:
    package = dict(package or {})
    state = _safe_dict(package.get("state"))
    simulation_state = _safe_dict(state.get("simulation_state"))

    simulation_state.setdefault("player_state", {})
    simulation_state.setdefault("social_state", {})
    simulation_state.setdefault("debug_meta", {})
    simulation_state.setdefault("gm_overrides", {})

    state["simulation_state"] = simulation_state
    package["state"] = state
    package["schema_version"] = 2
    return package