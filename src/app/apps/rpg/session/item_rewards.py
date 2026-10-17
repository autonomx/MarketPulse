"""Deterministic RPG item reward table helpers."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from random import Random
from typing import Any, cast
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MECHANICS_SOURCE = "engine_item_reward_table_v1"

REWARD_TABLES: dict[str, dict[str, Any]] = {
    "road_cache": {
        "source_id": "road_cache",
        "name": "Road Cache",
        "roll_count": 2,
        "entries": [
            {"weight": 5, "item": {"item_id": "copper_coin", "name": "Copper coins", "item_type": "currency", "quantity": 6, "stackable": True, "value": 1, "rarity": "common"}},
            {"weight": 3, "item": {"item_id": "iron", "name": "Iron scrap", "item_type": "crafting_material", "material_id": "iron", "material_role": "metal", "quantity": 1, "stackable": True, "rarity": "common"}},
            {"weight": 2, "item": {"item_id": "travel_ration", "name": "Travel ration", "item_type": "consumable", "quantity": 1, "stackable": True, "rarity": "common"}},
        ],
    },
    "forest_cache": {
        "source_id": "forest_cache",
        "name": "Forest Cache",
        "roll_count": 2,
        "entries": [
            {"weight": 5, "item": {"item_id": "leather", "name": "Hide strips", "item_type": "crafting_material", "material_id": "leather", "material_role": "leather", "quantity": 1, "stackable": True, "rarity": "common"}},
            {"weight": 4, "item": {"item_id": "wood", "name": "Dry wood pieces", "item_type": "crafting_material", "material_id": "wood", "material_role": "wood", "quantity": 2, "stackable": True, "rarity": "common"}},
            {"weight": 1, "item": {"item_id": "keenleaf", "name": "Keenleaf sprig", "item_type": "crafting_material", "material_id": "keenleaf", "material_role": "herb", "quantity": 1, "stackable": True, "rarity": "uncommon"}},
        ],
    },
    "ruin_cache": {
        "source_id": "ruin_cache",
        "name": "Ruin Cache",
        "roll_count": 3,
        "entries": [
            {"weight": 4, "item": {"item_id": "stone", "name": "Worked stone chips", "item_type": "crafting_material", "material_id": "stone", "material_role": "stone", "quantity": 2, "stackable": True, "rarity": "common"}},
            {"weight": 3, "item": {"item_id": "paper", "name": "Old paper scraps", "item_type": "crafting_material", "material_id": "paper", "material_role": "cloth", "quantity": 1, "stackable": True, "rarity": "common"}},
            {"weight": 2, "item": {"item_id": "rune_note", "name": "Fragmentary rune note", "item_type": "document", "quantity": 1, "stackable": False, "rarity": "uncommon", "capabilities": [{"capability_id": "study_written_clue", "kind": "knowledge"}]}},
            {"weight": 1, "item": {"item_id": "relic_shard", "name": "Relic shard", "item_type": "artifact", "quantity": 1, "stackable": False, "rarity": "rare", "protected": True}},
        ],
    },
    "generic_cache": {
        "source_id": "generic_cache",
        "name": "Generic Cache",
        "roll_count": 1,
        "entries": [
            {"weight": 4, "item": {"item_id": "copper_coin", "name": "Copper coins", "item_type": "currency", "quantity": 3, "stackable": True, "value": 1, "rarity": "common"}},
            {"weight": 2, "item": {"item_id": "travel_ration", "name": "Travel ration", "item_type": "consumable", "quantity": 1, "stackable": True, "rarity": "common"}},
        ],
    },
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _positive_int(value: Any, fallback: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return fallback


def reward_table(source_id: str | None) -> dict[str, Any]:
    return deepcopy(REWARD_TABLES.get(_norm(source_id)) or REWARD_TABLES["generic_cache"])


def _rng(source_id: str, seed: str | int | None, context: dict[str, Any] | None) -> Random:
    safe_context = _safe_dict(context)
    context_bits = ":".join(f"{key}={safe_context[key]}" for key in sorted(safe_context))
    digest = sha256(f"{source_id}:{seed or 'default'}:{context_bits}".encode("utf-8")).hexdigest()
    return Random(int(digest[:16], 16))


def _choose_weighted(entries: list[dict[str, Any]], rng: Random) -> dict[str, Any]:
    weighted = [(entry, _positive_int(_safe_dict(entry).get("weight"), 1)) for entry in entries if _safe_dict(entry)]
    total = sum(weight for _entry, weight in weighted)
    pick = rng.uniform(0, total)
    running = 0.0
    for entry, weight in weighted:
        running += weight
        if pick <= running:
            return deepcopy(cast(Any, _safe_dict(entry).get("item")))
    return deepcopy(cast(Any, _safe_dict(weighted[-1][0]).get("item"))) if weighted else {}


def _stack_key(item: dict[str, Any]) -> tuple[str, str, str] | None:
    if item.get("stackable") is not True:
        return None
    return (_text(item.get("item_id") or item.get("id")), _text(item.get("material_id")), _text(item.get("rarity")))


def merge_reward_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    index_by_key: dict[tuple[str, str, str], int] = {}
    for item in items:
        normalized = deepcopy(_safe_dict(item))
        normalized["quantity"] = _positive_int(normalized.get("quantity"), 1)
        key = _stack_key(normalized)
        if key and key in index_by_key:
            existing = merged[index_by_key[key]]
            existing["quantity"] = _positive_int(existing.get("quantity"), 1) + normalized["quantity"]
            continue
        if key:
            index_by_key[key] = len(merged)
        merged.append(normalized)
    return merged


def generate_item_rewards(source_id: str | None, *, seed: str | int | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    table = reward_table(source_id)
    resolved_source_id = _text(table.get("source_id"), "generic_cache")
    entries = [_safe_dict(entry) for entry in _safe_list(table.get("entries")) if _safe_dict(entry)]
    if not entries:
        return {"ok": False, "error": "empty_reward_table", "source_id": resolved_source_id, "outputs": []}

    rng = _rng(resolved_source_id, seed, context)
    roll_count = _positive_int(table.get("roll_count"), 1)
    outputs = merge_reward_items([_choose_weighted(entries, rng) for _ in range(roll_count)])
    trace_outputs = [
        {
            "item_id": _text(item.get("item_id") or item.get("id")),
            "name": _text(item.get("name")),
            "quantity": _positive_int(item.get("quantity"), 1),
            "rarity": _text(item.get("rarity"), "common"),
            "item_type": _text(item.get("item_type") or item.get("type")),
            "material_id": _text(item.get("material_id")),
        }
        for item in outputs
    ]
    trace = {
        "event": "item_rewards_generated",
        "source_id": resolved_source_id,
        "source_name": _text(table.get("name"), resolved_source_id),
        "seed_digest": sha256(str(seed or "default").encode("utf-8")).hexdigest()[:12],
        "roll_count": roll_count,
        "outputs": trace_outputs,
        "mechanics_source": MECHANICS_SOURCE,
    }
    return {"ok": True, "source_id": resolved_source_id, "outputs": outputs, "trace": trace}
