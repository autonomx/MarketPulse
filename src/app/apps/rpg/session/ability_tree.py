"""Ability identity inference, tree construction, and validation."""
from __future__ import annotations

from typing import Any, cast

from app.apps.rpg.session.ability_catalog import ABILITY_TEMPLATES, BUILD_IDENTITY_DEFAULTS, GENRE_CLASS_NAMES, GENRE_NORMALIZATION, TEMPLATE_FAMILIES
from app.apps.rpg.session.ability_models import (
    ALLOWED_CAPABILITIES,
    ALLOWED_COST_RESOURCES,
    ALLOWED_DIMENSIONS,
    ALLOWED_EFFECT_OPS,
    ALLOWED_KINDS,
    ALLOWED_POWER_SOURCES,
    ALLOWED_PURPOSES,
    Capability,
    PowerSource,
    RpgAbilityDefinition,
    RpgAbilityValidationResult,
    RpgCharacterIdentity,
)
from app.apps.rpg.session.ability_utils import _is_plain_int, _non_empty_strings, _norm
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def normalize_genre(value: Any) -> str:
    return GENRE_NORMALIZATION.get(_norm(value) or "classic_fantasy", _norm(value) or "classic_fantasy")


def normalize_capability(value: Any, fallback: Capability = "recon") -> Capability:
    raw = _norm(value)
    return cast(Capability, raw) if raw in ALLOWED_CAPABILITIES else fallback


def normalize_power_source(value: Any, fallback: PowerSource = "mundane") -> PowerSource:
    raw = _norm(value)
    return cast(PowerSource, raw) if raw in ALLOWED_POWER_SOURCES else fallback


def _template_capability(value: Capability, fallback: Capability = "recon") -> Capability:
    if value in ABILITY_TEMPLATES:
        return value
    return fallback if fallback in ABILITY_TEMPLATES else "recon"


def _unique_template_capabilities(
    values: list[Capability],
    *,
    primary: Capability | None = None,
    limit: int = 3,
) -> list[Capability]:
    """Return unique secondary capabilities that have distinct ability templates.

    ``custom`` is a valid schema capability, but it currently reuses the recon
    template fallback. Treating repeated custom/unknown capability labels as real
    tree categories can duplicate every recon ability ID and make session creation
    fail validation. Keep identity inputs deterministic by retaining only concrete,
    template-backed capabilities once.
    """

    seen: set[Capability] = set()
    if primary is not None:
        seen.add(primary)
    unique: list[Capability] = []
    for value in values:
        if value in seen or value not in ABILITY_TEMPLATES:
            continue
        unique.append(value)
        seen.add(value)
        if len(unique) >= limit:
            break
    return unique


def infer_character_identity(request_payload: dict[str, Any], build_id: str = "balanced_adventurer") -> dict[str, Any]:
    player = _safe_dict(request_payload.get("player"))
    defaults = BUILD_IDENTITY_DEFAULTS.get(build_id, BUILD_IDENTITY_DEFAULTS["balanced_adventurer"])
    genre = normalize_genre(request_payload.get("genre") or request_payload.get("campaign_template"))
    primary = _template_capability(normalize_capability(request_payload.get("primary_capability"), defaults["primary_capability"]))
    secondary = [normalize_capability(value, "custom") for value in _safe_list(request_payload.get("secondary_capabilities"))]
    if not secondary:
        secondary = list(defaults.get("secondary_capabilities", []))
    secondary = _unique_template_capabilities(secondary, primary=primary, limit=3)
    power_source = normalize_power_source(request_payload.get("power_source"), defaults["power_source"])
    class_name = str(request_payload.get("generated_class_name") or "").strip() or GENRE_CLASS_NAMES.get((genre, primary, power_source)) or defaults.get("class_name") or primary.title()
    summary = str(request_payload.get("generated_class_summary") or "").strip() or f"A {class_name} whose abilities alter deterministic gameplay dimensions."
    return RpgCharacterIdentity(
        genre=genre,
        tone=str(request_payload.get("tone") or "heroic adventure"),
        background=str(player.get("background") or request_payload.get("background") or "Wanderer"),
        primary_capability=primary,
        secondary_capabilities=secondary,
        power_source=power_source,
        generated_class_name=class_name,
        generated_class_summary=summary,
    ).model_dump(mode="json")


def _ability(data: dict[str, Any], *, capability: str, power_source: str) -> dict[str, Any]:
    payload = dict(data)
    payload.setdefault("kind", "active")
    payload.setdefault("capability", capability)
    payload.setdefault("power_source", power_source)
    payload.setdefault("level_required", 1)
    payload.setdefault("rank", 1)
    payload.setdefault("max_rank", 3)
    payload.setdefault("resource_cost", {})
    payload.setdefault("cooldown_turns", 0)
    payload.setdefault("prerequisites", [])
    payload.setdefault("targeting", {})
    payload.setdefault("flavor_tags", [])
    return RpgAbilityDefinition.model_validate(payload).model_dump(mode="json")


