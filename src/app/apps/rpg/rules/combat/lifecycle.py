from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from app.apps.rpg.rules.combat.state import normalize_combat_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _actor_lookup(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    # Check player_state first
    if actor_id == "player":
        player_state = _safe_dict(simulation_state.get("player_state"))
        if player_state:
            return {
                "id": "player",
                "actor_id": "player",
                "combat_team": "party",
                "team": "party",
                "side": "party",
                "resources": {
                    "hp": _safe_int(player_state.get("hp"), 0),
                    "max_hp": _safe_int(player_state.get("max_hp"), 0),
                },
            }

    for collection_key in ("actor_states", "npc_states"):
        for actor in _safe_list(simulation_state.get(collection_key)):
            if str(actor.get("id") or "") == actor_id:
                return actor

    # Also check combat_state participants
    combat_state = _safe_dict(simulation_state.get("combat_state"))
    participants = _safe_dict(combat_state.get("participants"))
    if actor_id in participants:
        return _safe_dict(participants.get(actor_id))

    return {}


def _is_downed(actor: Dict[str, Any]) -> bool:
    resources = _safe_dict(actor.get("resources"))
    hp = int(resources.get("hp", 0) or actor.get("hp", 0) or 0)
    status = str(actor.get("status", "")).strip().lower()
    statuses = [str(x).strip().lower() for x in _safe_list(actor.get("status_effects"))]
    return hp <= 0 or "downed" in statuses or status in {"fled", "defeated", "dead", "downed", "unconscious"}


def _actor_team(actor: Dict[str, Any]) -> str:
    return str(actor.get("combat_team") or actor.get("team") or actor.get("faction") or actor.get("side") or "neutral")


def _generate_combat_reward(combat_state: Dict[str, Any], losers: List[str]) -> Dict[str, Any]:
    """Generate deterministic XP rewards for combat victory."""
    combat_id = str(combat_state.get("combat_id") or "")
    defeated_count = len(losers)

    # Deterministic XP based on combat_id and defeated enemies
    seed = f"{combat_id}:{','.join(sorted(losers))}:reward"
    hash_obj = hashlib.sha256(seed.encode())
    hash_int = int(hash_obj.hexdigest()[:8], 16)

    base_xp = 25
    xp = base_xp + (hash_int % 25)  # 25-49 XP

    # Skill XP - distribute among combat, weapon skills
    skill_xp_total = defeated_count * 5
    combat_xp = skill_xp_total * 2 // 3
    weapon_xp = skill_xp_total - combat_xp

    return {
        "granted": True,
        "source": "combat",
        "xp": xp,
        "skill_xp": {
            "combat": combat_xp,
            "weapon": weapon_xp,
        },
        "level_up": [],  # Will be filled by level progression logic
        "skill_level_ups": [],  # Will be filled by skill progression logic
    }


def _generate_combat_loot(combat_state: Dict[str, Any], losers: List[str]) -> Dict[str, Any]:
    """Generate deterministic loot from defeated enemies."""
    combat_id = str(combat_state.get("combat_id") or "")
    loot_container_id = f"loot:combat:{combat_id}"

    # Simple deterministic loot generation
    seed = f"{combat_id}:{','.join(sorted(losers))}:loot"
    hash_obj = hashlib.sha256(seed.encode())
    hash_int = int(hash_obj.hexdigest()[:8], 16)

    items = []
    currency = {}

    # Generate rusty dagger for some combats
    if hash_int % 3 == 0:
        items.append({
            "item_id": "item:rusty_dagger",
            "name": "Rusty dagger",
            "quantity": 1,
        })

    # Generate copper coins
    copper = 5 + (hash_int % 11)  # 5-15 copper
    currency["copper"] = copper

    return {
        "generated": True,
        "source": "combat",
        "combat_id": combat_id,
        "loot_container_id": loot_container_id,
        "items": items,
        "currency": currency,
    }


def build_combat_participants(simulation_state: Dict[str, Any], actor_ids: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for actor_id in actor_ids:
        actor_id = str(actor_id or "").strip()
        if not actor_id or actor_id in seen:
            continue
        if _actor_lookup(simulation_state, actor_id):
            seen.add(actor_id)
            out.append(actor_id)
    return out


def evaluate_combat_exit(simulation_state: Dict[str, Any], combat_state: Dict[str, Any]) -> Dict[str, Any]:
    state = normalize_combat_state(combat_state)
    if not state.get("active"):
        return state

    participants = [str(x) for x in state.get("participants") or [] if str(x or "").strip()]
    alive_by_team: Dict[str, List[str]] = {}
    downed_ids: List[str] = []

    for actor_id in participants:
        actor = _actor_lookup(simulation_state, actor_id)
        if not actor:
            continue
        if _is_downed(actor):
            downed_ids.append(actor_id)
            continue
        team = _actor_team(actor)
        alive_by_team.setdefault(team, []).append(actor_id)

    if len(alive_by_team) <= 1:
        state["active"] = False
        state["phase"] = "resolved"
        alive_teams = list(alive_by_team.keys())
        winners = alive_by_team.get(alive_teams[0], []) if alive_teams else []
        losers = [actor_id for actor_id in participants if actor_id not in winners]
        state["winner_ids"] = winners
        state["loser_ids"] = losers

        # Determine specific exit reason
        if not winners:
            state["exit_reason"] = "all_downed"
            state["ended_reason"] = "all_sides_defeated"
        elif "player" in winners:
            state["exit_reason"] = "victory"
            state["ended_reason"] = "enemy_side_defeated"
        elif "player" in losers:
            state["exit_reason"] = "party_defeat"
            state["ended_reason"] = "party_side_defeated"
        else:
            # Fallback for non-player combats
            state["exit_reason"] = "last_team_standing"
            state["ended_reason"] = "last_team_standing"

        # Generate rewards and loot for victory
        if state["exit_reason"] == "victory":
            state["reward_result"] = _generate_combat_reward(state, losers)
            state["loot_result"] = _generate_combat_loot(state, losers)

        # Post-combat cleanup
        state["defense_modifiers"] = {}
        state["pending_npc_turn"] = False
        state["current_actor_id"] = ""

    return state
