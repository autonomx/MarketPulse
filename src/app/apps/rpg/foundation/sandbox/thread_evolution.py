"""Phase 8.3 — Thread Evolution.

Update thread trend state from projected outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def update_thread_trends(simulation_state: Dict[str, Any], projected_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Update thread trends from projected outcomes.

    Args:
        simulation_state: The current simulation state dict.
        projected_outcomes: List of projected outcome dicts.

    Returns:
        Updated simulation_state with thread_trends in sandbox_state.
    """
    simulation_state = dict(simulation_state or {})
    sandbox_state = simulation_state.setdefault("sandbox_state", {})
    trends = _safe_dict(sandbox_state.get("thread_trends"))
    tick = int(simulation_state.get("tick", 0) or 0)

    for outcome in _safe_list(projected_outcomes):
        if not isinstance(outcome, dict):
            continue
        if _safe_str(outcome.get("type")) != "thread_shift":
            continue

        thread_id = _safe_str(outcome.get("target_id"))
        if not thread_id:
            continue

        rec = _safe_dict(trends.get(thread_id))
        rec.setdefault("trajectory", "cooling")
        rec.setdefault("intensity", 0.5)
        rec["updated_tick"] = tick

        summary = _safe_str(outcome.get("summary")).lower()
        if "resolve" in summary:
            rec["trajectory"] = "resolved"
            rec["intensity"] = _clamp01(float(rec["intensity"]) - 0.3)
        elif "branch" in summary:
            rec["trajectory"] = "branching"
            rec["intensity"] = _clamp01(float(rec["intensity"]) + 0.2)
        else:
            rec["trajectory"] = "escalating"
            rec["intensity"] = _clamp01(float(rec["intensity"]) + 0.1)

        trends[thread_id] = rec

    sandbox_state["thread_trends"] = {
        k: trends[k] for k in sorted(trends)[:100]
    }
    return simulation_state