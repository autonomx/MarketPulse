from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.locations.encounters import NO_ENCOUNTER
from app.apps.rpg.world.locations.events import record_world_event
from app.apps.rpg.world.locations.graph import OLD_MILL, get_canonical_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_encounter_combat_events"
COMBAT_HOOK_TAG = "combat_hook"
COMBAT_ENCOUNTER_IDS = {"encounter:old_mill_route:bandit_patrol"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _encounter_from_result(encounter_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(encounter_result)
    encounter = _safe_dict(result.get("encounter"))
    if not encounter:
        encounter = _safe_dict(_safe_dict(result.get("encounter_log_entry")).get("encounter"))
    return encounter


def _location_from_result(encounter_result: Dict[str, Any]) -> str:
    result = _safe_dict(encounter_result)
    location_id = _safe_str(result.get("location_id"))
    if location_id and get_canonical_location(location_id):
        return location_id
    entry = _safe_dict(result.get("encounter_log_entry"))
    location_id = _safe_str(entry.get("location_id"))
    if location_id and get_canonical_location(location_id):
        return location_id
    edge_id = _safe_str(result.get("edge_id") or entry.get("edge_id"))
    if edge_id == "route:old_road:old_mill":
        return OLD_MILL
    return ""


def classify_encounter_resolution(encounter_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(encounter_result)
    if result.get("ok") is not True:
        return {"kind": "invalid", "reason": "encounter_result_not_ok", "source": SOURCE}
    encounter = _encounter_from_result(result)
    if not encounter:
        return {"kind": "noop", "reason": "no_encounter_payload", "source": SOURCE}
    encounter_id = _safe_str(encounter.get("encounter_id"))
    tags = {_safe_str(tag) for tag in _safe_list(encounter.get("tags"))}
    if encounter_id == NO_ENCOUNTER or _safe_str(encounter.get("kind")) == "none":
        return {"kind": "noop", "reason": "no_encounter", "encounter_id": encounter_id, "source": SOURCE}
    if encounter_id in COMBAT_ENCOUNTER_IDS or COMBAT_HOOK_TAG in tags:
        return {"kind": "combat_candidate", "reason": "combat_capable_encounter", "encounter_id": encounter_id, "source": SOURCE}
    return {"kind": "world_event", "reason": "non_combat_encounter_event", "encounter_id": encounter_id, "source": SOURCE}


def _build_combat_candidate(encounter_result: Dict[str, Any], turn_index: int) -> Dict[str, Any]:
    encounter = _encounter_from_result(encounter_result)
    return {
        "combat_candidate": True,
        "encounter_id": _safe_str(encounter.get("encounter_id")),
        "encounter_kind": _safe_str(encounter.get("kind")),
        "summary": _safe_str(encounter.get("summary")),
        "tags": list(_safe_list(encounter.get("tags"))),
        "location_id": _location_from_result(encounter_result),
        "turn_index": max(0, _safe_int(turn_index, 0)),
        "requires_canonical_combat_start_api": True,
        "source": SOURCE,
    }


def apply_seeded_encounter_runtime(
    simulation_state: Dict[str, Any],
    encounter_result: Dict[str, Any],
    *,
    turn_index: int = 0,
) -> Dict[str, Any]:
    classification = classify_encounter_resolution(encounter_result)
    kind = _safe_str(classification.get("kind"))
    encounter = _encounter_from_result(encounter_result)
    location_id = _location_from_result(encounter_result)
    if kind == "invalid":
        return {"ok": False, "reason": "encounter_result_not_ok", "classification": classification, "source": SOURCE}
    if kind == "noop":
        return {"ok": True, "reason": "encounter_runtime_noop", "classification": classification, "source": SOURCE}
    if kind == "combat_candidate":
        candidate = _build_combat_candidate(encounter_result, turn_index)
        return {
            "ok": True,
            "reason": "combat_candidate_created",
            "classification": classification,
            "combat_candidate": candidate,
            "world_event_result": None,
            "source": SOURCE,
        }
    if not location_id or not get_canonical_location(location_id):
        return {
            "ok": False,
            "reason": "unknown_event_location",
            "classification": classification,
            "location_id": location_id,
            "source": SOURCE,
        }
    event = record_world_event(
        simulation_state,
        location_id=location_id,
        event_id=f"event:{_safe_str(encounter.get('encounter_id'))}",
        kind=f"encounter:{_safe_str(encounter.get('kind')) or 'event'}",
        summary=_safe_str(encounter.get("summary")),
        turn_index=max(0, _safe_int(turn_index, 0)),
        source_detail=SOURCE,
    )
    return {
        "ok": event.get("ok") is True,
        "reason": "encounter_world_event_recorded" if event.get("ok") is True else event.get("reason"),
        "classification": classification,
        "world_event_result": event,
        "combat_candidate": None,
        "source": SOURCE,
    }


def build_encounter_runtime_narration_contract(runtime_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(runtime_result)
    allowed = [f"Encounter runtime result: {result.get('reason')}"]
    world_event = _safe_dict(_safe_dict(result.get("world_event_result")).get("event"))
    if world_event:
        allowed.append(
            f"Encounter world event: {world_event.get('location_id')} — {world_event.get('kind')} — {world_event.get('summary')}"
        )
    candidate = _safe_dict(result.get("combat_candidate"))
    if candidate:
        allowed.append(f"Combat candidate: {candidate.get('encounter_id')} at {candidate.get('location_id')}")
    return {
        "source": SOURCE,
        "allowed_encounter_runtime_claims": allowed,
        "forbidden_encounter_runtime_claims": [
            "Do not claim combat started unless a canonical deterministic combat-start API is called.",
            "Do not invent enemies, enemy stats, damage, combat outcomes, XP, loot, rewards, or quest progress.",
            "Do not mutate inventory, survival resources, route access, discovery state, or time from encounter runtime results.",
            "Do not invent local world events beyond the returned source-backed world_event_result.",
        ],
    }


def assert_phase4_encounter_combat_events_ready() -> Dict[str, Any]:
    from app.apps.rpg.world.locations.encounters import roll_seeded_encounter

    state: Dict[str, Any] = {}
    event_result = {
        "ok": True,
        "location_id": "location:old_road",
        "encounter": {
            "encounter_id": "encounter:old_road:bandit_scout_tracks",
            "kind": "evidence",
            "summary": "Boot prints and snapped brush suggest a bandit scout crossed the road recently.",
            "tags": ["road", "tracks", "bandit_risk"],
            "source": "deterministic_phase4_seeded_encounters",
        },
    }
    combat_result = {
        "ok": True,
        "edge_id": "route:old_road:old_mill",
        "encounter": {
            "encounter_id": "encounter:old_mill_route:bandit_patrol",
            "kind": "threat_signal",
            "summary": "A distant bandit patrol passes near the old mill route, not yet committing to combat.",
            "tags": ["route", "bandit_patrol", "combat_hook"],
            "source": "deterministic_phase4_seeded_encounters",
        },
    }
    no_encounter = {"ok": True, "location_id": "location:old_road", "encounter": {"encounter_id": NO_ENCOUNTER, "kind": "none"}}
    event_applied = apply_seeded_encounter_runtime(state, event_result, turn_index=7)
    combat_applied = apply_seeded_encounter_runtime(state, combat_result, turn_index=8)
    noop = apply_seeded_encounter_runtime({}, no_encounter, turn_index=9)
    seeded_sample = roll_seeded_encounter("phase4.10", 3, edge_id="route:old_road:old_mill")
    contract = build_encounter_runtime_narration_contract(combat_applied)
    blockers = []
    if event_applied.get("reason") != "encounter_world_event_recorded":
        blockers.append({"kind": "world_event_not_recorded", "source": SOURCE})
    if combat_applied.get("reason") != "combat_candidate_created":
        blockers.append({"kind": "combat_candidate_not_created", "source": SOURCE})
    if "combat_state" in state:
        blockers.append({"kind": "combat_state_mutated_without_canonical_api", "source": SOURCE})
    if noop.get("reason") != "encounter_runtime_noop":
        blockers.append({"kind": "no_encounter_not_noop", "source": SOURCE})
    if not contract.get("forbidden_encounter_runtime_claims"):
        blockers.append({"kind": "missing_encounter_runtime_guardrails", "source": SOURCE})
    if seeded_sample.get("ok") is not True:
        blockers.append({"kind": "seeded_encounter_sample_failed", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_encounter_combat_events_ready" if not blockers else "phase4_encounter_combat_events_not_ready",
        "event_result": event_applied,
        "combat_result": combat_applied,
        "noop_result": noop,
        "blockers": blockers,
        "source": SOURCE,
    }
