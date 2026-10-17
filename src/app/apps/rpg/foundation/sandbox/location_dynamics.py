"""Phase 8.3 — Location Dynamics.

Update location trend state from projected outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def update_location_trends(simulation_state: Dict[str, Any], projected_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Update location trends from projected outcomes.

    Args:
        simulation_state: The current simulation state dict.
        projected_outcomes: List of projected outcome dicts.

    Returns:
        Updated simulation_state with location_trends in sandbox_state.
    """
    simulation_state = dict(simulation_state or {})
    sandbox_state = simulation_state.setdefault("sandbox_state", {})
    trends = _safe_dict(sandbox_state.get("location_trends"))
    tick = int(simulation_state.get("tick", 0) or 0)

    for outcome in _safe_list(projected_outcomes):
        if not isinstance(outcome, dict):
            continue
        location_id = _safe_str(outcome.get("location_id")) or _safe_str(outcome.get("target_id"))
        if not location_id:
            continue

        rec = _safe_dict(trends.get(location_id))
        rec.setdefault("stability", 0.5)
        rec.setdefault("danger", 0.5)
        rec.setdefault("control_faction_id", "")
        rec["updated_tick"] = tick

        typ = _safe_str(outcome.get("type"))
        if typ == "location_stabilization":
            rec["stability"] = _clamp01(float(rec["stability"]) + 0.1)
            rec["danger"] = _clamp01(float(rec["danger"]) - 0.1)
        elif typ in {"faction_pressure", "rumor_pressure", "encounter_resolution"}:
            rec["danger"] = _clamp01(float(rec["danger"]) + 0.05)

        trends[location_id] = rec

    sandbox_state["location_trends"] = {
        k: trends[k] for k in sorted(trends)[:100]
    }
    return simulation_state