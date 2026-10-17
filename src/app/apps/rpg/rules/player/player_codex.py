"""Phase 8 — Player-facing codex.

Mirrors authoritative simulation state (npc_index, factions, locations,
threads) into a lightweight, read-only codex that the player UI can
render without risking accidental mutation.

Bounds:
    - 200 entries per bucket (npcs, factions, locations, threads)
"""
from __future__ import annotations

from typing import Any, Dict

from .player_scene_state import ensure_player_state
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str

_MAX_BUCKET = 200


def _trim_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    """Return at most _MAX_BUCKET entries, sorted by key."""
    items = sorted(bucket.items(), key=lambda item: str(item[0]))
    return dict(items[:_MAX_BUCKET])


def update_codex_from_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh the player codex from the current simulation state.

    Populates npcs, factions, locations, and threads buckets.
    """
    simulation_state = ensure_player_state(simulation_state)
    player_state = simulation_state["player_state"]
    codex = _safe_dict(player_state.get("codex"))
    codex.setdefault("npcs", {})
    codex.setdefault("factions", {})
    codex.setdefault("locations", {})
    codex.setdefault("threads", {})

    npc_index = _safe_dict(simulation_state.get("npc_index"))
    for npc_id, npc in sorted(npc_index.items()):
        npc = _safe_dict(npc)
        codex["npcs"][str(npc_id)] = {
            "id": str(npc_id),
            "name": _safe_str(npc.get("name")) or str(npc_id),
            "role": _safe_str(npc.get("role")),
            "faction_id": _safe_str(npc.get("faction_id")),
            "location_id": _safe_str(npc.get("location_id")),
        }

    for bucket_name in ("factions", "locations", "threads"):
        bucket = _safe_dict(simulation_state.get(bucket_name))
        for item_id, item in sorted(bucket.items()):
            item = _safe_dict(item)
            codex[bucket_name][str(item_id)] = {
                "id": str(item_id),
                "name": _safe_str(item.get("name")) or str(item_id),
                "status": _safe_str(item.get("status")),
                "pressure": int(item.get("pressure", 0) or 0),
            }

    for key in ("npcs", "factions", "locations", "threads"):
        codex[key] = _trim_bucket(_safe_dict(codex.get(key)))

    player_state["codex"] = codex
    return simulation_state