from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.locations.encounter_runtime import apply_seeded_encounter_runtime
from app.apps.rpg.world.locations.encounters import record_encounter, roll_seeded_encounter
from app.apps.rpg.world.locations.graph import MARKET, OLD_MILL, OLD_ROAD, RUSTY_FLAGON, get_canonical_location
from app.apps.rpg.world.locations.travel_resources import apply_runtime_travel_with_resource_consumption
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_runtime_travel_encounter_routing"

DESTINATION_ALIASES = {
    "rusty flagon": RUSTY_FLAGON,
    "tavern": RUSTY_FLAGON,
    "market": MARKET,
    "market square": MARKET,
    "old road": OLD_ROAD,
    "road": OLD_ROAD,
    "old mill": OLD_MILL,
    "mill": OLD_MILL,
}

TRAVEL_VERBS = ("go", "travel", "walk", "head", "move")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _current_location_id(simulation_state: Dict[str, Any], fallback: str) -> str:
    travel_state = _safe_dict(_safe_dict(simulation_state).get("travel_state"))
    current = _safe_str(travel_state.get("current_location_id"))
    return current if get_canonical_location(current) else fallback


def resolve_travel_command(
    command_text: str,
    *,
    current_location_id: str = RUSTY_FLAGON,
) -> Dict[str, Any]:
    text = _safe_str(command_text).strip().lower()
    if not text:
        return {"ok": False, "reason": "empty_command", "source": SOURCE}
    if not any(text == verb or text.startswith(f"{verb} ") for verb in TRAVEL_VERBS):
        return {"ok": False, "reason": "not_travel_command", "command_text": command_text, "source": SOURCE}
    destination = ""
    for alias, location_id in sorted(DESTINATION_ALIASES.items(), key=lambda row: len(row[0]), reverse=True):
        if alias in text:
            destination = location_id
            break
    if not destination:
        return {"ok": False, "reason": "unknown_travel_destination", "command_text": command_text, "source": SOURCE}
    start = _safe_str(current_location_id) or RUSTY_FLAGON
    if not get_canonical_location(start):
        return {"ok": False, "reason": "unknown_current_location", "current_location_id": start, "source": SOURCE}
    return {
        "ok": True,
        "reason": "travel_command_resolved",
        "command_text": command_text,
        "start_location_id": start,
        "end_location_id": destination,
        "source": SOURCE,
    }


def _encounter_route_key(travel_result: Dict[str, Any], end_location_id: str) -> Dict[str, str]:
    travel_entry = _safe_dict(_safe_dict(travel_result.get("travel_result")).get("travel_log_entry"))
    path = [_safe_str(row) for row in _safe_list(travel_entry.get("path")) if _safe_str(row)]
    if OLD_ROAD in path and OLD_MILL in path:
        return {"edge_id": "route:old_road:old_mill", "location_id": ""}
    return {"edge_id": "", "location_id": end_location_id}


def apply_runtime_travel_command(
    simulation_state: Dict[str, Any],
    command_text: str,
    *,
    turn_index: int = 0,
    encounter_seed: str = "phase4.11",
    current_location_id: str | None = None,
    roll_encounter: bool = True,
) -> Dict[str, Any]:
    start = current_location_id or _current_location_id(simulation_state, RUSTY_FLAGON)
    command = resolve_travel_command(command_text, current_location_id=start)
    if command.get("ok") is not True:
        return {
            "ok": False,
            "reason": command.get("reason") or "travel_command_not_resolved",
            "command_result": command,
            "travel_result": None,
            "encounter_result": None,
            "encounter_runtime_result": None,
            "source": SOURCE,
        }
    travel = apply_runtime_travel_with_resource_consumption(
        simulation_state,
        start_location_id=_safe_str(command.get("start_location_id")),
        end_location_id=_safe_str(command.get("end_location_id")),
        turn_index=turn_index,
    )
    if travel.get("ok") is not True:
        return {
            "ok": False,
            "reason": travel.get("reason") or "runtime_travel_command_denied",
            "command_result": command,
            "travel_result": travel,
            "encounter_result": None,
            "encounter_runtime_result": None,
            "source": SOURCE,
        }
    encounter_result = None
    encounter_runtime = None
    if roll_encounter:
        route_key = _encounter_route_key(travel, _safe_str(command.get("end_location_id")))
        rolled = roll_seeded_encounter(
            encounter_seed,
            turn_index,
            location_id=route_key.get("location_id") or None,
            edge_id=route_key.get("edge_id") or None,
        )
        encounter_result = record_encounter(simulation_state, rolled, turn_index=turn_index)
        encounter_runtime = apply_seeded_encounter_runtime(simulation_state, encounter_result, turn_index=turn_index)
    return {
        "ok": True,
        "reason": "runtime_travel_command_applied",
        "command_result": command,
        "travel_result": travel,
        "encounter_result": encounter_result,
        "encounter_runtime_result": encounter_runtime,
        "source": SOURCE,
    }


