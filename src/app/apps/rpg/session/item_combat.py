"""Deterministic RPG item damage and defense resolution."""
from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _positive_amount(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def normalize_damage_profile(raw: Any) -> dict[str, int]:
    profile: dict[str, int] = {}
    if isinstance(raw, dict):
        if "type" in raw and "amount" in raw:
            damage_type = _text(raw.get("type"))
            amount = _positive_amount(raw.get("amount"))
            if damage_type and amount:
                profile[damage_type] = profile.get(damage_type, 0) + amount
            return profile
        for damage_type, amount in raw.items():
            normalized = _text(damage_type)
            value = _positive_amount(amount)
            if normalized and value:
                profile[normalized] = profile.get(normalized, 0) + value
    elif isinstance(raw, list):
        for entry in raw:
            for damage_type, amount in normalize_damage_profile(entry).items():
                profile[damage_type] = profile.get(damage_type, 0) + amount
    return profile


def normalize_defense_profile(raw: Any) -> dict[str, int]:
    return normalize_damage_profile(raw)


def item_damage_profile(item: dict[str, Any]) -> dict[str, int]:
    return normalize_damage_profile(_safe_dict(item).get("damage") or _safe_dict(item).get("damage_profile"))


def resolve_damage_against_defense(incoming_damage: Any, defense_profile: Any, *, resistances: Any | None = None) -> dict[str, Any]:
    incoming = normalize_damage_profile(incoming_damage)
    defense = normalize_defense_profile(defense_profile)
    resistance = normalize_defense_profile(resistances or {})
    resolved: dict[str, int] = {}
    reductions: dict[str, dict[str, int]] = {}

    for damage_type, amount in incoming.items():
        typed_defense = defense.get(damage_type, 0)
        physical_defense = defense.get("physical", 0) if damage_type in {"slashing", "piercing", "bludgeoning", "blunt"} else 0
        resistance_reduction = resistance.get(damage_type, 0)
        reduced_by = min(amount, typed_defense + physical_defense + resistance_reduction)
        final_amount = max(0, amount - reduced_by)
        resolved[damage_type] = final_amount
        reductions[damage_type] = {
            "incoming": amount,
            "defense": typed_defense,
            "physical_defense": physical_defense,
            "resistance": resistance_reduction,
            "reduced_by": reduced_by,
            "final": final_amount,
        }

    return {
        "incoming_damage": incoming,
        "defense_profile": defense,
        "resistances": resistance,
        "resolved_damage": resolved,
        "total_incoming": sum(incoming.values()),
        "total_resolved": sum(resolved.values()),
        "reductions": reductions,
        "mechanics_source": "engine_item_damage_resolution_v1",
    }


def build_attack_profile_from_item(item: dict[str, Any]) -> dict[str, Any]:
    item = _safe_dict(item)
    damage = item_damage_profile(item)
    return {
        "source_item_id": _text(item.get("item_id") or item.get("id")),
        "source_item_name": _text(item.get("name") or _safe_dict(item.get("display")).get("name"), "Item"),
        "damage": damage,
        "total_damage": sum(damage.values()),
        "mechanics_source": "engine_item_attack_profile_v1",
    }


