from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.combat.archetypes import (
    get_enemy_archetype,
    instantiate_enemy_from_archetype,
)
from app.apps.rpg.rules.combat.state import normalize_combat_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


DIFFICULTY_MULTIPLIER = {
    "trivial": 0.5,
    "easy": 1.0,
    "normal": 1.5,
    "hard": 2.25,
    "deadly": 3.0,
}


ENCOUNTER_PRESETS: Dict[str, Dict[str, Any]] = {
    "bandit_easy": {
        "encounter_id": "enc:bandit_easy",
        "difficulty": "easy",
        "enemy_pool": ["enemy:bandit_grunt"],
        "location_id": "loc_tavern_road",
    },
    "bandit_normal": {
        "encounter_id": "enc:bandit_normal",
        "difficulty": "normal",
        "enemy_pool": ["enemy:bandit_grunt", "enemy:bandit_archer"],
        "location_id": "loc_tavern_road",
    },
    "bandit_hard": {
        "encounter_id": "enc:bandit_hard",
        "difficulty": "hard",
        "enemy_pool": ["enemy:bandit_grunt", "enemy:bandit_archer", "enemy:bandit_brute"],
        "location_id": "loc_tavern_road",
    },
    "wolf_easy": {
        "encounter_id": "enc:wolf_easy",
        "difficulty": "easy",
        "enemy_pool": ["enemy:wolf"],
        "location_id": "loc_forest_road",
    },
    "rat_easy": {
        "encounter_id": "enc:rat_easy",
        "difficulty": "easy",
        "enemy_pool": ["enemy:giant_rat"],
        "location_id": "loc_cellar",
    },
}


def get_encounter_preset(preset_id: str) -> Dict[str, Any]:
    return dict(_safe_dict(ENCOUNTER_PRESETS.get(_safe_str(preset_id).strip())))


def _player_participant_from_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))

    hp = _safe_int(
        player_state.get("hp")
        or _safe_dict(player_state.get("resources")).get("hp"),
        20,
    )
    max_hp = _safe_int(
        player_state.get("max_hp")
        or _safe_dict(player_state.get("resources")).get("max_hp"),
        max(1, hp),
    )

    return {
        "actor_id": "player",
        "id": "player",
        "side": "party",
        "team": "party",
        "combat_team": "party",
        "name": "You",
        "hp": hp,
        "max_hp": max_hp,
        "resources": {
            "hp": hp,
            "max_hp": max_hp,
        },
        "defense": _safe_int(player_state.get("defense"), 10),
        "armor": _safe_int(player_state.get("armor"), 0),
        "initiative_bonus": _safe_int(player_state.get("initiative_bonus"), 0),
        "status": "active",
        "status_effects": [],
    }


def _party_size_from_state(simulation_state: Dict[str, Any]) -> int:
    party_state = _safe_dict(_safe_dict(simulation_state).get("party_state"))
    companions = _safe_list(party_state.get("companions"))
    return max(1, 1 + len(companions))


def _party_level_from_state(simulation_state: Dict[str, Any]) -> int:
    player_state = _safe_dict(_safe_dict(simulation_state).get("player_state"))
    return max(1, _safe_int(player_state.get("level"), 1))


def _encounter_budget(*, party_level: int, party_size: int, difficulty: str) -> int:
    multiplier = DIFFICULTY_MULTIPLIER.get(_safe_str(difficulty).strip(), 1.0)
    return max(10, int(25 * max(1, party_level) * max(1, party_size) * multiplier))


def _choose_enemy_archetypes(enemy_pool: List[str], budget: int) -> List[str]:
    pool = [x for x in enemy_pool if get_enemy_archetype(x)]
    if not pool:
        pool = ["enemy:bandit_grunt"]

    selected: List[str] = []
    spent = 0

    sorted_pool = sorted(
        pool,
        key=lambda archetype_id: (
            _safe_int(get_enemy_archetype(archetype_id).get("budget_cost"), 25),
            archetype_id,
        ),
    )

    guard = 0
    while guard < 8:
        guard += 1
        affordable = [
            archetype_id
            for archetype_id in sorted_pool
            if spent + _safe_int(get_enemy_archetype(archetype_id).get("budget_cost"), 25) <= budget
        ]
        if not affordable:
            break

        # Deterministic: pick the most expensive affordable enemy, then stable id.
        choice = sorted(
            affordable,
            key=lambda archetype_id: (
                _safe_int(get_enemy_archetype(archetype_id).get("budget_cost"), 25),
                archetype_id,
            ),
            reverse=True,
        )[0]
        selected.append(choice)
        spent += _safe_int(get_enemy_archetype(choice).get("budget_cost"), 25)

        if len(selected) >= 6:
            break

    if not selected:
        selected.append(sorted_pool[0])

    return selected


