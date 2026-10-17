"""Deterministic RPG item-use effect operations.

AI may describe item fiction, but this module owns item-use mechanics. Items can
expose engine-owned use ops; unsupported or malformed ops are ignored and
recorded as repairs instead of mutating state.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.inventory_items import consume_inventory_item, display_item_name, inventory_quantity, item_type
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

SUPPORTED_ITEM_EFFECT_OPS = {
    "restore_resource",
    "add_scene_status",
    "add_affordance",
    "equip_item",
}
SUPPORTED_RESOURCES = {"hp", "mana", "stamina"}
RESOURCE_ALIASES = {
    "health": "hp",
    "hit_points": "hp",
    "hitpoints": "hp",
    "energy": "stamina",
}
AFFORDANCE_BUCKETS = {"dialogue", "travel", "access", "evidence", "crafting", "combat", "social"}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _positive_int(value: Any, fallback: int = 1, *, limit: int = 999) -> int:
    try:
        return max(1, min(limit, int(value)))
    except Exception:
        return fallback


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _item_id(item: dict[str, Any]) -> str:
    return _text(item.get("item_id") or item.get("id") or item.get("instance_id") or display_item_name(item), "item")


def _resource_name(value: Any) -> str:
    resource = _norm(value)
    resource = RESOURCE_ALIASES.get(resource, resource)
    return resource if resource in SUPPORTED_RESOURCES else ""


def _tags(item: dict[str, Any]) -> set[str]:
    return {_norm(tag) for tag in _safe_list(item.get("tags") or item.get("flavor_tags")) if _text(tag)}


def _effect_ops(item: dict[str, Any]) -> list[Any]:
    for key in ("use_effect_ops", "effect_ops", "effects"):
        raw = item.get(key)
        if isinstance(raw, list):
            return raw
    return []


def _normalize_effect_ops(item: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    ops: list[dict[str, Any]] = []
    repairs: list[str] = []
    for ordinal, raw_op in enumerate(_effect_ops(item)):
        op = deepcopy(_safe_dict(raw_op))
        op_name = _norm(op.get("op") or op.get("operation") or op.get("type"))
        if op_name not in SUPPORTED_ITEM_EFFECT_OPS:
            repairs.append(f"ignored_unsupported_item_effect_op:{op_name or ordinal}")
            continue
        if op_name == "restore_resource":
            resource = _resource_name(op.get("resource") or op.get("stat"))
            if not resource:
                repairs.append(f"ignored_restore_resource_without_supported_resource:{ordinal}")
                continue
            ops.append({"op": op_name, "resource": resource, "amount": _positive_int(op.get("amount") or op.get("delta"), 1), "consume": op.get("consume", True) is not False})
        elif op_name == "add_scene_status":
            status = _text(op.get("status") or op.get("tag"))
            if not status:
                repairs.append(f"ignored_scene_status_without_status:{ordinal}")
                continue
            ops.append({"op": op_name, "status": status[:80], "dimension": _text(op.get("dimension"), "environment")[:40], "consume": op.get("consume") is True})
        elif op_name == "add_affordance":
            bucket = _norm(op.get("bucket") or op.get("affordance_type") or op.get("kind")) or "dialogue"
            if bucket not in AFFORDANCE_BUCKETS:
                repairs.append(f"repaired_affordance_bucket:{bucket}")
                bucket = "dialogue"
            tag = _text(op.get("tag") or op.get("affordance"))
            if not tag:
                repairs.append(f"ignored_affordance_without_tag:{ordinal}")
                continue
            ops.append({"op": op_name, "bucket": bucket, "tag": tag[:80], "dimension": _text(op.get("dimension"), "narrative")[:40], "consume": op.get("consume") is True})
        elif op_name == "equip_item":
            ops.append({"op": op_name, "slot": _text(op.get("slot") or op.get("equip_slot")), "consume": False})
    return ops, repairs


def _knowledge_affordance_ops(item: dict[str, Any], name: str) -> list[dict[str, Any]]:
    tags = _tags(item)
    normalized_type = _norm(item_type(item))
    is_document = normalized_type in {"document", "quest_item", "key", "tool"} or tags & {"document", "knowledge", "evidence", "map", "blueprint"}
    if "map" in name or "map" in tags:
        return [{"op": "add_affordance", "bucket": "travel", "tag": "study_map_route", "dimension": "access", "consume": False}]
    if "blueprint" in name or "schematic" in name or "blueprint" in tags:
        return [{"op": "add_affordance", "bucket": "crafting", "tag": "study_blueprint_recipe_clue", "dimension": "knowledge", "consume": False}]
    if "ledger" in name or "record" in name or "evidence" in tags:
        return [{"op": "add_affordance", "bucket": "evidence", "tag": "present_record_as_evidence", "dimension": "information", "consume": False}]
    if any(token in name for token in ("seal", "signet", "papers", "pass")):
        return [{"op": "add_affordance", "bucket": "access", "tag": "present_authorizing_document", "dimension": "access", "consume": False}]
    if is_document and any(token in name for token in ("letter", "memo", "note")):
        return [{"op": "add_affordance", "bucket": "dialogue", "tag": "ask_about_written_message", "dimension": "narrative", "consume": False}]
    return []


def _legacy_fallback_ops(item: dict[str, Any]) -> list[dict[str, Any]]:
    name = _norm(display_item_name(item))
    if "health" in name or "healing" in name:
        return [{"op": "restore_resource", "resource": "hp", "amount": 25, "consume": True}]
    if "mana" in name or "tonic" in name:
        return [{"op": "restore_resource", "resource": "mana", "amount": 25, "consume": True}]
    if "ration" in name or "food" in name:
        return [{"op": "restore_resource", "resource": "stamina", "amount": 10, "consume": True}]
    if "torch" in name:
        return [{"op": "add_scene_status", "status": "lit_torch", "dimension": "environment", "consume": False}]
    if "focus" in name or "crystal" in name:
        return [{"op": "restore_resource", "resource": "mana", "amount": 10, "consume": False}]
    if any(token in name for token in ("cloak", "armor", "bow", "dagger", "ring", "band")):
        return [{"op": "equip_item", "slot": "", "consume": False}]
    if "journal" in name or "scroll" in name:
        return [{"op": "add_affordance", "bucket": "dialogue", "tag": "ask_about_written_clue", "dimension": "narrative", "consume": False}]
    knowledge_ops = _knowledge_affordance_ops(item, name)
    if knowledge_ops:
        return knowledge_ops
    return []


def _metric(player: dict[str, Any], key: str) -> dict[str, Any]:
    resources = _safe_dict(player.get("resources"))
    player["resources"] = resources
    metric = _safe_dict(resources.get(key))
    resources[key] = metric
    metric.setdefault("current", 0)
    metric.setdefault("max", metric.get("current", 0))
    return metric


def _restore_resource(player: dict[str, Any], resource: str, amount: int) -> dict[str, Any]:
    metric = _metric(player, resource)
    current = int(metric.get("current") or 0)
    maximum = int(metric.get("max") or current)
    metric["current"] = max(0, min(maximum, current + amount))
    return {"op": "restore_resource", "resource": resource, "amount": amount, "current": int(metric["current"]), "max": maximum}


def _add_scene_status(state: dict[str, Any], *, status: str, dimension: str, source: str) -> dict[str, Any]:
    scene_state = _safe_dict(state.get("scene_state"))
    statuses = _safe_list(scene_state.get("statuses"))
    entry = {"status": status, "dimension": dimension, "source": source, "created_at": _utc_now()}
    scene_state["statuses"] = [entry, *statuses][:20]
    state["scene_state"] = scene_state
    return {"op": "add_scene_status", "status": status, "dimension": dimension}


def _add_affordance(state: dict[str, Any], *, bucket: str, tag: str, dimension: str, source: str) -> dict[str, Any]:
    affordances = _safe_dict(state.get("narrative_affordances"))
    entries = _safe_list(affordances.get(bucket))
    entry = {"tag": tag, "source": source, "dimension": dimension, "created_at": _utc_now()}
    affordances[bucket] = [entry, *entries][:20]
    state["narrative_affordances"] = affordances
    return {"op": "add_affordance", "bucket": bucket, "tag": tag, "dimension": dimension}


def _item_slot(item: dict[str, Any], requested_slot: str = "") -> str:
    if requested_slot:
        return requested_slot
    raw = _norm(item.get("slot") or item.get("equip_slot") or item.get("type") or item.get("category") or display_item_name(item))
    name = _norm(display_item_name(item))
    if any(token in raw or token in name for token in ("bow", "dagger", "sword", "axe", "weapon")):
        return "Weapon"
    if any(token in raw or token in name for token in ("armor", "mail", "leather", "plate")):
        return "Armor"
    if "cloak" in raw or "cloak" in name:
        return "Cloak"
    if "ring" in raw or "band" in name:
        return "Ring"
    return "Utility"


def _equip_item(player: dict[str, Any], item: dict[str, Any], requested_slot: str = "") -> dict[str, Any]:
    equipment = _safe_list(player.get("equipment"))
    normalized = [existing if isinstance(existing, dict) else {"name": str(existing)} for existing in equipment]
    slot = _item_slot(item, requested_slot)
    equipped = {"slot": slot, "name": display_item_name(item)}
    for index, existing in enumerate(normalized):
        if _norm(existing.get("slot")) == _norm(slot):
            normalized[index] = equipped
            player["equipment"] = normalized
            return {"op": "equip_item", "slot": slot, "name": equipped["name"]}
    normalized.append(equipped)
    player["equipment"] = normalized
    return {"op": "equip_item", "slot": slot, "name": equipped["name"]}


def _detail_for_result(name: str, effects: list[dict[str, Any]]) -> str:
    if len(effects) == 1:
        effect = effects[0]
        if effect.get("op") == "restore_resource":
            resource = str(effect.get("resource") or "resource").upper()
            verb = "recovered" if effect.get("resource") in {"hp", "stamina"} else "restored"
            return f"You used {name} and {verb} {resource} to {effect.get('current')}/{effect.get('max')}."
        if effect.get("op") == "add_scene_status" and effect.get("status") == "lit_torch":
            return f"You lit {name}. The immediate area is easier to inspect."
        if effect.get("op") == "add_affordance":
            return f"You reviewed {name} for useful clues."
        if effect.get("op") == "equip_item":
            return f"You equipped {name} in the {effect.get('slot')} slot."
    if effects:
        return f"You used {name}; {len(effects)} deterministic effect(s) resolved."
    return f"You used {name}, but it has no special deterministic effect yet."


def use_inventory_item(state: dict[str, Any], player: dict[str, Any], inventory: list[dict[str, Any]], index: int, item: dict[str, Any]) -> dict[str, Any]:
    """Resolve an item use through validated engine-owned effect operations."""

    name = display_item_name(item)
    ops, repairs = _normalize_effect_ops(item)
    source = "explicit_item_use_effect_ops_v1" if ops else "legacy_item_use_fallback_v1"
    if not ops:
        ops = _legacy_fallback_ops(item)

    effects: list[dict[str, Any]] = []
    consumed = False
    for op in ops:
        if op.get("op") == "restore_resource":
            effects.append(_restore_resource(player, str(op.get("resource")), int(op.get("amount") or 1)))
        elif op.get("op") == "add_scene_status":
            effects.append(_add_scene_status(state, status=str(op.get("status")), dimension=str(op.get("dimension") or "environment"), source=name))
        elif op.get("op") == "add_affordance":
            effects.append(_add_affordance(state, bucket=str(op.get("bucket") or "dialogue"), tag=str(op.get("tag")), dimension=str(op.get("dimension") or "narrative"), source=name))
        elif op.get("op") == "equip_item":
            effects.append(_equip_item(player, item, str(op.get("slot") or "")))
        consumed = consumed or op.get("consume") is True

    if not effects and item_type(item) == "consumable":
        repairs.append("consumable_without_valid_effect_ops_not_consumed")

    if consumed:
        consume_inventory_item(inventory, index)

    trace = {
        "event": "item_used",
        "source_item_id": _item_id(item),
        "source_item_name": name,
        "source_item_type": item_type(item),
        "consumed": consumed,
        "remaining_quantity": inventory_quantity(inventory[index]) if index < len(inventory) and inventory[index] is item else None,
        "effects": deepcopy(effects),
        "repairs": repairs,
        "mechanics_source": source,
    }
    return {
        "ok": True,
        "name": name,
        "detail": _detail_for_result(name, effects),
        "consumed": consumed,
        "effects": effects,
        "repairs": repairs,
        "trace": trace,
    }
