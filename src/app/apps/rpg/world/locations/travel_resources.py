from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Tuple

from app.apps.rpg.rules.economy.survival import FOOD_ITEM_IDS, WATER_ITEM_IDS, consume_food, consume_water
from app.apps.rpg.rules.items.inventory_state import normalize_inventory_state
from app.apps.rpg.world.locations.discovery import validate_route_access
from app.apps.rpg.world.locations.graph import OLD_MILL, OLD_ROAD, RUSTY_FLAGON
from app.apps.rpg.world.locations.runtime_travel import apply_runtime_travel
from app.apps.rpg.world.locations.travel import apply_causal_travel_projection, calculate_route_travel_cost
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_travel_resource_consumption"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _player_inventory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(_safe_dict(simulation_state).get("player_state"))
    return normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))


def _count_matching_items(inventory_state: Dict[str, Any], item_ids: Tuple[str, ...]) -> int:
    ids = {_safe_str(item_id) for item_id in item_ids if _safe_str(item_id)}
    return sum(
        max(0, _safe_int(_safe_dict(item).get("qty"), 0))
        for item in _safe_list(normalize_inventory_state(inventory_state).get("items"))
        if _safe_str(_safe_dict(item).get("item_id")) in ids
    )


def build_travel_resource_requirement(travel_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(travel_result)
    entry = _safe_dict(result.get("travel_log_entry"))
    if not entry:
        entry = _safe_dict(_safe_dict(result.get("travel_result")).get("travel_log_entry"))
    return {
        "ration_units": max(0, _safe_int(entry.get("ration_units"), 0)),
        "water_units": max(0, _safe_int(entry.get("water_units"), 0)),
        "source": SOURCE,
    }


def validate_travel_resources_available(
    simulation_state: Dict[str, Any],
    *,
    ration_units: int,
    water_units: int,
) -> Dict[str, Any]:
    inventory_state = _player_inventory_state(simulation_state)
    available_rations = _count_matching_items(inventory_state, FOOD_ITEM_IDS)
    available_water = _count_matching_items(inventory_state, WATER_ITEM_IDS)
    required_rations = max(0, _safe_int(ration_units, 0))
    required_water = max(0, _safe_int(water_units, 0))
    missing = []
    if available_rations < required_rations:
        missing.append(
            {
                "kind": "ration_units",
                "required": required_rations,
                "available": available_rations,
                "source": SOURCE,
            }
        )
    if available_water < required_water:
        missing.append(
            {
                "kind": "water_units",
                "required": required_water,
                "available": available_water,
                "source": SOURCE,
            }
        )
    return {
        "ok": not missing,
        "reason": "travel_resources_available" if not missing else "insufficient_travel_resources",
        "required": {"ration_units": required_rations, "water_units": required_water},
        "available": {"ration_units": available_rations, "water_units": available_water},
        "missing": missing,
        "source": SOURCE,
    }


def _append_travel_resource_log(simulation_state: Dict[str, Any], entry: Dict[str, Any]) -> None:
    economy_state = _safe_dict(_safe_dict(simulation_state).get("economy_state"))
    log = list(_safe_list(economy_state.get("travel_resource_log")))
    log.append(deepcopy(entry))
    economy_state["travel_resource_log"] = log[-50:]
    simulation_state["economy_state"] = economy_state


def apply_travel_resource_consumption(
    simulation_state: Dict[str, Any],
    travel_result: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    result = _safe_dict(travel_result)
    if result.get("ok") is not True:
        return {"ok": False, "reason": "travel_not_applied", "travel_result": result, "source": SOURCE}
    requirement = build_travel_resource_requirement(result)
    availability = validate_travel_resources_available(
        simulation_state,
        ration_units=_safe_int(requirement.get("ration_units"), 0),
        water_units=_safe_int(requirement.get("water_units"), 0),
    )
    if availability.get("ok") is not True:
        return {
            "ok": False,
            "reason": "insufficient_travel_resources",
            "requirement": requirement,
            "availability": availability,
            "source": SOURCE,
        }
    food_results = []
    water_results = []
    for _ in range(_safe_int(requirement.get("ration_units"), 0)):
        food_results.append(consume_food(simulation_state, tick=tick, recovery=1))
    for _ in range(_safe_int(requirement.get("water_units"), 0)):
        water_results.append(consume_water(simulation_state, tick=tick, recovery=1))
    failed = [row for row in food_results + water_results if _safe_dict(row).get("resolved") is not True]
    log_entry = {
        "kind": "travel_resource_consumption",
        "tick": max(0, _safe_int(tick, 0)),
        "ration_units": _safe_int(requirement.get("ration_units"), 0),
        "water_units": _safe_int(requirement.get("water_units"), 0),
        "reason": "travel_resources_consumed" if not failed else "travel_resource_consumption_failed",
        "source": SOURCE,
    }
    if not failed:
        _append_travel_resource_log(simulation_state, log_entry)
    return {
        "ok": not failed,
        "reason": log_entry["reason"],
        "requirement": requirement,
        "availability": availability,
        "food_results": food_results,
        "water_results": water_results,
        "log_entry": log_entry if not failed else {},
        "source": SOURCE,
    }


def apply_runtime_travel_with_resource_consumption(
    simulation_state: Dict[str, Any],
    *,
    start_location_id: str,
    end_location_id: str,
    turn_index: int = 0,
) -> Dict[str, Any]:
    access = validate_route_access(
        simulation_state,
        start_location_id=start_location_id,
        end_location_id=end_location_id,
    )
    if access.get("ok") is not True:
        return {"ok": False, "reason": access.get("reason") or "route_access_denied", "access_result": access, "source": SOURCE}
    cost = apply_causal_travel_projection(
        calculate_route_travel_cost(start_location_id, end_location_id),
        _safe_dict(simulation_state.get("travel_state")),
    )
    totals = _safe_dict(cost.get("totals"))
    availability = validate_travel_resources_available(
        simulation_state,
        ration_units=_safe_int(totals.get("ration_units"), 0),
        water_units=_safe_int(totals.get("water_units"), 0),
    )
    if availability.get("ok") is not True:
        return {
            "ok": False,
            "reason": "insufficient_travel_resources",
            "access_result": access,
            "cost": cost,
            "availability": availability,
            "travel_result": None,
            "resource_consumption_result": None,
            "source": SOURCE,
        }
    runtime = apply_runtime_travel(
        simulation_state,
        start_location_id=start_location_id,
        end_location_id=end_location_id,
        turn_index=turn_index,
    )
    if runtime.get("ok") is not True:
        return {
            "ok": False,
            "reason": runtime.get("reason") or "runtime_travel_not_applied",
            "access_result": access,
            "travel_result": runtime,
            "resource_consumption_result": None,
            "source": SOURCE,
        }
    runtime_entry = _safe_dict(runtime.get("travel_log_entry"))
    runtime_entry.update(
        {
            "ration_units": _safe_int(totals.get("ration_units"), 0),
            "water_units": _safe_int(totals.get("water_units"), 0),
            "causal_cost_multiplier_bps": _safe_int(cost.get("causal_cost_multiplier_bps"), 10000),
            "causal_safety_index": _safe_int(cost.get("causal_safety_index"), 100),
        }
    )
    runtime["travel_log_entry"] = runtime_entry
    runtime["causal_cost"] = cost
    consumption = apply_travel_resource_consumption(simulation_state, runtime, tick=turn_index)
    return {
        "ok": consumption.get("ok") is True,
        "reason": "runtime_travel_resources_consumed" if consumption.get("ok") is True else consumption.get("reason"),
        "access_result": access,
        "travel_result": runtime,
        "resource_consumption_result": consumption,
        "source": SOURCE,
    }


def build_travel_resource_narration_contract(consumption_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(consumption_result)
    allowed = [f"Travel resource result: {result.get('reason')}"]
    resource_result = _safe_dict(result.get("resource_consumption_result") or result)
    requirement = _safe_dict(resource_result.get("requirement"))
    if requirement:
        allowed.append(
            f"Consumed travel resources: ration_units={_safe_int(requirement.get('ration_units'), 0)}, "
            f"water_units={_safe_int(requirement.get('water_units'), 0)}"
        )
    return {
        "source": SOURCE,
        "allowed_travel_resource_claims": allowed,
        "forbidden_travel_resource_claims": [
            "Do not claim ration or water was consumed unless this result returned ok=true.",
            "Do not invent resource requirements beyond the deterministic travel cost totals.",
            "Do not mutate inventory directly; use canonical survival consumption APIs.",
            "Do not claim survival, quest, combat, XP, route access, or discovery changes beyond returned source-backed rows.",
        ],
    }


def assert_phase4_travel_resource_consumption_ready() -> Dict[str, Any]:
    state = {
        "player_state": {
            "inventory_state": {"items": [{"item_id": "ration", "qty": 1}, {"item_id": "water_skin", "qty": 2}]},
            "survival_state": {"hunger": 10, "thirst": 10},
        }
    }
    from app.apps.rpg.world.locations.discovery import discover_location, discover_route, unblock_route

    discover_location(state, location_id=OLD_MILL, reason="scouted_old_road", turn_index=1)
    discover_route(state, edge_id="route:old_road:old_mill", reason="scouted_old_road", turn_index=1)
    unblock_route(state, edge_id="route:old_road:old_mill", reason="bandit_threat_resolved", turn_index=2)
    applied = apply_runtime_travel_with_resource_consumption(
        state,
        start_location_id=RUSTY_FLAGON,
        end_location_id=OLD_MILL,
        turn_index=3,
    )
    missing = apply_runtime_travel_with_resource_consumption(
        {"player_state": {"inventory_state": {"items": []}}},
        start_location_id=RUSTY_FLAGON,
        end_location_id=OLD_ROAD,
        turn_index=4,
    )
    contract = build_travel_resource_narration_contract(applied)
    blockers: list[dict[str, Any]] = []
    items = {row.get("item_id"): row.get("qty") for row in _safe_list(_player_inventory_state(state).get("items"))}
    if applied.get("reason") != "runtime_travel_resources_consumed":
        blockers.append({"kind": "expected_runtime_travel_resources_consumed", "source": SOURCE})
    if "ration" in items or "water_skin" in items:
        blockers.append({"kind": "expected_travel_items_consumed", "items": items, "source": SOURCE})
    if missing.get("reason") != "insufficient_travel_resources":
        blockers.append({"kind": "expected_missing_resources_rejection", "source": SOURCE})
    if not contract.get("forbidden_travel_resource_claims"):
        blockers.append({"kind": "missing_travel_resource_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_travel_resource_consumption_ready" if not blockers else "phase4_travel_resource_consumption_not_ready",
        "applied": applied,
        "missing": missing,
        "blockers": blockers,
        "source": SOURCE,
    }
