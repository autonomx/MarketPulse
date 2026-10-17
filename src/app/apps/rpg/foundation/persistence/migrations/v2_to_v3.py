from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict


def migrate_v2_to_v3(package: Dict[str, Any]) -> Dict[str, Any]:
    package = dict(package or {})
    state = _safe_dict(package.get("state"))
    simulation_state = _safe_dict(state.get("simulation_state"))
    player_state = _safe_dict(simulation_state.get("player_state"))

    player_state.setdefault("encounter_state", {})
    player_state.setdefault("dialogue_state", {})
    simulation_state.setdefault("sandbox_state", {})
    simulation_state["player_state"] = player_state
    state["simulation_state"] = simulation_state
    package["state"] = state

    artifacts = _safe_dict(package.get("artifacts"))
    artifacts.setdefault("snapshots", [])
    artifacts.setdefault("timeline", {})
    package["artifacts"] = artifacts

    package["schema_version"] = 3
    return package