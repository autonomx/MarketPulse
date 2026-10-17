from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.item_model import (
    add_item_to_items_list,
    normalize_item_instance,
    recalculate_inventory_derived_fields,
)
from app.apps.rpg.rules.interactions.loot_catalog import get_loot_table
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


COMBAT_DEFEAT_XP_DEFAULT = 25
COMBAT_DEFEAT_XP_TO_NEXT_DEFAULT = 100


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _deterministic_int(seed: str, *, modulo: int) -> int:
    modulo = max(1, int(modulo))
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % modulo


def _weighted_choice(options: List[Dict[str, Any]], *, seed: str) -> Dict[str, Any]:
    clean = [_safe_dict(option) for option in options if _safe_int(_safe_dict(option).get("weight"), 0) > 0]
    if not clean:
        return {}

    total = sum(_safe_int(option.get("weight"), 0) for option in clean)
    cursor = _deterministic_int(seed, modulo=total)

    running = 0
    for option in clean:
        running += _safe_int(option.get("weight"), 0)
        if cursor < running:
            return deepcopy(option)

    return deepcopy(clean[-1])


def _quantity_for_roll(roll: Dict[str, Any], *, seed: str) -> int:
    q_min = _safe_int(roll.get("quantity_min"), 1)
    q_max = max(q_min, _safe_int(roll.get("quantity_max"), q_min))
    return q_min + _deterministic_int(seed, modulo=(q_max - q_min + 1))


def _player_inventory(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    inv = _safe_dict(player_state.get("inventory"))
    if not isinstance(inv.get("items"), list):
        inv["items"] = []
    if not isinstance(inv.get("equipment"), dict):
        inv["equipment"] = {}
    player_state["inventory"] = inv
    simulation_state["player_state"] = player_state
    return inv


def _apply_combat_defeat_xp_reward(
    simulation_state: Dict[str, Any],
    *,
    source_id: str,
    tick: int = 0,
) -> Dict[str, Any]:
    source_id = _safe_str(source_id).strip()
    if not source_id.startswith("enemy:"):
        return {
            "awarded": False,
            "reason": "not_combat_enemy_source",
            "source_id": source_id,
            "source": "deterministic_combat_reward_runtime",
        }

    player_state = _safe_dict(simulation_state.get("player_state"))
    claim_key = f"combat_defeat:{source_id}:{int(tick or 0)}"
    claims = _safe_dict(player_state.get("combat_reward_claims"))
    if claims.get(claim_key):
        return {
            "awarded": False,
            "reason": "combat_reward_already_claimed",
            "claim_key": claim_key,
            "source_id": source_id,
            "source": "deterministic_combat_reward_runtime",
        }

    level_before = max(1, _safe_int(player_state.get("level"), 1))
    xp_before = max(0, _safe_int(player_state.get("xp"), 0))
    xp_to_next = max(1, _safe_int(player_state.get("xp_to_next_level"), COMBAT_DEFEAT_XP_TO_NEXT_DEFAULT))
    xp_awarded = max(0, _safe_int(player_state.get("combat_defeat_xp_reward"), COMBAT_DEFEAT_XP_DEFAULT))
    xp_after = xp_before + xp_awarded
    level_after = level_before
    level_ups: List[Dict[str, Any]] = []

    while xp_after >= xp_to_next:
        xp_after -= xp_to_next
        level_after += 1
        level_ups.append({
            "level": level_after,
            "reason": "combat_defeat_xp_threshold",
        })
        xp_to_next = COMBAT_DEFEAT_XP_TO_NEXT_DEFAULT + ((level_after - 1) * 50)

    claims[claim_key] = {
        "source_id": source_id,
        "tick": int(tick or 0),
        "xp_awarded": xp_awarded,
    }
    player_state["combat_reward_claims"] = claims
    player_state["level"] = level_after
    player_state["xp"] = xp_after
    player_state["xp_to_next_level"] = xp_to_next
    player_state.setdefault("progression_source", "deterministic_combat_reward_runtime")
    simulation_state["player_state"] = player_state

    return {
        "awarded": xp_awarded > 0,
        "reason": "combat_defeat_xp_awarded",
        "claim_key": claim_key,
        "source_id": source_id,
        "xp_awarded": xp_awarded,
        "xp_before": xp_before,
        "xp_after": xp_after,
        "level_before": level_before,
        "level_after": level_after,
        "xp_to_next_level": xp_to_next,
        "level_ups": level_ups,
        "source": "deterministic_combat_reward_runtime",
    }


def generate_loot_from_table(
    simulation_state: Dict[str, Any],
    *,
    loot_table_id: str,
    source_id: str = "",
    session_id: str = "",
    tick: int = 0,
    add_to_inventory: bool = True,
) -> Dict[str, Any]:
    table = get_loot_table(loot_table_id)
    if not table:
        return {
            "resolved": False,
            "changed_state": False,
            "reason": "loot_table_not_found",
            "loot_table_id": loot_table_id,
            "source": "deterministic_loot_runtime",
        }

    rolls = _safe_list(table.get("rolls"))
    roll_count = max(1, _safe_int(table.get("roll_count"), 1))
    seed_base = f"{session_id}|{loot_table_id}|{source_id}|{tick}"

    created = []
    add_result: Dict[str, Any] = {}
    xp_result: Dict[str, Any] = {}

    inventory = _player_inventory(simulation_state) if add_to_inventory else {}

    for idx in range(roll_count):
        roll = _weighted_choice(rolls, seed=f"{seed_base}|roll|{idx}")
        if not roll:
            continue

        quantity = _quantity_for_roll(roll, seed=f"{seed_base}|qty|{idx}")
        item = normalize_item_instance({
            "definition_id": _safe_str(roll.get("definition_id")),
            "quantity": quantity,
            "source": "deterministic_loot_runtime",
        })

        created.append({
            "definition_id": _safe_str(item.get("definition_id")),
            "quantity": quantity,
            "item": deepcopy(item),
        })

        if add_to_inventory:
            add_result = add_item_to_items_list(_safe_list(inventory.get("items")), item)
            inventory["items"] = _safe_list(add_result.get("items"))

    if add_to_inventory:
        inventory = recalculate_inventory_derived_fields(inventory)
        simulation_state["player_state"]["inventory"] = inventory
        xp_result = _apply_combat_defeat_xp_reward(
            simulation_state,
            source_id=source_id,
            tick=tick,
        )

    return {
        "resolved": True,
        "changed_state": bool(add_to_inventory and (created or xp_result.get("awarded"))),
        "reason": "loot_generated",
        "loot_table_id": loot_table_id,
        "source_id": source_id,
        "session_id": session_id,
        "tick": int(tick or 0),
        "items_created": created,
        "added_to_inventory": bool(add_to_inventory),
        "xp_result": deepcopy(xp_result),
        "carry_weight": inventory.get("carry_weight") if inventory else 0,
        "encumbrance_state": inventory.get("encumbrance_state") if inventory else "",
        "source": "deterministic_loot_runtime",
    }