def build_runtime_travel_command_narration_contract(command_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(command_result)
    allowed = [f"Runtime travel command result: {result.get('reason')}"]
    command = _safe_dict(result.get("command_result"))
    if command:
        allowed.append(f"Resolved travel command: {command.get('start_location_id')} -> {command.get('end_location_id')}")
    travel = _safe_dict(result.get("travel_result"))
    if travel:
        allowed.append(f"Guarded travel/resource result: {travel.get('reason')}")
    encounter_runtime = _safe_dict(result.get("encounter_runtime_result"))
    if encounter_runtime:
        allowed.append(f"Encounter runtime result: {encounter_runtime.get('reason')}")
    return {
        "source": SOURCE,
        "allowed_runtime_travel_command_claims": allowed,
        "forbidden_runtime_travel_command_claims": [
            "Do not route travel commands through harness shortcuts.",
            "Do not apply travel unless guarded travel with resource consumption returns ok=true.",
            "Do not claim encounters were rolled unless a source-backed encounter_result is present.",
            "Do not claim combat started unless a canonical combat-start API is called.",
            "Do not invent inventory, survival, route, discovery, world-event, quest, reward, XP, or combat changes.",
        ],
    }


def assert_phase4_runtime_travel_encounter_routing_ready() -> Dict[str, Any]:
    from app.apps.rpg.world.locations.discovery import discover_location, discover_route, unblock_route

    state = {
        "player_state": {
            "inventory_state": {"items": [{"item_id": "ration", "qty": 1}, {"item_id": "water_skin", "qty": 2}]},
            "survival_state": {"hunger": 10, "thirst": 10},
        }
    }
    discover_location(state, location_id=OLD_MILL, reason="scouted_old_road", turn_index=1)
    discover_route(state, edge_id="route:old_road:old_mill", reason="scouted_old_road", turn_index=1)
    unblock_route(state, edge_id="route:old_road:old_mill", reason="bandit_threat_resolved", turn_index=2)
    applied = apply_runtime_travel_command(state, "go to the old mill", turn_index=14, encounter_seed="phase4.11")
    missing = apply_runtime_travel_command({"player_state": {"inventory_state": {"items": []}}}, "go to the old road", turn_index=4)
    unknown = apply_runtime_travel_command({}, "sing a song", turn_index=5)
    contract = build_runtime_travel_command_narration_contract(applied)
    blockers = []
    if applied.get("reason") != "runtime_travel_command_applied":
        blockers.append({"kind": "expected_runtime_travel_command_applied", "source": SOURCE})
    if _safe_dict(applied.get("travel_result")).get("reason") != "runtime_travel_resources_consumed":
        blockers.append({"kind": "expected_guarded_resource_travel", "source": SOURCE})
    if not _safe_dict(applied.get("encounter_result")).get("encounter_log_entry"):
        blockers.append({"kind": "expected_recorded_encounter", "source": SOURCE})
    if not _safe_dict(applied.get("encounter_runtime_result")).get("reason"):
        blockers.append({"kind": "expected_encounter_runtime_result", "source": SOURCE})
    if missing.get("reason") != "insufficient_travel_resources":
        blockers.append({"kind": "expected_missing_resource_rejection", "source": SOURCE})
    if unknown.get("reason") != "not_travel_command":
        blockers.append({"kind": "expected_non_travel_rejection", "source": SOURCE})
    if not contract.get("forbidden_runtime_travel_command_claims"):
        blockers.append({"kind": "missing_runtime_command_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_runtime_travel_encounter_routing_ready" if not blockers else "phase4_runtime_travel_encounter_routing_not_ready",
        "applied": applied,
        "missing": missing,
        "unknown": unknown,
        "blockers": blockers,
        "source": SOURCE,
    }