def build_encounter(
    simulation_state: Dict[str, Any],
    *,
    encounter_id: str,
    location_id: str = "",
    party_level: int = 0,
    party_size: int = 0,
    difficulty: str = "easy",
    enemy_pool: List[str] | None = None,
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    difficulty = _safe_str(difficulty or "easy").strip()

    party_level = party_level or _party_level_from_state(simulation_state)
    party_size = party_size or _party_size_from_state(simulation_state)
    enemy_pool = list(enemy_pool or ["enemy:bandit_grunt"])
    budget = _encounter_budget(
        party_level=party_level,
        party_size=party_size,
        difficulty=difficulty,
    )

    selected_archetypes = _choose_enemy_archetypes(enemy_pool, budget)

    participants: Dict[str, Any] = {
        "player": _player_participant_from_state(simulation_state),
    }

    selected_enemies: List[Dict[str, Any]] = []
    for index, archetype_id in enumerate(selected_archetypes, start=1):
        enemy = instantiate_enemy_from_archetype(archetype_id, instance_index=index)
        if not enemy:
            continue
        actor_id = _safe_str(enemy.get("actor_id")).strip()
        participants[actor_id] = enemy
        selected_enemies.append({
            "actor_id": actor_id,
            "archetype_id": archetype_id,
            "name": enemy.get("name", actor_id),
            "budget_cost": enemy.get("budget_cost", 0),
            "xp_value": enemy.get("xp_value", 0),
            "loot_table_id": enemy.get("loot_table_id", ""),
        })

    initiative_order: List[Dict[str, Any]] = []
    for index, (actor_id, participant) in enumerate(participants.items()):
        participant = _safe_dict(participant)
        initiative = 20 - index + _safe_int(participant.get("initiative_bonus"), 0)
        initiative_order.append({
            "actor_id": actor_id,
            "initiative": initiative,
            "roll": max(1, min(20, 20 - index)),
            "bonus": _safe_int(participant.get("initiative_bonus"), 0),
        })

    initiative_order.sort(
        key=lambda row: (-_safe_int(row.get("initiative"), 0), _safe_str(row.get("actor_id"))),
    )

    current_actor_id = _safe_str(initiative_order[0].get("actor_id")).strip() if initiative_order else "player"

    combat_state = normalize_combat_state({
        "active": True,
        "phase": "active",
        "combat_id": f"combat:{encounter_id}",
        "encounter_id": encounter_id,
        "location_id": location_id,
        "round": 1,
        "turn_index": 0,
        "current_actor_id": current_actor_id,
        "initiative_order": initiative_order,
        "participants": participants,
        "combat_log": [],
        "recent_events": [
            {
                "type": "encounter_started",
                "encounter_id": encounter_id,
                "difficulty": difficulty,
                "selected_enemies": selected_enemies,
            }
        ],
        "source": "deterministic_encounter_builder",
    })

    encounter_result = {
        "generated": True,
        "source": "combat_encounter_builder",
        "encounter_id": encounter_id,
        "location_id": location_id,
        "difficulty": difficulty,
        "party_level": party_level,
        "party_size": party_size,
        "budget": budget,
        "selected_enemies": selected_enemies,
        "enemy_count": len(selected_enemies),
    }

    return {
        "combat_state": combat_state,
        "encounter_result": encounter_result,
    }


def build_encounter_from_preset(
    simulation_state: Dict[str, Any],
    preset_id: str,
) -> Dict[str, Any]:
    preset = get_encounter_preset(preset_id)
    if not preset:
        return {
            "combat_state": {},
            "encounter_result": {
                "generated": False,
                "reason": "unknown_encounter_preset",
                "preset_id": preset_id,
            },
        }

    return build_encounter(
        simulation_state,
        encounter_id=_safe_str(preset.get("encounter_id") or f"enc:{preset_id}").strip(),
        location_id=_safe_str(preset.get("location_id")).strip(),
        difficulty=_safe_str(preset.get("difficulty") or "easy").strip(),
        enemy_pool=[_safe_str(x).strip() for x in _safe_list(preset.get("enemy_pool"))],
    )