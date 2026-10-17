"""Deterministic item description context and display helpers.

This module builds a safe boundary between engine-owned item mechanics and
presentation-only item text. It never changes damage, defense, effects,
materials, quantities, values, or other mechanics; callers may use the returned
context to request or validate display text from a presentation layer.
"""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.item_system import (
    AI_FICTION_ITEM_FIELDS,
    ENGINE_OWNED_ITEM_FIELDS,
    normalize_item_instance,
    suggest_genre_item_name,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

ITEM_DESCRIPTION_CONTEXT_VERSION = "item_description_context_v1"
DISPLAY_FIELDS = tuple(sorted(AI_FICTION_ITEM_FIELDS))
MECHANIC_FIELDS = tuple(sorted(ENGINE_OWNED_ITEM_FIELDS))


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _compact_stat_entries(entries: Any) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for entry in _safe_list(entries):
        raw = _safe_dict(entry)
        kind = _text(raw.get("type") or raw.get("stat") or raw.get("resource"))
        if not kind:
            continue
        amount = raw.get("amount")
        compact.append({"type": kind, "amount": amount})
    return compact


def _item_id(item: dict[str, Any]) -> str:
    return _text(item.get("item_id") or item.get("id"), "unknown_item")


def build_item_mechanics_summary(item: dict[str, Any]) -> dict[str, Any]:
    """Return a compact mechanics-only summary safe to expose to presentation."""

    normalized = normalize_item_instance(item)
    summary: dict[str, Any] = {
        "item_id": _item_id(normalized),
        "item_type": _text(normalized.get("item_type") or normalized.get("type"), "supply"),
        "rarity": _text(normalized.get("rarity"), "common"),
        "level": int(normalized.get("level") or normalized.get("item_level") or 1),
        "quantity": int(normalized.get("quantity") or 0),
        "stackable": bool(normalized.get("stackable")),
        "tags": [_text(tag) for tag in _safe_list(normalized.get("tags")) if _text(tag)],
    }
    if normalized.get("weapon_type"):
        summary["weapon_type"] = _text(normalized.get("weapon_type"))
    if normalized.get("armor_type"):
        summary["armor_type"] = _text(normalized.get("armor_type"))
    if normalized.get("consumable_type"):
        summary["consumable_type"] = _text(normalized.get("consumable_type"))
    if normalized.get("damage"):
        summary["damage"] = _compact_stat_entries(normalized.get("damage"))
    if normalized.get("defense"):
        summary["defense"] = _compact_stat_entries(normalized.get("defense"))
    if normalized.get("effects"):
        effect_ops = []
        for effect in _safe_list(normalized.get("effects")):
            raw = _safe_dict(effect)
            op = _text(raw.get("op"))
            if op:
                effect_ops.append(op)
        if effect_ops:
            summary["effect_ops"] = effect_ops
    if normalized.get("modifications"):
        summary["modifications"] = [
            _text(_safe_dict(mod).get("mod_id") or _safe_dict(mod).get("id") or _safe_dict(mod).get("name"))
            for mod in _safe_list(normalized.get("modifications"))
            if _safe_dict(mod)
        ]
    if normalized.get("value") is not None:
        summary["value"] = normalized.get("value")
    if normalized.get("weight") is not None:
        summary["weight"] = normalized.get("weight")
    if normalized.get("protected") is True or "protected" in summary.get("tags", []):
        summary["protected"] = True
    return summary


def build_item_description_context(item: dict[str, Any], *, genre: str = "classic_fantasy") -> dict[str, Any]:
    """Build a deterministic context packet for display-text generation."""

    normalized = normalize_item_instance(item)
    mechanics = build_item_mechanics_summary(normalized)
    suggested_name = suggest_genre_item_name(normalized, genre)
    return {
        "version": ITEM_DESCRIPTION_CONTEXT_VERSION,
        "source": "engine_item_description_context",
        "item_id": mechanics["item_id"],
        "genre": genre,
        "current_display": {
            "name": _text(normalized.get("name"), suggested_name),
            "description": _text(normalized.get("description")),
            "flavor_text": _text(normalized.get("flavor_text")),
            "flavor_tags": [_text(tag) for tag in _safe_list(normalized.get("flavor_tags")) if _text(tag)],
            "icon": _text(normalized.get("icon")),
        },
        "suggested_name": suggested_name,
        "allowed_display_fields": list(DISPLAY_FIELDS),
        "locked_mechanic_fields": list(MECHANIC_FIELDS),
        "mechanics_summary": mechanics,
    }


