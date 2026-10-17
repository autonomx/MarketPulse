from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.conversation_effects import build_conversation_world_signal
from app.apps.rpg.world.conversation_settings import normalize_conversation_settings
from app.apps.rpg.world.conversation_topics import select_conversation_topic
from app.apps.rpg.rules.location_registry import current_location_id
from app.apps.rpg.world.npc_biography_registry import get_npc_biography
from app.apps.rpg.world.npc_goal_state import dominant_goal_for_npc, seed_default_npc_goals
from app.apps.rpg.world.npc_presence_runtime import (
    present_npcs_at_location,
    update_present_npcs_for_location,
)
from app.apps.rpg.world.scene_continuity_state import update_scene_continuity_from_activity
from app.apps.rpg.world.world_event_log import add_world_event
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_SCHEDULED_ACTIVITIES = 8
MAX_RECENT_ACTIVITIES = 24
DEFAULT_ACTIVITY_COOLDOWN_TICKS = 3


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def ensure_scene_activity_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    state = simulation_state.get("scene_activity_state")
    if not isinstance(state, dict):
        state = {"scheduled": [], "recent": [], "cooldowns": {}, "debug": {}}
        simulation_state["scene_activity_state"] = state
    if not isinstance(state.get("scheduled"), list):
        state["scheduled"] = []
    if not isinstance(state.get("recent"), list):
        state["recent"] = []
    if not isinstance(state.get("cooldowns"), dict):
        state["cooldowns"] = {}
    if not isinstance(state.get("debug"), dict):
        state["debug"] = {}
    return state


def _present_npcs(simulation_state: Dict[str, Any], *, tick: int) -> List[Dict[str, str]]:
    location_id = current_location_id(simulation_state)
    update_present_npcs_for_location(simulation_state, location_id=location_id, tick=tick)
    npc_ids = present_npcs_at_location(simulation_state, location_id=location_id)
    if npc_ids:
        result = []
        for npc_id in npc_ids:
            bio = get_npc_biography(npc_id)
            result.append(
                {
                    "npc_id": npc_id,
                    "name": _safe_str(bio.get("name")) or npc_id.replace("npc:", ""),
                    "role": _safe_str(bio.get("role")),
                }
            )
        return result
    # Conservative fallback
    if location_id == "loc_tavern":
        return [
            {"npc_id": "npc:Bran", "name": "Bran", "role": "tavern_keeper"},
            {"npc_id": "npc:Mira", "name": "Mira", "role": "regular"},
        ]
    return [
        {"npc_id": "npc:Bran", "name": "Bran", "role": "local"},
        {"npc_id": "npc:Mira", "name": "Mira", "role": "local"},
    ]


def _cooldown_key(*, npc_id: str, location_id: str, kind: str) -> str:
    return f"{_safe_str(location_id)}::{_safe_str(npc_id)}::{_safe_str(kind)}"


def _location_cooldown_key(*, location_id: str) -> str:
    return f"{_safe_str(location_id)}::__location__::scene_activity"


def _activity_kind_for_goal(goal: Dict[str, Any], tick: int) -> str:
    goal_kind = _safe_str(_safe_dict(goal).get("kind"))
    if goal_kind == "gather_rumors":
        return "rumor_mention"
    if goal_kind == "maintain_order":
        return "service_activity"
    if goal_kind in {"avoid_trouble", "watch_suspicious_activity"}:
        return "npc_idle_action"
    if goal_kind in {"warn_player", "seek_help"}:
        return "goal_driven_action"
    return "location_activity" if tick % 2 else "npc_idle_action"


def _activity_text(*, npc: Dict[str, str], kind: str, goal: Dict[str, Any], topic: Dict[str, Any]) -> str:
    name = _safe_str(npc.get("name") or npc.get("npc_id") or "Someone")
    goal_kind = _safe_str(goal.get("kind"))
    topic_title = _safe_str(topic.get("title") or topic.get("topic_id") or "the room")
    if kind == "rumor_mention":
        return f"{name} lowers their voice while listening for talk about {topic_title}."
    if kind == "service_activity":
        return f"{name} keeps the room moving with practiced attention."
    if kind == "goal_driven_action" and goal_kind == "warn_player":
        return f"{name} watches the room as if weighing whether to warn someone."
    if kind == "npc_idle_action":
        return f"{name} pauses, scans the room, and returns to their business."
    return f"{name} reacts quietly to the activity around {topic_title}."


