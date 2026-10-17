"""Deterministic RPG item schema, catalog, crafting, and upgrade helpers.

N129 foundation: AI may propose item names, descriptions, and flavor tags, but
item mechanics are owned by the engine. Damage, defense, rarity, level, upgrade
costs, recipe ingredients, and deterministic effects are validated here before
being written into session state.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

from pydantic import BaseModel, Field
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_TYPES = (
    "weapon",
    "armor",
    "consumable",
    "crafting_material",
    "tool",
    "ammo",
    "quest",
    "clothing",
    "supply",
    "camping",
)
WEAPON_TYPES = (
    "dagger",
    "sword",
    "axe",
    "mace",
    "spear",
    "bow",
    "crossbow",
    "staff",
    "wand",
    "firearm",
    "energy_pistol",
    "thrown",
    "unarmed",
)
ARMOR_TYPES = (
    "light",
    "medium",
    "heavy",
    "shield",
    "cloak",
    "robe",
    "helmet",
    "boots",
    "gloves",
    "accessory",
)
CONSUMABLE_TYPES = (
    "healing",
    "mana",
    "stamina",
    "food",
    "antidote",
    "buff",
    "utility",
)
DAMAGE_TYPES = (
    "slashing",
    "piercing",
    "bludgeoning",
    "fire",
    "cold",
    "lightning",
    "poison",
    "psychic",
    "radiant",
    "necrotic",
    "force",
    "arcane",
    "ballistic",
    "laser",
    "plasma",
    "explosive",
)
DEFENSE_TYPES = (
    "physical",
    "slashing",
    "piercing",
    "bludgeoning",
    "fire",
    "cold",
    "lightning",
    "poison",
    "psychic",
    "arcane",
    "ballistic",
    "energy",
    "explosive",
)
RARITIES = ("common", "uncommon", "rare", "epic", "legendary", "mythic")
RARITY_POWER_SCALE = {
    "common": 1.0,
    "uncommon": 1.15,
    "rare": 1.35,
    "epic": 1.65,
    "legendary": 2.0,
    "mythic": 2.5,
}
RARITY_VALUE_SCALE = {
    "common": 1,
    "uncommon": 3,
    "rare": 8,
    "epic": 20,
    "legendary": 60,
    "mythic": 120,
}
ENGINE_OWNED_ITEM_FIELDS = {
    "id",
    "item_id",
    "type",
    "item_type",
    "subtype",
    "weapon_type",
    "armor_type",
    "consumable_type",
    "damage",
    "defense",
    "stats",
    "modifiers",
    "rarity",
    "level",
    "item_level",
    "quantity",
    "stackable",
    "slot",
    "equip_slot",
    "hands",
    "range",
    "value",
    "weight",
    "effects",
    "upgrade",
    "upgrade_level",
    "max_upgrade_level",
    "crafting",
    "recipe",
    "ingredients",
    "requirements",
}
AI_FICTION_ITEM_FIELDS = {
    "name",
    "description",
    "flavor_text",
    "flavor_tags",
    "icon",
    "visual_prompt",
    "maker",
    "culture",
}


class RpgItemValidationResult(BaseModel):
    ok: bool
    item_id: str | None = None
    error: str | None = None
    detail: str = ""
    warnings: list[str] = Field(default_factory=list)


class RpgItemFictionProposalResult(BaseModel):
    ok: bool
    item: dict[str, Any]
    source: str = "ai_item_fiction_proposal_v1"
    ignored_fields: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _positive_int(value: Any, fallback: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return fallback


def _item_type(item: dict[str, Any]) -> str:
    return _text(item.get("item_type") or item.get("type"), "supply")


def _item_id(item: dict[str, Any]) -> str:
    return _text(item.get("id") or item.get("item_id"))


def _rarity(item: dict[str, Any]) -> str:
    rarity = _text(item.get("rarity"), "common").casefold()
    return rarity if rarity in RARITIES else "common"


def _scaled_amount(base_amount: int, *, level: int = 1, rarity: str = "common", upgrade_level: int = 0) -> int:
    scale = RARITY_POWER_SCALE.get(rarity, 1.0)
    level_bonus = max(0, int(level) - 1) * 0.5
    upgrade_bonus = max(0, int(upgrade_level)) * 2
    return max(1, int(round((base_amount + level_bonus) * scale + upgrade_bonus)))


def _base_value(base_value: int, *, level: int = 1, rarity: str = "common", upgrade_level: int = 0) -> int:
    value = int(base_value) + max(0, int(level) - 1) * 2 + max(0, int(upgrade_level)) * 15
    return max(1, value * RARITY_VALUE_SCALE.get(rarity, 1))


def _damage(damage_type: str, amount: int) -> dict[str, Any]:
    return {"type": damage_type, "amount": int(amount)}


def _defense(defense_type: str, amount: int) -> dict[str, Any]:
    return {"type": defense_type, "amount": int(amount)}


def _upgrade_block(max_upgrade_level: int, material_id: str, material_name: str) -> dict[str, Any]:
    return {
        "upgrade_level": 0,
        "max_upgrade_level": max_upgrade_level,
        "material_id": material_id,
        "material_name": material_name,
        "base_cost_quantity": 1,
    }


def make_weapon_template(
    item_id: str,
    name: str,
    *,
    weapon_type: str,
    damage_type: str,
    base_damage: int,
    level: int = 1,
    rarity: str = "common",
    slot: str = "Weapon",
    quantity: int = 1,
    hands: int = 1,
    range_kind: str = "melee",
    tags: Sequence[str] | None = None,
    value: int = 10,
) -> dict[str, Any]:
    damage_amount = _scaled_amount(base_damage, level=level, rarity=rarity)
    return {
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "type": "weapon",
        "item_type": "weapon",
        "weapon_type": weapon_type,
        "slot": slot,
        "equip_slot": slot,
        "level": level,
        "item_level": level,
        "rarity": rarity,
        "quantity": quantity,
        "stackable": False,
        "hands": hands,
        "range": range_kind,
        "damage": [_damage(damage_type, damage_amount)],
        "stats": {},
        "tags": list(tags or []),
        "value": _base_value(value, level=level, rarity=rarity),
        "weight": 1.0 if weapon_type in {"dagger", "wand"} else 2.0,
        "upgrade": _upgrade_block(5, "iron_ingot", "Iron ingot"),
    }


def make_armor_template(
    item_id: str,
    name: str,
    *,
    armor_type: str,
    defense_type: str = "physical",
    base_defense: int,
    level: int = 1,
    rarity: str = "common",
    slot: str = "Armor",
    quantity: int = 1,
    tags: Sequence[str] | None = None,
    value: int = 12,
) -> dict[str, Any]:
    defense_amount = _scaled_amount(base_defense, level=level, rarity=rarity)
    material_id = "leather_strip" if armor_type in {"light", "cloak", "robe", "boots", "gloves"} else "iron_ingot"
    material_name = "Leather strip" if material_id == "leather_strip" else "Iron ingot"
    return {
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "type": "armor" if armor_type not in {"cloak", "accessory"} else armor_type,
        "item_type": "armor",
        "armor_type": armor_type,
        "slot": slot,
        "equip_slot": slot,
        "level": level,
        "item_level": level,
        "rarity": rarity,
        "quantity": quantity,
        "stackable": False,
        "defense": [_defense(defense_type, defense_amount)],
        "stats": {},
        "tags": list(tags or []),
        "value": _base_value(value, level=level, rarity=rarity),
        "weight": 2.0 if armor_type == "light" else 4.0,
        "upgrade": _upgrade_block(5, material_id, material_name),
    }


def make_consumable_template(
    item_id: str,
    name: str,
    *,
    consumable_type: str,
    effects: list[dict[str, Any]],
    level: int = 1,
    rarity: str = "common",
    quantity: int = 1,
    legacy_type: str = "consumable",
    tags: Sequence[str] | None = None,
    value: int = 5,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "type": legacy_type,
        "item_type": "consumable",
        "consumable_type": consumable_type,
        "level": level,
        "item_level": level,
        "rarity": rarity,
        "quantity": quantity,
        "stackable": True,
        "effects": deepcopy(effects),
        "tags": list(tags or []),
        "value": _base_value(value, level=level, rarity=rarity),
        "weight": 0.1,
    }


def make_material_template(item_id: str, name: str, *, quantity: int = 1, rarity: str = "common", tags: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "type": "crafting_material",
        "item_type": "crafting_material",
        "level": 1,
        "item_level": 1,
        "rarity": rarity,
        "quantity": quantity,
        "stackable": True,
        "tags": list(tags or []),
        "value": RARITY_VALUE_SCALE.get(rarity, 1),
        "weight": 0.1,
    }


def make_misc_template(item_id: str, name: str, *, item_type: str, quantity: int = 1, tags: Sequence[str] | None = None) -> dict[str, Any]:
    return {
        "id": item_id,
        "item_id": item_id,
        "name": name,
        "type": item_type,
        "item_type": item_type,
        "level": 1,
        "item_level": 1,
        "rarity": "common",
        "quantity": quantity,
        "stackable": quantity > 1 or item_type in {"ammo", "supply"},
        "tags": list(tags or []),
        "value": 1,
        "weight": 0.1,
    }


def _genre_names(genre: str) -> dict[str, str]:
    normalized = _norm(genre).replace(" ", "_")
    if normalized in {"cyberpunk", "sci_fi", "science_fiction"}:
        return {
            "iron_dagger": "Streetline mono-knife",
            "simple_bow": "Compact smartbow",
            "arrow": "Smart quarrel",
            "travelers_cloak": "Weatherproof synth-cloak",
            "leather_armor": "Patchwork kevlar vest",
            "health_potion": "Trauma gel injector",
            "mana_potion": "Neural focus ampoule",
            "ration": "Shelf-stable ration pack",
            "torch": "Pocket lumen flare",
            "waterskin": "Filtered hydration pouch",
            "bedroll": "Thermal bivy roll",
            "journal": "Encrypted field log",
            "iron_ingot": "Alloy plate",
            "leather_strip": "Polymer weave strip",
            "keenleaf": "Stimulant herb patch",
            "focus_crystal": "Focus crystal",
            "rope_coil": "Microfilament rope coil",
        }
    if normalized in {"detective_noir", "modern_occult"}:
        return {
            "iron_dagger": "Boot knife",
            "simple_bow": "Take-down hunting bow",
            "arrow": "Broadhead arrow",
            "travelers_cloak": "Rain-dark overcoat",
            "leather_armor": "Padded coat lining",
            "health_potion": "First-aid tonic",
            "mana_potion": "Black-label focus draught",
            "ration": "Wrapped sandwich",
            "torch": "Heavy flashlight",
            "waterskin": "Canteen",
            "bedroll": "Canvas bedroll",
            "journal": "Case notebook",
            "iron_ingot": "Scrap metal",
            "leather_strip": "Leather strap",
            "keenleaf": "Bitterroot",
            "focus_crystal": "Glass focus charm",
            "rope_coil": "Rope coil",
        }
    return {
        "iron_dagger": "Iron dagger",
        "simple_bow": "Simple bow",
        "arrow": "Arrow",
        "travelers_cloak": "Traveler's cloak",
        "leather_armor": "Leather armor",
        "health_potion": "Health Potion",
        "mana_potion": "Mana Potion",
        "ration": "Ration",
        "torch": "Torch",
        "waterskin": "Waterskin",
        "bedroll": "Bedroll",
        "journal": "Journal",
        "iron_ingot": "Iron ingot",
        "leather_strip": "Leather strip",
        "keenleaf": "Keenleaf",
        "focus_crystal": "Focus Crystal",
        "rope_coil": "Rope Coil",
    }


def build_item_catalog(genre: str = "classic_fantasy", *, level: int = 1) -> dict[str, dict[str, Any]]:
    names = _genre_names(genre)
    level = _positive_int(level, 1)
    catalog = {
        "iron_dagger": make_weapon_template(
            "iron_dagger",
            names["iron_dagger"],
            weapon_type="dagger",
            damage_type="piercing",
            base_damage=4,
            level=level,
            slot="Weapon",
            hands=1,
            range_kind="melee",
            tags=["starter", "finesse"],
            value=8,
        ),
        "simple_bow": make_weapon_template(
            "simple_bow",
            names["simple_bow"],
            weapon_type="bow",
            damage_type="piercing",
            base_damage=6,
            level=level,
            slot="Ranged",
            hands=2,
            range_kind="ranged",
            tags=["starter", "ranged"],
            value=14,
        ),
        "leather_armor": make_armor_template(
            "leather_armor",
            names["leather_armor"],
            armor_type="light",
            base_defense=3,
            level=level,
            slot="Armor",
            tags=["starter", "light_armor"],
            value=18,
        ),
        "travelers_cloak": make_armor_template(
            "travelers_cloak",
            names["travelers_cloak"],
            armor_type="cloak",
            defense_type="cold",
            base_defense=1,
            level=level,
            slot="Cloak",
            tags=["travel", "weather"],
            value=6,
        ),
        "health_potion": make_consumable_template(
            "health_potion",
            names["health_potion"],
            consumable_type="healing",
            effects=[{"op": "restore_resource", "resource": "hp", "amount": _scaled_amount(25, level=level, rarity="common"), "dimension": "resources"}],
            level=level,
            quantity=1,
            legacy_type="consumable",
            tags=["healing"],
            value=12,
        ),
        "mana_potion": make_consumable_template(
            "mana_potion",
            names["mana_potion"],
            consumable_type="mana",
            effects=[{"op": "restore_resource", "resource": "mana", "amount": _scaled_amount(25, level=level, rarity="common"), "dimension": "resources"}],
            level=level,
            quantity=1,
            legacy_type="consumable",
            tags=["focus"],
            value=12,
        ),
        "ration": make_consumable_template(
            "ration",
            names["ration"],
            consumable_type="food",
            effects=[{"op": "restore_resource", "resource": "stamina", "amount": 10, "dimension": "resources"}],
            level=1,
            quantity=1,
            legacy_type="food",
            tags=["food", "travel"],
            value=2,
        ),
        "torch": make_consumable_template(
            "torch",
            names["torch"],
            consumable_type="utility",
            effects=[{"op": "apply_scene_status", "status": "lit_torch", "dimension": "environment"}],
            level=1,
            quantity=1,
            legacy_type="tool",
            tags=["light", "tool"],
            value=1,
        ),
        "arrow": make_misc_template("arrow", names["arrow"], item_type="ammo", quantity=1, tags=["ammo", "ranged"]),
        "bedroll": make_misc_template("bedroll", names["bedroll"], item_type="camping", quantity=1, tags=["rest"]),
        "waterskin": make_misc_template("waterskin", names["waterskin"], item_type="supply", quantity=1, tags=["water"]),
        "journal": make_misc_template("journal", names["journal"], item_type="quest", quantity=1, tags=["notes", "protected"]),
        "rope_coil": make_misc_template("rope_coil", names["rope_coil"], item_type="tool", quantity=1, tags=["climbing", "travel"]),
        "iron_ingot": make_material_template("iron_ingot", names["iron_ingot"], tags=["metal", "upgrade_weapon"]),
        "leather_strip": make_material_template("leather_strip", names["leather_strip"], tags=["leather", "upgrade_armor"]),
        "keenleaf": make_material_template("keenleaf", names["keenleaf"], tags=["herb", "potion"]),
        "focus_crystal": make_material_template("focus_crystal", names["focus_crystal"], rarity="uncommon", tags=["crystal", "focus"]),
    }
    return {item_id: normalize_item_instance(item) for item_id, item in catalog.items()}


def validate_item_template(item: dict[str, Any]) -> RpgItemValidationResult:
    item = _safe_dict(item)
    item_id = _item_id(item)
    warnings: list[str] = []
    if not item_id:
        return RpgItemValidationResult(ok=False, error="missing_item_id", detail="Item requires a stable id.")
    if not _text(item.get("name")):
        return RpgItemValidationResult(ok=False, item_id=item_id, error="missing_item_name", detail="Item requires a display name.")
    item_type = _item_type(item)
    if item_type not in ITEM_TYPES:
        return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_item_type", detail=f"Unsupported item type: {item_type}")
    if _text(item.get("rarity"), "common") not in RARITIES:
        return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_item_rarity", detail=f"Unsupported item rarity: {item.get('rarity')}")
    if _positive_int(item.get("level"), 1) < 1:
        return RpgItemValidationResult(ok=False, item_id=item_id, error="invalid_item_level", detail="Item level must be >= 1.")

    if item_type == "weapon":
        weapon_type = _text(item.get("weapon_type"))
        if weapon_type not in WEAPON_TYPES:
            return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_weapon_type", detail=f"Unsupported weapon type: {weapon_type}")
        damage = _safe_list(item.get("damage"))
        if not damage:
            return RpgItemValidationResult(ok=False, item_id=item_id, error="missing_weapon_damage", detail="Weapon requires at least one damage entry.")
        for entry in damage:
            damage_entry = _safe_dict(entry)
            if damage_entry.get("type") not in DAMAGE_TYPES:
                return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_damage_type", detail=f"Unsupported damage type: {damage_entry.get('type')}")
            if int(damage_entry.get("amount") or 0) <= 0:
                return RpgItemValidationResult(ok=False, item_id=item_id, error="invalid_damage_amount", detail="Weapon damage amount must be positive.")

    if item_type == "armor":
        armor_type = _text(item.get("armor_type"))
        if armor_type not in ARMOR_TYPES:
            return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_armor_type", detail=f"Unsupported armor type: {armor_type}")
        defense = _safe_list(item.get("defense"))
        if not defense:
            return RpgItemValidationResult(ok=False, item_id=item_id, error="missing_armor_defense", detail="Armor requires at least one defense entry.")
        for entry in defense:
            defense_entry = _safe_dict(entry)
            if defense_entry.get("type") not in DEFENSE_TYPES:
                return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_defense_type", detail=f"Unsupported defense type: {defense_entry.get('type')}")
            if int(defense_entry.get("amount") or 0) <= 0:
                return RpgItemValidationResult(ok=False, item_id=item_id, error="invalid_defense_amount", detail="Armor defense amount must be positive.")

    if item_type == "consumable":
        consumable_type = _text(item.get("consumable_type"))
        if consumable_type not in CONSUMABLE_TYPES:
            return RpgItemValidationResult(ok=False, item_id=item_id, error="unsupported_consumable_type", detail=f"Unsupported consumable type: {consumable_type}")
        if not _safe_list(item.get("effects")):
            return RpgItemValidationResult(ok=False, item_id=item_id, error="missing_consumable_effect", detail="Consumable requires at least one deterministic effect.")

    if item_type in {"crafting_material", "ammo", "supply"} and not bool(item.get("stackable", True)):
        warnings.append(f"{item_id}: {item_type} is usually stackable")
    return RpgItemValidationResult(ok=True, item_id=item_id, detail="Item template is valid.", warnings=warnings)


def normalize_item_instance(item: dict[str, Any], *, quantity: int | None = None) -> dict[str, Any]:
    normalized = deepcopy(_safe_dict(item))
    item_id = _item_id(normalized)
    if item_id:
        normalized["id"] = item_id
        normalized["item_id"] = item_id
    normalized["item_type"] = _item_type(normalized)
    normalized.setdefault("type", normalized["item_type"])
    normalized["rarity"] = _rarity(normalized)
    level = _positive_int(normalized.get("level") or normalized.get("item_level"), 1)
    normalized["level"] = level
    normalized["item_level"] = level
    normalized["quantity"] = max(0, int(quantity if quantity is not None else normalized.get("quantity", 1) or 0))
    normalized.setdefault("stackable", normalized["quantity"] > 1 or normalized["item_type"] in {"consumable", "crafting_material", "ammo", "supply"})
    if normalized["item_type"] in {"weapon", "armor"}:
        upgrade = _safe_dict(normalized.get("upgrade"))
        upgrade.setdefault("upgrade_level", int(normalized.get("upgrade_level") or 0))
        upgrade.setdefault("max_upgrade_level", int(normalized.get("max_upgrade_level") or 0))
        normalized["upgrade"] = upgrade
    return normalized


def _find_inventory_item(inventory: list[dict[str, Any]], item_id: str) -> tuple[int, dict[str, Any] | None]:
    for index, item in enumerate(inventory):
        if _item_id(_safe_dict(item)) == item_id:
            return index, _safe_dict(item)
    return -1, None


CRAFTING_RECIPES: dict[str, dict[str, Any]] = {
    "healing_potion": {
        "recipe_id": "healing_potion",
        "result_item_id": "health_potion",
        "quantity": 1,
        "ingredients": [{"item_id": "keenleaf", "quantity": 2}, {"item_id": "waterskin", "quantity": 1}],
        "station": "campfire_or_alchemy_kit",
    },
    "iron_dagger": {
        "recipe_id": "iron_dagger",
        "result_item_id": "iron_dagger",
        "quantity": 1,
        "ingredients": [{"item_id": "iron_ingot", "quantity": 2}, {"item_id": "leather_strip", "quantity": 1}],
        "station": "forge_or_field_kit",
    },
    "leather_armor": {
        "recipe_id": "leather_armor",
        "result_item_id": "leather_armor",
        "quantity": 1,
        "ingredients": [{"item_id": "leather_strip", "quantity": 4}],
        "station": "armorers_kit",
    },
}


def apply_item_fiction_proposal(item: dict[str, Any], proposal: dict[str, Any] | None, *, genre: str | None = None) -> RpgItemFictionProposalResult:
    """Apply AI-authored item fiction while preserving deterministic mechanics."""

    compiled = normalize_item_instance(item)
    proposal = _safe_dict(proposal)
    ignored_fields: list[str] = []
    repairs: list[str] = []
    for key, value in proposal.items():
        if key in ENGINE_OWNED_ITEM_FIELDS:
            ignored_fields.append(key)
            repairs.append(f"ignored_engine_owned_field:{key}")
            continue
        if key not in AI_FICTION_ITEM_FIELDS:
            ignored_fields.append(key)
            repairs.append(f"ignored_unsupported_fiction_field:{key}")
            continue
        if key == "name":
            name = _text(value)
            if not name:
                repairs.append("ignored_blank_name")
                continue
            compiled["name"] = name[:80]
        elif key == "flavor_tags":
            tags = [_text(tag)[:40] for tag in _safe_list(value) if _text(tag)]
            compiled["flavor_tags"] = tags[:8]
        else:
            compiled[key] = _text(value)[:500]
    if genre and not proposal.get("name"):
        suggested = suggest_genre_item_name(compiled, genre)
        if suggested:
            compiled["name"] = suggested
            repairs.append("filled_genre_name")
    validation = validate_item_template(compiled)
    if not validation.ok:
        repairs.append(f"fallback_original_item:{validation.error}")
        compiled = normalize_item_instance(item)
        validation = validate_item_template(compiled)
    return RpgItemFictionProposalResult(ok=validation.ok, item=compiled, ignored_fields=ignored_fields, repairs=repairs, validation=validation.model_dump())


def suggest_genre_item_name(item: dict[str, Any], genre: str) -> str:
    """Deterministic genre-aware naming fallback for item fiction proposals."""

    item = normalize_item_instance(item)
    names = _genre_names(genre)
    item_id = _item_id(item)
    if item_id in names:
        return names[item_id]
    rarity = _rarity(item)
    base = _text(item.get("name"), "Item")
    normalized_genre = _norm(genre).replace(" ", "_")
    if normalized_genre == "cyberpunk":
        prefix = {"common": "Street", "uncommon": "Modded", "rare": "Prototype", "epic": "Black-ICE", "legendary": "Ghostline", "mythic": "Mythware"}.get(rarity, "Street")
        return f"{prefix} {base}"
    if normalized_genre in {"detective_noir", "modern_occult"}:
        prefix = {"common": "Worn", "uncommon": "Marked", "rare": "Casefile", "epic": "Moonlit", "legendary": "Sainted", "mythic": "Unwritten"}.get(rarity, "Worn")
        return f"{prefix} {base}"
    prefix = {"common": "Iron", "uncommon": "Fine", "rare": "Enchanted", "epic": "Runesung", "legendary": "Dragonforged", "mythic": "Starfallen"}.get(rarity, "Iron")
    return f"{prefix} {base}"
