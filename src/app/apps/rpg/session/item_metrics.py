"""Deterministic item-system metrics helpers for RPG sessions."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.inventory_items import display_item_name, inventory_quantity, item_type
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_metrics_v1"
TRACE_KEYS = {
    "inventory_normalized": "inventory_traces",
    "item_used": "item_use_traces",
    "salvaged": "salvage_traces",
    "crafted": "crafting_traces",
    "picked_up": "pickup_traces",
    "modified": "modification_traces",
    "market_quoted": "market_traces",
    "signaled": "signal_traces",
    "rewarded": "reward_traces",
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _known_recipes(state: dict[str, Any]) -> list[str]:
    crafting = _safe_dict(state.get("crafting"))
    known = crafting.get("known_recipes") or crafting.get("known_recipe_ids") or []
    if isinstance(known, dict):
        known = known.keys()
    return sorted({_text(recipe_id) for recipe_id in _safe_list(list(known)) if _text(recipe_id)})


def _inventory_metrics(state: dict[str, Any]) -> dict[str, Any]:
    player = _safe_dict(state.get("player"))
    items = [_safe_dict(item) for item in _safe_list(player.get("inventory")) if _safe_dict(item)]
    by_type: dict[str, int] = {}
    material_ids: set[str] = set()
    stackable_count = 0
    protected_count = 0
    total_quantity = 0
    sample_names: list[str] = []
    for item in items:
        quantity = inventory_quantity(item)
        total_quantity += quantity
        normalized_type = item_type(item) or "misc"
        by_type[normalized_type] = by_type.get(normalized_type, 0) + quantity
        if item.get("material_id"):
            material_ids.add(_text(item.get("material_id")))
        if item.get("stackable") is True:
            stackable_count += 1
        if item.get("protected") is True:
            protected_count += 1
        if len(sample_names) < 8:
            sample_names.append(display_item_name(item))
    return {
        "item_count": len(items),
        "total_quantity": total_quantity,
        "by_type": dict(sorted(by_type.items())),
        "material_ids": sorted(material_ids),
        "stackable_count": stackable_count,
        "protected_count": protected_count,
        "sample_names": sample_names,
    }


def _trace_metrics(state: dict[str, Any]) -> dict[str, Any]:
    mechanics = _safe_dict(state.get("mechanics"))
    counts: dict[str, int] = {}
    present: list[str] = []
    missing: list[str] = []
    for label, key in TRACE_KEYS.items():
        traces = _safe_list(mechanics.get(key))
        counts[label] = len(traces)
        if traces:
            present.append(label)
        else:
            missing.append(label)
    return {"counts": counts, "present": present, "missing": missing}


def build_item_metrics_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    inventory = _inventory_metrics(state)
    traces = _trace_metrics(state)
    known_recipes = _known_recipes(state)
    feature_flags = {
        "has_inventory": inventory["item_count"] > 0,
        "has_materials": bool(inventory["material_ids"]),
        "has_known_recipes": bool(known_recipes),
        "has_item_use": traces["counts"].get("item_used", 0) > 0,
        "has_salvage": traces["counts"].get("salvaged", 0) > 0,
        "has_crafting": traces["counts"].get("crafted", 0) > 0,
        "has_scene_pickups": traces["counts"].get("picked_up", 0) > 0,
        "has_market_quotes": traces["counts"].get("market_quoted", 0) > 0,
        "has_item_signals": traces["counts"].get("signaled", 0) > 0,
        "has_modifications": traces["counts"].get("modified", 0) > 0,
    }
    return {
        "ok": True,
        "inventory": inventory,
        "known_recipes": known_recipes,
        "traces": traces,
        "feature_flags": feature_flags,
        "mechanics_source": MECHANICS_SOURCE,
    }


