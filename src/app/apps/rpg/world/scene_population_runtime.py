from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_presence_runtime import (
    present_npcs_at_location,
    update_present_npcs_for_location,
)
from app.apps.rpg.world.npc_schedule_state import active_schedule_for_npc
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str


def build_scene_population_state(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    tick: int,
) -> Dict[str, Any]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    update_present_npcs_for_location(simulation_state, location_id=location_id, tick=tick)

    present: List[Dict[str, Any]] = []
    for npc_id in present_npcs_at_location(simulation_state, location_id=location_id):
        bio = get_npc_biography(npc_id)
        schedule = active_schedule_for_npc(simulation_state, npc_id=npc_id, tick=tick)
        present.append(
            {
                "npc_id": npc_id,
                "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
                "role": _safe_str(bio.get("role")),
                "activity": _safe_str(schedule.get("activity")) or "present",
                "availability": "available",
                "schedule_id": _safe_str(schedule.get("schedule_id")),
                "source": "deterministic_scene_population_runtime",
            }
        )

    state = {
        "location_id": location_id,
        "present_npcs": present[:12],
        "debug": {
            "last_updated_tick": int(tick or 0),
            "source": "deterministic_scene_population_runtime",
        },
    }
    simulation_state["scene_population_state"] = state
    return deepcopy(state)
