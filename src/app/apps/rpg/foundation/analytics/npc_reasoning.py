from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def inspect_npc_reasoning(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    npc_minds = _safe_dict(simulation_state.get("npc_minds"))
    social_state = _safe_dict(simulation_state.get("social_state"))

    npc = _safe_dict(npc_index.get(npc_id))
    mind = _safe_dict(npc_minds.get(npc_id))
    memory = _safe_dict(mind.get("memory"))
    beliefs = _safe_dict(mind.get("beliefs"))
    goals = _safe_list(mind.get("goals"))
    last_decision = _safe_dict(mind.get("last_decision"))

    faction_id = _safe_str(npc.get("faction_id"))
    faction_position = _safe_dict(_safe_dict(social_state.get("group_positions")).get(faction_id))

    return {
        "npc": {
            "npc_id": npc_id,
            "name": _safe_str(npc.get("name")) or npc_id,
            "role": _safe_str(npc.get("role")),
            "faction_id": faction_id,
            "location_id": _safe_str(npc.get("location_id")),
        },
        "reasoning": {
            "beliefs": beliefs,
            "top_goals": sorted(goals, key=lambda g: str(g.get("goal_id")))[:5],
            "recent_memories": _safe_list(memory.get("entries"))[:8],
            "last_decision": last_decision,
            "faction_position": faction_position,
        },
        "why": {
            "player_beliefs": _safe_dict(beliefs.get("player")),
            "decision_reason": _safe_str(last_decision.get("reason")),
            "dialogue_hint": _safe_str(last_decision.get("dialogue_hint")),
        },
    }