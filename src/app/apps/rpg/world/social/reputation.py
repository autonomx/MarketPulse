from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.world.social.state import (
    clamp_social_value,
    ensure_relationship,
    ensure_social_state,
)


def get_relationship(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    return dict(ensure_relationship(simulation_state, npc_id))


def set_relationship_values(
    simulation_state: Dict[str, Any],
    npc_id: str,
    values: Dict[str, Any],
) -> Dict[str, Any]:
    relationship = ensure_relationship(simulation_state, npc_id)
    for key in ("trust", "fear", "respect", "hostility", "reputation"):
        if key in values:
            relationship[key] = clamp_social_value(values.get(key))
    if "last_stance" in values:
        relationship["last_stance"] = str(values.get("last_stance") or "neutral")
    return dict(relationship)


def apply_social_deltas(
    simulation_state: Dict[str, Any],
    npc_id: str,
    deltas: Dict[str, Any],
    *,
    actor_id: str = "player",
) -> Dict[str, Any]:
    relationship = ensure_relationship(simulation_state, npc_id)
    applied: Dict[str, int] = {}
    for key in ("trust", "fear", "respect", "hostility", "reputation"):
        delta = int(deltas.get(key) or 0)
        if delta:
            relationship[key] = clamp_social_value(int(relationship.get(key) or 0) + delta)
        applied[key] = delta

    stance = deltas.get("last_stance")
    if stance:
        relationship["last_stance"] = str(stance)

    return {
        "ok": True,
        "npc_id": npc_id,
        "actor_id": actor_id,
        "applied": applied,
        "relationship": dict(relationship),
    }


def get_global_reputation(
    simulation_state: Dict[str, Any],
    actor_id: str = "player",
) -> int:
    state = ensure_social_state(simulation_state)
    return int(state.setdefault("global_reputation", {}).get(actor_id) or 0)


def set_global_reputation(
    simulation_state: Dict[str, Any],
    actor_id: str,
    value: Any,
) -> int:
    state = ensure_social_state(simulation_state)
    state.setdefault("global_reputation", {})[actor_id] = clamp_social_value(value)
    return int(state["global_reputation"][actor_id])


def apply_global_reputation_delta(
    simulation_state: Dict[str, Any],
    actor_id: str,
    delta: Any,
) -> int:
    current = get_global_reputation(simulation_state, actor_id)
    return set_global_reputation(simulation_state, actor_id, current + int(delta or 0))