def _make_activity(
    *,
    simulation_state: Dict[str, Any],
    npc: Dict[str, str],
    tick: int,
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    location_id = current_location_id(simulation_state)
    goal = dominant_goal_for_npc(simulation_state, _safe_str(npc.get("npc_id")), tick=tick, location_id=location_id)
    topic = select_conversation_topic(simulation_state, settings=settings)
    kind = _activity_kind_for_goal(goal, tick)
    activity_id = f"scene_activity:{int(tick or 0)}:{location_id}:{_safe_str(npc.get('npc_id'))}:{kind}"
    return {
        "activity_id": activity_id,
        "kind": kind,
        "npc_id": _safe_str(npc.get("npc_id")),
        "npc_name": _safe_str(npc.get("name")),
        "location_id": location_id,
        "goal_id": _safe_str(goal.get("goal_id")),
        "goal_kind": _safe_str(goal.get("kind")),
        "topic_id": _safe_str(topic.get("topic_id")),
        "topic_type": _safe_str(topic.get("topic_type")),
        "text": _activity_text(npc=npc, kind=kind, goal=goal, topic=topic),
        "created_tick": int(tick or 0),
        "source": "deterministic_scene_activity_runtime",
    }


def maybe_schedule_scene_activity(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    settings: Dict[str, Any] | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    settings = normalize_conversation_settings(settings or {})
    state = ensure_scene_activity_state(simulation_state)
    location_id = current_location_id(simulation_state)
    seed_default_npc_goals(simulation_state, tick=tick, location_id=location_id)

    if not settings.get("allow_scene_activities", True) and not force:
        return {"scheduled": False, "reason": "scene_activities_disabled", "scene_activity_state": deepcopy(state)}

    interval = max(1, _safe_int(settings.get("scene_activity_interval_ticks"), 2))
    if not force and int(tick or 0) % interval != 0:
        return {"scheduled": False, "reason": "scene_activity_interval", "scene_activity_state": deepcopy(state)}

    npcs = _present_npcs(simulation_state, tick=tick)
    if not npcs:
        return {"scheduled": False, "reason": "no_present_npcs", "scene_activity_state": deepcopy(state)}

    npc = npcs[int(tick or 0) % len(npcs)]
    goal = dominant_goal_for_npc(simulation_state, _safe_str(npc.get("npc_id")), tick=tick, location_id=location_id)
    kind = _activity_kind_for_goal(goal, tick)
    cooldowns = _safe_dict(state.get("cooldowns"))
    location_cooldown_key = _location_cooldown_key(location_id=location_id)
    location_cooldown_until = _safe_int(cooldowns.get(location_cooldown_key), 0)
    if location_cooldown_until and int(tick or 0) < location_cooldown_until:
        state["debug"] = {
            **_safe_dict(state.get("debug")),
            "last_schedule_tick": int(tick or 0),
            "last_reason": "scene_activity_location_cooldown",
            "location_cooldown_key": location_cooldown_key,
            "location_cooldown_until": location_cooldown_until,
        }
        return {
            "scheduled": False,
            "reason": "scene_activity_location_cooldown",
            "cooldown_key": location_cooldown_key,
            "cooldown_until": location_cooldown_until,
            "scene_activity_state": deepcopy(state),
        }
    cooldown_key = _cooldown_key(npc_id=_safe_str(npc.get("npc_id")), location_id=location_id, kind=kind)
    cooldown_until = _safe_int(cooldowns.get(cooldown_key), 0)
    if cooldown_until and int(tick or 0) < cooldown_until:
        return {
            "scheduled": False,
            "reason": "scene_activity_cooldown",
            "cooldown_key": cooldown_key,
            "cooldown_until": cooldown_until,
            "scene_activity_state": deepcopy(state),
        }

    activity = _make_activity(simulation_state=simulation_state, npc=npc, tick=tick, settings=settings)
    scheduled = _safe_list(state.get("scheduled"))
    recent = _safe_list(state.get("recent"))
    scheduled.append(activity)
    recent.append(activity)
    state["scheduled"] = scheduled[-MAX_SCHEDULED_ACTIVITIES:]
    state["recent"] = recent[-MAX_RECENT_ACTIVITIES:]
    cooldown_ticks = max(
        0,
        _safe_int(settings.get("scene_activity_cooldown_ticks"), DEFAULT_ACTIVITY_COOLDOWN_TICKS),
    )
    cooldown_until_val = int(tick or 0) + cooldown_ticks
    cooldowns[cooldown_key] = cooldown_until_val
    cooldowns[location_cooldown_key] = cooldown_until_val
    state["cooldowns"] = cooldowns

    world_event = {}
    if settings.get("allow_world_events", True) and settings.get("allow_scene_activity_world_events", True):
        world_event = add_world_event(
            simulation_state,
            {
                "event_id": f"world:event:scene_activity:{int(tick or 0)}:{activity['activity_id']}",
                "kind": _safe_str(activity.get("kind")),
                "title": "Scene activity",
                "summary": _safe_str(activity.get("text")),
                "activity_id": _safe_str(activity.get("activity_id")),
                "npc_id": _safe_str(activity.get("npc_id")),
                "goal_id": _safe_str(activity.get("goal_id")),
                "location_id": location_id,
                "tick": int(tick or 0),
                "source": "deterministic_scene_activity_runtime",
            },
        )

    world_signal = {}
    if settings.get("allow_world_signals", True) and settings.get("allow_scene_activity_world_signals", True):
        topic = select_conversation_topic(simulation_state, settings=settings)
        world_signal = build_conversation_world_signal(
            tick=tick,
            thread_id=f"scene_activity:{activity['activity_id']}",
            beat_id=_safe_str(activity.get("activity_id")),
            topic=topic,
            settings=settings,
        )
        world_signal["source"] = "deterministic_scene_activity_scheduler"
        conversation_state = simulation_state.get("conversation_thread_state")
        if isinstance(conversation_state, dict):
            signals = conversation_state.get("world_signals")
            if not isinstance(signals, list):
                signals = []
            signals.append(world_signal)
            conversation_state["world_signals"] = signals[
                -max(1, _safe_int(settings.get("max_world_signals_per_thread"), 2)) * 4:
            ]
            simulation_state["conversation_thread_state"] = conversation_state

    state["debug"] = {
        **_safe_dict(state.get("debug")),
        "last_schedule_tick": int(tick or 0),
        "last_activity_id": _safe_str(activity.get("activity_id")),
        "last_reason": "scheduled",
    }

    # Y1: update scene continuity from activity.
    if settings.get("scene_continuity_enabled", True):
        update_scene_continuity_from_activity(
            simulation_state,
            location_id=location_id,
            activity=activity,
            tick=tick,
        )

    return {
        "scheduled": True,
        "reason": "scheduled",
        "activity": deepcopy(activity),
        "world_event": deepcopy(world_event),
        "world_signal": deepcopy(world_signal),
        "scene_activity_state": deepcopy(state),
        "source": "deterministic_scene_activity_runtime",
    }
