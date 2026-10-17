from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from app.apps.rpg.rules.combat.state import get_current_actor_id, normalize_combat_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _find_actor(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    for collection_key in ("actor_states", "npc_states"):
        for actor in _safe_list(simulation_state.get(collection_key)):
            if str(actor.get("id") or "") == actor_id:
                return actor
    return {}


def _actor_speed(actor: Dict[str, Any]) -> int:
    stats = _safe_dict(actor.get("stats"))
    skills = _safe_dict(actor.get("skills"))
    agility = _safe_int(stats.get("agility"), 0)
    awareness = _safe_int(skills.get("awareness"), 0)
    return agility + awareness


def _initiative_roll(seed_key: str) -> int:
    digest = hashlib.sha256(seed_key.encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 20) + 1


def roll_initiative(
    simulation_state: Dict[str, Any],
    participant_ids: List[str],
    *,
    combat_id: str,
    tick: int,
) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    for actor_id in participant_ids:
        actor = _find_actor(simulation_state, actor_id)
        base = _actor_speed(actor)
        roll = _initiative_roll(f"{combat_id}:{tick}:initiative:{actor_id}")
        scores[actor_id] = base + roll
    return scores


def build_turn_order(initiative_scores: Dict[str, int]) -> List[str]:
    items: List[Tuple[str, int]] = list(initiative_scores.items())
    items.sort(key=lambda x: (-int(x[1]), str(x[0])))
    return [actor_id for actor_id, _ in items]


def begin_combat(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    participant_ids: List[str],
    *,
    combat_id: str,
    tick: int,
    initial_target_id: str = "",
) -> Dict[str, Any]:
    state = normalize_combat_state(combat_state)
    unique_ids = []
    seen = set()
    for actor_id in participant_ids:
        actor_id = str(actor_id or "").strip()
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        unique_ids.append(actor_id)

    initiative = roll_initiative(
        simulation_state,
        unique_ids,
        combat_id=combat_id,
        tick=tick,
    )
    turn_order = build_turn_order(initiative)
    current_actor_id = turn_order[0] if turn_order else ""

    state.update({
        "active": bool(turn_order),
        "combat_id": combat_id,
        "round": 1 if turn_order else 0,
        "phase": "active" if turn_order else "idle",
        "participants": unique_ids,
        "initiative": initiative,
        "turn_order": turn_order,
        "turn_index": 0,
        "current_actor_id": current_actor_id,
        "current_target_id": str(initial_target_id or ""),
        "pending_npc_turn": False,
        "winner_ids": [],
        "loser_ids": [],
        "exit_reason": "",
    })
    return state


def advance_turn(combat_state: Dict[str, Any]) -> Dict[str, Any]:
    state = normalize_combat_state(combat_state)
    turn_order = state.get("turn_order") or []
    if not state.get("active") or not turn_order:
        return state

    next_index = int(state.get("turn_index", 0) or 0) + 1
    if next_index >= len(turn_order):
        next_index = 0
        state["round"] = int(state.get("round", 0) or 0) + 1

    state["turn_index"] = next_index
    state["current_actor_id"] = get_current_actor_id(state)
    state["pending_npc_turn"] = False
    return state
