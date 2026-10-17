from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.equipment_runtime import project_equipment_stats

SOURCE = "deterministic_combat_runtime"


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def deterministic_roll(seed: str, low: int, high: int) -> int:
    low = int(low)
    high = max(low, int(high))
    span = high - low + 1
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return low + (int(digest[:16], 16) % span)


def get_combat_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return safe_dict(simulation_state.get("combat_state"))


def is_combat_active(simulation_state: Dict[str, Any]) -> bool:
    return get_combat_state(simulation_state).get("active") is True


def player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = safe_dict(simulation_state.get("player_state"))
    simulation_state["player_state"] = state
    return state


def party_companions(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    party_state = safe_dict(player_state(simulation_state).get("party_state"))
    return [safe_dict(item) for item in safe_list(party_state.get("companions"))]


def default_enemy_bandit() -> Dict[str, Any]:
    return {
        "actor_id": "enemy:bandit_1",
        "side": "enemy",
        "name": "Bandit",
        "hp": 8,
        "max_hp": 8,
        "armor": 0,
        "defense": 10,
        "damage_min": 2,
        "damage_max": 4,
        "accuracy_bonus": 1,
        "initiative_bonus": 0,
        "status": "active",
        "loot_table_id": "loot:bandit_common",
        "source": SOURCE,
    }


def participant_from_player(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = player_state(simulation_state)
    return {
        "actor_id": "player",
        "side": "party",
        "name": "You",
        "hp": safe_int(state.get("hp"), safe_int(state.get("max_hp"), 20)),
        "max_hp": safe_int(state.get("max_hp"), 20),
        "armor": 0,
        "defense": 10,
        "initiative_bonus": 1,
        "status": "active",
        "source": SOURCE,
    }


def participant_from_companion(companion: Dict[str, Any]) -> Dict[str, Any]:
    companion = safe_dict(companion)
    npc_id = safe_str(companion.get("npc_id"))
    return {
        "actor_id": npc_id,
        "side": "party",
        "name": safe_str(companion.get("name") or npc_id),
        "hp": safe_int(companion.get("hp"), safe_int(companion.get("max_hp"), 16)),
        "max_hp": safe_int(companion.get("max_hp"), 16),
        "armor": 0,
        "defense": 10,
        "initiative_bonus": 0,
        "status": safe_str(companion.get("combat_status") or "active"),
        "identity_arc": safe_str(companion.get("identity_arc")),
        "current_role": safe_str(companion.get("current_role")),
        "source": SOURCE,
    }


def participant_from_enemy(enemy: Dict[str, Any]) -> Dict[str, Any]:
    enemy = safe_dict(enemy)
    actor_id = safe_str(enemy.get("actor_id") or enemy.get("enemy_id") or enemy.get("id"))
    return {
        "actor_id": actor_id,
        "side": safe_str(enemy.get("side") or "enemy"),
        "name": safe_str(enemy.get("name") or actor_id),
        "hp": safe_int(enemy.get("hp"), safe_int(enemy.get("max_hp"), 10)),
        "max_hp": safe_int(enemy.get("max_hp"), 10),
        "armor": safe_int(enemy.get("armor"), 0),
        "defense": safe_int(enemy.get("defense"), 10),
        "damage_min": safe_int(enemy.get("damage_min"), 1),
        "damage_max": safe_int(enemy.get("damage_max"), 3),
        "accuracy_bonus": safe_int(enemy.get("accuracy_bonus"), 0),
        "initiative_bonus": safe_int(enemy.get("initiative_bonus"), 0),
        "status": safe_str(enemy.get("status") or "active"),
        "loot_table_id": safe_str(enemy.get("loot_table_id")),
        "definition_id": safe_str(enemy.get("definition_id")),
        "definition_revision": safe_int(enemy.get("definition_revision"), 0),
        "level": safe_int(enemy.get("level"), 1),
        "morale_threshold": safe_int(enemy.get("morale_threshold"), 35),
        "tags": list(safe_list(enemy.get("tags"))),
        "xp_value": safe_int(enemy.get("xp_value"), 0),
        "budget_cost": safe_int(enemy.get("budget_cost"), 1),
        "condition_immunities": list(
            safe_list(enemy.get("condition_immunities"))
        ),
        "vulnerabilities": deepcopy(safe_list(enemy.get("vulnerabilities"))),
        "status_effects": deepcopy(safe_list(enemy.get("status_effects"))),
        "mechanics_source": safe_str(enemy.get("mechanics_source")),
        "source": SOURCE,
    }


def current_actor_id(combat_state: Dict[str, Any]) -> str:
    order = safe_list(combat_state.get("initiative_order"))
    if not order:
        return ""
    idx = safe_int(combat_state.get("turn_index"), 0) % len(order)
    return safe_str(safe_dict(order[idx]).get("actor_id"))


def participant(combat_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    return safe_dict(safe_dict(combat_state.get("participants")).get(actor_id))


def living_actor_ids(combat_state: Dict[str, Any], side: str) -> List[str]:
    ids: List[str] = []
    for actor_id, row in safe_dict(combat_state.get("participants")).items():
        row = safe_dict(row)
        if (
            safe_str(row.get("side")) == side
            and safe_str(row.get("status") or "active") == "active"
            and safe_int(row.get("hp"), 0) > 0
        ):
            ids.append(safe_str(actor_id))
    return ids


def living_enemy_ids(combat_state: Dict[str, Any]) -> List[str]:
    return living_actor_ids(combat_state, "enemy")


def living_party_ids(combat_state: Dict[str, Any]) -> List[str]:
    return living_actor_ids(combat_state, "party")


def default_target_for_actor(combat_state: Dict[str, Any], actor_id: str) -> str:
    actor = participant(combat_state, actor_id)
    if safe_str(actor.get("side")) == "enemy":
        party = living_party_ids(combat_state)
        return party[0] if party else ""
    enemies = living_enemy_ids(combat_state)
    return enemies[0] if enemies else ""


def combat_seed(combat_state: Dict[str, Any], *parts: Any) -> str:
    return "|".join(
        [
            safe_str(combat_state.get("encounter_id")),
            str(safe_int(combat_state.get("round"), 1)),
            str(safe_int(combat_state.get("turn_index"), 0)),
            *[safe_str(part) for part in parts],
        ]
    )


def damage_bounds_for_actor(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, int]:
    combat_state = get_combat_state(simulation_state)
    row = participant(combat_state, actor_id)
    if safe_str(row.get("side")) == "enemy":
        return {
            "damage_min": max(1, safe_int(row.get("damage_min"), 1)),
            "damage_max": max(1, safe_int(row.get("damage_max"), 3)),
            "accuracy_bonus": safe_int(row.get("accuracy_bonus"), 0),
            "encumbrance_penalty": 0,
            "armor": safe_int(row.get("armor"), 0),
        }

    stats = safe_dict(project_equipment_stats(simulation_state, actor_id=actor_id).get("stats"))
    return {
        "damage_min": max(1, safe_int(stats.get("damage_min"), 1)),
        "damage_max": max(1, safe_int(stats.get("damage_max"), 2)),
        "accuracy_bonus": safe_int(stats.get("accuracy_bonus"), 0),
        "encumbrance_penalty": safe_int(stats.get("encumbrance_penalty"), 0),
        "armor": safe_int(stats.get("armor"), 0),
    }


def sync_participant_hp_to_actor_state(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
    hp: int,
    status: str,
) -> None:
    if actor_id == "player":
        state = player_state(simulation_state)
        state["hp"] = max(0, int(hp))
        if status:
            state["combat_status"] = status
        simulation_state["player_state"] = state
        return

    if not actor_id.startswith("npc:"):
        return

    state = player_state(simulation_state)
    party_state = safe_dict(state.get("party_state"))
    companions = safe_list(party_state.get("companions"))
    for companion in companions:
        companion = safe_dict(companion)
        if safe_str(companion.get("npc_id")) == actor_id:
            companion["hp"] = max(0, int(hp))
            if status:
                companion["combat_status"] = status
            break
    party_state["companions"] = companions
    state["party_state"] = party_state
    simulation_state["player_state"] = state


def build_initiative_order(
    *,
    encounter_id: str,
    participants: Dict[str, Any],
) -> List[Dict[str, Any]]:
    rows = []
    for actor_id, row in safe_dict(participants).items():
        row = safe_dict(row)
        if safe_str(row.get("status") or "active") != "active":
            continue

        roll = deterministic_roll(f"{encounter_id}|initiative|{actor_id}", 1, 20)
        bonus = safe_int(row.get("initiative_bonus"), 0)
        rows.append({
            "actor_id": actor_id,
            "initiative": roll + bonus,
            "roll": roll,
            "bonus": bonus,
        })

    rows.sort(key=lambda row: (-safe_int(row.get("initiative")), safe_str(row.get("actor_id"))))
    return rows
