from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_RECENT_FOCUS = 8
MAX_RECENT_SPEAKERS = 8
MAX_RECENT_ACTIVITIES = 8
DEFAULT_FOCUS_TTL_TICKS = 50


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_scene_continuity_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state.get("scene_continuity_state"))
    if not isinstance(state.get("by_location"), dict):
        state["by_location"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    simulation_state["scene_continuity_state"] = state
    return state


def update_scene_continuity_from_conversation(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    topic_id: str = "",
    topic_type: str = "",
    speaker_id: str = "",
    listener_id: str = "",
    tick: int,
) -> Dict[str, Any]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    state = ensure_scene_continuity_state(simulation_state)
    by_location = _safe_dict(state.get("by_location"))
    entry = _safe_dict(by_location.get(location_id))

    recent_focus = []
    for focus in _safe_list(entry.get("recent_focus")):
        focus = _safe_dict(focus)
        expires_tick = _safe_int(focus.get("expires_tick"), 0)
        if expires_tick and int(tick or 0) >= expires_tick:
            continue
        if _safe_str(focus.get("topic_id")) == _safe_str(topic_id):
            continue
        recent_focus.append(focus)

    if topic_id:
        recent_focus.insert(
            0,
            {
                "focus_id": f"focus:{location_id}:{topic_id}",
                "topic_id": _safe_str(topic_id),
                "topic_type": _safe_str(topic_type),
                "kind": f"{_safe_str(topic_type) or 'topic'}_discussion",
                "last_tick": int(tick or 0),
                "weight": 3,
                "expires_tick": int(tick or 0) + DEFAULT_FOCUS_TTL_TICKS,
                "source": "deterministic_scene_continuity_runtime",
            },
        )

    speakers = [
        value
        for value in [_safe_str(speaker_id), _safe_str(listener_id)]
        if value.startswith("npc:")
    ]
    recent_speakers = []
    for speaker in speakers + _safe_list(entry.get("recent_speakers")):
        speaker = _safe_str(speaker)
        if speaker and speaker not in recent_speakers:
            recent_speakers.append(speaker)

    entry["recent_focus"] = recent_focus[:MAX_RECENT_FOCUS]
    entry["recent_speakers"] = recent_speakers[:MAX_RECENT_SPEAKERS]
    by_location[location_id] = entry
    state["by_location"] = by_location
    state["debug"] = {
        "last_updated_tick": int(tick or 0),
        "location_id": location_id,
        "source": "deterministic_scene_continuity_runtime",
    }

    return {
        "updated": True,
        "location_id": location_id,
        "scene_continuity": deepcopy(entry),
        "source": "deterministic_scene_continuity_runtime",
    }


def update_scene_continuity_from_activity(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    activity: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    state = ensure_scene_continuity_state(simulation_state)
    by_location = _safe_dict(state.get("by_location"))
    entry = _safe_dict(by_location.get(location_id))

    recent_activities = _safe_list(entry.get("recent_activities"))
    activity = _safe_dict(activity)
    recent_activities.insert(
        0,
        {
            "activity_id": _safe_str(activity.get("activity_id") or activity.get("kind")),
            "kind": _safe_str(activity.get("kind")),
            "npc_id": _safe_str(activity.get("npc_id")),
            "summary": _safe_str(activity.get("text") or activity.get("summary")),
            "tick": int(tick or 0),
            "source": "deterministic_scene_continuity_runtime",
        },
    )
    entry["recent_activities"] = recent_activities[:MAX_RECENT_ACTIVITIES]
    by_location[location_id] = entry
    state["by_location"] = by_location

    return {
        "updated": True,
        "location_id": location_id,
        "scene_continuity": deepcopy(entry),
        "source": "deterministic_scene_continuity_runtime",
    }


def scene_continuity_for_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
) -> Dict[str, Any]:
    location_id = _safe_str(location_id or current_location_id(simulation_state))
    state = ensure_scene_continuity_state(simulation_state)
    return deepcopy(_safe_dict(_safe_dict(state.get("by_location")).get(location_id)))
