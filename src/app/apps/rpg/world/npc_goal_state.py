from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, cast

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_GOALS_PER_NPC = 4
MAX_RECENT_GOAL_EVENTS = 20

GOAL_RESPONSE_STYLE_HINTS = {
    "maintain_order": "guarded",
    "gather_rumors": "helpful",
    "protect_customer": "friendly",
    "avoid_trouble": "evasive",
    "sell_goods": "friendly",
    "warn_player": "helpful",
    "seek_help": "helpful",
    "watch_suspicious_activity": "guarded",
}

DEFAULT_NPC_GOALS = {
    "npc:Bran": [
        {
            "goal_id": "goal:bran:keep_tavern_orderly",
            "kind": "maintain_order",
            "priority": 3,
            "location_id": "loc_tavern",
            "status": "active",
            "summary": "Keep the tavern calm, paid, and orderly.",
        },
        {
            "goal_id": "goal:bran:gather_road_rumors",
            "kind": "gather_rumors",
            "priority": 2,
            "location_id": "loc_tavern",
            "status": "active",
            "summary": "Listen for reliable news from the roads.",
        },
    ],
    "npc:Mira": [
        {
            "goal_id": "goal:mira:learn_local_trouble",
            "kind": "gather_rumors",
            "priority": 3,
            "location_id": "loc_tavern",
            "status": "active",
            "summary": "Collect useful rumors without drawing too much attention.",
        },
        {
            "goal_id": "goal:mira:avoid_trouble",
            "kind": "avoid_trouble",
            "priority": 1,
            "location_id": "loc_tavern",
            "status": "active",
            "summary": "Avoid being pulled into open trouble.",
        },
    ],
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_npc_goal_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = simulation_state.get("npc_goal_state")
    if not isinstance(state, dict):
        state = {"goals": {}, "recent_goal_events": [], "debug": {}}
        simulation_state["npc_goal_state"] = state
    if not isinstance(state.get("goals"), dict):
        state["goals"] = {}
    if not isinstance(state.get("recent_goal_events"), list):
        state["recent_goal_events"] = []
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    return state


def _goal_active_at(goal: Dict[str, Any], *, tick: int, location_id: str = "") -> bool:
    goal = _safe_dict(goal)
    if _safe_str(goal.get("status") or "active") != "active":
        return False
    expires_tick = _safe_int(goal.get("expires_tick"), 0)
    if expires_tick and int(tick or 0) >= expires_tick:
        return False
    goal_location = _safe_str(goal.get("location_id"))
    if goal_location and location_id and goal_location != location_id:
        return False
    return True


def seed_default_npc_goals(
    simulation_state: Dict[str, Any],
    *,
    tick: int = 0,
    location_id: str = "",
) -> Dict[str, Any]:
    state = ensure_npc_goal_state(simulation_state)
    goals_root = _safe_dict(state.get("goals"))
    created: List[str] = []
    active_location = location_id or current_location_id(simulation_state)
    for npc_id, goals in DEFAULT_NPC_GOALS.items():
        existing = _safe_list(goals_root.get(npc_id))
        existing_ids = {_safe_str(goal.get("goal_id")) for goal in existing if isinstance(goal, dict)}
        for goal in goals:
            goal = dict(goal)
            if active_location and goal.get("location_id") and goal.get("location_id") != active_location:
                continue
            if goal["goal_id"] in existing_ids:
                continue
            goal.setdefault("created_tick", int(tick or 0))
            goal.setdefault("expires_tick", int(tick or 0) + 200)
            existing.append(goal)
            created.append(cast(str, goal["goal_id"]))
        goals_root[npc_id] = existing[-MAX_GOALS_PER_NPC:]
    state["goals"] = goals_root
    if created:
        events = _safe_list(state.get("recent_goal_events"))
        events.append({
            "kind": "default_goals_seeded",
            "goal_ids": created,
            "tick": int(tick or 0),
            "location_id": active_location,
            "source": "deterministic_npc_goal_state",
        })
        state["recent_goal_events"] = events[-MAX_RECENT_GOAL_EVENTS:]
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_seed_tick": int(tick or 0),
        "last_created_goal_count": len(created),
    }
    return {"created_goal_ids": created, "npc_goal_state": deepcopy(state)}


def active_goals_for_npc(
    simulation_state: Dict[str, Any],
    npc_id: str,
    *,
    tick: int = 0,
    location_id: str = "",
) -> List[Dict[str, Any]]:
    state = ensure_npc_goal_state(simulation_state)
    active_location = location_id or current_location_id(simulation_state)
    goals = []
    for goal in _safe_list(_safe_dict(state.get("goals")).get(npc_id)):
        goal = _safe_dict(goal)
        if _goal_active_at(goal, tick=tick, location_id=active_location):
            goals.append(deepcopy(goal))
    goals.sort(key=lambda g: (_safe_int(g.get("priority"), 0), _safe_str(g.get("goal_id"))), reverse=True)
    return goals


def dominant_goal_for_npc(
    simulation_state: Dict[str, Any],
    npc_id: str,
    *,
    tick: int = 0,
    location_id: str = "",
) -> Dict[str, Any]:
    goals = active_goals_for_npc(simulation_state, npc_id, tick=tick, location_id=location_id)
    return deepcopy(goals[0]) if goals else {}


def response_style_from_goal(goal: Dict[str, Any]) -> str:
    goal = _safe_dict(goal)
    return GOAL_RESPONSE_STYLE_HINTS.get(_safe_str(goal.get("kind")), "")


def goal_topic_bias(goal: Dict[str, Any]) -> Dict[str, Any]:
    goal = _safe_dict(goal)
    kind = _safe_str(goal.get("kind"))
    if kind == "gather_rumors":
        return {"preferred_topic_types": ["rumor", "recent_event", "quest"], "player_invitation_bias": 10}
    if kind == "warn_player":
        return {"preferred_topic_types": ["quest", "recent_event"], "player_invitation_bias": 20}
    if kind == "avoid_trouble":
        return {"preferred_topic_types": ["location_smalltalk"], "player_invitation_bias": -10}
    if kind == "maintain_order":
        return {"preferred_topic_types": ["recent_event", "location_smalltalk"], "player_invitation_bias": 0}
    return {"preferred_topic_types": [], "player_invitation_bias": 0}


def record_goal_influence(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    npc_id: str,
    goal: Dict[str, Any],
    influence_kind: str,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    state = ensure_npc_goal_state(simulation_state)
    events = _safe_list(state.get("recent_goal_events"))
    event = {
        "kind": _safe_str(influence_kind),
        "npc_id": _safe_str(npc_id),
        "goal_id": _safe_str(_safe_dict(goal).get("goal_id")),
        "goal_kind": _safe_str(_safe_dict(goal).get("kind")),
        "tick": int(tick or 0),
        "details": deepcopy(_safe_dict(details)),
        "source": "deterministic_npc_goal_state",
    }
    events.append(event)
    state["recent_goal_events"] = events[-MAX_RECENT_GOAL_EVENTS:]
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_goal_influence": deepcopy(event),
    }
    return deepcopy(event)
