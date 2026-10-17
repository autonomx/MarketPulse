"""Phase 8.3 — Outcome Projection.

Derive structured world-impacting outcomes from recent events, consequences,
and encounters.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list, safe_str as _safe_str

_MAX_PROJECTED = 24


def project_outcomes_from_state(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Project world outcomes from simulation state.

    Args:
        simulation_state: The current simulation state dict.

    Returns:
        A list of projected outcome dicts, bounded to ``_MAX_PROJECTED``.
    """
    simulation_state = dict(simulation_state or {})
    tick = int(simulation_state.get("tick", 0) or 0)

    events = _safe_list(simulation_state.get("events"))
    consequences = _safe_list(simulation_state.get("consequences"))
    active_rumors = _safe_list(simulation_state.get("active_rumors"))
    encounter_state = dict((simulation_state.get("player_state") or {}).get("encounter_state") or {})

    projected: List[Dict[str, Any]] = []

    for item in events[-8:]:
        if not isinstance(item, dict):
            continue
        typ = _safe_str(item.get("type"))
        target_id = _safe_str(item.get("target_id"))
        location_id = _safe_str(item.get("location_id"))
        faction_id = _safe_str(item.get("faction_id"))

        if typ in {"betrayal", "player_escalation", "trust_collapse"}:
            projected.append({
                "type": "faction_pressure",
                "target_id": faction_id or target_id or location_id,
                "location_id": location_id,
                "summary": _safe_str(item.get("summary")) or typ,
                "tick": tick,
            })
        elif typ in {"player_support", "support", "stabilize"}:
            projected.append({
                "type": "location_stabilization",
                "target_id": location_id or target_id,
                "location_id": location_id,
                "summary": _safe_str(item.get("summary")) or typ,
                "tick": tick,
            })

    for item in consequences[-8:]:
        if not isinstance(item, dict):
            continue
        projected.append({
            "type": "thread_shift",
            "target_id": _safe_str(item.get("target_id")) or _safe_str(item.get("thread_id")),
            "location_id": _safe_str(item.get("location_id")),
            "summary": _safe_str(item.get("summary")) or _safe_str(item.get("type")),
            "tick": tick,
        })

    if encounter_state.get("status") == "resolved":
        projected.append({
            "type": "encounter_resolution",
            "target_id": _safe_str(encounter_state.get("scene_id")),
            "location_id": "",
            "summary": "Encounter resolution projected into world state.",
            "tick": tick,
        })

    for rumor in active_rumors[:4]:
        if not isinstance(rumor, dict):
            continue
        projected.append({
            "type": "rumor_pressure",
            "target_id": _safe_str(rumor.get("subject_id")) or _safe_str(rumor.get("faction_id")),
            "location_id": _safe_str(rumor.get("location_id")),
            "summary": _safe_str(rumor.get("text")) or _safe_str(rumor.get("type")),
            "tick": tick,
        })

    projected.sort(key=lambda x: (x.get("type", ""), x.get("target_id", ""), x.get("location_id", ""), x.get("summary", "")))
    return projected[:_MAX_PROJECTED]