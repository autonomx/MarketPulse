"""Phase 18.3A — Controlled dynamic world expansion.

Allows bounded creation of new entities after startup.
All generated entities are recorded in simulation state.
IDs are deterministic. LLM flavor is normalized before commit.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _deterministic_id(prefix: str, seed_str: str) -> str:
    return prefix + "_" + hashlib.sha256(seed_str.encode()).hexdigest()[:10]


def _get_expansion(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    sim = _safe_dict(simulation_state)
    return _safe_dict(sim.get("world_expansion"))


def _check_budget(expansion: Dict[str, Any], entity_type: str) -> bool:
    """Check if we have budget to spawn another entity of this type."""
    total_budget = _safe_int(expansion.get("world_growth_budget"), 20)
    total_spawned = _safe_int(expansion.get("entities_spawned"), 0)
    if total_spawned >= total_budget:
        return False

    type_budget_key = f"{entity_type}_budget"
    type_budget = _safe_int(expansion.get(type_budget_key), 10)
    type_count_key = f"{entity_type}s_spawned"
    type_count = _safe_int(expansion.get(type_count_key), 0)
    return type_count < type_budget


def _increment_budget(expansion: Dict[str, Any], entity_type: str) -> Dict[str, Any]:
    expansion = dict(expansion)
    expansion["entities_spawned"] = _safe_int(expansion.get("entities_spawned"), 0) + 1
    type_count_key = f"{entity_type}s_spawned"
    expansion[type_count_key] = _safe_int(expansion.get(type_count_key), 0) + 1
    return expansion


def maybe_spawn_dynamic_npc(
    simulation_state: Dict[str, Any],
    trigger: Dict[str, Any],
) -> Dict[str, Any]:
    """Attempt to spawn a dynamic NPC if budget allows.

    trigger should contain: name, role, faction (optional), location (optional)
    Returns updated simulation_state with new NPC added (or unchanged if budget exceeded).
    """
    sim = dict(simulation_state or {})
    expansion = _get_expansion(sim)

    if not expansion.get("allow_dynamic_npc_generation", True):
        sim["_spawn_result"] = {"ok": False, "reason": "npc_generation_disabled"}
        return sim

    if not _check_budget(expansion, "npc"):
        sim["_spawn_result"] = {"ok": False, "reason": "budget_exceeded"}
        return sim

    trigger = _safe_dict(trigger)
    name = _safe_str(trigger.get("name")) or "Unknown NPC"
    npc_id = _safe_str(trigger.get("npc_id")) or _deterministic_id("npc", name + _safe_str(trigger.get("role", "")))

    npc = {
        "npc_id": npc_id,
        "name": name,
        "role": _safe_str(trigger.get("role")),
        "faction": _safe_str(trigger.get("faction")),
        "location": _safe_str(trigger.get("location")),
        "seed_origin": "dynamic",
        "spawn_tick": _safe_int(sim.get("tick"), 0),
        "disposition": _safe_str(trigger.get("disposition", "neutral")),
    }

    npcs = list(sim.get("npcs") or sim.get("npc_seeds") or [])
    npcs.append(npc)
    sim["npcs"] = npcs

    expansion = _increment_budget(expansion, "npc")
    sim["world_expansion"] = expansion
    sim["_spawn_result"] = {"ok": True, "entity_type": "npc", "entity_id": npc_id}
    return sim


def maybe_spawn_dynamic_location(
    simulation_state: Dict[str, Any],
    trigger: Dict[str, Any],
) -> Dict[str, Any]:
    """Attempt to spawn a dynamic location if budget allows."""
    sim = dict(simulation_state or {})
    expansion = _get_expansion(sim)

    if not expansion.get("allow_dynamic_location_generation", True):
        sim["_spawn_result"] = {"ok": False, "reason": "location_generation_disabled"}
        return sim

    if not _check_budget(expansion, "location"):
        sim["_spawn_result"] = {"ok": False, "reason": "budget_exceeded"}
        return sim

    trigger = _safe_dict(trigger)
    name = _safe_str(trigger.get("name")) or "Unknown Location"
    loc_id = _safe_str(trigger.get("location_id")) or _deterministic_id("loc", name)

    location = {
        "location_id": loc_id,
        "name": name,
        "type": _safe_str(trigger.get("type", "area")),
        "description": _safe_str(trigger.get("description", "")),
        "seed_origin": "dynamic",
        "spawn_tick": _safe_int(sim.get("tick"), 0),
        "connected_to": list(trigger.get("connected_to") or [])[:5],
    }

    locations = list(sim.get("locations") or sim.get("location_seeds") or [])
    locations.append(location)
    sim["locations"] = locations

    expansion = _increment_budget(expansion, "location")
    sim["world_expansion"] = expansion
    sim["_spawn_result"] = {"ok": True, "entity_type": "location", "entity_id": loc_id}
    return sim


