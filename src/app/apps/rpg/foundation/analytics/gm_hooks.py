from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list


def gm_force_npc_goal(simulation_state: Dict[str, Any], npc_id: str, goal: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = dict(simulation_state or {})
    npc_minds = simulation_state.setdefault("npc_minds", {})
    mind = npc_minds.setdefault(npc_id, {})
    goals = _safe_list(mind.get("goals"))
    normalized = {
        "goal_id": str(goal.get("goal_id") or "gm_forced"),
        "type": str(goal.get("type") or "gm_override"),
        "priority": float(goal.get("priority") or 1.0),
    }
    goals.insert(0, normalized)
    mind["goals"] = goals[:8]
    debug_meta = simulation_state.setdefault("debug_meta", {})
    audit = _safe_list(debug_meta.get("gm_audit"))
    audit.append({"action": "force_npc_goal", "npc_id": npc_id})
    debug_meta["gm_audit"] = audit[-100:]
    return simulation_state


def gm_force_faction_trend(simulation_state: Dict[str, Any], faction_id: str, trend_patch: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = dict(simulation_state or {})
    sandbox_state = simulation_state.setdefault("sandbox_state", {})
    faction_trends = sandbox_state.setdefault("faction_trends", {})
    current = _safe_dict(faction_trends.get(faction_id))
    current.update(dict(trend_patch or {}))
    faction_trends[faction_id] = current
    debug_meta = simulation_state.setdefault("debug_meta", {})
    audit = _safe_list(debug_meta.get("gm_audit"))
    audit.append({"action": "force_faction_trend", "faction_id": faction_id})
    debug_meta["gm_audit"] = audit[-100:]
    return simulation_state


def gm_append_debug_note(simulation_state: Dict[str, Any], note: str) -> Dict[str, Any]:
    simulation_state = dict(simulation_state or {})
    debug_meta = simulation_state.setdefault("debug_meta", {})
    audit = _safe_list(debug_meta.get("gm_audit"))
    audit.append({"action": "append_debug_note", "note": str(note or "")})
    debug_meta["gm_audit"] = audit[-100:]
    notes = _safe_list(debug_meta.get("gm_notes"))
    notes.append({"note": str(note or "")})
    debug_meta["gm_notes"] = notes[-50:]
    return simulation_state
