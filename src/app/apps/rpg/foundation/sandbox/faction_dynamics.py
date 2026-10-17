"""Phase 8.3 — Faction Dynamics.

Update faction trend state from projected outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def update_faction_trends(simulation_state: Dict[str, Any], projected_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Update faction trends from projected outcomes.

    Args:
        simulation_state: The current simulation state dict.
        projected_outcomes: List of projected outcome dicts.

    Returns:
        Updated simulation_state with faction_trends in sandbox_state.
    """
    simulation_state = dict(simulation_state or {})
    sandbox_state = simulation_state.setdefault("sandbox_state", {})
    trends = _safe_dict(sandbox_state.get("faction_trends"))
    tick = int(simulation_state.get("tick", 0) or 0)

    for outcome in _safe_list(projected_outcomes):
        if not isinstance(outcome, dict):
            continue
        if _safe_str(outcome.get("type")) != "faction_pressure":
            continue

        faction_id = _safe_str(outcome.get("target_id"))
        if not faction_id:
            continue

        rec = _safe_dict(trends.get(faction_id))
        rec.setdefault("momentum", 0.5)
        rec.setdefault("cohesion", 0.5)
        rec.setdefault("aggression", 0.5)
        rec["updated_tick"] = tick

        rec["aggression"] = _clamp01(float(rec["aggression"]) + 0.1)
        rec["momentum"] = _clamp01(float(rec["momentum"]) + 0.05)

        trends[faction_id] = rec

    sandbox_state["faction_trends"] = {
        k: trends[k] for k in sorted(trends)[:100]
    }
    return simulation_state