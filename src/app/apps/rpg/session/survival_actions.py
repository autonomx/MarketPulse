from __future__ import annotations

"""Deterministic survival action resolution and suggestions.

N123.2 resolves relief actions (eat, drink, rest/sleep, and purchased meal,
drink, or lodging) without invoking an LLM.  N123.3 adds deterministic suggested
actions backed by inventory/service availability.
"""

from typing import Any, Dict, List, Tuple

from app.apps.rpg.rules.economy.currency import (
    can_afford,
    format_currency,
    get_player_currency,
    normalize_currency,
    set_player_currency,
    subtract_currency_cost,
)
from app.apps.rpg.rules.economy.service_registry import (
    SERVICE_KIND_DRINK,
    SERVICE_KIND_LODGING,
    SERVICE_KIND_MEAL,
    SERVICE_PROVIDERS,
    get_provider_offers,
)
from app.apps.rpg.rules.location_registry import (
    has_explicit_location,
    location_allows_service,
    provider_present_at_location,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clamp(value: Any, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, _safe_int(value, minimum)))


def _contains_any(text_l: str, terms: Tuple[str, ...]) -> bool:
    return any(term in text_l for term in terms)


def _player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    player_state = raw_player_state if isinstance(raw_player_state := state.get("player_state"), dict) else {}
    state["player_state"] = player_state
    return player_state


def _inventory_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _player_state(simulation_state)
    inventory = raw_inventory_state if isinstance(raw_inventory_state := player_state.get("inventory_state"), dict) else {}
    player_state["inventory_state"] = inventory
    inventory.setdefault("items", [])
    inventory.setdefault("currency", normalize_currency(inventory.get("currency")))
    return inventory


