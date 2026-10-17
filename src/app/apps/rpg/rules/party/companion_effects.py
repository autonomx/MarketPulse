"""Phase 9.2 — Companion item/effect hooks.

Allows companions to consume shared inventory items for effects like healing.

Fixes applied:
- Fix #1: Equipment stores item_id pointer only; item use pulls from inventory
- Fix #7: Atomic effect application — compute everything first, then mutate
- Fix #8: Downed companion guard — cannot use items on downed companions
"""
from typing import Any, Dict

from app.apps.rpg.rules.items import (
    get_item_definition,
    normalize_inventory_state,
    remove_inventory_item,
)

from .party_state import (
    ensure_party_state,
    get_companion_by_id,
    update_companion_hp,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def apply_party_item_to_companion(simulation_state: Dict[str, Any], npc_id: str, item_id: str) -> Dict[str, Any]:
    """Apply an inventory item to a companion, consuming it from shared inventory.

    Fix #8: Validates companion is not downed before applying effect.
    Fix #7: Computes everything first, then applies state mutation once.

    Returns the updated simulation_state and result info.
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = ensure_party_state(_safe_dict(simulation_state.get("player_state")))
    inventory_state = normalize_inventory_state(_safe_dict(player_state.get("inventory_state")))

    # Fix #8: Validate companion exists and is not downed
    companion = get_companion_by_id(player_state, npc_id)
    if companion and companion.get("status") == "downed":
        return {
            "simulation_state": simulation_state,
            "result": {"ok": False, "reason": "companion_downed", "npc_id": npc_id, "item_id": item_id},
        }

    item_def = get_item_definition(_safe_str(item_id))

    if not item_def:
        return {
            "simulation_state": simulation_state,
            "result": {"ok": False, "reason": "unknown_item", "npc_id": npc_id, "item_id": item_id},
        }

    # Check ownership in inventory
    items = inventory_state.get("items") or []
    has_item = any(
        isinstance(item, dict)
        and _safe_str(item.get("item_id")) == _safe_str(item_id)
        and int(item.get("qty", 0)) > 0
        for item in items
    )
    if not has_item:
        return {
            "simulation_state": simulation_state,
            "result": {"ok": False, "reason": "item_not_owned", "npc_id": npc_id, "item_id": item_id},
        }

    # Fix #7: Compute effect first, then apply
    effect = _safe_dict(item_def.get("effect"))
    effect_type = _safe_str(effect.get("type"))

    # Apply effect to companion (atomic mutation)
    new_player_state = player_state
    if effect_type == "restore_resource" and _safe_str(effect.get("resource")) == "health":
        amount = int(effect.get("amount") or 0)
        new_player_state = update_companion_hp(new_player_state, npc_id, amount)

    # Consume from inventory
    new_inventory_state = remove_inventory_item(new_player_state.get("inventory_state", {}), item_id, qty=1)
    new_player_state["inventory_state"] = new_inventory_state

    # Single mutation to simulation_state
    simulation_state["player_state"] = new_player_state

    return {
        "simulation_state": simulation_state,
        "result": {
            "ok": True,
            "npc_id": npc_id,
            "item_id": item_id,
            "effect": effect,
        },
    }