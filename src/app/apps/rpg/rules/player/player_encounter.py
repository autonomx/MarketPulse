"""Phase 8 — Player-facing encounter view.

Builds a structured encounter payload that bundles scene actors,
player choices, and pressure context for the UI to render.
"""
from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import list_copy as _safe_list, safe_str as _safe_str


def build_encounter_view(scene: Dict[str, Any], simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a player-facing encounter view from a scene.

    Parameters
    ----------
    scene :
        Scene dict with actors, choices, etc.
    simulation_state :
        Authoritative simulation state (for pressure context).

    Returns
    -------
    dict
        Encounter view dict with scene_id, actors, choices, and
        pressure_context.
    """
    scene = dict(scene or {})
    simulation_state = dict(simulation_state or {})

    actors: list[dict[str, str]] = []
    for actor in _safe_list(scene.get("actors"))[:8]:
        if isinstance(actor, dict):
            actors.append({
                "id": _safe_str(actor.get("id")),
                "name": _safe_str(actor.get("name")) or _safe_str(actor.get("id")),
                "role": _safe_str(actor.get("role")),
                "faction_id": _safe_str(actor.get("faction_id")),
            })
        else:
            actors.append({
                "id": _safe_str(actor),
                "name": _safe_str(actor),
                "role": "",
                "faction_id": "",
            })

    return {
        "scene_id": _safe_str(scene.get("scene_id")),
        "scene_title": _safe_str(scene.get("title")),
        "scene_type": _safe_str(scene.get("scene_type") or scene.get("type")),
        "encounter_state": "active",
        "actors": actors,
        "choices": [
            dict(choice)
            for choice in _safe_list(scene.get("choices"))[:8]
            if isinstance(choice, dict)
        ],
        "pressure_context": {
            "threads": sorted(
                [
                    {"id": k, "pressure": int(v.get("pressure", 0) or 0)}
                    for k, v in (simulation_state.get("threads") or {}).items()
                    if isinstance(v, dict)
                ],
                key=lambda x: (-x["pressure"], x["id"])
            )[:3]
        },
    }