from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.rules.economy.currency import normalize_currency
from app.apps.rpg.rules.economy.pricing import ITEM_PRICES, SERVICE_PRICES
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


SHOP_MENU_ITEMS: Dict[str, Dict[str, Any]] = {
    "general_store": {
        "label": "General Store",
        "items": [
            "torch",
            "rope",
            "bread",
            "waterskin",
        ],
    },
    "weaponsmith": {
        "label": "Weaponsmith",
        "items": [
            "dagger",
            "iron_sword",
            "short_bow",
        ],
    },
    "alchemist": {
        "label": "Alchemist",
        "items": [
            "health_potion",
        ],
    },
}


SERVICE_MENUS: Dict[str, Dict[str, Any]] = {
    "inn": {
        "label": "Inn Services",
        "services": [
            "common_room",
            "private_room",
            "meal",
            "ale",
        ],
    },
    "travel": {
        "label": "Travel Services",
        "services": [
            "local_passage",
            "guarded_passage",
        ],
    },
    "repair": {
        "label": "Repair Services",
        "services": [
            "basic_repair",
            "weapon_repair",
            "armor_repair",
        ],
    },
}


def _build_item_entry(item_id: str) -> Dict[str, Any]:
    item_id = _safe_str(item_id)
    price = normalize_currency(_safe_dict(ITEM_PRICES.get(item_id)))
    return {
        "entry_type": "item",
        "item_id": item_id,
        "label": item_id.replace("_", " ").title(),
        "currency_cost": price,
        "action": {
            "action_type": "buy",
            "item_id": item_id,
            "quantity": 1,
            "apply_cost": True,
        },
    }


def _build_service_entry(service_type: str, service_id: str) -> Dict[str, Any]:
    service_type = _safe_str(service_type)
    service_id = _safe_str(service_id)
    service_prices = _safe_dict(SERVICE_PRICES.get(service_type))
    price = normalize_currency(_safe_dict(service_prices.get(service_id)))

    action_type = "use_service"
    if service_type == "inn" and service_id == "private_room":
        action_type = "rent_room"
    elif service_type == "inn" and service_id == "common_room":
        action_type = "rent_bed"

    return {
        "entry_type": "service",
        "service_type": service_type,
        "service_id": service_id,
        "label": service_id.replace("_", " ").title(),
        "currency_cost": price,
        "action": {
            "action_type": action_type,
            "service_type": service_type,
            "service_id": service_id,
            "apply_cost": True,
        },
    }


def build_shop_menu(menu_id: str) -> Dict[str, Any]:
    menu_id = _safe_str(menu_id).lower()
    menu = _safe_dict(SHOP_MENU_ITEMS.get(menu_id))
    item_ids = list(menu.get("items") or [])
    return {
        "menu_id": menu_id,
        "menu_type": "shop",
        "label": _safe_str(menu.get("label")) or menu_id.replace("_", " ").title(),
        "entries": [_build_item_entry(item_id) for item_id in item_ids],
    }


def build_service_menu(service_type: str) -> Dict[str, Any]:
    service_type = _safe_str(service_type).lower()
    menu = _safe_dict(SERVICE_MENUS.get(service_type))
    service_ids = list(menu.get("services") or [])
    return {
        "menu_id": service_type,
        "menu_type": "service",
        "label": _safe_str(menu.get("label")) or service_type.replace("_", " ").title(),
        "entries": [_build_service_entry(service_type, service_id) for service_id in service_ids],
    }


def build_available_transaction_menus(context_tags: List[Any] | None = None) -> List[Dict[str, Any]]:
    tags = []
    for value in list(context_tags or [])[:16]:
        text = _safe_str(value).lower()
        if text:
            tags.append(text)

    menus: List[Dict[str, Any]] = []

    if "inn" in tags:
        menus.append(build_service_menu("inn"))
    if "shop" in tags or "general_store" in tags:
        menus.append(build_shop_menu("general_store"))
    if "weaponsmith" in tags:
        menus.append(build_shop_menu("weaponsmith"))
    if "alchemist" in tags:
        menus.append(build_shop_menu("alchemist"))
    if "travel" in tags:
        menus.append(build_service_menu("travel"))
    if "repair" in tags:
        menus.append(build_service_menu("repair"))

    return menus


def _attach_provider_to_entry(entry: Dict[str, Any], provider: Dict[str, Any]) -> Dict[str, Any]:
    entry = dict(_safe_dict(entry))
    provider = _safe_dict(provider)

    action = dict(_safe_dict(entry.get("action")))
    action["provider_id"] = _safe_str(provider.get("provider_id"))
    action["provider_name"] = _safe_str(provider.get("provider_name"))

    entry["provider_id"] = _safe_str(provider.get("provider_id"))
    entry["provider_name"] = _safe_str(provider.get("provider_name"))
    entry["provider_kind"] = _safe_str(provider.get("provider_kind"))
    entry["action"] = action
    return entry


def build_provider_bound_menu(menu_id: str, provider: Dict[str, Any]) -> Dict[str, Any]:
    provider = _safe_dict(provider)
    provider_id = _safe_str(provider.get("provider_id"))
    provider_name = _safe_str(provider.get("provider_name"))
    provider_kind = _safe_str(provider.get("provider_kind"))

    if menu_id in SHOP_MENU_ITEMS:
        base_menu = build_shop_menu(menu_id)
    else:
        base_menu = build_service_menu(menu_id)

    entries = [_attach_provider_to_entry(entry, provider) for entry in _safe_list(base_menu.get("entries"))]

    return {
        "menu_id": _safe_str(base_menu.get("menu_id")),
        "menu_type": _safe_str(base_menu.get("menu_type")),
        "label": _safe_str(base_menu.get("label")),
        "provider_id": provider_id,
        "provider_name": provider_name,
        "provider_kind": provider_kind,
        "entries": entries,
    }


def build_provider_transaction_menus(providers: List[Any]) -> List[Dict[str, Any]]:
    menus: List[Dict[str, Any]] = []
    seen = set()

    for raw_provider in _safe_list(providers)[:24]:
        provider = _safe_dict(raw_provider)
        provider_id = _safe_str(provider.get("provider_id"))
        for menu_id in list(provider.get("menu_ids") or [])[:8]:
            menu_id = _safe_str(menu_id)
            if not menu_id:
                continue
            dedupe_key = (provider_id, menu_id)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            menus.append(build_provider_bound_menu(menu_id, provider))

    return menus[:24]
