from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

ITEM_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "def:rusty_key": {
        "definition_id": "def:rusty_key",
        "name": "rusty key",
        "kind": "key",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 0.05,
        "rarity": "common",
        "value": {"gold": 0, "silver": 0, "copper": 5},
        "tags": ["key"],
    },
    "def:rusty_dagger": {
        "definition_id": "def:rusty_dagger",
        "name": "rusty dagger",
        "kind": "weapon",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 1.2,
        "rarity": "common",
        "value": {"gold": 0, "silver": 8, "copper": 0},
        "tags": ["weapon", "blade", "dagger"],
        "equipment": {
            "slot": "main_hand",
            "stats": {
                "damage_min": 1,
                "damage_max": 4,
                "accuracy_bonus": 0,
            },
        },
        "condition": {
            "durability": 0.55,
            "max_durability": 1.0,
        },
    },
    "def:small_knife": {
        "definition_id": "def:small_knife",
        "name": "small knife",
        "kind": "weapon",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 0.8,
        "rarity": "common",
        "value": {"gold": 0, "silver": 5, "copper": 0},
        "tags": ["weapon", "blade", "knife"],
        "equipment": {
            "slot": "main_hand",
            "stats": {
                "damage_min": 1,
                "damage_max": 3,
                "accuracy_bonus": 1,
            },
        },
        "condition": {
            "durability": 0.8,
            "max_durability": 1.0,
        },
    },
    "def:rope": {
        "definition_id": "def:rope",
        "name": "rope",
        "kind": "tool",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 2.0,
        "rarity": "common",
        "value": {"gold": 0, "silver": 2, "copper": 0},
        "tags": ["tool", "rope"],
    },
    "def:iron_arrow": {
        "definition_id": "def:iron_arrow",
        "name": "iron arrow",
        "kind": "ammo",
        "stackable": True,
        "max_stack": 99,
        "unit_weight": 0.05,
        "rarity": "common",
        "value": {"gold": 0, "silver": 0, "copper": 2},
        "tags": ["ammo", "arrow", "piercing"],
    },
    "def:heavy_anvil": {
        "definition_id": "def:heavy_anvil",
        "name": "heavy anvil",
        "kind": "tool",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 65.0,
        "rarity": "uncommon",
        "value": {"gold": 2, "silver": 5, "copper": 0},
        "tags": ["tool", "smithing", "heavy"],
    },
    "def:leather_satchel": {
        "definition_id": "def:leather_satchel",
        "name": "leather satchel",
        "kind": "container",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 1.0,
        "rarity": "common",
        "value": {"gold": 0, "silver": 6, "copper": 0},
        "tags": ["container", "bag", "satchel"],
        "container": {
            "capacity_weight": 15.0,
            "items": [],
        },
    },
    "def:whetstone": {
        "definition_id": "def:whetstone",
        "name": "whetstone",
        "kind": "tool",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 0.5,
        "rarity": "common",
        "value": {"gold": 0, "silver": 1, "copper": 0},
        "tags": ["tool", "repair_tool", "sharpening"],
        "repair": {
            "tool": True,
            "target_tags": ["blade", "weapon"],
            "durability_restore": 0.20,
            "consumed": False,
        },
    },
    "def:cloth_scrap": {
        "definition_id": "def:cloth_scrap",
        "name": "cloth scrap",
        "kind": "material",
        "stackable": True,
        "max_stack": 99,
        "unit_weight": 0.05,
        "rarity": "common",
        "value": {"gold": 0, "silver": 0, "copper": 1},
        "tags": ["material", "cloth", "repair_material"],
        "repair": {
            "material": True,
            "target_tags": ["cloth", "armor_light"],
            "durability_restore_per_unit": 0.15,
            "default_quantity": 2,
            "consumed": True,
        },
    },
    "def:wooden_stick": {
        "definition_id": "def:wooden_stick",
        "name": "wooden stick",
        "kind": "material",
        "stackable": True,
        "max_stack": 20,
        "unit_weight": 0.4,
        "rarity": "common",
        "value": {"gold": 0, "silver": 0, "copper": 1},
        "tags": ["material", "wood", "stick"],
    },
    "def:oil_flask": {
        "definition_id": "def:oil_flask",
        "name": "oil flask",
        "kind": "material",
        "stackable": True,
        "max_stack": 10,
        "unit_weight": 0.5,
        "rarity": "common",
        "value": {"gold": 0, "silver": 2, "copper": 0},
        "tags": ["material", "oil", "flammable"],
    },
    "def:torch": {
        "definition_id": "def:torch",
        "name": "torch",
        "kind": "tool",
        "stackable": True,
        "max_stack": 10,
        "unit_weight": 0.7,
        "rarity": "common",
        "value": {"gold": 0, "silver": 1, "copper": 0},
        "tags": ["tool", "light", "fire_source"],
        "consumable": {
            "effect": {
                "kind": "light_source",
                "duration_turns": 20,
                "target": "self"
            },
            "consumed_quantity": 1
        },
    },
    "def:torn_cloak": {
        "definition_id": "def:torn_cloak",
        "name": "torn cloak",
        "kind": "armor",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 1.0,
        "rarity": "common",
        "value": {"gold": 0, "silver": 3, "copper": 0},
        "tags": ["cloth", "armor_light", "cloak"],
        "equipment": {
            "slot": "cloak",
            "stats": {
                "armor": 0,
                "stealth_bonus": 0,
            },
        },
        "condition": {
            "durability": 0.35,
            "max_durability": 1.0,
        },
    },
    "def:minor_healing_potion": {
        "definition_id": "def:minor_healing_potion",
        "name": "minor healing potion",
        "kind": "consumable",
        "stackable": True,
        "max_stack": 10,
        "unit_weight": 0.3,
        "rarity": "common",
        "value": {"gold": 0, "silver": 10, "copper": 0},
        "tags": ["consumable", "potion", "healing"],
        "consumable": {
            "effect": {
                "kind": "heal",
                "amount": 5,
                "target": "self"
            },
            "consumed_quantity": 1
        },
    },
    "def:hunting_bow": {
        "definition_id": "def:hunting_bow",
        "name": "hunting bow",
        "kind": "weapon",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 2.0,
        "rarity": "common",
        "value": {"gold": 1, "silver": 5, "copper": 0},
        "tags": ["weapon", "bow", "ranged"],
        "equipment": {
            "slot": "main_hand",
            "requires_ammo_tag": "arrow",
            "stats": {
                "damage_min": 1,
                "damage_max": 6,
                "accuracy_bonus": 1,
                "range": 4
            },
        },
        "condition": {
            "durability": 0.9,
            "max_durability": 1.0,
        },
    },
    "def:padded_armor": {
        "definition_id": "def:padded_armor",
        "name": "padded armor",
        "kind": "armor",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 6.0,
        "rarity": "common",
        "value": {"gold": 1, "silver": 2, "copper": 0},
        "tags": ["armor", "armor_light", "body"],
        "equipment": {
            "slot": "body",
            "stats": {
                "armor": 1,
                "stealth_penalty": 0
            },
        },
        "condition": {
            "durability": 0.85,
            "max_durability": 1.0,
        },
    },
    "def:copper_coin": {
        "definition_id": "def:copper_coin",
        "name": "copper coin",
        "kind": "currency_item",
        "stackable": True,
        "max_stack": 999,
        "unit_weight": 0.01,
        "rarity": "common",
        "value": {"gold": 0, "silver": 0, "copper": 1},
        "tags": ["currency", "coin"],
    },
    "def:bandit_token": {
        "definition_id": "def:bandit_token",
        "name": "bandit token",
        "kind": "trinket",
        "stackable": True,
        "max_stack": 20,
        "unit_weight": 0.05,
        "rarity": "common",
        "value": {"gold": 0, "silver": 1, "copper": 0},
        "tags": ["trinket", "bandit"],
    },
    "def:stolen_ring": {
        "definition_id": "def:stolen_ring",
        "name": "stolen ring",
        "kind": "trinket",
        "stackable": False,
        "max_stack": 1,
        "unit_weight": 0.05,
        "rarity": "uncommon",
        "value": {"gold": 1, "silver": 0, "copper": 0},
        "tags": ["valuable", "jewelry", "ring", "stolen", "illicit"],
    },
}


