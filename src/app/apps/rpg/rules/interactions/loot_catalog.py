from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

LOOT_TABLES: Dict[str, Dict[str, Any]] = {
    "loot:bandit_common": {
        "loot_table_id": "loot:bandit_common",
        "name": "Bandit Common Loot",
        "rolls": [
            {
                "definition_id": "def:copper_coin",
                "quantity_min": 6,
                "quantity_max": 16,
                "weight": 10,
            },
            {
                "definition_id": "def:iron_arrow",
                "quantity_min": 3,
                "quantity_max": 8,
                "weight": 5,
            },
            {
                "definition_id": "def:bandit_token",
                "quantity_min": 1,
                "quantity_max": 2,
                "weight": 3,
            },
            {
                "definition_id": "def:rusty_dagger",
                "quantity_min": 1,
                "quantity_max": 1,
                "weight": 1,
            },
        ],
        "roll_count": 2,
        "source": "deterministic_loot_catalog",
    },
    "loot:tavern_supplies": {
        "loot_table_id": "loot:tavern_supplies",
        "name": "Tavern Supplies",
        "rolls": [
            {
                "definition_id": "def:minor_healing_potion",
                "quantity_min": 1,
                "quantity_max": 2,
                "weight": 2,
            },
            {
                "definition_id": "def:oil_flask",
                "quantity_min": 1,
                "quantity_max": 3,
                "weight": 4,
            },
            {
                "definition_id": "def:cloth_scrap",
                "quantity_min": 2,
                "quantity_max": 5,
                "weight": 6,
            },
        ],
        "roll_count": 2,
        "source": "deterministic_loot_catalog",
    },
}


def get_loot_table(loot_table_id: str) -> Dict[str, Any]:
    return deepcopy(LOOT_TABLES.get(_safe_str(loot_table_id), {}))
