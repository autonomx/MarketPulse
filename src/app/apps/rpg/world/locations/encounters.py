from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.world.locations.graph import (
    NEARBY_WILDERNESS,
    OLD_ROAD,
    get_canonical_location,
    list_canonical_edges,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_seeded_encounters"

NO_ENCOUNTER = "encounter:no_encounter"

ENCOUNTER_TABLES: Dict[str, List[Dict[str, Any]]] = {
    OLD_ROAD: [
        {
            "encounter_id": "encounter:old_road:traveler_warning",
            "kind": "warning",
            "summary": "A nervous traveler warns that the old road has seen fresh bandit movement.",
            "weight": 2,
            "tags": ["road", "rumor", "bandit_risk"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:old_road:bandit_scout_tracks",
            "kind": "evidence",
            "summary": "Boot prints and snapped brush suggest a bandit scout crossed the road recently.",
            "weight": 2,
            "tags": ["road", "tracks", "bandit_risk"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:old_road:suspicious_camp_smoke",
            "kind": "evidence",
            "summary": "Thin camp smoke curls beyond the trees near the old road.",
            "weight": 1,
            "tags": ["road", "smoke", "scouting"],
            "source": SOURCE,
        },
        {
            "encounter_id": NO_ENCOUNTER,
            "kind": "none",
            "summary": "No encounter interrupts this stretch of the old road.",
            "weight": 3,
            "tags": ["quiet"],
            "source": SOURCE,
        },
    ],
    "route:old_road:old_mill": [
        {
            "encounter_id": "encounter:old_mill_route:bandit_ambush_warning",
            "kind": "warning",
            "summary": "A crow-scattered silence and cut rope warn of a possible ambush near the mill spur.",
            "weight": 2,
            "tags": ["route", "warning", "bandit_risk"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:old_mill_route:bandit_patrol",
            "kind": "threat_signal",
            "summary": "A distant bandit patrol passes near the old mill route, not yet committing to combat.",
            "weight": 1,
            "tags": ["route", "bandit_patrol", "combat_hook"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:old_mill_route:broken_cart_evidence",
            "kind": "evidence",
            "summary": "A broken cart axle and scattered grain mark where someone was stopped on the mill spur.",
            "weight": 2,
            "tags": ["route", "evidence", "quest_hook"],
            "source": SOURCE,
        },
        {
            "encounter_id": NO_ENCOUNTER,
            "kind": "none",
            "summary": "No encounter interrupts the old mill route.",
            "weight": 2,
            "tags": ["quiet"],
            "source": SOURCE,
        },
    ],
    NEARBY_WILDERNESS: [
        {
            "encounter_id": "encounter:wilderness:wildlife_tracks",
            "kind": "evidence",
            "summary": "Fresh wildlife tracks cross the scrub and gullies.",
            "weight": 2,
            "tags": ["wilderness", "tracks"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:wilderness:foraging_opportunity",
            "kind": "opportunity",
            "summary": "Edible berries and dry kindling suggest a safe foraging opportunity if later systems use it.",
            "weight": 1,
            "tags": ["wilderness", "forage", "resource_hook"],
            "source": SOURCE,
        },
        {
            "encounter_id": "encounter:wilderness:wolf_howl",
            "kind": "warning",
            "summary": "A wolf howl carries from the brush, warning of nearby wildlife pressure.",
            "weight": 1,
            "tags": ["wilderness", "wildlife", "warning"],
            "source": SOURCE,
        },
        {
            "encounter_id": NO_ENCOUNTER,
            "kind": "none",
            "summary": "No encounter interrupts the nearby wilderness.",
            "weight": 3,
            "tags": ["quiet"],
            "source": SOURCE,
        },
    ],
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _known_edge_ids() -> set[str]:
    return {_safe_str(edge.get("edge_id")) for edge in list_canonical_edges()}


def _table_key(location_id: str | None = None, edge_id: str | None = None) -> str:
    edge = _safe_str(edge_id)
    location = _safe_str(location_id)
    return edge or location


def ensure_encounter_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    encounter_state = _safe_dict(state.get("encounter_state"))
    if not encounter_state:
        encounter_state = {}
        state["encounter_state"] = encounter_state
    encounter_state["encounter_log"] = list(_safe_list(encounter_state.get("encounter_log")))
    encounter_state["last_encounter"] = _safe_dict(encounter_state.get("last_encounter")) or None
    encounter_state["source"] = SOURCE
    return encounter_state


def list_encounter_table(location_id: str | None = None, edge_id: str | None = None) -> Dict[str, Any]:
    location = _safe_str(location_id)
    edge = _safe_str(edge_id)
    if edge and edge not in _known_edge_ids():
        return {"ok": False, "reason": "unknown_route", "edge_id": edge, "encounters": [], "source": SOURCE}
    if location and not get_canonical_location(location):
        return {"ok": False, "reason": "unknown_location", "location_id": location, "encounters": [], "source": SOURCE}
    key = _table_key(location, edge)
    table = ENCOUNTER_TABLES.get(key)
    if table is None:
        return {"ok": True, "reason": "encounter_table_empty", "table_key": key, "encounters": [], "source": SOURCE}
    return {
        "ok": True,
        "reason": "encounter_table_listed",
        "table_key": key,
        "location_id": location,
        "edge_id": edge,
        "encounters": deepcopy(table),
        "source": SOURCE,
    }


def roll_seeded_encounter(
    seed: str | int,
    turn_index: int,
    location_id: str | None = None,
    edge_id: str | None = None,
) -> Dict[str, Any]:
    table_result = list_encounter_table(location_id=location_id, edge_id=edge_id)
    if not table_result.get("ok"):
        return dict(table_result)
    table = list(_safe_list(table_result.get("encounters")))
    if not table:
        return {
            "ok": True,
            "reason": "no_encounter_table",
            "table_key": table_result.get("table_key"),
            "encounter": None,
            "source": SOURCE,
        }
    total_weight = sum(max(1, _safe_int(row.get("weight"), 1)) for row in table)
    material = f"{_safe_str(seed)}|{int(turn_index or 0)}|{table_result.get('table_key')}|{SOURCE}".encode("utf-8")
    roll = int(hashlib.sha256(material).hexdigest()[:12], 16) % total_weight
    running = 0
    chosen = table[-1]
    for row in table:
        running += max(1, _safe_int(row.get("weight"), 1))
        if roll < running:
            chosen = row
            break
    return {
        "ok": True,
        "reason": "seeded_encounter_rolled",
        "seed": _safe_str(seed),
        "turn_index": int(turn_index or 0),
        "table_key": table_result.get("table_key"),
        "location_id": _safe_str(location_id),
        "edge_id": _safe_str(edge_id),
        "roll": roll,
        "total_weight": total_weight,
        "encounter": deepcopy(chosen),
        "source": SOURCE,
    }


def record_encounter(simulation_state: Dict[str, Any], encounter_result: Dict[str, Any], turn_index: int = 0) -> Dict[str, Any]:
    encounter_state = ensure_encounter_state(simulation_state)
    result = _safe_dict(encounter_result)
    if result.get("ok") is not True:
        return {"ok": False, "reason": "encounter_result_not_recorded", "encounter_result": result, "source": SOURCE}
    entry = {
        "turn_index": int(turn_index or result.get("turn_index") or 0),
        "table_key": _safe_str(result.get("table_key")),
        "location_id": _safe_str(result.get("location_id")),
        "edge_id": _safe_str(result.get("edge_id")),
        "encounter": deepcopy(result.get("encounter")),
        "source": SOURCE,
    }
    encounter_state["last_encounter"] = deepcopy(entry)
    encounter_state["encounter_log"] = list(_safe_list(encounter_state.get("encounter_log"))) + [deepcopy(entry)]
    return {"ok": True, "reason": "encounter_recorded", "encounter_log_entry": entry, "source": SOURCE}


def build_encounter_narration_contract(encounter_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(encounter_result)
    encounter = _safe_dict(result.get("encounter"))
    allowed = [f"Encounter result: {result.get('reason')}"]
    if encounter:
        allowed.extend(
            [
                f"Encounter id: {encounter.get('encounter_id')}",
                f"Encounter kind: {encounter.get('kind')}",
                f"Encounter summary: {encounter.get('summary')}",
            ]
        )
    return {
        "source": SOURCE,
        "allowed_encounter_claims": allowed,
        "forbidden_encounter_claims": [
            "Do not invent enemies, rewards, locations, route access, or combat outcomes.",
            "Do not start combat or apply damage unless a later deterministic combat bridge does so.",
            "Do not claim items, currency, XP, quest state, discovery state, or survival resources changed.",
        ],
    }


def assert_phase4_seeded_encounters_ready() -> Dict[str, Any]:
    blockers = []
    old_road_a = roll_seeded_encounter("phase4", 3, location_id=OLD_ROAD)
    old_road_b = roll_seeded_encounter("phase4", 3, location_id=OLD_ROAD)
    mill_route = roll_seeded_encounter("phase4", 3, edge_id="route:old_road:old_mill")
    unknown = roll_seeded_encounter("phase4", 3, location_id="location:unknown")
    state: Dict[str, Any] = {"player_state": {"hp": 10}, "combat_state": {"active": False}}
    recorded = record_encounter(state, old_road_a, turn_index=3)
    contract = build_encounter_narration_contract(old_road_a)
    if old_road_a != old_road_b:
        blockers.append({"kind": "same_seed_not_deterministic", "source": SOURCE})
    if mill_route.get("encounter", {}).get("encounter_id") not in {
        row["encounter_id"] for row in ENCOUNTER_TABLES["route:old_road:old_mill"]
    }:
        blockers.append({"kind": "route_roll_outside_table", "source": SOURCE})
    if unknown.get("reason") != "unknown_location":
        blockers.append({"kind": "unknown_location_not_rejected", "source": SOURCE})
    if recorded.get("reason") != "encounter_recorded" or len(state.get("encounter_state", {}).get("encounter_log", [])) != 1:
        blockers.append({"kind": "encounter_not_recorded", "source": SOURCE})
    if state.get("player_state") != {"hp": 10} or state.get("combat_state") != {"active": False}:
        blockers.append({"kind": "encounter_mutated_player_or_combat_state", "source": SOURCE})
    if not contract.get("forbidden_encounter_claims"):
        blockers.append({"kind": "missing_narration_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_seeded_encounters_ready" if not blockers else "phase4_seeded_encounters_not_ready",
        "sample_location_roll": old_road_a,
        "sample_route_roll": mill_route,
        "recorded": recorded,
        "blockers": blockers,
        "source": SOURCE,
    }
