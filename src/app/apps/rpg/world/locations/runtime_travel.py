from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.locations.discovery import discover_location, discover_route, unblock_route, validate_route_access
from app.apps.rpg.world.locations.graph import OLD_MILL, RUSTY_FLAGON
from app.apps.rpg.world.locations.travel import apply_travel, build_travel_narration_contract
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

SOURCE = "deterministic_phase4_runtime_travel_access"


def apply_runtime_travel(
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
        return {
            "ok": False,
            "reason": access.get("reason") or "route_access_denied",
            "access_result": access,
            "travel_result": None,
            "source": SOURCE,
        }
    travel = apply_travel(
        simulation_state,
        start_location_id=start_location_id,
        end_location_id=end_location_id,
        turn_index=turn_index,
    )
    if travel.get("ok") is not True:
        return {
            "ok": False,
            "reason": travel.get("reason") or "travel_not_applied",
            "access_result": access,
            "travel_result": travel,
            "source": SOURCE,
        }
    return {
        "ok": True,
        "reason": "runtime_travel_applied",
        "access_result": access,
        "travel_result": travel,
        "travel_log_entry": travel.get("travel_log_entry"),
        "source": SOURCE,
    }


def build_runtime_travel_narration_contract(runtime_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(runtime_result)
    allowed = [f"Runtime travel result: {result.get('reason')}"]
    access = _safe_dict(result.get("access_result"))
    if access:
        allowed.append(f"Route access result: {access.get('reason')}")
    travel_contract = build_travel_narration_contract(_safe_dict(result.get("travel_result")))
    allowed.extend(_safe_list(travel_contract.get("allowed_travel_claims")))
    return {
        "source": SOURCE,
        "allowed_runtime_travel_claims": allowed,
        "forbidden_runtime_travel_claims": [
            "Do not claim travel happened unless apply_runtime_travel returned ok=true.",
            "Do not bypass discovery or route-block validation for runtime travel commands.",
            "Do not claim blocked or undiscovered routes are passable.",
            "Do not invent travel resource consumption or inventory changes.",
        ],
    }


def assert_phase4_runtime_travel_access_ready() -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    denied_undiscovered = apply_runtime_travel(
        state,
        start_location_id=RUSTY_FLAGON,
        end_location_id=OLD_MILL,
        turn_index=1,
    )
    discover_location(state, location_id=OLD_MILL, reason="scouted_old_road", turn_index=2)
    discover_route(state, edge_id="route:old_road:old_mill", reason="scouted_old_road", turn_index=2)
    denied_blocked = apply_runtime_travel(
        state,
        start_location_id=RUSTY_FLAGON,
        end_location_id=OLD_MILL,
        turn_index=3,
    )
    unblock_route(state, edge_id="route:old_road:old_mill", reason="bandit_threat_resolved", turn_index=4)
    applied = apply_runtime_travel(
        state,
        start_location_id=RUSTY_FLAGON,
        end_location_id=OLD_MILL,
        turn_index=5,
    )
    unknown = apply_runtime_travel(
        {},
        start_location_id=RUSTY_FLAGON,
        end_location_id="location:missing",
        turn_index=1,
    )
    contract = build_runtime_travel_narration_contract(applied)
    blockers = []
    if denied_undiscovered.get("reason") != "undiscovered_location":
        blockers.append({"kind": "expected_undiscovered_location_denial", "source": SOURCE})
    if denied_blocked.get("reason") != "route_blocked":
        blockers.append({"kind": "expected_route_blocked_denial", "source": SOURCE})
    if applied.get("reason") != "runtime_travel_applied":
        blockers.append({"kind": "expected_runtime_travel_applied", "source": SOURCE})
    if unknown.get("reason") != "unknown_location":
        blockers.append({"kind": "expected_unknown_location_denial", "source": SOURCE})
    if not contract.get("forbidden_runtime_travel_claims"):
        blockers.append({"kind": "missing_runtime_travel_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_runtime_travel_access_ready" if not blockers else "phase4_runtime_travel_access_not_ready",
        "denied_undiscovered": denied_undiscovered,
        "denied_blocked": denied_blocked,
        "applied": applied,
        "unknown": unknown,
        "blockers": blockers,
        "source": SOURCE,
    }
