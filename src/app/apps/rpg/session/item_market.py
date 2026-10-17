"""Deterministic RPG merchant item catalogs and pricing helpers.

This module is AI-free and state-light: it builds repeatable item offers from the
engine item catalog, normalizes prices into copper, and returns transaction
quotes without mutating inventory or currency.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import display_item_name, inventory_quantity
from app.apps.rpg.session.item_system import build_item_catalog, normalize_item_instance
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

CURRENCY_VALUES = {"copper": 1, "silver": 10, "gold": 100}
MERCHANT_STOCK: dict[str, tuple[str, ...]] = {
    "general_store": ("ration", "torch", "waterskin", "bedroll", "rope_coil"),
    "apothecary": ("health_potion", "mana_potion", "keenleaf", "focus_crystal"),
    "forge": ("iron_dagger", "leather_armor", "iron_ingot", "leather_strip"),
    "outfitter": ("travelers_cloak", "bedroll", "waterskin", "rope_coil", "arrow"),
}
DEFAULT_PROFILE = "general_store"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _slug(value: Any, fallback: str = "merchant") -> str:
    raw = _norm(value or fallback)
    slug = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def value_to_copper(value: Any) -> int:
    """Normalize integer or gold/silver/copper dict values into copper."""

    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, dict):
        total = 0
        for key, multiplier in CURRENCY_VALUES.items():
            total += max(0, int(value.get(key) or 0)) * multiplier
        return total
    return 0


def copper_to_currency(copper: int) -> dict[str, int]:
    copper = max(0, int(copper))
    gold, remainder = divmod(copper, CURRENCY_VALUES["gold"])
    silver, copper_remainder = divmod(remainder, CURRENCY_VALUES["silver"])
    return {"gold": gold, "silver": silver, "copper": copper_remainder}


def _stock_ids(profile: str | None) -> tuple[str, ...]:
    normalized = _slug(profile or DEFAULT_PROFILE)
    return MERCHANT_STOCK.get(normalized, MERCHANT_STOCK[DEFAULT_PROFILE])


def _merchant_multiplier(reputation: int = 0) -> float:
    """Positive reputation modestly improves prices; negative reputation worsens them."""

    bounded = max(-5, min(5, int(reputation or 0)))
    return max(0.85, min(1.15, 1.0 - bounded * 0.03))


def _stock_quantity(item_id: str, base_quantity: int = 1) -> int:
    if item_id in {"ration", "torch", "arrow", "keenleaf", "iron_ingot", "leather_strip"}:
        return max(1, base_quantity * 4)
    if item_id in {"health_potion", "mana_potion"}:
        return max(1, base_quantity * 2)
    return max(1, base_quantity)


def _priced_offer(item: dict[str, Any], *, profile: str, quantity: int, reputation: int) -> dict[str, Any]:
    normalized = normalize_item_instance(item, quantity=quantity)
    base = max(1, value_to_copper(normalized.get("value")))
    multiplier = _merchant_multiplier(reputation)
    bounded_reputation = max(-5, min(5, int(reputation or 0)))
    neutral_buy_copper = max(base + 1, round(base * 1.25))
    adjusted_buy_copper = max(1, round(base * 1.25 * multiplier))
    if bounded_reputation > 0:
        buy_copper = max(1, min(adjusted_buy_copper, neutral_buy_copper - 1))
    elif bounded_reputation < 0:
        buy_copper = max(neutral_buy_copper + 1, adjusted_buy_copper)
    else:
        buy_copper = neutral_buy_copper
    sell_copper = max(1, round(base * 0.5 * multiplier))
    return {
        "merchant_profile": profile,
        "item_id": normalized.get("item_id") or normalized.get("id"),
        "name": display_item_name(normalized),
        "quantity": inventory_quantity(normalized),
        "item": normalized,
        "base_value_copper": base,
        "buy_price": {"copper_total": buy_copper, "currency": copper_to_currency(buy_copper)},
        "sell_price": {"copper_total": sell_copper, "currency": copper_to_currency(sell_copper)},
        "mechanics_source": "engine_item_market_v1",
    }


def build_merchant_catalog(
    profile: str | None = None,
    *,
    genre: str = "classic_fantasy",
    level: int = 1,
    reputation: int = 0,
    base_quantity: int = 1,
) -> dict[str, Any]:
    """Build a deterministic merchant catalog from the canonical item catalog."""

    normalized_profile = _slug(profile or DEFAULT_PROFILE)
    if normalized_profile not in MERCHANT_STOCK:
        normalized_profile = DEFAULT_PROFILE
    item_catalog = build_item_catalog(genre, level=level)
    offers: list[dict[str, Any]] = []
    for item_id in _stock_ids(normalized_profile):
        item = item_catalog.get(item_id)
        if not item:
            continue
        offers.append(_priced_offer(item, profile=normalized_profile, quantity=_stock_quantity(item_id, base_quantity), reputation=reputation))
    trace = {
        "event": "merchant_catalog_built",
        "mechanics_source": "engine_item_market_v1",
        "profile": normalized_profile,
        "genre": genre,
        "level": max(1, int(level or 1)),
        "offer_count": len(offers),
        "stock_item_ids": [offer["item_id"] for offer in offers],
    }
    return {"ok": True, "profile": normalized_profile, "offers": offers, "trace": trace}


def find_offer(catalog: dict[str, Any], item_id: str | None) -> dict[str, Any] | None:
    wanted = _slug(item_id or "", "")
    if not wanted:
        return None
    for offer in _safe_list(_safe_dict(catalog).get("offers")):
        offer_dict = _safe_dict(offer)
        if wanted in {_slug(offer_dict.get("item_id"), ""), _slug(offer_dict.get("name"), "")}:
            return deepcopy(offer_dict)
    return None


def quote_merchant_transaction(catalog: dict[str, Any], item_id: str | None, *, action: str = "buy", quantity: int = 1) -> dict[str, Any]:
    """Return a deterministic quote for buying from or selling to a merchant."""

    offer = find_offer(catalog, item_id)
    if not offer:
        return {"ok": False, "error": "offer_not_found", "item_id": item_id}
    amount = max(1, int(quantity or 1))
    normalized_action = _norm(action)
    if normalized_action not in {"buy", "sell"}:
        return {"ok": False, "error": "unsupported_market_action", "action": action}
    if normalized_action == "buy" and amount > inventory_quantity(offer):
        return {"ok": False, "error": "insufficient_stock", "item_id": offer.get("item_id"), "available": inventory_quantity(offer), "requested": amount}
    price_key = "buy_price" if normalized_action == "buy" else "sell_price"
    unit_copper = int(_safe_dict(offer.get(price_key)).get("copper_total") or 0)
    total_copper = max(1, unit_copper * amount)
    return {
        "ok": True,
        "action": normalized_action,
        "item_id": offer.get("item_id"),
        "name": offer.get("name"),
        "quantity": amount,
        "unit_price": _safe_dict(offer.get(price_key)),
        "total_price": {"copper_total": total_copper, "currency": copper_to_currency(total_copper)},
        "mechanics_source": "engine_item_market_v1",
    }
