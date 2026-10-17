"""Phase 9.0 — Loot builder from encounter state.

Deterministic loot generation based on resolved encounter outcomes.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def build_loot_from_encounter_state(encounter_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive loot drops from a resolved encounter state.

    Loot is only awarded when the encounter status is "resolved" and there
    are hostile participants.  The amount and type of loot scale with
    the number of hostiles defeated.
    """
    encounter_state = _safe_dict(encounter_state)
    participants = _safe_list(encounter_state.get("participants"))
    status = _safe_str(encounter_state.get("status"))

    if status != "resolved":
        return []

    hostile_count = 0
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        disposition = _safe_str(participant.get("disposition"))
        role = _safe_str(participant.get("role"))
        if disposition == "hostile" or role == "enemy":
            hostile_count += 1

    if hostile_count <= 0:
        return []

    loot: List[Dict[str, Any]] = [
        {"item_id": "gold_coin", "qty": max(1, hostile_count * 3)},
    ]

    if hostile_count >= 2:
        loot.append({"item_id": "bandit_token", "qty": 1})

    if hostile_count >= 3:
        loot.append({"item_id": "healing_potion", "qty": 1})

    return loot[:5]