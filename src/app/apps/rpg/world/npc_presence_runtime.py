from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_schedule_state import scheduled_npcs_for_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _normalize_npc_id(value: Any) -> str:
    text = _safe_str(value).strip()
    if not text:
        return ""
    if text.startswith("npc:"):
        return text
    if text == "player":
        return "player"
    return f"npc:{text}"


def _add_unique(out: List[str], value: Any) -> None:
    npc_id = _normalize_npc_id(value)
    if npc_id.startswith("npc:") and npc_id not in out:
        out.append(npc_id)


def ensure_present_npc_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("present_npc_state"))
    state.setdefault("debug", {})
    simulation_state["present_npc_state"] = state
    return state


def update_present_npcs_for_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    tick: int,
    preserve_manual: bool = True,
) -> Dict[str, Any]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    state = ensure_present_npc_state(simulation_state)

    present: List[str] = []

    if preserve_manual:
        for npc_id in _safe_list(state.get(location_id)):
            _add_unique(present, npc_id)

    for scheduled in scheduled_npcs_for_location(simulation_state, location_id=location_id, tick=tick):
        _add_unique(present, scheduled.get("npc_id"))

    conversation_state = _safe_dict(simulation_state.get("conversation_thread_state"))
    for thread in _safe_list(conversation_state.get("threads")):
        thread = _safe_dict(thread)
        if _safe_str(thread.get("location_id")) != location_id:
            continue
        for participant in _safe_list(thread.get("participants")):
            participant = _safe_dict(participant)
            npc_id = _safe_str(participant.get("npc_id") or participant.get("id"))
            _add_unique(present, npc_id)

    state[location_id] = present
    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_updated_tick": int(tick or 0),
        "location_id": location_id,
        "present_npcs": list(present),
        "source": "deterministic_npc_presence_runtime",
    }
    simulation_state["present_npc_state"] = state

    return {
        "updated": True,
        "location_id": location_id,
        "present_npcs": list(present),
        "source": "deterministic_npc_presence_runtime",
    }


def present_npcs_at_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
) -> List[str]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    state = ensure_present_npc_state(simulation_state)
    return [
        _safe_str(npc_id)
        for npc_id in _safe_list(state.get(location_id))
        if _safe_str(npc_id).startswith("npc:")
    ]
