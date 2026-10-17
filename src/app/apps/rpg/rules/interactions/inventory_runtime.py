from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.companion_auto_equip import apply_companion_auto_equip
from app.apps.rpg.rules.interactions.companion_item_policy import (
    evaluate_companion_item_acceptance,
)
from app.apps.rpg.rules.interactions.equipment_runtime import project_equipment_stats
from app.apps.rpg.rules.interactions.item_model import (
    add_item_to_items_list,
    normalize_item_instance,
    recalculate_inventory_derived_fields,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

DEFAULT_EQUIPMENT_SLOTS = {
    "weapon": "main_hand",
    "shield": "off_hand",
    "armor": "body",
    "helmet": "head",
    "ring": "ring",
    "tool": "tool",
}


def _item_id(item: Dict[str, Any]) -> str:
    item = _safe_dict(item)
    return _safe_str(item.get("item_id") or item.get("id") or item.get("entity_id"))


def _item_name(item: Dict[str, Any]) -> str:
    item = _safe_dict(item)
    return _safe_str(item.get("name") or _item_id(item).replace("item:", ""))


def _ensure_player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    if not player_state:
        player_state = {}
        simulation_state["player_state"] = player_state
    return player_state


def _ensure_inventory(player_state: Dict[str, Any]) -> Dict[str, Any]:
    inventory = _safe_dict(player_state.get("inventory"))
    if not inventory:
        inventory = {}
        player_state["inventory"] = inventory

    if not isinstance(inventory.get("items"), list):
        inventory["items"] = []

    if not isinstance(inventory.get("equipment"), dict):
        inventory["equipment"] = {}

    inventory = recalculate_inventory_derived_fields(inventory)
    player_state["inventory"] = inventory
    return inventory


def _current_location_id(simulation_state: Dict[str, Any]) -> str:
    player_state = _safe_dict(simulation_state.get("player_state"))
    return (
        _safe_str(player_state.get("location_id"))
        or _safe_str(simulation_state.get("location_id"))
        or _safe_str(simulation_state.get("current_location_id"))
    )


def _scene_items(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = simulation_state.get("scene_items")
    if isinstance(items, list):
        return items

    if isinstance(items, dict):
        converted = []
        for item in items.values():
            item = _safe_dict(item)
            if item:
                converted.append(item)
        simulation_state["scene_items"] = converted
        return converted

    simulation_state["scene_items"] = []
    return simulation_state["scene_items"]


def _find_item_index(items: List[Any], item_id: str) -> int:
    for index, item in enumerate(items):
        if _item_id(_safe_dict(item)) == item_id:
            return index
    return -1


def _clone_item_for_inventory(item: Dict[str, Any]) -> Dict[str, Any]:
    cloned = normalize_item_instance(_safe_dict(item))
    cloned.pop("location_id", None)
    cloned["source"] = "deterministic_inventory_runtime"
    return cloned


def _clone_item_for_location(item: Dict[str, Any], location_id: str) -> Dict[str, Any]:
    cloned = normalize_item_instance(_safe_dict(item))
    cloned["location_id"] = location_id
    cloned["source"] = "deterministic_inventory_runtime"
    return cloned


def _companion_inventory(simulation_state: Dict[str, Any], npc_id: str) -> Dict[str, Any]:
    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    companions = _safe_list(party_state.get("companions"))

    for companion in companions:
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) != npc_id:
            continue

        inventory = _safe_dict(companion.get("inventory"))
        if not inventory:
            inventory = {}
            companion["inventory"] = inventory

        if not isinstance(inventory.get("items"), list):
            inventory["items"] = []

        if not isinstance(inventory.get("equipment"), dict):
            inventory["equipment"] = {}

        return inventory

    return {}


def _slot_for_item(item: Dict[str, Any]) -> str:
    item = normalize_item_instance(_safe_dict(item))

    explicit = _safe_str(item.get("slot") or item.get("equipment_slot"))
    if explicit:
        return explicit

    equipment = _safe_dict(item.get("equipment"))
    if _safe_str(equipment.get("slot")):
        return _safe_str(equipment.get("slot"))

    kind = _safe_str(item.get("kind") or item.get("item_type") or item.get("type")).lower()
    return DEFAULT_EQUIPMENT_SLOTS.get(kind, "main_hand")


def _inventory_result(
    *,
    resolved: bool,
    changed_state: bool,
    reason: str,
    action: Dict[str, Any],
    item: Dict[str, Any] | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = {
        "resolved": resolved,
        "changed_state": changed_state,
        "reason": reason,
        "semantic_action_v2": deepcopy(_safe_dict(action)),
        "item": deepcopy(_safe_dict(item or {})),
        "source": "deterministic_inventory_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def apply_inventory_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    kind = _safe_str(action.get("kind"))
    target_id = _safe_str(action.get("target_id"))
    target_resolution = _safe_dict(action.get("target_resolution"))
    target_entity = _safe_dict(target_resolution.get("entity"))
    location_id = _current_location_id(simulation_state)

    player_state = _ensure_player_state(simulation_state)
    inventory = _ensure_inventory(player_state)
    inventory_items = _safe_list(inventory.get("items"))

    if kind not in {"take", "drop", "give", "equip", "unequip"}:
        return _inventory_result(
            resolved=False,
            changed_state=False,
            reason="not_inventory_action",
            action=action,
        )

    if not target_id:
        return _inventory_result(
            resolved=False,
            changed_state=False,
            reason="missing_item_target",
            action=action,
        )

    if kind == "take":
        scene_items = _scene_items(simulation_state)
        index = _find_item_index(scene_items, target_id)
        if index < 0:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="item_not_available_at_location",
                action=action,
            )

        item = _safe_dict(scene_items.pop(index))
        inventory_item = _clone_item_for_inventory(item)

        requested_quantity = int(action.get("quantity") or inventory_item.get("quantity") or 1)
        available_quantity = int(inventory_item.get("quantity") or 1)
        take_quantity = max(1, min(requested_quantity, available_quantity))
        inventory_item["quantity"] = take_quantity
        inventory_item["total_weight"] = round(float(inventory_item.get("unit_weight") or 0.0) * take_quantity, 4)

        if available_quantity > take_quantity:
            remaining = normalize_item_instance(item)
            remaining["quantity"] = available_quantity - take_quantity
            remaining["total_weight"] = round(float(remaining.get("unit_weight") or 0.0) * int(remaining.get("quantity") or 1), 4)
            remaining["location_id"] = location_id
            scene_items.insert(index, remaining)

        add_result = add_item_to_items_list(inventory_items, inventory_item)
        inventory["items"] = add_result["items"]
        inventory = recalculate_inventory_derived_fields(inventory)

        player_state["inventory"] = inventory
        simulation_state["player_state"] = player_state

        return _inventory_result(
            resolved=True,
            changed_state=True,
            reason="item_added_to_inventory",
            action=action,
            item=inventory_item,
            extra={
                "item_id": _item_id(inventory_item),
                "definition_id": _safe_str(inventory_item.get("definition_id")),
                "quantity": int(inventory_item.get("quantity") or 1),
                "stacked": bool(add_result.get("stacked")),
                "stack_events": deepcopy(_safe_list(add_result.get("stack_events"))),
                "added_items": deepcopy(_safe_list(add_result.get("added_items"))),
                "carry_weight": inventory.get("carry_weight"),
                "carry_capacity": inventory.get("carry_capacity"),
                "encumbrance_state": inventory.get("encumbrance_state"),
                "location_id": location_id,
                "tick": int(tick or 0),
            },
        )

    if kind == "drop":
        index = _find_item_index(inventory_items, target_id)
        if index < 0:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="item_not_in_inventory",
                action=action,
            )

        item = _safe_dict(inventory_items.pop(index))
        location_item = _clone_item_for_location(item, location_id)
        _scene_items(simulation_state).append(location_item)

        inventory["items"] = inventory_items
        inventory = recalculate_inventory_derived_fields(inventory)
        player_state["inventory"] = inventory
        simulation_state["player_state"] = player_state

        return _inventory_result(
            resolved=True,
            changed_state=True,
            reason="item_dropped_to_location",
            action=action,
            item=location_item,
            extra={
                "item_id": _item_id(location_item),
                "quantity": int(location_item.get("quantity") or 1),
                "carry_weight": inventory.get("carry_weight"),
                "carry_capacity": inventory.get("carry_capacity"),
                "encumbrance_state": inventory.get("encumbrance_state"),
                "location_id": location_id,
                "tick": int(tick or 0),
            },
        )

    if kind == "give":
        recipient_id = _safe_str(action.get("secondary_target_id"))
        if not recipient_id:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="missing_recipient",
                action=action,
            )

        index = _find_item_index(inventory_items, target_id)
        if index < 0:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="item_not_in_inventory",
                action=action,
                extra={"recipient_id": recipient_id},
            )

        item = _clone_item_for_inventory(_safe_dict(inventory_items[index]))

        companion = {}
        party_state = _safe_dict(player_state.get("party_state"))
        for candidate in _safe_list(party_state.get("companions")):
            candidate = _safe_dict(candidate)
            if _safe_str(candidate.get("npc_id")) == recipient_id:
                companion = candidate
                break

        if not companion:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="recipient_companion_not_found",
                action=action,
                item=item,
                extra={"recipient_id": recipient_id},
            )

        acceptance = evaluate_companion_item_acceptance(
            companion=companion,
            item=item,
        )
        if acceptance.get("accepted") is not True:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="companion_refused_item",
                action=action,
                item=item,
                extra={
                    "recipient_id": recipient_id,
                    "companion_item_acceptance_result": deepcopy(acceptance),
                },
            )

        # Remove from player only after acceptance.
        item = _clone_item_for_inventory(_safe_dict(inventory_items.pop(index)))

        recipient_inventory = _companion_inventory(simulation_state, recipient_id)
        if not recipient_inventory:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="recipient_inventory_not_available",
                action=action,
                item=item,
                extra={
                    "recipient_id": recipient_id,
                    "companion_item_acceptance_result": deepcopy(acceptance),
                },
            )

        recipient_items = _safe_list(recipient_inventory.get("items"))
        recipient_add = add_item_to_items_list(recipient_items, item)
        recipient_inventory["items"] = recipient_add["items"]
        recipient_inventory = recalculate_inventory_derived_fields(recipient_inventory)
        companion["inventory"] = recipient_inventory

        inventory["items"] = inventory_items
        inventory = recalculate_inventory_derived_fields(inventory)
        player_state["inventory"] = inventory
        simulation_state["player_state"] = player_state

        auto_equip = apply_companion_auto_equip(
            simulation_state,
            npc_id=recipient_id,
            item_id=_item_id(item),
            tick=tick,
        )

        return _inventory_result(
            resolved=True,
            changed_state=True,
            reason="item_given_to_npc",
            action=action,
            item=item,
            extra={
                "item_id": _item_id(item),
                "quantity": int(item.get("quantity") or 1),
                "recipient_id": recipient_id,
                "recipient_carry_weight": recipient_inventory.get("carry_weight"),
                "recipient_encumbrance_state": recipient_inventory.get("encumbrance_state"),
                "carry_weight": inventory.get("carry_weight"),
                "encumbrance_state": inventory.get("encumbrance_state"),
                "companion_item_acceptance_result": deepcopy(acceptance),
                "companion_auto_equip_result": deepcopy(auto_equip),
                "tick": int(tick or 0),
            },
        )

    if kind == "equip":
        index = _find_item_index(inventory_items, target_id)
        if index < 0:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="item_not_in_inventory",
                action=action,
            )

        item = _safe_dict(inventory_items[index])
        normalized_item = normalize_item_instance(item)
        slot = _slot_for_item(normalized_item)

        requested_slot = _safe_str(action.get("equipment_slot"))
        if requested_slot:
            slot = requested_slot

        if slot == "ammo":
            tags = {_safe_str(tag) for tag in _safe_list(normalized_item.get("tags"))}
            if "ammo" not in tags:
                return _inventory_result(
                    resolved=False,
                    changed_state=False,
                    reason="item_is_not_ammo",
                    action=action,
                    item=normalized_item,
                )

        equipment = _safe_dict(inventory.get("equipment"))
        equipment[slot] = target_id
        inventory["equipment"] = equipment
        inventory = recalculate_inventory_derived_fields(inventory)
        player_state["inventory"] = inventory
        simulation_state["player_state"] = player_state

        equipment_stats = project_equipment_stats(simulation_state, actor_id="player")
        reason = "ammo_equipped" if slot == "ammo" else "item_equipped"

        return _inventory_result(
            resolved=True,
            changed_state=True,
            reason=reason,
            action=action,
            item=item,
            extra={
                "item_id": target_id,
                "slot": slot,
                "equipment_stats": deepcopy(equipment_stats),
                "carry_weight": inventory.get("carry_weight"),
                "encumbrance_state": inventory.get("encumbrance_state"),
                "tick": int(tick or 0),
            },
        )

    if kind == "unequip":
        equipment = _safe_dict(inventory.get("equipment"))
        removed_slot = ""

        for slot, equipped_item_id in list(equipment.items()):
            if _safe_str(equipped_item_id) == target_id or _safe_str(slot) == _safe_str(action.get("target_ref")):
                removed_slot = _safe_str(slot)
                equipment.pop(slot, None)
                break

        if not removed_slot:
            return _inventory_result(
                resolved=False,
                changed_state=False,
                reason="item_not_equipped",
                action=action,
            )

        inventory["equipment"] = equipment
        inventory = recalculate_inventory_derived_fields(inventory)
        player_state["inventory"] = inventory
        simulation_state["player_state"] = player_state

        equipment_stats = project_equipment_stats(simulation_state, actor_id="player")

        return _inventory_result(
            resolved=True,
            changed_state=True,
            reason="item_unequipped",
            action=action,
            extra={
                "item_id": target_id,
                "slot": removed_slot,
                "equipment_stats": deepcopy(equipment_stats),
                "carry_weight": inventory.get("carry_weight"),
                "encumbrance_state": inventory.get("encumbrance_state"),
                "tick": int(tick or 0),
            },
        )

    return _inventory_result(
        resolved=False,
        changed_state=False,
        reason="unsupported_inventory_action",
        action=action,
    )
