from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _participant_name(combat_state: Dict[str, Any], actor_id: str) -> str:
    participant = _safe_dict(_safe_dict(combat_state.get("participants")).get(actor_id))
    return _safe_str(participant.get("name") or actor_id)


def build_combat_narration_contract(
    *,
    combat_result: Dict[str, Any],
    combat_state: Dict[str, Any],
) -> Dict[str, Any]:
    combat_result = _safe_dict(combat_result)
    combat_state = _safe_dict(combat_state)

    reason = _safe_str(combat_result.get("reason"))
    actor_id = _safe_str(combat_result.get("actor_id") or combat_result.get("enemy_id") or combat_result.get("npc_id"))
    target_id = _safe_str(combat_result.get("target_id"))

    attack_result = _safe_dict(combat_result.get("attack_result"))
    if attack_result:
        actor_id = _safe_str(attack_result.get("actor_id") or actor_id)
        target_id = _safe_str(attack_result.get("target_id") or target_id)

    effective = attack_result if attack_result else combat_result

    actor_name = _participant_name(combat_state, actor_id) if actor_id else ""
    target_name = _participant_name(combat_state, target_id) if target_id else ""

    hit = effective.get("hit")
    defeated = bool(effective.get("defeated") or reason in {"combat_defeat_resolved", "companion_combat_defeat_resolved"})
    party_defeated = bool(combat_result.get("party_defeated") or reason == "party_defeat_resolved")
    combat_ended = bool(combat_result.get("combat_ended") or effective.get("combat_ended") or not combat_state.get("active", True))

    hp_before = _safe_int(effective.get("target_hp_before"), -1)
    hp_after = _safe_int(effective.get("target_hp_after"), -1)

    facts = {
        "reason": reason,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "target_id": target_id,
        "target_name": target_name,
        "hit": hit,
        "attack_roll": effective.get("attack_roll"),
        "attack_total": effective.get("attack_total"),
        "target_defense": effective.get("target_defense"),
        "damage_applied": effective.get("damage_applied"),
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "defeated": defeated,
        "party_defeated": party_defeated,
        "combat_ended": combat_ended,
        "next_actor_id": _safe_str(effective.get("next_actor_id") or combat_state.get("current_actor_id")),
        "round": _safe_int(combat_state.get("round"), 1),
    }

    ammo = _safe_dict(effective.get("ammo_result"))
    if ammo:
        facts["ammo_result"] = {
            "consumed": bool(ammo.get("consumed")),
            "ammo_item_id": _safe_str(ammo.get("ammo_item_id")),
            "quantity_before": ammo.get("quantity_before"),
            "quantity_after": ammo.get("quantity_after"),
        }

    loot = _safe_dict(effective.get("loot_result") or combat_result.get("loot_result"))
    if loot:
        facts["loot_result"] = {
            "resolved": bool(loot.get("resolved")),
            "reason": _safe_str(loot.get("reason")),
            "loot_table_id": _safe_str(loot.get("loot_table_id")),
            "items_created": deepcopy(_safe_list(loot.get("items_created"))),
        }

    constraints = [
        "Narrate only the resolved combat facts in this contract.",
        "Do not invent hits, misses, damage, death, loot, surrender, escape, dismemberment, or new enemies.",
        "If defeated is false, do not say the target dies, collapses dead, is slain, is killed, or is finished.",
        "If defeated is true, acknowledge that the target is defeated.",
        "If party_defeated is true, acknowledge that the party/player is defeated.",
        "Do not mention JSON, contract, simulation, validator, system, prompt, or LLM.",
        "Do not repeat the player's command verbatim.",
    ]

    if party_defeated:
        constraints.append(
            "Because party_defeated is true, explicitly state that you/the party are defeated, downed, overwhelmed, or fall. Do not merely imply it."
        )

    return {
        "format_version": "combat_narration_contract_v1",
        "kind": "combat_narration",
        "facts": facts,
        "constraints": constraints,
        "raw_combat_result": deepcopy(combat_result),
        "source": "deterministic_combat_narration_contract",
    }


def combat_contract_requires_llm(combat_result: Dict[str, Any]) -> bool:
    reason = _safe_str(_safe_dict(combat_result).get("reason"))
    return reason in {
        "combat_started",
        "combat_attack_resolved",
        "combat_defeat_resolved",
        "companion_combat_attack_resolved",
        "companion_combat_defeat_resolved",
        "enemy_combat_attack_resolved",
        "party_defeat_resolved",
    }