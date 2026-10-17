from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_str as _safe_str

MERCHANTS: Dict[str, Dict[str, Any]] = {
    "npc:Elara": {
        "merchant_id": "npc:Elara",
        "name": "Elara",
        "buy_price_multiplier": 1.0,
        "sell_price_multiplier": 0.5,
        "inventory": {
            "items": [
                {
                    "item_id": "merchant:elara:water",
                    "definition_id": "def:water",
                    "name": "water",
                    "kind": "supply",
                    "quantity": 12,
                    "stackable": True,
                    "max_stack": 20,
                    "unit_weight": 1.0,
                    "value": {"gold": 0, "silver": 0, "copper": 3},
                    "tags": ["water", "drink", "survival"],
                    "aliases": ["clean water", "fresh water"],
                    "price_multiplier": 1.0,
                },
                {
                    "item_id": "merchant:elara:rations",
                    "definition_id": "def:rations",
                    "name": "trail rations",
                    "kind": "supply",
                    "quantity": 10,
                    "stackable": True,
                    "max_stack": 20,
                    "unit_weight": 0.5,
                    "value": {"gold": 0, "silver": 0, "copper": 8},
                    "tags": ["rations", "food", "survival"],
                    "aliases": ["ration", "trail ration", "provisions"],
                    "price_multiplier": 1.0,
                },
                {
                    "item_id": "merchant:elara:waterskin",
                    "definition_id": "def:waterskin",
                    "name": "waterskin",
                    "kind": "supply",
                    "quantity": 4,
                    "stackable": False,
                    "max_stack": 1,
                    "unit_weight": 1.0,
                    "value": {"gold": 0, "silver": 1, "copper": 0},
                    "tags": ["waterskin", "water", "survival"],
                    "aliases": ["water skin", "filled waterskin"],
                    "metadata": {"water_charges": 3},
                    "price_multiplier": 1.0,
                },
                {
                    "item_id": "merchant:elara:minor_healing_potion",
                    "definition_id": "def:minor_healing_potion",
                    "quantity": 5,
                    "price_multiplier": 1.0,
                },
                {
                    "item_id": "merchant:elara:oil_flask",
                    "definition_id": "def:oil_flask",
                    "quantity": 3,
                    "price_multiplier": 1.0,
                },
                {
                    "item_id": "merchant:elara:rope",
                    "definition_id": "def:rope",
                    "quantity": 2,
                    "price_multiplier": 1.0,
                },
            ]
        },
        "source": "deterministic_merchant_catalog",
    },
}


def get_default_merchant(merchant_id: str) -> Dict[str, Any]:
    return deepcopy(MERCHANTS.get(_safe_str(merchant_id), {}))