def _resources(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _player_state(simulation_state)
    resources = raw_resources if isinstance(raw_resources := player_state.get("resources"), dict) else {}
    player_state["resources"] = resources
    return resources


def _climate_survival(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    climate = raw_climate_survival if isinstance(raw_climate_survival := state.get("climate_survival"), dict) else {}
    state["climate_survival"] = climate
    survival = raw_survival if isinstance(raw_survival := climate.get("survival"), dict) else {}
    climate["survival"] = survival
    return climate


def _item_identity(item: Dict[str, Any]) -> str:
    return _safe_str(item.get("item_id") or item.get("id") or item.get("key") or item.get("name")).lower()


def _item_name(item: Dict[str, Any]) -> str:
    return _safe_str(item.get("name") or item.get("label") or item.get("item_id") or item.get("id") or "item")


def _item_quantity(item: Dict[str, Any]) -> int:
    return max(0, _safe_int(item.get("quantity", item.get("qty", 1)), 1))


def _item_tags(item: Dict[str, Any]) -> List[str]:
    tags: list[Any] = []
    for key in ("tags", "item_tags", "categories"):
        tags.extend(_safe_str(value).lower() for value in _safe_list(item.get(key)))
    kind = _safe_str(item.get("kind") or item.get("type") or item.get("category")).lower()
    if kind:
        tags.append(kind)
    return tags


def _is_food_item(item: Dict[str, Any]) -> bool:
    ident = _item_identity(item)
    tags = _item_tags(item)
    haystack = " ".join([ident, _item_name(item).lower()] + tags)
    return any(term in haystack for term in ("food", "ration", "meal", "bread", "stew", "meat", "fruit"))


def _is_drink_item(item: Dict[str, Any]) -> bool:
    ident = _item_identity(item)
    tags = _item_tags(item)
    haystack = " ".join([ident, _item_name(item).lower()] + tags)
    return any(term in haystack for term in ("drink", "water", "waterskin", "ale", "wine", "beer", "canteen"))


def _find_first_matching_item(simulation_state: Dict[str, Any], predicate) -> Dict[str, Any]:
    for raw_item in _safe_list(_inventory_state(simulation_state).get("items")):
        item = _safe_dict(raw_item)
        if _item_quantity(item) > 0 and predicate(item):
            return dict(item)
    return {}


def _consume_first_matching_item(simulation_state: Dict[str, Any], predicate) -> Dict[str, Any]:
    inventory = _inventory_state(simulation_state)
    items = _safe_list(inventory.get("items"))
    new_items: List[Dict[str, Any]] = []
    consumed: Dict[str, Any] = {}
    consumed_quantity = 0

    for raw_item in items:
        item = dict(_safe_dict(raw_item))
        quantity = max(1, _safe_int(item.get("quantity", item.get("qty", 1)), 1))
        if not consumed and predicate(item) and quantity > 0:
            consumed = dict(item)
            consumed_quantity = 1
            remaining = quantity - 1
            if remaining > 0:
                item["quantity"] = remaining
                if "qty" in item:
                    item["qty"] = remaining
                new_items.append(item)
            continue
        new_items.append(item)

    inventory["items"] = new_items
    if not consumed:
        return {"consumed": False, "item": {}, "quantity": 0}
    return {
        "consumed": True,
        "item": consumed,
        "quantity": consumed_quantity,
        "item_id": _safe_str(consumed.get("item_id") or consumed.get("id") or consumed.get("name")),
        "name": _item_name(consumed),
    }


def _current_needs(simulation_state: Dict[str, Any]) -> Dict[str, int]:
    climate = _climate_survival(simulation_state)
    survival = _safe_dict(climate.get("survival"))
    resources = _resources(simulation_state)
    return {
        "hunger": _clamp(survival.get("hunger", resources.get("hunger", 0))),
        "thirst": _clamp(survival.get("thirst", resources.get("thirst", 0))),
        "fatigue": _clamp(survival.get("fatigue", resources.get("fatigue", 0))),
    }


def _persist_needs(simulation_state: Dict[str, Any], needs: Dict[str, int]) -> None:
    resources = _resources(simulation_state)
    climate = _climate_survival(simulation_state)
    survival = _safe_dict(climate.get("survival"))
    for key in ("hunger", "thirst", "fatigue"):
        value = _clamp(needs.get(key, 0))
        resources[key] = value
        survival[key] = value
    climate["survival"] = survival
    climate["runtime_enforced"] = True
    climate.setdefault("source", "deterministic_authoritative_turn_tick")


def _warnings_for_needs(needs: Dict[str, int]) -> List[str]:
    warnings: List[str] = []
    if needs.get("hunger", 0) >= 70:
        warnings.append("hunger_high")
    if needs.get("thirst", 0) >= 70:
        warnings.append("thirst_high")
    if needs.get("fatigue", 0) >= 70:
        warnings.append("fatigue_high")
    return warnings


def _relief_result(
    *,
    action_kind: str,
    before: Dict[str, int],
    after: Dict[str, int],
    inventory_consumed: Dict[str, Any] | None = None,
    purchase: Dict[str, Any] | None = None,
    blocked_reason: str = "",
) -> Dict[str, Any]:
    inventory_consumed = _safe_dict(inventory_consumed)
    purchase = _safe_dict(purchase)
    applied = not blocked_reason
    deltas = {f"{key}_delta": after.get(key, 0) - before.get(key, 0) for key in ("hunger", "thirst", "fatigue")}
    warnings = _warnings_for_needs(after)
    effects = []
    if applied:
        effects.append({
            "effect_id": f"survival_{action_kind}_relief",
            "kind": "survival_relief",
            "action_kind": action_kind,
            "severity": "beneficial",
        })
    resource_changes = {
        "source": "n1232_survival_action_resolution",
        "action_kind": action_kind,
        **deltas,
        "before": before,
        "after": after,
        "warnings": warnings,
        "inventory_consumed": inventory_consumed,
        "purchase": purchase,
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
    }
    effect_result = {
        "source": "n1232_survival_action_resolution",
        "applied": applied,
        "action_kind": action_kind,
        "effects": effects,
        "warnings": warnings,
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
    }
    return {
        "matched": True,
        "applied": applied,
        "action_kind": action_kind,
        "blocked": bool(blocked_reason),
        "blocked_reason": blocked_reason,
        "resource_changes": resource_changes,
        "effect_result": effect_result,
    }


def _apply_relief(simulation_state: Dict[str, Any], *, hunger: int = 0, thirst: int = 0, fatigue: int = 0) -> tuple[Dict[str, int], Dict[str, int]]:
    before = _current_needs(simulation_state)
    after = {
        "hunger": _clamp(before["hunger"] - max(0, hunger)),
        "thirst": _clamp(before["thirst"] - max(0, thirst)),
        "fatigue": _clamp(before["fatigue"] - max(0, fatigue)),
    }
    _persist_needs(simulation_state, after)
    return before, after


def _selected_purchase(service_result: Dict[str, Any]) -> Dict[str, Any]:
    purchase = _safe_dict(service_result.get("purchase"))
    if not purchase or purchase.get("blocked"):
        return purchase
    return purchase


def _selected_offer(service_result: Dict[str, Any]) -> Dict[str, Any]:
    selected_id = _safe_str(service_result.get("selected_offer_id"))
    for offer in _safe_list(service_result.get("offers")):
        offer = _safe_dict(offer)
        if _safe_str(offer.get("offer_id")) == selected_id:
            return offer
    return {}


def _apply_purchase_cost(simulation_state: Dict[str, Any], purchase: Dict[str, Any]) -> Dict[str, Any]:
    price = normalize_currency(purchase.get("price"))
    before = get_player_currency(simulation_state)
    if not can_afford(before, price):
        return {"applied": False, "blocked_reason": "insufficient_funds", "price": price, "currency_before": before, "currency_after": before}
    after = subtract_currency_cost(before, price)
    set_player_currency(simulation_state, after)
    return {"applied": True, "price": price, "currency_before": before, "currency_after": after}


def _offer_available_here(simulation_state: Dict[str, Any], provider_id: str, offer: Dict[str, Any]) -> bool:
    service_kind = _safe_str(offer.get("service_kind"))
    provider = _safe_dict(SERVICE_PROVIDERS.get(provider_id))
    if not has_explicit_location(simulation_state):
        return False
    if not provider_present_at_location(
        simulation_state,
        provider_id=provider_id,
        provider_name=_safe_str(provider.get("provider_name")),
    ):
        return False
    if service_kind and not location_allows_service(simulation_state, service_kind):
        return False
    return True


def _service_suggestion_for_kind(simulation_state: Dict[str, Any], service_kind: str) -> Dict[str, Any]:
    wallet = get_player_currency(simulation_state)
    for provider_id, provider in SERVICE_PROVIDERS.items():
        provider = _safe_dict(provider)
        for offer in get_provider_offers(provider_id, service_kind):
            offer = _safe_dict(offer)
            if not _offer_available_here(simulation_state, provider_id, offer):
                continue
            price = normalize_currency(offer.get("price"))
            if not can_afford(wallet, price):
                continue
            label = _safe_str(offer.get("label") or offer.get("offer_id"))
            provider_name = _safe_str(provider.get("provider_name") or provider_id)
            verb = "buy"
            if service_kind == SERVICE_KIND_LODGING:
                verb = "rent"
            return {
                "type": "survival_relief",
                "source": "n1233_survival_suggestion",
                "action_kind": f"buy_{service_kind}",
                "service_kind": service_kind,
                "provider_id": provider_id,
                "provider_name": provider_name,
                "offer_id": _safe_str(offer.get("offer_id")),
                "label": f"{verb.title()} {label}",
                "command": f"I {verb} {label} from {provider_name}",
                "reason": f"{service_kind}_available",
                "price": price,
                "price_label": format_currency(price),
            }
    return {}


def build_survival_suggested_actions(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Return deterministic, availability-backed survival relief suggestions."""

    del runtime_state  # reserved for future schedule/provider-context filters
    needs = _current_needs(simulation_state)
    suggestions: List[Dict[str, Any]] = []

    if needs["hunger"] >= 50:
        food = _find_first_matching_item(simulation_state, _is_food_item)
        if food:
            suggestions.append({
                "type": "survival_relief",
                "source": "n1233_survival_suggestion",
                "action_kind": "eat_food",
                "label": f"Eat {_item_name(food)}",
                "command": f"I eat {_item_name(food)}",
                "reason": "hunger_high_inventory_food_available",
                "item_id": _safe_str(food.get("item_id") or food.get("id") or food.get("name")),
                "quantity": _item_quantity(food),
            })
        meal = _service_suggestion_for_kind(simulation_state, SERVICE_KIND_MEAL)
        if meal:
            meal["reason"] = "hunger_high_service_meal_available"
            suggestions.append(meal)

    if needs["thirst"] >= 50:
        drink = _find_first_matching_item(simulation_state, _is_drink_item)
        if drink:
            suggestions.append({
                "type": "survival_relief",
                "source": "n1233_survival_suggestion",
                "action_kind": "drink_water",
                "label": f"Drink {_item_name(drink)}",
                "command": f"I drink {_item_name(drink)}",
                "reason": "thirst_high_inventory_drink_available",
                "item_id": _safe_str(drink.get("item_id") or drink.get("id") or drink.get("name")),
                "quantity": _item_quantity(drink),
            })
        beverage = _service_suggestion_for_kind(simulation_state, SERVICE_KIND_DRINK)
        if beverage:
            beverage["reason"] = "thirst_high_service_drink_available"
            suggestions.append(beverage)

    if needs["fatigue"] >= 50:
        suggestions.append({
            "type": "survival_relief",
            "source": "n1233_survival_suggestion",
            "action_kind": "rest",
            "label": "Rest",
            "command": "I rest",
            "reason": "fatigue_high_rest_available",
        })
        lodging = _service_suggestion_for_kind(simulation_state, SERVICE_KIND_LODGING)
        if lodging:
            lodging["reason"] = "fatigue_high_lodging_available"
            suggestions.append(lodging)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for suggestion in suggestions:
        key = (_safe_str(suggestion.get("action_kind")), _safe_str(suggestion.get("item_id")), _safe_str(suggestion.get("offer_id")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suggestion)
    return deduped[:8]


def resolve_survival_action(
    *,
    player_input: str,
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve deterministic survival relief and mutate state when applied."""

    text_l = _safe_str(player_input).lower()
    service_result = _safe_dict(service_result)
    service_kind = _safe_str(service_result.get("service_kind"))
    purchase = _selected_purchase(service_result)
    offer = _selected_offer(service_result)

    if purchase and service_kind in {SERVICE_KIND_MEAL, SERVICE_KIND_DRINK, SERVICE_KIND_LODGING}:
        cost = _apply_purchase_cost(simulation_state, purchase)
        if not cost.get("applied"):
            before = _current_needs(simulation_state)
            return _relief_result(
                action_kind=f"buy_{service_kind}",
                before=before,
                after=before,
                purchase=cost,
                blocked_reason=_safe_str(cost.get("blocked_reason") or "insufficient_funds"),
            )
        if service_kind == SERVICE_KIND_MEAL:
            before, after = _apply_relief(simulation_state, hunger=35)
        elif service_kind == SERVICE_KIND_DRINK:
            before, after = _apply_relief(simulation_state, thirst=30)
        else:
            effects = _safe_dict(offer.get("effects"))
            fatigue_relief = 55 if effects.get("rest_quality") == "good" else 40
            before, after = _apply_relief(simulation_state, fatigue=fatigue_relief)
        return _relief_result(
            action_kind=f"buy_{service_kind}",
            before=before,
            after=after,
            purchase=cost,
        )

    eat_intent = _contains_any(text_l, ("eat", "consume food", "use ration", "eat ration", "food"))
    drink_intent = _contains_any(text_l, ("drink", "sip", "use waterskin", "water"))
    rest_intent = _contains_any(text_l, ("rest", "sleep", "nap", "make camp", "camp"))

    if eat_intent:
        consumed = _consume_first_matching_item(simulation_state, _is_food_item)
        if not consumed.get("consumed"):
            before = _current_needs(simulation_state)
            return _relief_result(action_kind="eat_food", before=before, after=before, inventory_consumed=consumed, blocked_reason="no_food_item")
        before, after = _apply_relief(simulation_state, hunger=30)
        return _relief_result(action_kind="eat_food", before=before, after=after, inventory_consumed=consumed)

    if drink_intent:
        consumed = _consume_first_matching_item(simulation_state, _is_drink_item)
        if not consumed.get("consumed"):
            before = _current_needs(simulation_state)
            return _relief_result(action_kind="drink_water", before=before, after=before, inventory_consumed=consumed, blocked_reason="no_drink_item")
        before, after = _apply_relief(simulation_state, thirst=30)
        return _relief_result(action_kind="drink_water", before=before, after=after, inventory_consumed=consumed)

    if rest_intent:
        before, after = _apply_relief(simulation_state, fatigue=25)
        return _relief_result(action_kind="rest", before=before, after=after)

    return {"matched": False, "applied": False, "source": "n1232_survival_action_resolution"}