def _category_label(capability: str, power_source: str) -> str:
    if capability == "technical" and power_source == "technology":
        return "Systems & Devices"
    labels = {"combat": "Combat Discipline", "recon": "Recon & Position", "influence": "Leverage & Influence", "survival": "Survival Craft", "knowledge": "Knowledge & Signs", "support": "Support & Recovery"}
    return labels.get(capability, capability.replace("_", " ").title())


def _family_bonus_abilities(family: dict[str, Any], *, capability: str, power_source: str) -> list[dict[str, Any]]:
    passive_id, passive_name, hook, passive_description = family["passive"]
    trait_id, trait_name, influence_tags = family["trait"]
    return [
        _ability({"ability_id": passive_id, "kind": "passive", "name": passive_name, "icon": "+", "description": passive_description, "purpose": "utility", "dimensions": ["information", "position"], "resource_cost": {}, "cooldown_turns": 0, "max_rank": 3, "hooks": [hook], "effect_ops": []}, capability=capability, power_source=power_source),
        _ability({"ability_id": trait_id, "kind": "narrative_trait", "name": trait_name, "icon": "*", "description": "A grounded background trait that unlocks deterministic story affordances.", "purpose": "information_gathering", "dimensions": ["information", "relationships", "narrative"], "resource_cost": {}, "cooldown_turns": 0, "max_rank": 1, "influence_tags": list(influence_tags), "effect_ops": []}, capability=capability, power_source=power_source),
    ]


def build_ability_tree(identity: dict[str, Any], *, seed: int | None = None) -> dict[str, Any]:
    primary = _template_capability(normalize_capability(identity.get("primary_capability"), "recon"))
    secondary = _unique_template_capabilities(
        [normalize_capability(value, "custom") for value in _safe_list(identity.get("secondary_capabilities"))],
        primary=primary,
        limit=2,
    )
    power_source = normalize_power_source(identity.get("power_source"), "mundane")
    genre = normalize_genre(identity.get("genre"))
    family = TEMPLATE_FAMILIES.get((genre, primary, power_source))
    capabilities = [primary, *secondary]
    categories: list[dict[str, Any]] = []
    all_abilities: list[dict[str, Any]] = []
    for capability in capabilities:
        abilities = [_ability(data, capability=capability, power_source=power_source) for data in ABILITY_TEMPLATES.get(capability, ABILITY_TEMPLATES["recon"])]
        if capability == primary and family:
            abilities.extend(_family_bonus_abilities(family, capability=capability, power_source=power_source))
        category_name = str(family.get("category_name")) if family and capability == primary else _category_label(capability, power_source)
        categories.append({"category_id": capability, "name": category_name, "capability": capability, "dimensions": sorted({dimension for ability in abilities for dimension in ability.get("dimensions", [])}), "abilities": [ability["ability_id"] for ability in abilities]})
        all_abilities.extend(abilities)
    starting_unlocks = [ability["ability_id"] for ability in all_abilities if ability.get("kind") == "active" and int(ability.get("level_required") or 1) <= 1]
    recommended_hotbar = [ability_id for ability_id in starting_unlocks[:6]]
    tree_id = f"ability_tree_{genre}_{primary}_{power_source}_v1"
    if seed is not None:
        tree_id = f"{tree_id}_{abs(int(seed)) % 100000:05d}"
    tree = {
        "tree_id": tree_id,
        "version": 1,
        "source": "template_family_v1" if family else "template_capability_v1",
        "template_family": family.get("family_id") if family else None,
        "class_name": str(identity.get("generated_class_name") or (family or {}).get("class_name") or primary.title()),
        "genre": genre,
        "primary_capability": primary,
        "secondary_capabilities": secondary,
        "power_source": power_source,
        "dimensions": sorted({dimension for ability in all_abilities for dimension in ability.get("dimensions", [])}),
        "categories": categories,
        "abilities": all_abilities,
        "starting_unlocks": starting_unlocks,
        "recommended_hotbar": recommended_hotbar,
        "design_rule": "Every active ability must alter at least one gameplay dimension through validated deterministic operations.",
    }
    validation = validate_ability_tree(tree)
    if not validation.ok:
        raise ValueError("Invalid ability tree: " + "; ".join(validation.errors))
    return tree


def build_initial_ability_state(tree: dict[str, Any], *, level: int = 1) -> dict[str, Any]:
    abilities = _safe_list(tree.get("abilities"))
    unlocked = [ability["ability_id"] for ability in abilities if ability.get("kind") == "active" and int(ability.get("level_required") or 1) <= level]
    ranks = {ability_id: 1 for ability_id in unlocked}
    recommended = [ability_id for ability_id in _safe_list(tree.get("recommended_hotbar")) if ability_id in unlocked]
    hotbar_ids = [*recommended, *[ability_id for ability_id in unlocked if ability_id not in recommended]][:6]
    hotbar = {str(index + 1): ability_id for index, ability_id in enumerate(hotbar_ids)}
    return {"ability_points": max(0, int(level) - 1), "unlocked": unlocked, "ranks": ranks, "cooldowns": {}, "active_effects": [], "hotbar": hotbar}


