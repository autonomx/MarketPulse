from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_list as _safe_list, safe_str as _safe_str

SERVICE_KIND_LODGING = "lodging"
SERVICE_KIND_MEAL = "meal"
SERVICE_KIND_DRINK = "drink"
SERVICE_KIND_SHOP_GOODS = "shop_goods"
SERVICE_KIND_HEALING = "healing"
SERVICE_KIND_TRAINING = "training"
SERVICE_KIND_REPAIR = "repair"
SERVICE_KIND_TRANSPORT = "transport"
SERVICE_KIND_PAID_INFORMATION = "paid_information"


SERVICE_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "npc:Bran": {
        "provider_id": "npc:Bran",
        "provider_name": "Bran",
        "aliases": ["bran", "innkeeper", "bran the innkeeper"],
        "location_id": "loc_tavern",
        "service_kinds": [
            SERVICE_KIND_LODGING,
            SERVICE_KIND_MEAL,
            SERVICE_KIND_DRINK,
            SERVICE_KIND_PAID_INFORMATION,
        ],
    },
    "npc:Elara": {
        "provider_id": "npc:Elara",
        "provider_name": "Elara",
        "aliases": ["elara", "merchant", "elara the merchant"],
        "location_id": "loc_market",
        "service_kinds": [
            SERVICE_KIND_SHOP_GOODS,
            SERVICE_KIND_REPAIR,
            SERVICE_KIND_PAID_INFORMATION,
        ],
    },
}


SERVICE_OFFERS: Dict[str, List[Dict[str, Any]]] = {
    "npc:Bran": [
        {
            "offer_id": "bran_lodging_common_cot",
            "service_kind": SERVICE_KIND_LODGING,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Common room cot",
            "description": "A simple cot in the common room for one night.",
            "price": {"gold": 0, "silver": 5, "copper": 0},
            "stock": 6,
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "lodging_reserved": True,
                "rest_quality": "basic",
                "duration": "one_night",
            },
        },
        {
            "offer_id": "bran_lodging_private_room",
            "service_kind": SERVICE_KIND_LODGING,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Private room",
            "description": "A private room for one night.",
            "price": {"gold": 1, "silver": 0, "copper": 0},
            "stock": 1,
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "lodging_reserved": True,
                "rest_quality": "good",
                "duration": "one_night",
            },
        },
        {
            "offer_id": "bran_meal_stew",
            "service_kind": SERVICE_KIND_MEAL,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Hot stew",
            "description": "A hot bowl of stew and bread.",
            "price": {"gold": 0, "silver": 1, "copper": 5},
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "meal_consumed": True,
                "morale": 1,
            },
        },
        {
            "offer_id": "bran_dried_rations",
            "service_kind": SERVICE_KIND_MEAL,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Dried rations",
            "description": "A wrapped packet of dried meat, hard cheese, and preserved berries.",
            "price": {"gold": 0, "silver": 5, "copper": 0},
            "stock": 4,
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "items_added": [
                    {
                        "item_id": "dried_rations",
                        "name": "Dried rations",
                        "quantity": 1,
                        "type": "food",
                    }
                ],
            },
        },
        {
            "offer_id": "bran_drink_ale",
            "service_kind": SERVICE_KIND_DRINK,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Mug of ale",
            "description": "A mug of local ale.",
            "price": {"gold": 0, "silver": 0, "copper": 8},
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "drink_consumed": True,
            },
        },
        {
            "offer_id": "bran_paid_rumor",
            "service_kind": SERVICE_KIND_PAID_INFORMATION,
            "provider_id": "npc:Bran",
            "provider_name": "Bran",
            "label": "Local rumor",
            "description": "A useful rumor from tavern gossip.",
            "price": {"gold": 0, "silver": 2, "copper": 0},
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_tavern",
            },
            "availability": "available",
            "effects": {
                "rumor_added": True,
            },
        },
    ],
    "npc:Elara": [
        {
            "offer_id": "elara_torch",
            "service_kind": SERVICE_KIND_SHOP_GOODS,
            "provider_id": "npc:Elara",
            "provider_name": "Elara",
            "label": "Torch",
            "description": "A simple torch for dark places.",
            "price": {"gold": 0, "silver": 1, "copper": 0},
            "stock": 3,
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_market",
            },
            "availability": "available",
            "effects": {
                "items_added": [
                    {
                        "item_id": "torch",
                        "name": "Torch",
                        "quantity": 1,
                    }
                ],
            },
        },
        {
            "offer_id": "elara_rope",
            "service_kind": SERVICE_KIND_SHOP_GOODS,
            "provider_id": "npc:Elara",
            "provider_name": "Elara",
            "label": "Rope",
            "description": "Fifty feet of sturdy hemp rope.",
            "price": {"gold": 0, "silver": 3, "copper": 0},
            "stock": 2,
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_market",
            },
            "availability": "available",
            "effects": {
                "items_added": [
                    {
                        "item_id": "rope",
                        "name": "Rope",
                        "quantity": 1,
                    }
                ],
            },
        },
        {
            "offer_id": "elara_paid_information",
            "service_kind": SERVICE_KIND_PAID_INFORMATION,
            "provider_id": "npc:Elara",
            "provider_name": "Elara",
            "label": "Market information",
            "description": "A piece of useful market intelligence.",
            "price": {"gold": 0, "silver": 2, "copper": 0},
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_market",
            },
            "availability": "available",
            "effects": {
                "rumor_added": True,
            },
        },
        {
            "offer_id": "elara_basic_repair",
            "service_kind": SERVICE_KIND_REPAIR,
            "provider_id": "npc:Elara",
            "provider_name": "Elara",
            "label": "Basic gear repair",
            "description": "Minor repair work on worn equipment.",
            "price": {"gold": 0, "silver": 4, "copper": 0},
            "availability_rules": {
                "requires_provider_present": True,
                "requires_location": "loc_market",
            },
            "availability": "available",
            "effects": {
                "repair_quality": "basic",
            },
        },
    ],
}


def get_service_provider(provider_id: str) -> Dict[str, Any]:
    provider = SERVICE_PROVIDERS.get(_safe_str(provider_id))
    return deepcopy(provider) if provider else {}


def get_provider_offers(provider_id: str, service_kind: str = "") -> List[Dict[str, Any]]:
    provider_id = _safe_str(provider_id)
    service_kind = _safe_str(service_kind)
    offers = []
    for offer in SERVICE_OFFERS.get(provider_id, []):
        if service_kind and _safe_str(offer.get("service_kind")) != service_kind:
            continue
        offers.append(deepcopy(offer))
    return offers


def find_provider_by_text(text: str) -> Dict[str, Any]:
    text_l = _safe_str(text).lower()
    for provider in SERVICE_PROVIDERS.values():
        aliases = [_safe_str(provider.get("provider_name")).lower()]
        aliases.extend(_safe_str(alias).lower() for alias in _safe_list(provider.get("aliases")))
        if any(alias and alias in text_l for alias in aliases):
            return deepcopy(provider)

    # Useful defaults for current starter world.
    if any(word in text_l for word in ("room", "rent", "lodging", "bed", "stay", "meal", "food", "stew", "bread", "ration", "rations", "provision", "provisions", "drink", "ale", "rumor", "rumour")):
        return deepcopy(SERVICE_PROVIDERS["npc:Bran"])
    if any(word in text_l for word in ("shop", "buy", "sell", "sells", "merchant", "goods", "torch", "rope", "repair")):
        return deepcopy(SERVICE_PROVIDERS["npc:Elara"])

    return {}
