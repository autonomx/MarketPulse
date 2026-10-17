from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, cast

from app.apps.rpg.world.locations.graph import OLD_MILL, OLD_ROAD, RUSTY_FLAGON, find_location_route, get_canonical_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_travel_costs"

DEFAULT_START_LOCATION_ID = RUSTY_FLAGON
MAX_FATIGUE = 100

ROUTE_TRAVEL_COSTS: Dict[str, Dict[str, Any]] = {
    "route:rusty_flagon:old_road": {
        "edge_id": "route:rusty_flagon:old_road",
        "minutes": 20,
        "fatigue": 4,
        "ration_units": 0,
        "water_units": 1,
        "risk": "low",
        "source": SOURCE,
    },
    "route:rusty_flagon:market": {
        "edge_id": "route:rusty_flagon:market",
        "minutes": 5,
        "fatigue": 1,
        "ration_units": 0,
        "water_units": 0,
        "risk": "safe",
        "source": SOURCE,
    },
    "route:market:old_road": {
        "edge_id": "route:market:old_road",
        "minutes": 18,
        "fatigue": 3,
        "ration_units": 0,
        "water_units": 1,
        "risk": "low",
        "source": SOURCE,
    },
    "route:old_road:old_mill": {
        "edge_id": "route:old_road:old_mill",
        "minutes": 35,
        "fatigue": 8,
        "ration_units": 1,
        "water_units": 1,
        "risk": "bandit_risk",
        "source": SOURCE,
    },
    "route:old_road:nearby_wilderness": {
        "edge_id": "route:old_road:nearby_wilderness",
        "minutes": 25,
        "fatigue": 7,
        "ration_units": 1,
        "water_units": 1,
        "risk": "wilderness_risk",
        "source": SOURCE,
    },
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_fatigue(value: int) -> int:
    return max(0, min(MAX_FATIGUE, int(value)))


def ensure_travel_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    travel_state = _safe_dict(state.get("travel_state"))
    if not travel_state:
        travel_state = {}
        state["travel_state"] = travel_state
    current = _safe_str(travel_state.get("current_location_id")) or DEFAULT_START_LOCATION_ID
    travel_state["current_location_id"] = current
    travel_state["elapsed_minutes"] = max(0, _safe_int(travel_state.get("elapsed_minutes"), 0))
    travel_state["fatigue"] = _clamp_fatigue(_safe_int(travel_state.get("fatigue"), 0))
    travel_state["travel_log"] = list(_safe_list(travel_state.get("travel_log")))
    travel_state["source"] = SOURCE
    return travel_state


def get_route_travel_cost(edge_id: str) -> Dict[str, Any] | None:
    cost = ROUTE_TRAVEL_COSTS.get(_safe_str(edge_id))
    return deepcopy(cost) if cost else None


def validate_route_travel_costs() -> Dict[str, Any]:
    blockers = []
    for edge_id, cost in ROUTE_TRAVEL_COSTS.items():
        if cost.get("source") != SOURCE:
            blockers.append({"kind": "missing_source", "edge_id": edge_id, "source": SOURCE})
        if _safe_int(cost.get("minutes"), -1) <= 0:
            blockers.append({"kind": "non_positive_minutes", "edge_id": edge_id, "source": SOURCE})
        if _safe_int(cost.get("fatigue"), -1) < 0:
            blockers.append({"kind": "negative_fatigue", "edge_id": edge_id, "source": SOURCE})
        if _safe_int(cost.get("ration_units"), -1) < 0 or _safe_int(cost.get("water_units"), -1) < 0:
            blockers.append({"kind": "negative_resource_cost", "edge_id": edge_id, "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "route_travel_costs_valid" if not blockers else "route_travel_costs_invalid",
        "route_cost_count": len(ROUTE_TRAVEL_COSTS),
        "blockers": blockers,
        "source": SOURCE,
    }


def calculate_route_travel_cost(start_location_id: str, end_location_id: str) -> Dict[str, Any]:
    route = find_location_route(start_location_id, end_location_id)
    if not route.get("ok"):
        return {
            "ok": False,
            "reason": route.get("reason") or "route_unavailable",
            "route": route,
            "source": SOURCE,
        }
    edge_costs = []
    totals = {"minutes": 0, "fatigue": 0, "ration_units": 0, "water_units": 0}
    risk_flags: List[str] = []
    missing_costs: List[str] = []
    for edge in _safe_list(route.get("edges")):
        edge_id = _safe_str(_safe_dict(edge).get("edge_id"))
        cost = get_route_travel_cost(edge_id)
        if not cost:
            missing_costs.append(edge_id)
            continue
        edge_costs.append(cost)
        for key in totals:
            totals[key] += _safe_int(cost.get(key), 0)
        risk = _safe_str(cost.get("risk"))
        if risk and risk not in risk_flags:
            risk_flags.append(risk)
    if missing_costs:
        return {
            "ok": False,
            "reason": "missing_route_travel_costs",
            "missing_costs": missing_costs,
            "route": route,
            "source": SOURCE,
        }
    return {
        "ok": True,
        "reason": "route_travel_cost_calculated",
        "start_location_id": _safe_str(start_location_id),
        "end_location_id": _safe_str(end_location_id),
        "path": list(_safe_list(route.get("path"))),
        "edge_costs": edge_costs,
        "totals": totals,
        "risk_flags": risk_flags,
        "source": SOURCE,
    }


def apply_causal_travel_projection(
    cost: Dict[str, Any],
    travel_state: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Apply the projected causal multiplier to one already validated route cost."""

    result = deepcopy(_safe_dict(cost))
    if result.get("ok") is not True:
        return result
    state = _safe_dict(travel_state)
    multiplier_bps = max(8000, min(16000, _safe_int(state.get("causal_cost_multiplier_bps"), 10000)))
    safety_index = max(0, min(100, _safe_int(state.get("causal_safety_index"), 100)))
    totals = _safe_dict(result.get("totals"))
    adjusted: Dict[str, int] = {}
    for key in ("minutes", "fatigue", "ration_units", "water_units"):
        base = max(0, _safe_int(totals.get(key), 0))
        value = int(round(base * multiplier_bps / 10000))
        adjusted[key] = max(1, value) if base > 0 else 0
    result["base_totals"] = deepcopy(totals)
    result["totals"] = adjusted
    result["causal_cost_multiplier_bps"] = multiplier_bps
    result["causal_safety_index"] = safety_index
    result["causal_projection_source"] = "deterministic_causal_runtime"
    risk_flags = list(_safe_list(result.get("risk_flags")))
    if safety_index < 35 and "causal_instability" not in risk_flags:
        risk_flags.append("causal_instability")
    result["risk_flags"] = risk_flags
    return result


def apply_travel(
    simulation_state: Dict[str, Any],
    *,
    start_location_id: str,
    end_location_id: str,
    turn_index: int = 0,
) -> Dict[str, Any]:
    travel_state = ensure_travel_state(simulation_state)
    start = _safe_str(start_location_id)
    end = _safe_str(end_location_id)
    current = _safe_str(travel_state.get("current_location_id"))
    if current != start:
        return {
            "ok": False,
            "reason": "travel_start_mismatch",
            "current_location_id": current,
            "requested_start_location_id": start,
            "source": SOURCE,
        }
    if not get_canonical_location(end):
        return {"ok": False, "reason": "unknown_destination", "destination_id": end, "source": SOURCE}
    cost = apply_causal_travel_projection(
        calculate_route_travel_cost(start, end),
        travel_state,
    )
    if not cost.get("ok"):
        return {"ok": False, "reason": cost.get("reason"), "cost": cost, "source": SOURCE}
    totals = _safe_dict(cost.get("totals"))
    before = {
        "location_id": current,
        "elapsed_minutes": _safe_int(travel_state.get("elapsed_minutes"), 0),
        "fatigue": _safe_int(travel_state.get("fatigue"), 0),
    }
    after_fatigue = _clamp_fatigue(cast(int, before["fatigue"]) + _safe_int(totals.get("fatigue"), 0))
    after = {
        "location_id": end,
        "elapsed_minutes": cast(int, before["elapsed_minutes"]) + _safe_int(totals.get("minutes"), 0),
        "fatigue": after_fatigue,
    }
    entry = {
        "turn_index": int(turn_index or 0),
        "from": start,
        "to": end,
        "path": list(_safe_list(cost.get("path"))),
        "minutes": _safe_int(totals.get("minutes"), 0),
        "fatigue_delta": _safe_int(totals.get("fatigue"), 0),
        "ration_units": _safe_int(totals.get("ration_units"), 0),
        "water_units": _safe_int(totals.get("water_units"), 0),
        "risk_flags": list(_safe_list(cost.get("risk_flags"))),
        "causal_cost_multiplier_bps": _safe_int(cost.get("causal_cost_multiplier_bps"), 10000),
        "source": SOURCE,
    }
    travel_state["current_location_id"] = end
    travel_state["elapsed_minutes"] = after["elapsed_minutes"]
    travel_state["fatigue"] = after["fatigue"]
    travel_state["last_travel"] = deepcopy(entry)
    travel_state["travel_log"] = list(_safe_list(travel_state.get("travel_log"))) + [deepcopy(entry)]
    return {
        "ok": True,
        "reason": "travel_applied",
        "before": before,
        "after": after,
        "cost": cost,
        "travel_log_entry": entry,
        "source": SOURCE,
    }


def build_travel_narration_contract(travel_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(travel_result)
    allowed_claims = []
    if result.get("ok"):
        entry = _safe_dict(result.get("travel_log_entry"))
        allowed_claims.extend(
            [
                f"Travel path: {' -> '.join(_safe_list(entry.get('path')))}",
                f"Travel minutes: {_safe_int(entry.get('minutes'), 0)}",
                f"Fatigue increased by {_safe_int(entry.get('fatigue_delta'), 0)}",
                f"Resource costs: ration_units={_safe_int(entry.get('ration_units'), 0)}, water_units={_safe_int(entry.get('water_units'), 0)}",
            ]
        )
    return {
        "source": SOURCE,
        "allowed_travel_claims": allowed_claims,
        "forbidden_travel_claims": [
            "Do not invent travel time, fatigue, or resource costs.",
            "Do not claim the player arrived unless apply_travel returned ok=true.",
            "Do not claim inventory items were consumed; this phase records travel resource costs only.",
        ],
    }


def assert_phase4_travel_costs_ready() -> Dict[str, Any]:
    validation = validate_route_travel_costs()
    old_mill_cost = calculate_route_travel_cost(RUSTY_FLAGON, OLD_MILL)
    expected_path = [RUSTY_FLAGON, OLD_ROAD, OLD_MILL]
    blockers = list(_safe_list(validation.get("blockers")))
    if old_mill_cost.get("path") != expected_path:
        blockers.append({"kind": "missing_required_old_mill_travel_path", "expected_path": expected_path, "source": SOURCE})
    totals = _safe_dict(old_mill_cost.get("totals"))
    if _safe_int(totals.get("minutes"), 0) != 55 or _safe_int(totals.get("fatigue"), 0) != 12:
        blockers.append({"kind": "unexpected_old_mill_travel_totals", "expected_minutes": 55, "expected_fatigue": 12, "source": SOURCE})
    ok = validation.get("ok") is True and old_mill_cost.get("ok") is True and not blockers
    return {
        "ok": ok,
        "reason": "phase4_travel_costs_ready" if ok else "phase4_travel_costs_not_ready",
        "validation": validation,
        "old_mill_cost": old_mill_cost,
        "blockers": blockers,
        "source": SOURCE,
    }
