from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

COPPER_PER_SILVER = 100
SILVER_PER_GOLD = 100
COPPER_PER_GOLD = COPPER_PER_SILVER * SILVER_PER_GOLD


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def normalize_currency(value: Any) -> Dict[str, int]:
    value = _safe_dict(value)
    return {
        "gold": max(0, _safe_int(value.get("gold"), 0)),
        "silver": max(0, _safe_int(value.get("silver"), 0)),
        "copper": max(0, _safe_int(value.get("copper"), 0)),
    }


def currency_to_copper(value: Any) -> int:
    value = normalize_currency(value)
    return (
        value["gold"] * COPPER_PER_GOLD
        + value["silver"] * COPPER_PER_SILVER
        + value["copper"]
    )


def copper_to_currency(copper: int) -> Dict[str, int]:
    copper = max(0, _safe_int(copper, 0))
    gold = copper // COPPER_PER_GOLD
    copper -= gold * COPPER_PER_GOLD
    silver = copper // COPPER_PER_SILVER
    copper -= silver * COPPER_PER_SILVER
    return {
        "gold": gold,
        "silver": silver,
        "copper": copper,
    }


def add_currency(a: Any, b: Any) -> Dict[str, int]:
    return copper_to_currency(currency_to_copper(a) + currency_to_copper(b))


def subtract_currency(a: Any, b: Any) -> Dict[str, Any]:
    a_copper = currency_to_copper(a)
    b_copper = currency_to_copper(b)
    if a_copper < b_copper:
        return {
            "ok": False,
            "reason": "insufficient_currency",
            "currency": copper_to_currency(a_copper),
            "required": copper_to_currency(b_copper),
            "missing_copper": b_copper - a_copper,
        }

    return {
        "ok": True,
        "currency": copper_to_currency(a_copper - b_copper),
        "spent": copper_to_currency(b_copper),
    }


def multiply_currency(value: Any, multiplier: float) -> Dict[str, int]:
    copper = currency_to_copper(value)
    return copper_to_currency(round(copper * float(multiplier)))


def currency_snapshot(value: Any) -> Dict[str, int]:
    return deepcopy(normalize_currency(value))
