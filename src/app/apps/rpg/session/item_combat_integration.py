"""Combat-facing helpers for deterministic RPG item damage resolution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.equipment import normalize_equipment
from app.apps.rpg.session.item_combat import build_attack_profile_from_item, resolve_damage_against_defense
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


_RESOURCE_KEYS = ("health", "hp", "hit_points")


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _actor_name(actor: dict[str, Any], fallback: str) -> str:
    return _text(actor.get("name") or actor.get("display_name") or actor.get("id"), fallback)


def actor_equipment(actor: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized equipment for a player, NPC, or compact actor mapping."""
    actor = _safe_dict(actor)
    equipment = actor.get("equipment")
    if equipment is None:
        equipment = _safe_dict(actor.get("loadout")).get("equipment")
    if equipment is None:
        equipment = _safe_dict(actor.get("player")).get("equipment")
    return normalize_equipment(_safe_list(equipment))


def select_item_damage_source(actor: dict[str, Any], *, preferred_slot: str = "Weapon") -> dict[str, Any]:
    """Choose the deterministic equipped item that should provide outgoing damage."""
    equipment = actor_equipment(actor)
    preferred = _norm(preferred_slot)
    for item in equipment:
        if _norm(item.get("slot")) == preferred and (item.get("damage") or item.get("damage_profile")):
            return deepcopy(item)
    for item in equipment:
        if item.get("damage") or item.get("damage_profile"):
            return deepcopy(item)
    return {
        "item_id": "unarmed",
        "name": "Unarmed",
        "slot": "Natural",
        "damage": {"bludgeoning": 1},
    }


def actor_resource_snapshot(actor: dict[str, Any]) -> dict[str, Any]:
    """Return a compact health-like resource snapshot without mutating the actor."""
    actor = _safe_dict(actor)
    resources = _safe_dict(actor.get("resources"))
    for key in _RESOURCE_KEYS:
        raw = resources.get(key)
        if isinstance(raw, dict):
            current = _as_int(raw.get("current"), _as_int(raw.get("value"), 0))
            maximum = _as_int(raw.get("max"), current)
            return {"key": key, "current": current, "max": maximum}
        if raw is not None:
            current = _as_int(raw, 0)
            return {"key": key, "current": current, "max": current}
    if actor.get("health") is not None:
        current = _as_int(actor.get("health"), 0)
        return {"key": "health", "current": current, "max": current}
    return {"key": "health", "current": 0, "max": 0}


def resolve_actor_item_damage(attacker: dict[str, Any], defender: dict[str, Any], *, source_item: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve an actor's item damage against another actor's equipment.

    This is intentionally state-neutral: callers can apply the returned effects in a
    turn loop without letting presentation text mutate combat resources.
    """
    attacker = _safe_dict(attacker)
    defender = _safe_dict(defender)
    item = deepcopy(_safe_dict(source_item)) if isinstance(source_item, dict) else select_item_damage_source(attacker)
    defender_equipment = actor_equipment(defender)
    defender_resources = actor_resource_snapshot(defender)
    attack = build_attack_profile_from_item(item)
    defense_profile: dict[str, int] = {}
    resistance_profile: dict[str, int] = {}
    for equipped in defender_equipment:
        defense = _safe_dict(equipped.get("defense") or equipped.get("defense_profile"))
        resistances = _safe_dict(equipped.get("resistances") or equipped.get("resistance_profile"))
        for key, value in defense.items():
            if isinstance(value, (int, float)):
                defense_profile[str(key)] = defense_profile.get(str(key), 0) + int(value)
        for key, value in resistances.items():
            if isinstance(value, (int, float)):
                resistance_profile[str(key)] = resistance_profile.get(str(key), 0) + int(value)
    resolution = resolve_damage_against_defense(attack["damage"], defense_profile, resistances=resistance_profile)
    damage = int(resolution.get("total_resolved") or 0)
    after = max(0, int(defender_resources.get("current") or 0) - damage)
    defeated = bool(defender_resources.get("current", 0) and after <= 0)
    effect = {
        "action": "change_resource",
        "target": _actor_name(defender, "defender"),
        "resource": defender_resources.get("key") or "health",
        "delta": -damage,
        "before": defender_resources.get("current", 0),
        "after": after,
    }
    trace = {
        "event": "item_combat_damage_resolved",
        "attacker": _actor_name(attacker, "attacker"),
        "defender": _actor_name(defender, "defender"),
        "source_item_id": attack.get("source_item_id"),
        "source_item_name": attack.get("source_item_name"),
        "incoming_damage": resolution.get("incoming_damage", {}),
        "resolved_damage": resolution.get("resolved_damage", {}),
        "total_resolved": damage,
        "defeated": defeated,
        "mechanics_source": "engine_item_combat_bridge_v1",
    }
    return {
        "ok": True,
        "attacker": trace["attacker"],
        "defender": trace["defender"],
        "source_item": item,
        "attack": attack,
        "defender_equipment": [deepcopy(item) for item in defender_equipment],
        "defender_resource": defender_resources,
        "resolution": resolution,
        "effects": [effect],
        "defeated": defeated,
        "trace": trace,
        "mechanics_source": "engine_item_combat_integration_v1",
    }