def build_progression_package(request_payload: dict[str, Any], *, build_id: str, level: int = 1, seed: int | None = None) -> dict[str, Any]:
    identity = infer_character_identity(request_payload, build_id=build_id)
    tree = build_ability_tree(identity, seed=seed)
    ability_state = build_initial_ability_state(tree, level=level)
    return {"character_identity": identity, "ability_tree": tree, "ability_state": ability_state, "hotbar": ability_state["hotbar"]}


def validate_ability(ability: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ability_id = str(ability.get("ability_id") or "").strip()
    kind = str(ability.get("kind") or "active")
    dimensions = _safe_list(ability.get("dimensions"))
    effect_ops = _safe_list(ability.get("effect_ops"))
    resource_cost = ability.get("resource_cost")
    cooldown_turns = ability.get("cooldown_turns", 0)
    level_required = ability.get("level_required", 1)
    rank = ability.get("rank", 1)
    max_rank = ability.get("max_rank", 3)
    prerequisites = ability.get("prerequisites", [])
    if not ability_id:
        errors.append("missing ability_id")
    if kind not in ALLOWED_KINDS:
        errors.append(f"{ability_id}: unsupported kind {kind}")
    if ability.get("capability") not in ALLOWED_CAPABILITIES:
        errors.append(f"{ability_id}: unsupported capability {ability.get('capability')}")
    if ability.get("power_source") not in ALLOWED_POWER_SOURCES:
        errors.append(f"{ability_id}: unsupported power_source {ability.get('power_source')}")
    if ability.get("purpose") not in ALLOWED_PURPOSES:
        errors.append(f"{ability_id}: unsupported purpose {ability.get('purpose')}")
    if not dimensions or any(dimension not in ALLOWED_DIMENSIONS for dimension in dimensions):
        errors.append(f"{ability_id}: invalid dimensions")
    if kind == "active" and not effect_ops:
        errors.append(f"{ability_id}: active ability has no effect_ops")
    if kind == "narrative_trait" and not effect_ops:
        if not (_non_empty_strings(ability.get("influence_tags")) or _non_empty_strings(ability.get("hooks"))):
            errors.append(f"{ability_id}: narrative_trait without effect_ops requires deterministic influence_tags or hooks")
    if not isinstance(resource_cost, dict):
        errors.append(f"{ability_id}: invalid resource_cost")
    else:
        for resource, cost in resource_cost.items():
            if resource not in ALLOWED_COST_RESOURCES:
                errors.append(f"{ability_id}: invalid cost resource {resource}")
            if not _is_plain_int(cost) or cost < 0:
                errors.append(f"{ability_id}: invalid cost value for {resource}")
    if not _is_plain_int(cooldown_turns) or cooldown_turns < 0:
        errors.append(f"{ability_id}: invalid cooldown_turns")
    if not _is_plain_int(level_required) or level_required < 1:
        errors.append(f"{ability_id}: invalid level_required")
    if not _is_plain_int(rank) or rank < 1:
        errors.append(f"{ability_id}: invalid rank")
    if not _is_plain_int(max_rank) or max_rank < 1:
        errors.append(f"{ability_id}: invalid max_rank")
    if _is_plain_int(rank) and _is_plain_int(max_rank) and rank > max_rank:
        errors.append(f"{ability_id}: rank exceeds max_rank")
    if not isinstance(prerequisites, list):
        errors.append(f"{ability_id}: invalid prerequisites")
    else:
        seen_prerequisites: set[str] = set()
        for prerequisite in prerequisites:
            if not isinstance(prerequisite, str) or not prerequisite.strip():
                errors.append(f"{ability_id}: invalid prerequisite {prerequisite}")
                continue
            if prerequisite == ability_id:
                errors.append(f"{ability_id}: prerequisite cannot reference itself")
            if prerequisite in seen_prerequisites:
                errors.append(f"{ability_id}: duplicate prerequisite {prerequisite}")
            seen_prerequisites.add(prerequisite)
    for op in effect_ops:
        record = _safe_dict(op)
        if record.get("dimension") not in ALLOWED_DIMENSIONS:
            errors.append(f"{ability_id}: effect has invalid dimension")
        if record.get("dimension") not in dimensions:
            errors.append(f"{ability_id}: effect dimension missing from ability dimensions")
        if record.get("op") not in ALLOWED_EFFECT_OPS:
            errors.append(f"{ability_id}: unsupported effect op {record.get('op')}")
    return errors


def validate_ability_tree(tree: dict[str, Any]) -> RpgAbilityValidationResult:
    errors: list[str] = []
    seen: set[str] = set()
    for ability in _safe_list(tree.get("abilities")):
        ability_id = str(_safe_dict(ability).get("ability_id") or "")
        if ability_id in seen:
            errors.append(f"duplicate ability_id {ability_id}")
        seen.add(ability_id)
        errors.extend(validate_ability(_safe_dict(ability)))
    for ability in _safe_list(tree.get("abilities")):
        for prerequisite in _safe_list(_safe_dict(ability).get("prerequisites")):
            if prerequisite not in seen:
                errors.append(f"{ability.get('ability_id')}: missing prerequisite {prerequisite}")
    return RpgAbilityValidationResult(ok=not errors, errors=errors)
