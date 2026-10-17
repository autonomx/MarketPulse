from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _combat_id(combat_state: Dict[str, Any], combat_result: Dict[str, Any]) -> str:
    return _safe_str(
        _safe_dict(combat_state).get("combat_id")
        or _safe_dict(combat_state).get("encounter_id")
        or _safe_dict(combat_result).get("combat_id")
        or _safe_dict(combat_result).get("encounter_id")
        or "manual_combat"
    ).strip()


def _location_id(simulation_state: Dict[str, Any], combat_state: Dict[str, Any]) -> str:
    return _safe_str(
        _safe_dict(combat_state).get("location_id")
        or _safe_dict(simulation_state).get("player_location_id")
        or _safe_dict(_safe_dict(simulation_state).get("player_state")).get("location_id")
        or "unknown"
    ).strip()


def emit_combat_world_consequence(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    combat_result: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    simulation_state = dict(_safe_dict(simulation_state))
    combat_state = _safe_dict(combat_state)
    combat_result = _safe_dict(combat_result)

    combat_id = _combat_id(combat_state, combat_result)
    exit_reason = _safe_str(
        combat_state.get("exit_reason")
        or combat_result.get("exit_reason")
        or combat_result.get("reason")
    ).strip()

    if not exit_reason:
        return simulation_state, {
            "emitted": False,
            "reason": "no_exit_reason",
            "events": [],
        }

    emitted_keys = set(_safe_list(simulation_state.get("combat_world_event_keys")))
    dedupe_key = f"{combat_id}:{exit_reason}"
    if dedupe_key in emitted_keys:
        return simulation_state, {
            "emitted": False,
            "reason": "duplicate",
            "dedupe_key": dedupe_key,
            "events": [],
        }

    location_id = _location_id(simulation_state, combat_state)
    events: List[Dict[str, Any]] = []

    if exit_reason == "victory":
        event = {
            "event_id": f"world_event:combat:{combat_id}:victory",
            "kind": "combat_victory",
            "source": "combat",
            "combat_id": combat_id,
            "location_id": location_id,
            "summary": "Hostile forces were defeated.",
            "faction_deltas": {},
            "pressure_deltas": {},
        }

        participant_tags = []
        for participant in _safe_dict(combat_state.get("participants")).values():
            participant = _safe_dict(participant)
            participant_tags.extend([_safe_str(x).lower() for x in _safe_list(participant.get("tags"))])
            archetype_id = _safe_str(participant.get("archetype_id")).lower()
            if "bandit" in archetype_id:
                participant_tags.append("bandit")
            if "wolf" in archetype_id:
                participant_tags.append("wolf")

        if "bandit" in participant_tags:
            event["summary"] = "Bandits were defeated nearby."
            event["faction_deltas"] = {"faction:bandits": -1}
            event["pressure_deltas"] = {"bandit_pressure": -1}
        elif "wolf" in participant_tags:
            event["summary"] = "Predators were driven back nearby."
            event["pressure_deltas"] = {"predator_pressure": -1}

        events.append(event)

    elif exit_reason in {"fled", "flee", "player_fled"}:
        events.append({
            "event_id": f"world_event:combat:{combat_id}:fled",
            "kind": "combat_fled",
            "source": "combat",
            "combat_id": combat_id,
            "location_id": location_id,
            "summary": "The player fled from combat.",
            "faction_deltas": {},
            "pressure_deltas": {},
        })

    elif exit_reason in {"party_defeat", "defeat", "player_defeat"}:
        events.append({
            "event_id": f"world_event:combat:{combat_id}:party_defeat",
            "kind": "combat_party_defeat",
            "source": "combat",
            "combat_id": combat_id,
            "location_id": location_id,
            "summary": "The party was defeated in combat.",
            "faction_deltas": {},
            "pressure_deltas": {"danger_pressure": 1},
        })

    if not events:
        return simulation_state, {
            "emitted": False,
            "reason": "unsupported_exit_reason",
            "events": [],
            "exit_reason": exit_reason,
        }

    world_events = list(_safe_list(simulation_state.get("world_events")))
    world_events.extend(events)
    simulation_state["world_events"] = world_events[-200:]

    location_pressure = dict(_safe_dict(simulation_state.get("location_pressure")))
    for event in events:
        for key, delta in _safe_dict(event.get("pressure_deltas")).items():
            location_pressure[key] = int(location_pressure.get(key, 0)) + int(delta)
    simulation_state["location_pressure"] = location_pressure

    emitted_keys.add(dedupe_key)
    simulation_state["combat_world_event_keys"] = sorted(emitted_keys)

    return simulation_state, {
        "emitted": True,
        "source": "combat",
        "dedupe_key": dedupe_key,
        "events": events,
    }