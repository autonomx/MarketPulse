"""Deterministic RPG item modification helpers."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import consume_inventory_item, display_item_name, find_inventory_item, inventory_quantity
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MODIFICATION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "edge_damage_minor": {
        "mod_id": "edge_damage_minor",
        "name": "Honed Edge",
        "valid_item_types": ["weapon"],
        "required_materials": [{"material_id": "iron", "quantity": 1}],
        "effects": [{"op": "add_damage", "damage_type": "slashing", "amount": 1}],
    },
    "reinforced_armor_minor": {
        "mod_id": "reinforced_armor_minor",
        "name": "Reinforced Panels",
        "valid_item_types": ["armor", "clothing"],
        "required_materials": [{"material_id": "leather", "quantity": 1}],
        "effects": [{"op": "add_defense", "defense_type": "slashing", "amount": 1}],
    },
    "insulated_lining_minor": {
        "mod_id": "insulated_lining_minor",
        "name": "Insulated Lining",
        "valid_item_types": ["armor", "clothing"],
        "required_materials": [{"material_id": "cloth", "quantity": 1}],
        "effects": [{"op": "add_resistance", "damage_type": "cold", "amount": 1}],
    },
}

SUPPORTED_MOD_EFFECTS = {"add_damage", "add_defense", "add_resistance"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _positive_quantity(value: Any, fallback: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return fallback


def item_type(item: dict[str, Any]) -> str:
    return _norm(item.get("item_type") or item.get("type") or item.get("category"))


def item_modifications(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [deepcopy(_safe_dict(mod)) for mod in _safe_list(item.get("modifications")) if _safe_dict(mod)]


def _definition(mod_id: str) -> dict[str, Any] | None:
    return deepcopy(MODIFICATION_DEFINITIONS.get(_norm(mod_id)))


def _material_requirements(definition: dict[str, Any]) -> list[dict[str, Any]]:
    return [deepcopy(_safe_dict(entry)) for entry in _safe_list(definition.get("required_materials")) if _safe_dict(entry)]


def _find_material_index(inventory: list[dict[str, Any]], material_id: str) -> int:
    for index, item in enumerate(inventory):
        if _norm(item.get("material_id") or item.get("item_id") or item.get("id")) == _norm(material_id):
            return index
    return -1


def preview_item_modification(item: dict[str, Any], inventory: list[dict[str, Any]], mod_id: str) -> dict[str, Any]:
    definition = _definition(mod_id)
    if not definition:
        return {"ok": False, "error": "unknown_modification", "mod_id": mod_id, "detail": f"Unknown modification: {mod_id}."}

    normalized_item_type = item_type(item)
    valid_types = {_norm(value) for value in _safe_list(definition.get("valid_item_types"))}
    if valid_types and normalized_item_type not in valid_types:
        return {
            "ok": False,
            "error": "invalid_modification_target",
            "mod_id": definition["mod_id"],
            "item_name": display_item_name(item),
            "item_type": normalized_item_type,
            "valid_item_types": sorted(valid_types),
            "detail": f"{definition['name']} cannot be applied to {display_item_name(item)}.",
        }

    existing_mods = {_norm(mod.get("mod_id") or mod.get("id")) for mod in item_modifications(item)}
    if _norm(definition["mod_id"]) in existing_mods:
        return {"ok": False, "error": "modification_already_applied", "mod_id": definition["mod_id"], "item_name": display_item_name(item)}

    missing: list[dict[str, Any]] = []
    for requirement in _material_requirements(definition):
        material_id = _text(requirement.get("material_id"))
        required_quantity = _positive_quantity(requirement.get("quantity"), 1)
        index = _find_material_index(inventory, material_id)
        available = inventory_quantity(inventory[index]) if index >= 0 else 0
        if available < required_quantity:
            missing.append({"material_id": material_id, "required": required_quantity, "available": available})

    if missing:
        return {"ok": False, "error": "missing_modification_materials", "mod_id": definition["mod_id"], "missing": missing, "detail": f"Missing materials for {definition['name']}."}

    return {"ok": True, "modification": definition, "required_materials": _material_requirements(definition), "item_name": display_item_name(item)}


def _add_amount(profile: dict[str, Any], key: str, amount: int) -> dict[str, Any]:
    updated = deepcopy(_safe_dict(profile))
    updated[key] = int(updated.get(key) or 0) + amount
    return updated


def _apply_effect(item: dict[str, Any], effect: dict[str, Any], repairs: list[str]) -> None:
    op = _norm(effect.get("op"))
    if op not in SUPPORTED_MOD_EFFECTS:
        repairs.append(f"ignored_unsupported_mod_effect:{op or 'unknown'}")
        return
    amount = _positive_quantity(effect.get("amount"), 1)
    if op == "add_damage":
        damage_type = _text(effect.get("damage_type") or effect.get("type"), "physical")
        item["damage"] = _add_amount(_safe_dict(item.get("damage")), damage_type, amount)
    elif op == "add_defense":
        defense_type = _text(effect.get("defense_type") or effect.get("type"), "physical")
        item["defense"] = _add_amount(_safe_dict(item.get("defense")), defense_type, amount)
    elif op == "add_resistance":
        damage_type = _text(effect.get("damage_type") or effect.get("type"), "physical")
        item["resistances"] = _add_amount(_safe_dict(item.get("resistances")), damage_type, amount)


def apply_item_modification(item: dict[str, Any], inventory: list[dict[str, Any]], mod_id: str) -> dict[str, Any]:
    preview = preview_item_modification(item, inventory, mod_id)
    if not preview.get("ok"):
        return preview

    definition = _safe_dict(preview.get("modification"))
    updated_item = deepcopy(item)
    consumed_materials: list[dict[str, Any]] = []
    for requirement in _material_requirements(definition):
        material_id = _text(requirement.get("material_id"))
        quantity = _positive_quantity(requirement.get("quantity"), 1)
        index = _find_material_index(inventory, material_id)
        if index >= 0:
            consumed_materials.append({"material_id": material_id, "quantity": quantity, "name": display_item_name(inventory[index])})
            consume_inventory_item(inventory, index, quantity)

    repairs: list[str] = []
    for effect in _safe_list(definition.get("effects")):
        _apply_effect(updated_item, _safe_dict(effect), repairs)

    mods = item_modifications(updated_item)
    mods.append({"mod_id": definition["mod_id"], "name": definition["name"], "mechanics_source": "engine_item_modification_v1"})
    updated_item["modifications"] = mods
    updated_item["mechanics_source"] = "engine_item_modification_v1"

    trace = {
        "event": "item_modified",
        "source_item_id": _text(item.get("item_id") or item.get("id")),
        "source_item_name": display_item_name(item),
        "mod_id": definition["mod_id"],
        "mod_name": definition["name"],
        "consumed_materials": consumed_materials,
        "effects": deepcopy(_safe_list(definition.get("effects"))),
        "repairs": repairs,
        "mechanics_source": "engine_item_modification_v1",
    }
    return {
        "ok": True,
        "item": updated_item,
        "modification": {"mod_id": definition["mod_id"], "name": definition["name"]},
        "consumed_materials": consumed_materials,
        "repairs": repairs,
        "trace": trace,
        "detail": f"Applied {definition['name']} to {display_item_name(item)}.",
    }


def replace_inventory_item(inventory: list[dict[str, Any]], original_name: str, updated_item: dict[str, Any]) -> bool:
    found_inventory, index, _item = find_inventory_item({"inventory": inventory}, original_name)
    if index < 0:
        return False
    found_inventory[index] = deepcopy(updated_item)
    inventory[:] = found_inventory
    return True


