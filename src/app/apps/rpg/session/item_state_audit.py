"""Deterministic item-state audit helpers for RPG sessions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.crafting import CRAFTING_RECIPES
from app.apps.rpg.session.inventory_items import (
    display_item_name,
    inventory_quantity,
    is_stackable_item,
    normalize_inventory_items,
    stack_key,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_state_audit_v1"
TRACE_LIMIT = 20
TRACE_BUCKETS = {
    "item_traces",
    "inventory_traces",
    "item_use_traces",
    "salvage_traces",
    "crafting_traces",
    "pickup_traces",
    "modification_traces",
    "market_traces",
    "item_combat_traces",
    "item_report_sections",
    "item_command_traces",
    "item_scenario_traces",
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _issue(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "detail": detail, **{key: value for key, value in extra.items() if value is not None}}


def _raw_quantity(raw: dict[str, Any]) -> int | None:
    for key in ("quantity", "count", "qty", "amount"):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _currency_total(currency: dict[str, Any], issues: list[dict[str, Any]]) -> int:
    total = 0
    for key, multiplier in (("gold", 100), ("silver", 10), ("copper", 1)):
        value = currency.get(key, 0)
        if not isinstance(value, (int, float)):
            issues.append(_issue("invalid_currency_value", f"Currency field {key} is not numeric.", field=key))
            continue
        amount = int(value)
        if amount < 0:
            issues.append(_issue("negative_currency", f"Currency field {key} is negative.", field=key, amount=amount))
        total += amount * multiplier
    return total


def _inventory_identifiers(items: list[dict[str, Any]]) -> set[str]:
    identifiers: set[str] = set()
    for item in items:
        display = _safe_dict(item.get("display"))
        for value in (
            item.get("id"),
            item.get("item_id"),
            item.get("instance_id"),
            item.get("material_id"),
            item.get("name"),
            item.get("label"),
            item.get("display_name"),
            display.get("name"),
        ):
            if _text(value):
                identifiers.add(_norm(value))
    return identifiers


def _equipment_identifiers(value: Any) -> set[str]:
    if isinstance(value, dict):
        display = _safe_dict(value.get("display"))
        return {
            _norm(identifier)
            for identifier in (
                value.get("id"),
                value.get("item_id"),
                value.get("instance_id"),
                value.get("name"),
                value.get("label"),
                value.get("display_name"),
                display.get("name"),
            )
            if _text(identifier)
        }
    if _text(value):
        return {_norm(value)}
    return set()


def _known_recipes(state: dict[str, Any]) -> list[str]:
    crafting = _safe_dict(state.get("crafting"))
    known = crafting.get("known_recipes") or crafting.get("known_recipe_ids") or []
    if isinstance(known, dict):
        known = list(known.keys())
    return sorted({_text(recipe_id) for recipe_id in _safe_list(list(known)) if _text(recipe_id)})


def build_item_state_audit(state: dict[str, Any]) -> dict[str, Any]:
    """Return an AI-free audit of item-related RPG session state."""
    source = deepcopy(_safe_dict(state))
    player = _safe_dict(source.get("player"))
    raw_inventory = _safe_list(player.get("inventory"))
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []

    legacy_count = sum(1 for raw in raw_inventory if not isinstance(raw, dict))
    if legacy_count:
        warnings.append(_issue("legacy_inventory_entries", "Inventory contains legacy non-object entries.", count=legacy_count))

    for index, raw in enumerate(raw_inventory):
        if not isinstance(raw, dict):
            continue
        quantity = _raw_quantity(raw)
        if quantity is not None and quantity < 0:
            issues.append(
                _issue(
                    "negative_inventory_quantity",
                    f"Inventory entry {display_item_name(raw)} has a negative quantity.",
                    index=index,
                    quantity=quantity,
                )
            )

    normalized, normalization_trace = normalize_inventory_items(raw_inventory)
    normalization_needs_repair = bool(normalization_trace.get("legacy_count") or normalization_trace.get("merged_count"))
    if normalization_trace.get("changed") and normalization_needs_repair:
        warnings.append(_issue("inventory_requires_normalization", "Inventory can be normalized before gameplay use."))
        repairs.append(normalization_trace)

    instance_ids: set[str] = set()
    duplicate_instances: list[str] = []
    stack_keys: set[str] = set()
    duplicate_stacks: list[str] = []
    for item in normalized:
        if is_stackable_item(item):
            key = stack_key(item)
            if key and key in stack_keys:
                duplicate_stacks.append(key)
            stack_keys.add(key)
            continue
        instance_id = _text(item.get("instance_id"))
        if instance_id in instance_ids:
            duplicate_instances.append(instance_id)
        if instance_id:
            instance_ids.add(instance_id)
    for instance_id in duplicate_instances:
        issues.append(_issue("duplicate_item_instance_id", "Two non-stackable items share an instance id.", instance_id=instance_id))
    for key in duplicate_stacks:
        warnings.append(_issue("duplicate_stack_key", "Stackable items should be merged by stack key.", stack_key=key))

    currency_total = _currency_total(_safe_dict(player.get("currency")), issues)

    inventory_ids = _inventory_identifiers(normalized)
    equipment = _safe_dict(player.get("equipment"))
    for slot, value in sorted(equipment.items()):
        identifiers = _equipment_identifiers(value)
        if not identifiers:
            continue
        if identifiers.isdisjoint(inventory_ids):
            warnings.append(_issue("missing_equipment_inventory_reference", "Equipment slot does not reference an inventory item.", slot=slot))

    known_recipes = _known_recipes(source)
    unknown_recipes = [recipe_id for recipe_id in known_recipes if recipe_id not in CRAFTING_RECIPES]
    for recipe_id in unknown_recipes:
        warnings.append(_issue("unknown_known_recipe", "Known recipe id is not in the deterministic recipe catalog.", recipe_id=recipe_id))

    mechanics = _safe_dict(source.get("mechanics"))
    trace_counts: dict[str, int] = {}
    for key in sorted(TRACE_BUCKETS):
        value = mechanics.get(key)
        if value is None:
            trace_counts[key] = 0
            continue
        if not isinstance(value, list):
            warnings.append(_issue("mechanics_bucket_not_list", "Mechanics trace bucket is not a list.", bucket=key))
            trace_counts[key] = 0
            continue
        trace_counts[key] = len(value)
        if len(value) > 100:
            warnings.append(_issue("large_mechanics_bucket", "Mechanics trace bucket is large enough to affect report readability.", bucket=key, count=len(value)))

    summary = {
        "inventory_items": len(normalized),
        "inventory_quantity": sum(inventory_quantity(item) for item in normalized),
        "legacy_inventory_entries": legacy_count,
        "currency_copper_total": currency_total,
        "equipment_slots": len(equipment),
        "known_recipes": known_recipes,
        "trace_counts": trace_counts,
    }
    return {
        "ok": not issues,
        "severity": "error" if issues else "warning" if warnings else "ok",
        "issues": issues,
        "warnings": warnings,
        "repairs": repairs,
        "summary": summary,
        "mechanics_source": MECHANICS_SOURCE,
    }


def record_item_state_audit(state: dict[str, Any]) -> dict[str, Any]:
    """Record a compact item-state audit trace on the session mechanics."""
    audit = build_item_state_audit(state)
    trace = {
        "event": "item_state_audited",
        "ok": audit.get("ok") is True,
        "severity": audit.get("severity"),
        "issue_count": len(_safe_list(audit.get("issues"))),
        "warning_count": len(_safe_list(audit.get("warnings"))),
        "summary": deepcopy(_safe_dict(audit.get("summary"))),
        "mechanics_source": MECHANICS_SOURCE,
    }
    mechanics = _safe_dict(state.get("mechanics"))
    mechanics["item_state_audit_traces"] = [deepcopy(trace), *_safe_list(mechanics.get("item_state_audit_traces"))][:TRACE_LIMIT]
    mechanics["item_traces"] = [deepcopy(trace), *_safe_list(mechanics.get("item_traces"))][:TRACE_LIMIT]
    state["mechanics"] = mechanics
    audit["trace"] = trace
    return audit
