from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.interactions.currency import (
    add_currency,
    currency_snapshot,
    multiply_currency,
    subtract_currency,
)
from app.apps.rpg.rules.interactions.item_catalog import (
    definition_for_item_like,
    infer_definition_id_from_name,
)
from app.apps.rpg.rules.interactions.item_model import (
    add_item_to_items_list,
    normalize_item_instance,
    recalculate_inventory_derived_fields,
    remove_quantity_from_items_list,
)
from app.apps.rpg.rules.interactions.merchant_catalog import get_default_merchant
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    simulation_state["player_state"] = player_state
    return player_state


def _player_inventory(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _player_state(simulation_state)
    inv = _safe_dict(player_state.get("inventory"))
    if not isinstance(inv.get("items"), list):
        inv["items"] = []
    if not isinstance(inv.get("equipment"), dict):
        inv["equipment"] = {}
    player_state["inventory"] = inv
    return inv


def _merchant_state_root(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    root = _safe_dict(simulation_state.get("merchant_state"))
    if not isinstance(root.get("merchants"), dict):
        root["merchants"] = {}
    simulation_state["merchant_state"] = root
    return root


def _causal_price_multiplier_bps(
    simulation_state: Dict[str, Any],
    merchant: Dict[str, Any] | None = None,
) -> int:
    root = _merchant_state_root(simulation_state)
    economy = _safe_dict(simulation_state.get("economy_state"))
    value = _safe_int(
        _safe_dict(merchant).get("causal_price_multiplier_bps")
        or root.get("causal_price_multiplier_bps")
        or economy.get("causal_price_multiplier_bps"),
        10000,
    )
    return max(8000, min(15000, value))


def ensure_merchant_state(simulation_state: Dict[str, Any], merchant_id: str) -> Dict[str, Any]:
    root = _merchant_state_root(simulation_state)
    merchants = _safe_dict(root.get("merchants"))

    existing = _safe_dict(merchants.get(merchant_id))
    if existing:
        existing["causal_price_multiplier_bps"] = _causal_price_multiplier_bps(
            simulation_state,
            existing,
        )
        merchants[merchant_id] = existing
        root["merchants"] = merchants
        return existing

    default = get_default_merchant(merchant_id)
    if not default:
        return {}

    items = [
        normalize_item_instance(_safe_dict(item))
        for item in _safe_list(_safe_dict(default.get("inventory")).get("items"))
    ]
    default["inventory"] = recalculate_inventory_derived_fields(
        {
            "items": items,
            "equipment": {},
            "carry_capacity": 9999.0,
        }
    )
    default["causal_price_multiplier_bps"] = _causal_price_multiplier_bps(
        simulation_state,
        default,
    )

    merchants[merchant_id] = default
    root["merchants"] = merchants
    simulation_state["merchant_state"] = root
    return default


def _find_item_by_definition(items: List[Any], definition_id: str) -> Dict[str, Any]:
    for item in _safe_list(items):
        normalized = normalize_item_instance(_safe_dict(item))
        if _safe_str(normalized.get("definition_id")) == definition_id:
            return normalized
    return {}


def _find_item_by_id(items: List[Any], item_id: str) -> Dict[str, Any]:
    for item in _safe_list(items):
        normalized = normalize_item_instance(_safe_dict(item))
        if _safe_str(normalized.get("item_id")) == item_id:
            return normalized
    return {}


def _definition_id_from_ref(item_ref: str) -> str:
    item_ref = _safe_str(item_ref).strip().lower()
    direct_survival = {
        "water": "def:water",
        "clean water": "def:water",
        "fresh water": "def:water",
        "ration": "def:rations",
        "rations": "def:rations",
        "trail ration": "def:rations",
        "trail rations": "def:rations",
        "food": "def:rations",
        "provisions": "def:rations",
        "waterskin": "def:waterskin",
        "water skin": "def:waterskin",
        "filled waterskin": "def:waterskin",
    }
    if item_ref in direct_survival:
        return direct_survival[item_ref]
    definition_id = infer_definition_id_from_name(item_ref)
    if definition_id:
        return definition_id
    if item_ref.startswith("def:"):
        return item_ref
    return ""


def _item_base_value(item: Dict[str, Any]) -> Dict[str, int]:
    normalized = normalize_item_instance(item)
    definition = definition_for_item_like(normalized)
    return _safe_dict(normalized.get("value") or definition.get("value"))


def _purchased_item_from_merchant_item(
    merchant_item: Dict[str, Any],
    *,
    quantity: int,
) -> Dict[str, Any]:
    """Create the player-owned item while preserving merchant item metadata.

    Some survival supplies are intentionally local merchant definitions rather
    than global catalog definitions. Reconstructing by definition_id alone would
    lose their tags/value/waterskin charges and break later survival actions.
    """

    bought_item = normalize_item_instance(deepcopy(_safe_dict(merchant_item)))
    bought_item["item_id"] = ""
    bought_item["quantity"] = max(1, _safe_int(quantity, 1))
    bought_item["source"] = "deterministic_merchant_runtime"
    bought_item.pop("price_multiplier", None)
    return normalize_item_instance(bought_item)


def _result(
    *,
    resolved: bool,
    changed_state: bool,
    reason: str,
    action: Dict[str, Any],
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = {
        "resolved": resolved,
        "changed_state": changed_state,
        "reason": reason,
        "semantic_action_v2": deepcopy(_safe_dict(action)),
        "source": "deterministic_merchant_runtime",
    }
    payload.update(_safe_dict(extra or {}))
    return payload


def apply_merchant_interaction(
    simulation_state: Dict[str, Any],
    *,
    semantic_action_v2: Dict[str, Any],
    tick: int = 0,
) -> Dict[str, Any]:
    action = _safe_dict(semantic_action_v2)
    kind = _safe_str(action.get("kind"))

    if kind not in {"buy", "sell"}:
        return _result(
            resolved=False,
            changed_state=False,
            reason="not_merchant_action",
            action=action,
        )

    merchant_id = _safe_str(
        action.get("merchant_id")
        or action.get("secondary_target_id")
        or "npc:Elara"
    )
    merchant = ensure_merchant_state(simulation_state, merchant_id)
    if not merchant:
        return _result(
            resolved=False,
            changed_state=False,
            reason="merchant_not_found",
            action=action,
            extra={"merchant_id": merchant_id},
        )

    causal_multiplier_bps = _causal_price_multiplier_bps(
        simulation_state,
        merchant,
    )
    player_state = _player_state(simulation_state)
    player_inventory = _player_inventory(simulation_state)
    merchant_inventory = _safe_dict(merchant.get("inventory"))
    merchant_items = _safe_list(merchant_inventory.get("items"))

    quantity = max(1, _safe_int(action.get("quantity"), 1))
    item_ref = _safe_str(action.get("target_ref") or action.get("item_ref"))
    definition_id = _definition_id_from_ref(item_ref)

    if kind == "buy":
        if not definition_id:
            return _result(
                resolved=False,
                changed_state=False,
                reason="buy_item_not_recognized",
                action=action,
                extra={"item_ref": item_ref, "merchant_id": merchant_id},
            )

        merchant_item = _find_item_by_definition(merchant_items, definition_id)
        if not merchant_item:
            return _result(
                resolved=False,
                changed_state=False,
                reason="merchant_item_not_in_stock",
                action=action,
                extra={"definition_id": definition_id, "merchant_id": merchant_id},
            )
        available = _safe_int(merchant_item.get("quantity"), 1)
        if available < quantity:
            return _result(
                resolved=False,
                changed_state=False,
                reason="merchant_insufficient_stock",
                action=action,
                extra={
                    "definition_id": definition_id,
                    "requested_quantity": quantity,
                    "available_quantity": available,
                },
            )

        base_multiplier = float(
            merchant_item.get("price_multiplier")
            or merchant.get("buy_price_multiplier")
            or 1.0
        )
        unit_price = multiply_currency(
            _item_base_value(merchant_item),
            base_multiplier * causal_multiplier_bps / 10000.0,
        )
        total_price = multiply_currency(unit_price, quantity)

        currency_before = currency_snapshot(
            player_state.get("currency") or player_state.get("money")
        )
        subtract = subtract_currency(currency_before, total_price)
        if not subtract.get("ok"):
            return _result(
                resolved=False,
                changed_state=False,
                reason="insufficient_currency",
                action=action,
                extra={
                    "merchant_id": merchant_id,
                    "definition_id": definition_id,
                    "price": total_price,
                    "currency_before": currency_before,
                    "missing_copper": subtract.get("missing_copper"),
                    "causal_price_multiplier_bps": causal_multiplier_bps,
                },
            )

        merchant_remove = remove_quantity_from_items_list(
            merchant_items,
            item_id=_safe_str(merchant_item.get("item_id")),
            quantity=quantity,
        )
        if not merchant_remove.get("removed_all"):
            return _result(
                resolved=False,
                changed_state=False,
                reason="merchant_stock_remove_failed",
                action=action,
            )

        bought_item = _purchased_item_from_merchant_item(
            merchant_item,
            quantity=quantity,
        )

        player_add = add_item_to_items_list(
            _safe_list(player_inventory.get("items")),
            bought_item,
        )
        player_inventory["items"] = _safe_list(player_add.get("items"))
        player_inventory = recalculate_inventory_derived_fields(player_inventory)

        merchant_inventory["items"] = _safe_list(merchant_remove.get("items"))
        merchant["inventory"] = recalculate_inventory_derived_fields(
            merchant_inventory
        )

        player_state["currency"] = subtract.get("currency")
        player_state["inventory"] = player_inventory
        simulation_state["player_state"] = player_state

        return _result(
            resolved=True,
            changed_state=True,
            reason="item_bought_from_merchant",
            action=action,
            extra={
                "merchant_id": merchant_id,
                "item_definition_id": definition_id,
                "quantity": quantity,
                "price": total_price,
                "currency_before": currency_before,
                "currency_after": player_state["currency"],
                "item": bought_item,
                "stacked": bool(player_add.get("stacked")),
                "causal_price_multiplier_bps": causal_multiplier_bps,
                "tick": int(tick or 0),
            },
        )

    if kind == "sell":
        player_items = _safe_list(player_inventory.get("items"))

        item: Dict[str, Any] = {}
        if definition_id:
            item = _find_item_by_definition(player_items, definition_id)
        if not item:
            item = _find_item_by_id(
                player_items,
                _safe_str(action.get("target_id")),
            )

        if not item:
            return _result(
                resolved=False,
                changed_state=False,
                reason="sell_item_not_in_inventory",
                action=action,
                extra={"item_ref": item_ref, "merchant_id": merchant_id},
            )

        available = _safe_int(item.get("quantity"), 1)
        sell_qty = min(quantity, available)

        sale_price = multiply_currency(
            _item_base_value(item),
            float(merchant.get("sell_price_multiplier") or 0.5)
            * sell_qty
            * causal_multiplier_bps
            / 10000.0,
        )

        remove = remove_quantity_from_items_list(
            player_items,
            item_id=_safe_str(item.get("item_id")),
            quantity=sell_qty,
        )
        if not remove.get("removed_all"):
            return _result(
                resolved=False,
                changed_state=False,
                reason="failed_to_remove_sold_item",
                action=action,
            )

        sold_item = normalize_item_instance(item)
        sold_item["quantity"] = sell_qty

        merchant_add = add_item_to_items_list(merchant_items, sold_item)
        merchant_inventory["items"] = _safe_list(merchant_add.get("items"))
        merchant["inventory"] = recalculate_inventory_derived_fields(
            merchant_inventory
        )

        currency_before = currency_snapshot(
            player_state.get("currency") or player_state.get("money")
        )
        player_state["currency"] = add_currency(currency_before, sale_price)

        player_inventory["items"] = _safe_list(remove.get("items"))
        player_inventory = recalculate_inventory_derived_fields(player_inventory)
        player_state["inventory"] = player_inventory
        simulation_state["player_state"] = player_state

        return _result(
            resolved=True,
            changed_state=True,
            reason="item_sold_to_merchant",
            action=action,
            extra={
                "merchant_id": merchant_id,
                "item_id": _safe_str(item.get("item_id")),
                "item_definition_id": _safe_str(item.get("definition_id")),
                "quantity": sell_qty,
                "sale_price": sale_price,
                "currency_before": currency_before,
                "currency_after": player_state["currency"],
                "causal_price_multiplier_bps": causal_multiplier_bps,
                "tick": int(tick or 0),
            },
        )

    return _result(
        resolved=False,
        changed_state=False,
        reason="unsupported_merchant_action",
        action=action,
    )
