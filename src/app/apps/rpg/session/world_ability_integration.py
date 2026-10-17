"""Bridge world-scale ability templates into normal RPG loadout flows.

This module keeps N127 world effects usable from the same Ability UI as normal
active abilities. It does not let AI-authored text execute mechanics: world
abilities still use validated deterministic world-effect operations.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.ability_system import DEFAULT_SKILL_XP_PER_ABILITY_USE, grant_skill_xp, tick_ability_state
from app.apps.rpg.session.world_effects import WORLD_SCALE_EFFECT_OPS, apply_world_scale_ability_to_state, build_world_scale_ability_templates
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

WORLD_SCALE_TEMPLATE_VERSION = "world_scale_templates_v1"
WORLD_SCALE_DEFAULT_LEVEL = 5
WORLD_SCALE_DEFAULT_COOLDOWN = 8


def _utc_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold().replace(" ", "_")


def _append(target: dict[str, Any], key: str, value: dict[str, Any], limit: int = 20) -> None:
    values = _safe_list(target.get(key))
    values.insert(0, value)
    target[key] = values[:limit]


def _ability_state(state: dict[str, Any]) -> dict[str, Any]:
    ability_state = _safe_dict(state.get("ability_state"))
    ability_state.setdefault("ability_points", 0)
    ability_state.setdefault("unlocked", [])
    ability_state.setdefault("ranks", {})
    ability_state.setdefault("cooldowns", {})
    ability_state.setdefault("active_effects", [])
    state["ability_state"] = ability_state
    return ability_state


def _player(state: dict[str, Any]) -> dict[str, Any]:
    player = _safe_dict(state.get("player"))
    state["player"] = player
    return player


def _resource_metric(player: dict[str, Any], resource: str) -> dict[str, Any]:
    resources = _safe_dict(player.get("resources"))
    player["resources"] = resources
    metric = _safe_dict(resources.get(resource))
    metric.setdefault("current", 0)
    metric.setdefault("max", metric.get("current", 0))
    resources[resource] = metric
    return metric


def _tree_capabilities(tree: dict[str, Any]) -> set[str]:
    capabilities: set[str] = set()
    for key in ("primary_capability",):
        value = str(tree.get(key) or "").strip()
        if value:
            capabilities.add(value)
    for value in _safe_list(tree.get("secondary_capabilities")):
        text = str(value or "").strip()
        if text:
            capabilities.add(text)
    for category in _safe_list(tree.get("categories")):
        text = str(_safe_dict(category).get("capability") or "").strip()
        if text:
            capabilities.add(text)
    for ability in _safe_list(tree.get("abilities")):
        text = str(_safe_dict(ability).get("capability") or "").strip()
        if text:
            capabilities.add(text)
    return capabilities


def _world_category_for(tree: dict[str, Any], capability: str) -> dict[str, Any]:
    categories = _safe_list(tree.get("categories"))
    for category in categories:
        record = _safe_dict(category)
        if record.get("category_id") == f"{capability}_world" or (record.get("capability") == capability and record.get("name") == "World Influence"):
            return record
    category = {"category_id": f"{capability}_world", "name": "World Influence", "capability": capability, "dimensions": [], "abilities": []}
    categories.append(category)
    tree["categories"] = categories
    return category


def _prepare_world_ability(template: dict[str, Any]) -> dict[str, Any]:
    ability = deepcopy(template)
    ability.setdefault("kind", "active")
    ability.setdefault("icon", "♜")
    ability.setdefault("level_required", WORLD_SCALE_DEFAULT_LEVEL)
    ability.setdefault("rank", 1)
    ability.setdefault("max_rank", 1)
    ability.setdefault("resource_cost", {})
    ability.setdefault("cooldown_turns", WORLD_SCALE_DEFAULT_COOLDOWN)
    ability.setdefault("prerequisites", [])
    ability.setdefault("targeting", {"scope": "world"})
    flavor_tags = {str(tag) for tag in _safe_list(ability.get("flavor_tags"))}
    flavor_tags.add("world_scale")
    ability["flavor_tags"] = sorted(flavor_tags)
    ability["world_scale"] = True
    ability["source"] = WORLD_SCALE_TEMPLATE_VERSION
    return ability


def is_world_scale_ability(ability: dict[str, Any] | None) -> bool:
    record = _safe_dict(ability)
    if not record:
        return False
    if record.get("world_scale") is True or "world_scale" in {str(tag) for tag in _safe_list(record.get("flavor_tags"))}:
        return True
    return any(str(_safe_dict(op).get("op") or "") in WORLD_SCALE_EFFECT_OPS for op in _safe_list(record.get("effect_ops")))


def ensure_world_scale_abilities(state: dict[str, Any]) -> bool:
    """Append matching world-scale templates to a session ability tree.

    The helper is idempotent. It adds high-level active abilities only for
    capabilities already present in the tree, so a political character can see
    Broker Truce, a technical character can see Sabotage Supply Line, and a
    survival character can see Found Safehouse.
    """
    tree = _safe_dict(state.get("ability_tree"))
    if not tree:
        return False
    capabilities = _tree_capabilities(tree)
    abilities = _safe_list(tree.get("abilities"))
    existing_ids = {str(_safe_dict(ability).get("ability_id")) for ability in abilities}
    added = False
    for template in build_world_scale_ability_templates():
        capability = str(template.get("capability") or "")
        ability_id = str(template.get("ability_id") or "")
        if not capability or capability not in capabilities or not ability_id or ability_id in existing_ids:
            continue
        ability = _prepare_world_ability(template)
        abilities.append(ability)
        existing_ids.add(ability_id)
        category = _world_category_for(tree, capability)
        category_abilities = [str(value) for value in _safe_list(category.get("abilities"))]
        if ability_id not in category_abilities:
            category_abilities.append(ability_id)
        category["abilities"] = category_abilities
        category_dimensions = {str(value) for value in _safe_list(category.get("dimensions"))}
        category_dimensions.update(str(value) for value in _safe_list(ability.get("dimensions")))
        category["dimensions"] = sorted(category_dimensions)
        added = True
    if added:
        tree["abilities"] = abilities
        tree_dimensions = {str(value) for value in _safe_list(tree.get("dimensions"))}
        for ability in abilities:
            tree_dimensions.update(str(value) for value in _safe_list(_safe_dict(ability).get("dimensions")))
        tree["dimensions"] = sorted(tree_dimensions)
        tree["world_scale_template_version"] = WORLD_SCALE_TEMPLATE_VERSION
        state["ability_tree"] = tree
    return added


def find_ability_in_state(state: dict[str, Any], *, ability_name: str | None = None, hotbar_slot: str | int | None = None) -> dict[str, Any] | None:
    tree = _safe_dict(state.get("ability_tree"))
    abilities = {_safe_dict(ability).get("ability_id"): _safe_dict(ability) for ability in _safe_list(tree.get("abilities"))}
    ability_state = _ability_state(state)
    hotbar = _safe_dict(state.get("hotbar")) or _safe_dict(ability_state.get("hotbar"))
    if hotbar_slot is not None:
        ability_id = str(hotbar.get(str(hotbar_slot)) or "")
        if ability_id in abilities:
            return abilities[ability_id]
    wanted = _norm(ability_name)
    if not wanted:
        return None
    for ability in abilities.values():
        if _norm(ability.get("ability_id")) == wanted or _norm(ability.get("name")) == wanted:
            return ability
    return next((ability for ability in abilities.values() if wanted in _norm(ability.get("ability_id")) or wanted in _norm(ability.get("name"))), None)


def apply_world_scale_loadout_ability(state: dict[str, Any], *, ability_name: str | None = None, hotbar_slot: str | int | None = None, target: str | None = None) -> dict[str, Any]:
    ensure_world_scale_abilities(state)
    ability = find_ability_in_state(state, ability_name=ability_name, hotbar_slot=hotbar_slot)
    if not is_world_scale_ability(ability):
        return {"handled": False}
    ability = _safe_dict(ability)
    ability_id = str(ability.get("ability_id") or "")
    ability_state = _ability_state(state)
    unlocked = {str(value) for value in _safe_list(ability_state.get("unlocked"))}
    if ability_id not in unlocked:
        return {"handled": True, "ok": False, "error": "ability_locked", "detail": f"{ability.get('name')} is not unlocked yet.", "ability_id": ability_id}
    cooldowns = _safe_dict(ability_state.get("cooldowns"))
    if _safe_int(cooldowns.get(ability_id)) > 0:
        return {"handled": True, "ok": False, "error": "ability_on_cooldown", "detail": f"{ability.get('name')} is on cooldown for {cooldowns[ability_id]} more turn(s).", "ability_id": ability_id}

    player = _player(state)
    cost_parts: list[str] = []
    for resource, cost in _safe_dict(ability.get("resource_cost")).items():
        metric = _resource_metric(player, str(resource))
        current = _safe_int(metric.get("current"))
        if current < _safe_int(cost):
            return {"handled": True, "ok": False, "error": "insufficient_resource", "detail": f"{ability.get('name')} requires {cost} {resource}, but only {current}/{metric.get('max')} is available.", "ability_id": ability_id}
    for resource, cost in _safe_dict(ability.get("resource_cost")).items():
        metric = _resource_metric(player, str(resource))
        metric["current"] = max(0, _safe_int(metric.get("current")) - _safe_int(cost))
        cost_parts.append(f"{resource}: {metric['current']}/{metric.get('max')}")

    result = apply_world_scale_ability_to_state(state, ability, target=target)
    if not result.ok:
        return {"handled": True, "ok": False, "error": "world_scale_ability_failed", "detail": result.detail, "ability_id": ability_id, "effects": result.effects, "errors": result.errors}

    grant_skill_xp(state, str(ability.get("capability") or "world"), DEFAULT_SKILL_XP_PER_ABILITY_USE, source=ability_id)
    tick_ability_state(state)
    cooldown = _safe_int(ability.get("cooldown_turns"))
    if cooldown > 0:
        _ability_state(state).setdefault("cooldowns", {})[ability_id] = cooldown
    active_effects = _safe_list(_ability_state(state).get("active_effects"))
    active_effects.insert(
        0,
        {
            "ability_id": ability_id,
            "name": ability.get("name"),
            "rank": 1,
            "dimensions": ability.get("dimensions", []),
            "purpose": ability.get("purpose"),
            "target": target or "the wider world",
            "world_scale": True,
            "created_at": _utc_now(),
        },
    )
    _ability_state(state)["active_effects"] = active_effects[:20]
    cost_detail = f" Costs now {', '.join(cost_parts)}." if cost_parts else ""
    return {
        "handled": True,
        "ok": True,
        "ability_id": ability_id,
        "name": str(ability.get("name") or ability_id),
        "detail": f"You used {ability.get('name')} to change persistent world state.{cost_detail}",
        "effects": result.effects,
    }
