"""Deterministic recipe discovery helpers for RPG sessions.

The engine owns known recipe identifiers and discovery traces. Item fiction,
notes, and affordances may hint at recipes, but only catalog recipe ids are
recorded here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.crafting import CRAFTING_RECIPES, get_recipe
from app.apps.rpg.session.inventory_items import display_item_name, normalize_inventory_items
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

RECIPE_HINTS: dict[str, tuple[str, ...]] = {
    "torch": ("torch", "lamp", "light", "campfire"),
    "crude_blade": ("crude_blade", "blade", "blueprint", "forge", "metalwork", "recipe_clue"),
}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold()


def _slug(value: Any, fallback: str = "recipe") -> str:
    raw = _norm(value or fallback)
    slug = "".join(char if char.isalnum() else "_" for char in raw).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def _catalog_recipe_ids() -> set[str]:
    return {str(recipe_id) for recipe_id in CRAFTING_RECIPES}


def _canonical_recipe_id(value: Any) -> str:
    candidate = _slug(value, "")
    return candidate if candidate in _catalog_recipe_ids() else ""


def _recipe_display_name(recipe: dict[str, Any], recipe_id: str) -> str:
    output = _safe_dict(recipe.get("output"))
    return _text(output.get("name") or recipe.get("display_name") or recipe.get("name"), recipe_id.replace("_", " ").title())


def _recipe_display_station(recipe: dict[str, Any], recipe_id: str) -> str:
    station = _text(recipe.get("station"))
    if recipe_id == "torch" and station == "campfire":
        return "camp"
    return station


def _known_entry(recipe_id: str, *, source: str = "state", detail: str = "") -> dict[str, Any]:
    recipe = get_recipe(recipe_id) or {"recipe_id": recipe_id, "name": recipe_id.replace("_", " ").title()}
    entry = {
        "recipe_id": recipe.get("recipe_id") or recipe_id,
        "name": _recipe_display_name(recipe, recipe_id),
        "source": source,
    }
    if detail:
        entry["detail"] = detail
    station = _recipe_display_station(recipe, recipe_id)
    if station:
        entry["station"] = station
    return entry


def _recipe_ids_from_value(value: Any) -> list[str]:
    ids: list[str] = []
    if isinstance(value, str):
        recipe_id = _canonical_recipe_id(value)
        return [recipe_id] if recipe_id else []
    if isinstance(value, dict):
        for key in ("recipe_id", "id", "name"):
            recipe_id = _canonical_recipe_id(value.get(key))
            if recipe_id:
                ids.append(recipe_id)
        return ids
    if isinstance(value, list):
        for entry in value:
            ids.extend(_recipe_ids_from_value(entry))
    return ids


def known_recipe_ids(state: dict[str, Any] | None = None, player: dict[str, Any] | None = None) -> list[str]:
    """Return sorted known recipe ids from compatible state/player shapes."""

    state = _safe_dict(state)
    player = _safe_dict(player or state.get("player"))
    crafting = _safe_dict(state.get("crafting"))
    ids: set[str] = set()
    for source in (
        crafting.get("known_recipes"),
        crafting.get("recipes"),
        state.get("known_recipes"),
        player.get("known_recipes"),
    ):
        ids.update(_recipe_ids_from_value(source))
    return sorted(ids)


def _item_tags(item: dict[str, Any]) -> set[str]:
    tags: set[Any] = set()
    for key in ("tags", "flavor_tags", "theme_tags", "properties"):
        tags.update(_norm(tag) for tag in _safe_list(item.get(key)) if _text(tag))
    for key in ("item_id", "id", "name", "label", "display_name", "item_type", "type"):
        if _text(item.get(key)):
            tags.add(_norm(item.get(key)))
    return tags


def recipe_ids_from_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return catalog recipe discoveries implied by one inventory item."""

    item = _safe_dict(item)
    discoveries: list[dict[str, Any]] = []
    explicit_sources = (
        item.get("teaches_recipes"),
        item.get("recipe_ids"),
        item.get("unlocks_recipes"),
        _safe_dict(item.get("crafting")).get("teaches_recipes"),
    )
    for source in explicit_sources:
        for recipe_id in _recipe_ids_from_value(source):
            discoveries.append(_known_entry(recipe_id, source="inventory_item", detail=display_item_name(item)))

    tags = _item_tags(item)
    joined = " ".join(sorted(tags))
    for recipe_id, hints in RECIPE_HINTS.items():
        if recipe_id in {entry["recipe_id"] for entry in discoveries}:
            continue
        if any(hint in joined for hint in hints):
            discoveries.append(_known_entry(recipe_id, source="inventory_hint", detail=display_item_name(item)))
    return discoveries


