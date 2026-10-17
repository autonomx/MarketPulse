from __future__ import annotations

from typing import Any, Dict, Optional
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

Currency = Dict[str, int]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def normalize_currency(value: Any) -> Currency:
    value = _safe_dict(value)
    return {
        "gold": max(0, _safe_int(value.get("gold"), 0)),
        "silver": max(0, _safe_int(value.get("silver"), 0)),
        "copper": max(0, _safe_int(value.get("copper"), 0)),
    }


def normalize_delta_currency(value: Any) -> Currency:
    value = _safe_dict(value)
    return {
        "gold": _safe_int(value.get("gold"), 0),
        "silver": _safe_int(value.get("silver"), 0),
        "copper": _safe_int(value.get("copper"), 0),
    }


def currency_to_copper(value: Any) -> int:
    currency = normalize_delta_currency(value)
    return (
        currency["gold"] * 100
        + currency["silver"] * 10
        + currency["copper"]
    )


def copper_to_currency(total_copper: int) -> Currency:
    total = max(0, _safe_int(total_copper, 0))
    gold = total // 100
    total -= gold * 100
    silver = total // 10
    total -= silver * 10
    return {
        "gold": gold,
        "silver": silver,
        "copper": total,
    }


def can_afford(available: Any, price: Any) -> bool:
    return currency_to_copper(available) >= currency_to_copper(price)


def add_currency(left: Any, right: Any) -> Currency:
    left_total = currency_to_copper(left)
    right_total = currency_to_copper(right)
    return copper_to_currency(left_total + right_total)


def subtract_currency(left: Any, right: Any) -> Currency:
    left_total = currency_to_copper(left)
    right_total = currency_to_copper(right)
    return copper_to_currency(left_total - right_total)


def apply_currency_delta(current: Any, delta: Any) -> Currency:
    current_total = currency_to_copper(current)
    delta_total = currency_to_copper(delta)
    return copper_to_currency(current_total + delta_total)


def negative_currency(value: Any) -> Currency:
    currency = normalize_currency(value)
    return {
        "gold": -currency["gold"],
        "silver": -currency["silver"],
        "copper": -currency["copper"],
    }


def format_currency(value: Any) -> str:
    currency = normalize_currency(value)
    parts = []
    if currency["gold"]:
        parts.append(f"{currency['gold']} gold")
    if currency["silver"]:
        parts.append(f"{currency['silver']} silver")
    if currency["copper"]:
        parts.append(f"{currency['copper']} copper")
    return ", ".join(parts) if parts else "0 copper"


def _candidate_currency(value: Any) -> Optional[Currency]:
    if not isinstance(value, dict):
        return None
    return normalize_currency(value)


def _currency_has_value(value: Any) -> bool:
    return currency_to_copper(value) > 0


def get_player_currency(simulation_state: Dict[str, Any]) -> Currency:
    state = _safe_dict(simulation_state)

    player_state = _safe_dict(state.get("player_state"))
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    player_inventory = _safe_dict(player_state.get("inventory"))
    root_inventory_state = _safe_dict(state.get("inventory_state"))
    player_state_resources = _safe_dict(player_state.get("resources"))
    player_resources = _safe_dict(state.get("player_resources"))
    resources = _safe_dict(state.get("resources"))

    candidates = []
    for raw_currency in (
        player_state.get("currency"),
        inventory_state.get("currency"),
        player_inventory.get("currency"),
        root_inventory_state.get("currency"),
        player_state_resources.get("currency"),
        player_resources.get("currency"),
        resources.get("currency"),
        state.get("currency"),
    ):
        candidate = _candidate_currency(raw_currency)
        if candidate is None:
            continue
        candidates.append(candidate)
        if _currency_has_value(candidate):
            return candidate

    # Backward compatibility for older sessions that stored loose coin fields.
    for source in (
        player_state,
        player_state_resources,
        player_resources,
        resources,
        state,
    ):
        if any(source.get(key) is not None for key in ("gold", "silver", "copper")):
            candidate = normalize_currency({
                "gold": source.get("gold"),
                "silver": source.get("silver"),
                "copper": source.get("copper"),
            })
            candidates.append(candidate)
            if _currency_has_value(candidate):
                return candidate

    if candidates:
        return candidates[0]

    return normalize_currency({})


def set_player_currency(simulation_state: Dict[str, Any], currency: Any) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    player_state = _safe_dict(state.get("player_state"))
    if not player_state:
        player_state = {}
        state["player_state"] = player_state

    inventory_state = _safe_dict(player_state.get("inventory_state"))
    if not inventory_state:
        inventory_state = {}
        player_state["inventory_state"] = inventory_state

    normalized = normalize_currency(currency)
    inventory_state["currency"] = dict(normalized)
    player_state["currency"] = dict(normalized)

    player_inventory = _safe_dict(player_state.get("inventory"))
    if player_inventory:
        player_inventory["currency"] = dict(normalized)
        player_state["inventory"] = player_inventory

    return state


def currency_to_copper_value(currency: Any) -> int:
    return currency_to_copper(currency)


def currency_delta(before: Any, after: Any) -> Currency:
    before_value = currency_to_copper(before)
    after_value = currency_to_copper(after)
    delta = after_value - before_value
    sign = -1 if delta < 0 else 1
    normalized = copper_to_currency(abs(delta))
    return {
        "gold": sign * normalized["gold"],
        "silver": sign * normalized["silver"],
        "copper": sign * normalized["copper"],
    }


def subtract_currency_cost(wallet: Any, cost: Any) -> Currency:
    wallet_value = currency_to_copper(wallet)
    cost_value = currency_to_copper(cost)
    if cost_value > wallet_value:
        raise ValueError("insufficient currency")
    return copper_to_currency(wallet_value - cost_value)
