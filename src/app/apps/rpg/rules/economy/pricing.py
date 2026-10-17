from __future__ import annotations

from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


SERVICE_PRICES: Dict[str, Dict[str, Dict[str, int]]] = {
    "inn": {
        "common_room": {"silver": 8},
        "private_room": {"silver": 2},
        "meal": {"copper": 3},
        "ale": {"copper": 5},
    },
    "travel": {
        "local_passage": {"silver": 5},
        "guarded_passage": {"gold": 1, "silver": 5},
    },
    "repair": {
        "basic_repair": {"silver": 6},
        "armor_repair": {"gold": 1},
        "weapon_repair": {"silver": 8},
    },
}


ITEM_PRICES: Dict[str, Dict[str, int]] = {
    "torch": {"silver": 1},
    "rope": {"silver": 3},
    "dagger": {"silver": 8},
    "iron_sword": {"gold": 1, "silver": 5},
    "short_bow": {"gold": 1, "silver": 2},
    "health_potion": {"gold": 3},
    "bread": {"copper": 2},
    "waterskin": {"silver": 4},
}


PRICE_ALIASES: Dict[str, str] = {
    "room": "private_room",
    "bed": "common_room",
    "drink": "ale",
    "food": "meal",
    "potion": "health_potion",
    "sword": "iron_sword",
    "bow": "short_bow",
}


def get_item_price(item_id: str) -> Dict[str, int]:
    item_id = _safe_str(item_id).lower()
    item_id = PRICE_ALIASES.get(item_id, item_id)
    return dict(_safe_dict(ITEM_PRICES.get(item_id)))


def get_service_price(service_type: str, service_id: str) -> Dict[str, int]:
    service_type = _safe_str(service_type).lower()
    service_id = _safe_str(service_id).lower()
    service_id = PRICE_ALIASES.get(service_id, service_id)
    service_prices = _safe_dict(SERVICE_PRICES.get(service_type))
    return dict(_safe_dict(service_prices.get(service_id)))


def resolve_registry_price(action: Dict[str, Any]) -> Dict[str, int]:
    action = _safe_dict(action)

    # Explicit structured price always wins if already provided by authoritative code.
    explicit_cost = _safe_dict(action.get("cost"))
    if explicit_cost:
        return explicit_cost

    explicit_currency_cost = _safe_dict(action.get("currency_cost"))
    if explicit_currency_cost:
        return explicit_currency_cost

    action_type = _safe_str(action.get("action_type") or action.get("type")).lower()

    # Shop / item purchase paths
    if action_type in {"buy", "purchase", "shop_purchase", "trade"}:
        item_id = (
            _safe_str(action.get("item_id"))
            or _safe_str(action.get("target_id"))
            or _safe_str(action.get("item"))
        )
        if item_id:
            return get_item_price(item_id)

    # Service paths
    if action_type in {"rent_room", "rent_bed", "use_service", "pay"}:
        service_type = _safe_str(action.get("service_type"))
        service_id = (
            _safe_str(action.get("service_id"))
            or _safe_str(action.get("target_id"))
            or _safe_str(action.get("service"))
        )

        if not service_type:
            if action_type in {"rent_room", "rent_bed"}:
                service_type = "inn"
            elif action_type == "pay":
                service_type = "service"

        if not service_id:
            if action_type == "rent_room":
                service_id = "private_room"
            elif action_type == "rent_bed":
                service_id = "common_room"

        if service_type and service_id:
            return get_service_price(service_type, service_id)

    return {}