NAME_TO_DEFINITION_ID = {
    "rusty key": "def:rusty_key",
    "key": "def:rusty_key",
    "rusty dagger": "def:rusty_dagger",
    "dagger": "def:rusty_dagger",
    "small knife": "def:small_knife",
    "knife": "def:small_knife",
    "rope": "def:rope",
    "length of rope": "def:rope",
    "iron arrow": "def:iron_arrow",
    "iron arrows": "def:iron_arrow",
    "arrow": "def:iron_arrow",
    "arrows": "def:iron_arrow",
    "heavy anvil": "def:heavy_anvil",
    "anvil": "def:heavy_anvil",
    "leather satchel": "def:leather_satchel",
    "satchel": "def:leather_satchel",
    "bag": "def:leather_satchel",
    "whetstone": "def:whetstone",
    "cloth scrap": "def:cloth_scrap",
    "cloth scraps": "def:cloth_scrap",
    "scrap": "def:cloth_scrap",
    "scraps": "def:cloth_scrap",
    "torn cloak": "def:torn_cloak",
    "cloak": "def:torn_cloak",
    "minor healing potion": "def:minor_healing_potion",
    "healing potion": "def:minor_healing_potion",
    "potion": "def:minor_healing_potion",
    "hunting bow": "def:hunting_bow",
    "bow": "def:hunting_bow",
    "padded armor": "def:padded_armor",
    "armor": "def:padded_armor",
    "wooden stick": "def:wooden_stick",
    "stick": "def:wooden_stick",
    "sticks": "def:wooden_stick",
    "oil flask": "def:oil_flask",
    "oil flasks": "def:oil_flask",
    "oil": "def:oil_flask",
    "flask of oil": "def:oil_flask",
    "torch": "def:torch",
    "torches": "def:torch",
    "copper coin": "def:copper_coin",
    "copper coins": "def:copper_coin",
    "coin": "def:copper_coin",
    "coins": "def:copper_coin",
    "bandit token": "def:bandit_token",
    "bandit tokens": "def:bandit_token",
    "token": "def:bandit_token",
    "stolen ring": "def:stolen_ring",
    "ring": "def:stolen_ring",
}


def _norm(value: Any) -> str:
    return _safe_str(value).strip().lower()


def get_item_definition(definition_id: str) -> Dict[str, Any]:
    return deepcopy(ITEM_DEFINITIONS.get(_safe_str(definition_id), {}))


def infer_definition_id_from_name(name: str) -> str:
    return NAME_TO_DEFINITION_ID.get(_norm(name), "")


def definition_for_item_like(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    definition_id = _safe_str(item.get("definition_id"))
    if definition_id:
        found = get_item_definition(definition_id)
        if found:
            return found

    name = _safe_str(item.get("name") or item.get("title") or item.get("label"))
    inferred = infer_definition_id_from_name(name)
    if inferred:
        return get_item_definition(inferred)

    item_id = _safe_str(item.get("item_id") or item.get("id") or item.get("entity_id"))
    clean_id = item_id.replace("item:", "").replace("_", " ")
    inferred = infer_definition_id_from_name(clean_id)
    if inferred:
        return get_item_definition(inferred)

    return {}
