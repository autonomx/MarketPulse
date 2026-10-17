from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.apps.rpg.rules.combat.apply import apply_attack_resolution
from app.apps.rpg.rules.combat.initiative import advance_turn
from app.apps.rpg.rules.combat.lifecycle import evaluate_combat_exit
from app.apps.rpg.rules.combat.models import AttackIntent
from app.apps.rpg.rules.combat.resolver import resolve_attack
from app.apps.rpg.rules.combat.state import get_current_actor_id, normalize_combat_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _actor_lookup(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    for collection_key in ("actor_states", "npc_states"):
        for actor in _safe_list(simulation_state.get(collection_key)):
            if str(actor.get("id") or "") == actor_id:
                return actor
    return {}


def _is_player_actor(actor: Dict[str, Any]) -> bool:
    return bool(actor.get("is_player")) or str(actor.get("type") or "").strip().lower() == "player"


def _is_downed(actor: Dict[str, Any]) -> bool:
    resources = _safe_dict(actor.get("resources"))
    hp = int(resources.get("hp", 0) or 0)
    statuses = [str(x).strip().lower() for x in _safe_list(actor.get("status_effects"))]
    return hp <= 0 or "downed" in statuses


def _team(actor: Dict[str, Any]) -> str:
    return str(actor.get("combat_team") or actor.get("team") or actor.get("faction") or "neutral")


def _pick_target(simulation_state: Dict[str, Any], actor_id: str, participant_ids: List[str]) -> Optional[str]:
    actor = _actor_lookup(simulation_state, actor_id)
    actor_team = _team(actor)
    candidates: List[str] = []

    for other_id in participant_ids:
        if other_id == actor_id:
            continue
        other = _actor_lookup(simulation_state, other_id)
        if not other or _is_downed(other):
            continue
        if _team(other) == actor_team:
            continue
        candidates.append(other_id)

    candidates.sort()
    return candidates[0] if candidates else None


def run_npc_turn(
    simulation_state: Dict[str, Any],
    combat_state: Dict[str, Any],
    *,
    tick: int,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    state = normalize_combat_state(combat_state)
    current_actor_id = get_current_actor_id(state)
    actor = _actor_lookup(simulation_state, current_actor_id)

    if not actor or _is_player_actor(actor) or _is_downed(actor):
        state = advance_turn(state)
        return simulation_state, state, {}

    target_id = _pick_target(simulation_state, current_actor_id, state.get("participants") or [])
    if not target_id:
        state = evaluate_combat_exit(simulation_state, state)
        return simulation_state, state, {}

    intent = AttackIntent(
        actor_id=current_actor_id,
        target_id=target_id,
        action_type="melee_attack",
    )
    resolution = resolve_attack(
        simulation_state,
        state,
        intent,
        turn_id=f"{state.get('combat_id')}:npc:{current_actor_id}:r{state.get('round')}",
        tick=tick,
    )
    resolution_dict = resolution.to_dict()
    simulation_state, state = apply_attack_resolution(simulation_state, state, resolution_dict)
    state = evaluate_combat_exit(simulation_state, state)
    if state.get("active"):
        state = advance_turn(state)
    return simulation_state, state, resolution_dict
