"""Deterministic player item transaction helpers.

This module applies already-quoted item market transactions to a player snapshot.
It is intentionally route-free and UI-free so later loadout/service actions can
reuse the same currency and inventory mutation logic.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import (
    consume_inventory_item,
    find_inventory_item,
    inventory_quantity,
    merge_inventory_stack,
    normalize_player_inventory,
)
from app.apps.rpg.session.item_market import copper_to_currency, find_offer, quote_merchant_transaction, value_to_copper
from app.apps.rpg.session.item_system import normalize_item_instance
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

TRANSACTION_SOURCE = "engine_item_transaction_v1"


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def player_currency_copper(player: dict[str, Any]) -> int:
    """Return player currency normalized into copper."""

    if isinstance(player.get("currency"), dict):
        return value_to_copper(player.get("currency"))
    for key in ("wallet", "money", "coins"):
        if isinstance(player.get(key), dict):
            return value_to_copper(player.get(key))
    total = 0
    for key in ("gold", "silver", "copper"):
        if key in player:
            total += value_to_copper({key: player.get(key)})
    return total


def set_player_currency(player: dict[str, Any], copper: int) -> dict[str, int]:
    """Write normalized currency back to the player and return the display dict."""

    display = copper_to_currency(max(0, int(copper)))
    player["currency"] = display
    for key in ("gold", "silver", "copper"):
        if key in player:
            player[key] = display[key]
    return display


def _catalog_copy(catalog: dict[str, Any]) -> dict[str, Any]:
    copied = deepcopy(_safe_dict(catalog))
    copied["offers"] = [deepcopy(_safe_dict(offer)) for offer in _safe_list(copied.get("offers"))]
    return copied


def _adjust_offer_stock(catalog: dict[str, Any], item_id: str, delta: int) -> None:
    offer = find_offer(catalog, item_id)
    if not offer:
        return
    wanted = _norm(item_id)
    for existing in _safe_list(catalog.get("offers")):
        raw = _safe_dict(existing)
        if wanted not in {_norm(raw.get("item_id")), _norm(raw.get("name"))}:
            continue
        quantity = max(0, inventory_quantity(raw) + int(delta))
        raw["quantity"] = quantity
        if isinstance(raw.get("item"), dict):
            raw["item"]["quantity"] = quantity
        return


def _remove_from_inventory(player: dict[str, Any], item_id: str, quantity: int) -> tuple[bool, dict[str, Any] | None, str | None]:
    inventory, index, item = find_inventory_item(player, item_id)
    if item is None or index < 0:
        return False, None, "item_not_owned"
    if inventory_quantity(item) < quantity:
        return False, item, "insufficient_inventory"
    removed = normalize_item_instance(item, quantity=quantity)
    consume_inventory_item(inventory, index, quantity)
    player["inventory"] = inventory
    return True, removed, None


def _add_to_inventory(player: dict[str, Any], item: dict[str, Any], quantity: int) -> dict[str, Any]:
    normalize_player_inventory(player)
    inventory = player.setdefault("inventory", [])
    incoming = normalize_item_instance(item, quantity=quantity)
    return merge_inventory_stack(inventory, incoming)


def apply_item_transaction(
    player: dict[str, Any],
    catalog: dict[str, Any],
    item_id: str | None,
    *,
    action: str = "buy",
    quantity: int = 1,
) -> dict[str, Any]:
    """Apply a quoted buy/sell action to copied player and catalog snapshots."""

    updated_player = deepcopy(_safe_dict(player))
    updated_catalog = _catalog_copy(catalog)
    amount = max(1, int(quantity or 1))
    normalized_action = _norm(action)
    quote = quote_merchant_transaction(updated_catalog, item_id, action=normalized_action, quantity=amount)
    if not quote.get("ok"):
        return {
            "ok": False,
            "error": quote.get("error", "transaction_quote_failed"),
            "quote": quote,
            "player": updated_player,
            "catalog": updated_catalog,
        }

    total_copper = int(_safe_dict(quote.get("total_price")).get("copper_total") or 0)
    before_copper = player_currency_copper(updated_player)
    offer = find_offer(updated_catalog, quote.get("item_id"))
    if not offer:
        return {
            "ok": False,
            "error": "offer_not_found",
            "quote": quote,
            "player": updated_player,
            "catalog": updated_catalog,
        }

    moved_item: dict[str, Any] | None = None
    if normalized_action == "buy":
        if before_copper < total_copper:
            return {
                "ok": False,
                "error": "insufficient_currency",
                "needed": total_copper,
                "available": before_copper,
                "quote": quote,
                "player": updated_player,
                "catalog": updated_catalog,
            }
        moved_item = _add_to_inventory(updated_player, _safe_dict(offer.get("item")), amount)
        set_player_currency(updated_player, before_copper - total_copper)
        _adjust_offer_stock(updated_catalog, str(quote.get("item_id")), -amount)
    elif normalized_action == "sell":
        ok, removed_item, error = _remove_from_inventory(updated_player, str(quote.get("item_id")), amount)
        if not ok:
            return {
                "ok": False,
                "error": error or "sell_failed",
                "quote": quote,
                "player": updated_player,
                "catalog": updated_catalog,
            }
        moved_item = removed_item
        set_player_currency(updated_player, before_copper + total_copper)
        _adjust_offer_stock(updated_catalog, str(quote.get("item_id")), amount)
    else:
        return {
            "ok": False,
            "error": "unsupported_market_action",
            "quote": quote,
            "player": updated_player,
            "catalog": updated_catalog,
        }

    after_copper = player_currency_copper(updated_player)
    trace = {
        "event": "item_transaction_applied",
        "mechanics_source": TRANSACTION_SOURCE,
        "action": normalized_action,
        "item_id": quote.get("item_id"),
        "quantity": amount,
        "currency_before": {"copper_total": before_copper, "currency": copper_to_currency(before_copper)},
        "currency_after": {"copper_total": after_copper, "currency": copper_to_currency(after_copper)},
        "price": quote.get("total_price"),
    }
    return {
        "ok": True,
        "action": normalized_action,
        "item": moved_item,
        "quote": quote,
        "player": updated_player,
        "catalog": updated_catalog,
        "trace": trace,
    }
