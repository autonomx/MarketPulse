"""Phase 8.3 — Rumor Feedback.

Update rumor heat feedback from projected outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list, safe_str as _safe_str


def update_rumor_feedback(simulation_state: Dict[str, Any], projected_outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Update rumor heat from projected outcomes.

    Args:
        simulation_state: The current simulation state dict.
        projected_outcomes: List of projected outcome dicts.

    Returns:
        Updated simulation_state with updated rumor heat in social_state.
    """
    simulation_state = dict(simulation_state or {})
    social_state = dict(simulation_state.get("social_state") or {})
    rumors = _safe_list(social_state.get("rumors"))

    for rumor in rumors:
        if not isinstance(rumor, dict):
            continue
        subject_id = _safe_str(rumor.get("subject_id"))
        for outcome in _safe_list(projected_outcomes):
            if not isinstance(outcome, dict):
                continue
            if _safe_str(outcome.get("target_id")) == subject_id:
                rumor["heat"] = min(3, int(rumor.get("heat", 0) or 0) + 1)

    social_state["rumors"] = rumors[:64]
    simulation_state["social_state"] = social_state
    return simulation_state