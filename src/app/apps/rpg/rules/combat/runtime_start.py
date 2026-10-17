from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.combat.runtime_core import (
    SOURCE,
    build_initiative_order,
    current_actor_id,
    default_enemy_bandit,
    participant_from_companion,
    participant_from_enemy,
    participant_from_player,
    party_companions,
    safe_dict,
    safe_str,
)


def start_combat_encounter(
    simulation_state: Dict[str, Any],
    *,
    encounter_id: str = "enc:bandit_ambush",
    enemies: List[Dict[str, Any]] | None = None,
    tick: int = 0,
) -> Dict[str, Any]:
    existing = safe_dict(simulation_state.get("combat_state"))
    if existing.get("active") is True:
        return {
            "resolved": True,
            "changed_state": False,
            "reason": "combat_already_active",
            "combat_state": deepcopy(existing),
            "current_actor_id": current_actor_id(existing),
            "source": SOURCE,
        }

    participants: Dict[str, Dict[str, Any]] = {}
    player = participant_from_player(simulation_state)
    participants[player["actor_id"]] = player

    for companion in party_companions(simulation_state):
        participant = participant_from_companion(companion)
        if participant.get("actor_id"):
            participants[participant["actor_id"]] = participant

    enemy_rows = enemies if enemies is not None else [default_enemy_bandit()]
    for enemy in enemy_rows:
        participant = participant_from_enemy(enemy)
        if participant.get("actor_id"):
            participants[participant["actor_id"]] = participant

    initiative_order = build_initiative_order(
        encounter_id=encounter_id,
        participants=participants,
    )
    combat_state = {
        "active": True,
        "encounter_id": encounter_id,
        "round": 1,
        "turn_index": 0,
        "current_actor_id": safe_str(safe_dict(initiative_order[0]).get("actor_id")) if initiative_order else "",
        "initiative_order": initiative_order,
        "participants": participants,
        "combat_log": [],
        "source": SOURCE,
    }

    simulation_state["combat_state"] = combat_state
    return {
        "resolved": True,
        "changed_state": True,
        "reason": "combat_started",
        "encounter_id": encounter_id,
        "current_actor_id": combat_state["current_actor_id"],
        "combat_state": deepcopy(combat_state),
        "tick": int(tick or 0),
        "source": SOURCE,
    }
