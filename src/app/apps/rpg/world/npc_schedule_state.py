from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_NPC_SCHEDULES: Dict[str, List[Dict[str, Any]]] = {
    "npc:Bran": [
        {
            "schedule_id": "schedule:bran:tavern_default",
            "location_id": "loc_tavern",
            "activity": "keeping_tavern",
            "start_tick_mod": 0,
            "end_tick_mod": 1000000,
            "priority": 3,
            "source": "deterministic_npc_schedule_registry",
        }
    ],
    "npc:Mira": [
        {
            "schedule_id": "schedule:mira:tavern_default",
            "location_id": "loc_tavern",
            "activity": "listening_for_patterns",
            "start_tick_mod": 0,
            "end_tick_mod": 1000000,
            "priority": 2,
            "source": "deterministic_npc_schedule_registry",
        }
    ],
    "npc:Merchant": [
        {
            "schedule_id": "schedule:merchant:market_default",
            "location_id": "loc_market",
            "activity": "selling_goods",
            "start_tick_mod": 0,
            "end_tick_mod": 1000000,
            "priority": 2,
            "source": "deterministic_npc_schedule_registry",
        }
    ],
    "npc:GuardCaptain": [
        {
            "schedule_id": "schedule:guard_captain:patrol_default",
            "location_id": "loc_patrol",
            "activity": "patrolling",
            "start_tick_mod": 0,
            "end_tick_mod": 1000000,
            "priority": 1,
            "source": "deterministic_npc_schedule_registry",
        }
    ],
}


MAX_SCHEDULE_ENTRIES_PER_NPC = 12


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_npc_schedule_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("npc_schedule_state"))
    schedules = _safe_dict(state.get("schedules"))

    for npc_id, entries in DEFAULT_NPC_SCHEDULES.items():
        if npc_id not in schedules:
            schedules[npc_id] = deepcopy(entries)

    cleaned: Dict[str, List[Dict[str, Any]]] = {}
    for npc_id, entries in schedules.items():
        npc_id = _safe_str(npc_id)
        if not npc_id.startswith("npc:"):
            continue
        cleaned[npc_id] = [
            _safe_dict(entry)
            for entry in _safe_list(entries)
            if _safe_str(_safe_dict(entry).get("location_id"))
        ][:MAX_SCHEDULE_ENTRIES_PER_NPC]

    state["schedules"] = cleaned
    state.setdefault("debug", {})
    simulation_state["npc_schedule_state"] = state
    return state


def active_schedule_for_npc(
    simulation_state: Dict[str, Any],
    *,
    npc_id: str,
    tick: int,
) -> Dict[str, Any]:
    state = ensure_npc_schedule_state(simulation_state)
    schedules = _safe_list(_safe_dict(state.get("schedules")).get(_safe_str(npc_id)))
    current_tick = int(tick or 0)

    candidates = []
    for schedule in schedules:
        schedule = _safe_dict(schedule)
        start = _safe_int(schedule.get("start_tick_mod"), 0)
        end = _safe_int(schedule.get("end_tick_mod"), 1000000)
        if start <= current_tick <= end:
            candidates.append(schedule)

    if not candidates:
        return {}

    candidates.sort(
        key=lambda item: (
            _safe_int(item.get("priority"), 0),
            _safe_str(item.get("schedule_id")),
        ),
        reverse=True,
    )
    return deepcopy(candidates[0])


def scheduled_npcs_for_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str,
    tick: int,
) -> List[Dict[str, Any]]:
    state = ensure_npc_schedule_state(simulation_state)
    location_id = _safe_str(location_id)
    out = []

    for npc_id in sorted(_safe_dict(state.get("schedules")).keys()):
        active = active_schedule_for_npc(simulation_state, npc_id=npc_id, tick=tick)
        if _safe_str(active.get("location_id")) != location_id:
            continue
        bio = get_npc_biography(npc_id)
        out.append(
            {
                "npc_id": npc_id,
                "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
                "role": _safe_str(bio.get("role")),
                "location_id": location_id,
                "activity": _safe_str(active.get("activity")),
                "schedule_id": _safe_str(active.get("schedule_id")),
                "source": "deterministic_npc_schedule_runtime",
            }
        )

    return out
