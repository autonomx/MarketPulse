from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.economy.currency import (
    apply_currency_delta,
    get_player_currency,
    set_player_currency,
)
from app.apps.rpg.rules.economy.service_transactions import (
    append_service_transaction_record,
    build_service_transaction_record,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ensure_player_inventory(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    player_state = _safe_dict(state.get("player_state"))
    if not player_state:
        player_state = {}
        state["player_state"] = player_state

    inventory_state = _safe_dict(player_state.get("inventory_state"))
    if not inventory_state:
        inventory_state = {
            "items": [],
            "equipment": {},
            "capacity": 50,
            "currency": {"gold": 0, "silver": 0, "copper": 0},
            "last_loot": [],
        }
        player_state["inventory_state"] = inventory_state

    if not isinstance(inventory_state.get("items"), list):
        inventory_state["items"] = []

    if not isinstance(inventory_state.get("last_loot"), list):
        inventory_state["last_loot"] = []

    if not isinstance(inventory_state.get("currency"), dict):
        inventory_state["currency"] = {"gold": 0, "silver": 0, "copper": 0}

    return inventory_state


def _merge_item(items: List[Dict[str, Any]], incoming: Dict[str, Any]) -> Dict[str, Any]:
    incoming = deepcopy(_safe_dict(incoming))
    item_id = _safe_str(incoming.get("item_id") or incoming.get("id") or incoming.get("name")).strip()
    name = _safe_str(incoming.get("name") or item_id).strip()
    quantity = max(1, _safe_int(incoming.get("quantity"), 1))

    if not item_id:
        item_id = name.lower().replace(" ", "_") if name else "unknown_item"

    for item in items:
        if _safe_str(item.get("item_id") or item.get("id") or item.get("name")) == item_id:
            item["quantity"] = max(0, _safe_int(item.get("quantity"), 1)) + quantity
            item.setdefault("item_id", item_id)
            item.setdefault("name", name or item_id)
            return item

    merged = {
        "item_id": item_id,
        "name": name or item_id,
        "quantity": quantity,
    }
    for key, value in incoming.items():
        if key not in merged:
            merged[key] = value

    items.append(merged)
    return merged


def _selected_offer(service_result: Dict[str, Any]) -> Dict[str, Any]:
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    for offer in _safe_list(service_result.get("offers")):
        offer = _safe_dict(offer)
        if _safe_str(offer.get("offer_id")) == selected_offer_id:
            return deepcopy(offer)
    return {}


def _append_active_service(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    offer: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    active_services = state.get("active_services")
    if not isinstance(active_services, list):
        active_services = []
        state["active_services"] = active_services

    effects = _safe_dict(offer.get("effects"))
    service_id = _safe_str(offer.get("offer_id") or service_result.get("selected_offer_id"))

    record = {
        "service_id": service_id,
        "offer_id": service_id,
        "service_kind": _safe_str(service_result.get("service_kind")),
        "provider_id": _safe_str(service_result.get("provider_id")),
        "provider_name": _safe_str(service_result.get("provider_name")),
        "label": _safe_str(offer.get("label") or service_id),
        "started_tick": tick,
        "duration": _safe_str(effects.get("duration")),
        "status": "active",
        "effects": deepcopy(effects),
    }

    active_services.append(record)
    return record


def _append_paid_information_stub(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    offer: Dict[str, Any],
    tick: int,
) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    memory_state = _safe_dict(state.get("memory_state"))
    if not memory_state:
        memory_state = {}
        state["memory_state"] = memory_state

    rumors = memory_state.get("rumors")
    if not isinstance(rumors, list):
        rumors = []
        memory_state["rumors"] = rumors

    offer_id = _safe_str(offer.get("offer_id") or service_result.get("selected_offer_id"))
    rumor = {
        "rumor_id": f"rumor:{offer_id}:tick:{tick}",
        "source_provider_id": _safe_str(service_result.get("provider_id")),
        "source_provider_name": _safe_str(service_result.get("provider_name")),
        "summary": "A paid local rumor is owed or available.",
        "status": "purchased_pending_generation",
        "tick": tick,
    }
    rumors.append(rumor)

    active_rumors = state.get("active_rumors")
    if isinstance(active_rumors, list):
        active_rumors.append(deepcopy(rumor))

    return rumor


def apply_service_purchase_result(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    """
    Apply deterministic service purchase effects.

    This function is authoritative. It mutates only facts already represented
    by service_result.purchase and the selected registered offer.
    """
    state = _safe_dict(simulation_state)
    service_result = deepcopy(_safe_dict(service_result))

    if not service_result.get("matched"):
        return {
            "simulation_state": state,
            "service_result": service_result,
            "applied": False,
            "blocked": False,
            "blocked_reason": "not_service",
            "currency_before": get_player_currency(state),
            "currency_after": get_player_currency(state),
            "items_added": [],
            "active_service": {},
            "rumor_added": {},
            "transaction_record": {},
        }

    purchase = _safe_dict(service_result.get("purchase"))
    if not purchase:
        return {
            "simulation_state": state,
            "service_result": service_result,
            "applied": False,
            "blocked": False,
            "blocked_reason": "",
            "currency_before": get_player_currency(state),
            "currency_after": get_player_currency(state),
            "items_added": [],
            "active_service": {},
            "rumor_added": {},
            "transaction_record": {},
        }

    if purchase.get("blocked"):
        purchase["applied"] = False
        service_result["purchase"] = purchase
        service_result["status"] = "blocked"
        purchase_application = {
            "applied": False,
            "blocked": True,
            "blocked_reason": _safe_str(purchase.get("blocked_reason") or "blocked"),
            "currency_before": get_player_currency(state),
            "currency_after": get_player_currency(state),
            "items_added": [],
            "active_service": {},
            "rumor_added": {},
        }
        transaction_record = build_service_transaction_record(
            service_result=service_result,
            purchase_application=purchase_application,
            tick=tick,
        )
        append_service_transaction_record(state, transaction_record)
        return {
            "simulation_state": state,
            "service_result": service_result,
            "applied": False,
            "blocked": True,
            "blocked_reason": _safe_str(purchase.get("blocked_reason") or "blocked"),
            "currency_before": get_player_currency(state),
            "currency_after": get_player_currency(state),
            "items_added": [],
            "active_service": {},
            "rumor_added": {},
            "transaction_record": transaction_record,
        }

    currency_before = get_player_currency(state)
    # Preserve negative signs from purchase.resource_changes.
    raw_delta = _safe_dict(_safe_dict(purchase.get("resource_changes")).get("currency"))
    currency_after = apply_currency_delta(currency_before, raw_delta)
    set_player_currency(state, currency_after)

    offer = _selected_offer(service_result)
    effects = _safe_dict(purchase.get("effects") or offer.get("effects"))
    service_kind = _safe_str(service_result.get("service_kind"))
    inventory = _ensure_player_inventory(state)

    added_items: List[Dict[str, Any]] = []
    for item in _safe_list(effects.get("items_added")):
        merged = _merge_item(inventory["items"], _safe_dict(item))
        added_items.append(deepcopy(merged))

    inventory["last_loot"] = deepcopy(added_items)

    active_service = {}
    if service_kind in {"lodging", "healing", "training", "repair", "transport"}:
        active_service = _append_active_service(state, service_result, offer, tick)

    rumor_added = {}
    if service_kind == "paid_information":
        rumor_added = _append_paid_information_stub(state, service_result, offer, tick)

    purchase["applied"] = True
    purchase["applied_effects"] = {
        "currency_changed": currency_before != currency_after,
        "items_added": added_items,
        "active_service": active_service,
        "rumor_added": rumor_added,
    }
    service_result["purchase"] = purchase
    service_result["status"] = "purchased"
    purchase_application = {
        "applied": True,
        "blocked": False,
        "blocked_reason": "",
        "currency_before": currency_before,
        "currency_after": currency_after,
        "items_added": added_items,
        "active_service": active_service,
        "rumor_added": rumor_added,
    }
    transaction_record = build_service_transaction_record(
        service_result=service_result,
        purchase_application=purchase_application,
        tick=tick,
    )
    append_service_transaction_record(state, transaction_record)

    return {
        "simulation_state": state,
        "service_result": service_result,
        "applied": True,
        "blocked": False,
        "blocked_reason": "",
        "currency_before": currency_before,
        "currency_after": currency_after,
        "items_added": added_items,
        "active_service": active_service,
        "rumor_added": rumor_added,
        "transaction_record": transaction_record,
    }
