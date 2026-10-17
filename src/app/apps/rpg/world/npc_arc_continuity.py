from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.world.npc_evolution_state import get_npc_evolution
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def ensure_npc_arc_continuity_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("npc_arc_continuity_state"))
    if not isinstance(state.get("by_npc"), dict):
        state["by_npc"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["npc_arc_continuity_state"] = state
    return state


def update_npc_arc_continuity(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int,
) -> Dict[str, Any]:
    npc_id = _safe_str(npc_id)
    evo = get_npc_evolution(simulation_state, npc_id=npc_id)

    identity_arc = _safe_str(evo.get("identity_arc"))
    current_role = _safe_str(evo.get("current_role"))
    motivations = _safe_list(evo.get("active_motivations"))

    if not identity_arc and not current_role and not motivations:
        return {
            "updated": False,
            "reason": "no_evolution_arc",
            "source": "deterministic_npc_arc_continuity",
        }

    first_motivation = _safe_dict(motivations[0]) if motivations else {}
    state = ensure_npc_arc_continuity_state(simulation_state)
    by_npc = _safe_dict(state.get("by_npc"))

    entry = {
        "arc_id": f"arc:{npc_id}:{identity_arc or current_role or 'evolved'}",
        "npc_id": npc_id,
        "identity_arc": identity_arc,
        "current_role": current_role,
        "active_motivation_summary": _safe_str(first_motivation.get("summary")),
        "last_updated_tick": int(tick or 0),
        "source": "deterministic_npc_arc_continuity",
    }

    by_npc[npc_id] = entry
    state["by_npc"] = by_npc
    state["debug"] = {
        "last_updated_tick": int(tick or 0),
        "last_npc_id": npc_id,
        "source": "deterministic_npc_arc_continuity",
    }

    return {
        "updated": True,
        "arc": deepcopy(entry),
        "source": "deterministic_npc_arc_continuity",
    }
