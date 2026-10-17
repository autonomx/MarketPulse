"""Phase 8.3 — World Consequence Builder.

Build world consequences from projected outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list, safe_str as _safe_str

_MAX_WORLD_CONSEQUENCES = 100


def build_world_consequences(simulation_state: Dict[str, Any], projected_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build world consequences from projected outcomes.

    Args:
        simulation_state: The current simulation state dict.
        projected_outcomes: List of projected outcome dicts.

    Returns:
        Updated simulation_state with world_consequences in sandbox_state.
    """
    simulation_state = dict(simulation_state or {})
    sandbox_state = simulation_state.setdefault("sandbox_state", {})
    tick = int(simulation_state.get("tick", 0) or 0)

    consequences = _safe_list(sandbox_state.get("world_consequences"))
    idx = len(consequences)

    for outcome in _safe_list(projected_outcomes):
        if not isinstance(outcome, dict):
            continue
        typ = _safe_str(outcome.get("type"))
        target_id = _safe_str(outcome.get("target_id"))
        if not typ or not target_id:
            continue

        if typ == "location_stabilization":
            ctype = "location_shift"
            severity = "positive"
        elif typ == "faction_pressure":
            ctype = "faction_shift"
            severity = "negative"
        elif typ == "thread_shift":
            ctype = "thread_shift"
            severity = "mixed"
        else:
            ctype = "rumor_shift"
            severity = "mixed"

        consequences.append({
            "consequence_id": f"wc:{tick}:{idx}",
            "type": ctype,
            "target_id": target_id,
            "summary": _safe_str(outcome.get("summary")) or typ,
            "severity": severity,
            "tick": tick,
        })
        idx += 1

    sandbox_state["world_consequences"] = consequences[-_MAX_WORLD_CONSEQUENCES:]
    return simulation_state