def recipe_ids_from_affordances(state: dict[str, Any]) -> list[dict[str, Any]]:
    affordances = _safe_dict(_safe_dict(state).get("narrative_affordances"))
    discoveries: list[dict[str, Any]] = []
    for bucket, entries in affordances.items():
        for entry in _safe_list(entries):
            entry_dict = _safe_dict(entry)
            tag = _norm(entry_dict.get("tag") or entry_dict.get("affordance"))
            for recipe_id, hints in RECIPE_HINTS.items():
                if any(hint in tag for hint in hints):
                    discoveries.append(_known_entry(recipe_id, source=f"affordance:{bucket}", detail=tag))
    return discoveries


def discover_recipes(state: dict[str, Any], player: dict[str, Any] | None = None) -> dict[str, Any]:
    """Discover recipe ids without mutating state."""

    state = _safe_dict(state)
    player = _safe_dict(player or state.get("player"))
    known = set(known_recipe_ids(state, player))
    inventory, inventory_trace = normalize_inventory_items(_safe_list(player.get("inventory")))
    candidates: list[dict[str, Any]] = []
    for item in inventory:
        candidates.extend(recipe_ids_from_item(item))
    candidates.extend(recipe_ids_from_affordances(state))

    discovered: list[dict[str, Any]] = []
    seen = set(known)
    for candidate in candidates:
        recipe_id = _canonical_recipe_id(candidate.get("recipe_id"))
        if not recipe_id or recipe_id in seen:
            continue
        seen.add(recipe_id)
        discovered.append({**candidate, "recipe_id": recipe_id})

    trace = {
        "event": "recipe_discovery_checked",
        "mechanics_source": "engine_recipe_discovery_v1",
        "known_before": sorted(known),
        "discovered": deepcopy(discovered),
        "inventory_normalized": inventory_trace.get("changed", False),
    }
    return {"ok": True, "known_before": sorted(known), "discovered": discovered, "trace": trace}


def apply_recipe_discovery(state: dict[str, Any], player: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist newly discovered recipe ids into state["crafting"]."""

    state = _safe_dict(state)
    player = _safe_dict(player or state.get("player"))
    result = discover_recipes(state, player)
    crafting = _safe_dict(state.get("crafting"))
    existing_entries = [_known_entry(recipe_id, source="existing") for recipe_id in known_recipe_ids(state, player)]
    entries_by_id = {entry["recipe_id"]: entry for entry in existing_entries}
    for entry in _safe_list(result.get("discovered")):
        recipe_id = _canonical_recipe_id(_safe_dict(entry).get("recipe_id"))
        if recipe_id:
            entries_by_id[recipe_id] = _safe_dict(entry)
    crafting["known_recipes"] = [entries_by_id[recipe_id] for recipe_id in sorted(entries_by_id)]
    traces = _safe_list(crafting.get("recipe_discovery_traces"))
    trace = deepcopy(_safe_dict(result.get("trace")))
    trace["known_after"] = sorted(entries_by_id)
    crafting["recipe_discovery_traces"] = [trace, *traces][:50]
    state["crafting"] = crafting
    return {**result, "known_after": sorted(entries_by_id), "trace": trace}
