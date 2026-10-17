"""Phase 9.0 — Item effect hooks.

Applies deterministic item effects to simulation state.
"""
from __future__ import annotations

from typing import Any, Dict

from .inventory_state import normalize_inventory_state, remove_inventory_item
from .item_registry import get_item_definition
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def apply_item_use(simulation_state: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    """Apply a single item use, consuming it from inventory and returning effect result.

    Returns a dict with keys:
        - simulation_state: updated state
        - result: {ok, item_id, effect?, reason?}
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))
    item_id = _safe_str(item_id)
    item_def = get_item_definition(item_id)

    if not item_def:
        return {
            "simulation_state": simulation_state,
            "result": {
                "ok": False,
                "reason": "unknown_item",
                "item_id": item_id,
            },
        }

    has_item = any(
        isinstance(item, dict)
        and _safe_str(item.get("item_id")) == item_id
        and _safe_int(item.get("qty"), 0) > 0
        for item in (inventory_state.get("items") or [])
    )
    if not has_item:
        return {
            "simulation_state": simulation_state,
            "result": {
                "ok": False,
                "reason": "item_not_owned",
                "item_id": item_id,
            },
        }

    effect = _safe_dict(item_def.get("effect"))
    inventory_state = remove_inventory_item(inventory_state, item_id, qty=1)
    player_state["inventory_state"] = inventory_state
    simulation_state["player_state"] = player_state

    return {
        "simulation_state": simulation_state,
        "result": {
            "ok": True,
            "item_id": item_id,
            "effect": effect,
        },
    }


def _apply_effect(simulation_state: Dict[str, Any], effect: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a single effect to simulation state."""
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    effect_type = _safe_str(effect.get("type"))

    if effect_type == "restore_resource":
        resource = _safe_str(effect.get("resource", "health"))
        amount = _safe_int(effect.get("amount"), 0)
        if resource == "health":
            current_hp = _safe_int(player_state.get("hp", 100))
            max_hp = _safe_int(player_state.get("max_hp", 100))
            player_state["hp"] = min(max_hp, current_hp + amount)
        elif resource == "mana":
            current = _safe_int(player_state.get("mana", 50))
            max_val = _safe_int(player_state.get("max_mana", 50))
            player_state["mana"] = min(max_val, current + amount)
        elif resource == "stamina":
            current = _safe_int(player_state.get("stamina", 50))
            max_val = _safe_int(player_state.get("max_stamina", 50))
            player_state["stamina"] = min(max_val, current + amount)
    elif effect_type == "grant_status":
        status_id = _safe_str(effect.get("status_id", ""))
        duration = _safe_int(effect.get("duration"), 3)
        statuses = list(player_state.get("active_statuses", []))
        statuses.append({"status_id": status_id, "duration": duration})
        player_state["active_statuses"] = statuses[:20]
    elif effect_type == "equip":
        # Handled separately by inventory equip logic
        pass
    elif effect_type == "spawn_loot":
        from .world_items import spawn_world_item
        loc = _safe_str(player_state.get("current_scene_id", "unknown"))
        loot = _safe_dict(effect.get("item_def"))
        if loot.get("item_id"):
            simulation_state = spawn_world_item(simulation_state, loc, loot)

    simulation_state["player_state"] = player_state
    return simulation_state


def apply_item_effects(simulation_state: Dict[str, Any], item_id: str) -> Dict[str, Any]:
    """Apply an item's use, consuming it and applying all effects. Enhanced version."""
    result = apply_item_use(simulation_state, item_id)
    sim = result["simulation_state"]
    item_result = result["result"]
    if item_result.get("ok") and item_result.get("effect"):
        effect = _safe_dict(item_result["effect"])
        sim = _apply_effect(sim, effect)
    result["simulation_state"] = sim
    return result