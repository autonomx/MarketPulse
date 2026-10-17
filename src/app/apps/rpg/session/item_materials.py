"""Deterministic material identity, salvage, and AI display-fiction helpers.

Item/crafting rule: the engine owns mechanical material identity, material
roles, quantities, properties, and salvage yield. AI may name and describe what
players see, but it cannot create new mechanical material IDs during play.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

from pydantic import BaseModel, Field
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MATERIAL_ROLES = (
    "metal",
    "wood",
    "cloth",
    "leather",
    "bone",
    "glass",
    "paper",
    "herb",
    "fuel",
    "binding",
    "container",
    "arcane_reagent",
    "technical_component",
    "power_source",
    "lens",
    "medicine",
    "poison",
    "curiosity",
)

MATERIAL_PROPERTIES = (
    "forgeable",
    "burnable",
    "conductive",
    "flexible",
    "sharp",
    "sturdy",
    "fragile",
    "organic",
    "arcane",
    "holy",
    "corrupt",
    "toxic",
    "edible",
    "medicinal",
    "explosive",
    "insulating",
    "reflective",
    "binding",
    "technical",
    "focus",
)

MATERIAL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "iron": {
        "material_id": "iron",
        "family": "ferrous_metal",
        "role": "metal",
        "properties": ["forgeable", "sturdy", "conductive"],
        "rarity": "common",
        "base_display_name": "Iron scrap",
    },
    "steel": {
        "material_id": "steel",
        "family": "ferrous_metal",
        "role": "metal",
        "properties": ["forgeable", "sturdy", "sharp"],
        "rarity": "uncommon",
        "base_display_name": "Steel scrap",
    },
    "silver": {
        "material_id": "silver",
        "family": "precious_metal",
        "role": "metal",
        "properties": ["forgeable", "conductive", "holy", "reflective"],
        "rarity": "uncommon",
        "base_display_name": "Silver filings",
    },
    "wood": {
        "material_id": "wood",
        "family": "plant_fiber",
        "role": "wood",
        "properties": ["burnable", "organic"],
        "rarity": "common",
        "base_display_name": "Wood scrap",
    },
    "cloth": {
        "material_id": "cloth",
        "family": "textile",
        "role": "cloth",
        "properties": ["flexible", "burnable", "binding"],
        "rarity": "common",
        "base_display_name": "Cloth scrap",
    },
    "leather": {
        "material_id": "leather",
        "family": "hide",
        "role": "leather",
        "properties": ["flexible", "organic", "sturdy", "binding"],
        "rarity": "common",
        "base_display_name": "Leather strip",
    },
    "bone": {
        "material_id": "bone",
        "family": "organic_hardpart",
        "role": "bone",
        "properties": ["organic", "sturdy", "sharp"],
        "rarity": "common",
        "base_display_name": "Bone fragment",
    },
    "glass": {
        "material_id": "glass",
        "family": "silicate",
        "role": "glass",
        "properties": ["fragile", "reflective", "sharp"],
        "rarity": "common",
        "base_display_name": "Glass shard",
    },
    "paper": {
        "material_id": "paper",
        "family": "plant_fiber",
        "role": "paper",
        "properties": ["burnable", "organic"],
        "rarity": "common",
        "base_display_name": "Paper scrap",
    },
    "keenleaf": {
        "material_id": "keenleaf",
        "family": "healing_herb",
        "role": "herb",
        "properties": ["organic", "medicinal", "edible"],
        "rarity": "common",
        "base_display_name": "Keenleaf clipping",
    },
    "lamp_oil": {
        "material_id": "lamp_oil",
        "family": "fuel",
        "role": "fuel",
        "properties": ["burnable"],
        "rarity": "common",
        "base_display_name": "Lamp oil",
    },
    "moon_essence": {
        "material_id": "moon_essence",
        "family": "lunar_reagent",
        "role": "arcane_reagent",
        "properties": ["arcane", "focus", "reflective"],
        "rarity": "rare",
        "base_display_name": "Lunar essence",
    },
    "circuitry": {
        "material_id": "circuitry",
        "family": "electronics",
        "role": "technical_component",
        "properties": ["technical", "conductive"],
        "rarity": "common",
        "base_display_name": "Circuitry",
    },
    "power_cell": {
        "material_id": "power_cell",
        "family": "energy_storage",
        "role": "power_source",
        "properties": ["technical", "conductive"],
        "rarity": "uncommon",
        "base_display_name": "Power cell",
    },
    "unknown_reagent": {
        "material_id": "unknown_reagent",
        "family": "unknown",
        "role": "curiosity",
        "properties": [],
        "rarity": "common",
        "base_display_name": "Unidentified reagent",
        "usable_in_recipes": False,
    },
}

ENGINE_OWNED_MATERIAL_FIELDS = {
    "material_id",
    "material_role",
    "role",
    "family",
    "quantity",
    "properties",
    "rarity",
    "quality",
    "usable_in_recipes",
    "recipe_tags",
    "value",
}
AI_FICTION_MATERIAL_FIELDS = {
    "display_name",
    "name",
    "description",
    "visual_prompt",
    "lore",
    "salvage_narration",
    "theme_tags",
}


class RpgMaterialValidationResult(BaseModel):
    ok: bool
    material_id: str | None = None
    error: str | None = None
    detail: str = ""
    warnings: list[str] = Field(default_factory=list)


class RpgMaterialFictionResult(BaseModel):
    ok: bool
    material: dict[str, Any]
    source: str = "ai_material_fiction_proposal_v1"
    ignored_fields: list[str] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class RpgSalvageResult(BaseModel):
    ok: bool
    detail: str = ""
    error: str | None = None
    source_item_id: str | None = None
    source_item_name: str | None = None
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    consumed_items: list[dict[str, Any]] = Field(default_factory=list)
    repairs: list[str] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _norm(value: Any) -> str:
    return _text(value).casefold().replace(" ", "_").replace("-", "_")


def _positive_quantity(value: Any, fallback: int = 1) -> int:
    try:
        return max(1, int(value))
    except Exception:
        return fallback


def _item_id(item: dict[str, Any]) -> str:
    return _text(item.get("id") or item.get("item_id"))


def _item_name(item: dict[str, Any]) -> str:
    return _text(item.get("name") or item.get("display_name") or _item_id(item), "item")


def _item_type(item: dict[str, Any]) -> str:
    return _text(item.get("item_type") or item.get("type"), "supply")


def _tags(item: dict[str, Any]) -> set[str]:
    return {_norm(tag) for tag in _safe_list(item.get("tags")) if _text(tag)}


def canonical_material(material_id: str) -> dict[str, Any]:
    """Return a canonical mechanical material definition or controlled fallback."""

    material_key = _norm(material_id)
    definition = deepcopy(MATERIAL_DEFINITIONS.get(material_key) or MATERIAL_DEFINITIONS["unknown_reagent"])
    if material_key and material_key not in MATERIAL_DEFINITIONS:
        definition.setdefault("aliases", [])
        definition["source_alias"] = material_id
    return definition


def material_stack(material_id: str, quantity: int = 1, *, theme_tags: Sequence[str] | None = None, display_name: str | None = None) -> dict[str, Any]:
    """Create a stack that keeps mechanics canonical while allowing display fiction."""

    definition = canonical_material(material_id)
    stack = {
        "item_id": definition["material_id"],
        "id": definition["material_id"],
        "item_type": "crafting_material",
        "type": "crafting_material",
        "material_id": definition["material_id"],
        "material_role": definition["role"],
        "family": definition.get("family"),
        "quantity": _positive_quantity(quantity),
        "rarity": definition.get("rarity", "common"),
        "quality": "standard",
        "properties": list(definition.get("properties") or []),
        "theme_tags": [_text(tag)[:40] for tag in list(theme_tags or []) if _text(tag)],
        "stackable": True,
        "usable_in_recipes": bool(definition.get("usable_in_recipes", True)),
        "name": display_name or definition.get("base_display_name") or definition["material_id"],
        "display": {
            "name": display_name or definition.get("base_display_name") or definition["material_id"],
        },
        "mechanics_source": "engine_material_identity_v1",
    }
    return stack


def validate_material_stack(stack: dict[str, Any]) -> RpgMaterialValidationResult:
    stack = _safe_dict(stack)
    material_id = _text(stack.get("material_id") or stack.get("id") or stack.get("item_id"))
    if not material_id:
        return RpgMaterialValidationResult(ok=False, error="missing_material_id", detail="Material stack requires a stable material_id.")
    definition = canonical_material(material_id)
    if material_id not in MATERIAL_DEFINITIONS and definition["material_id"] == "unknown_reagent":
        return RpgMaterialValidationResult(ok=False, material_id=material_id, error="unsupported_material_id", detail=f"Unsupported material id: {material_id}")
    role = _text(stack.get("material_role") or stack.get("role"), definition["role"])
    if role not in MATERIAL_ROLES:
        return RpgMaterialValidationResult(ok=False, material_id=material_id, error="unsupported_material_role", detail=f"Unsupported material role: {role}")
    if int(stack.get("quantity") or 0) <= 0:
        return RpgMaterialValidationResult(ok=False, material_id=material_id, error="invalid_material_quantity", detail="Material quantity must be positive.")
    warnings: list[str] = []
    for prop in _safe_list(stack.get("properties")):
        if prop not in MATERIAL_PROPERTIES:
            warnings.append(f"{material_id}: ignored unsupported property {prop}")
    return RpgMaterialValidationResult(ok=True, material_id=material_id, detail="Material stack is valid.", warnings=warnings)


def _genre_material_name(material: dict[str, Any], genre: str | None) -> str:
    material_id = _text(material.get("material_id"))
    base = _text(material.get("name") or _safe_dict(material.get("display")).get("name") or material_id, material_id)
    normalized_genre = _norm(genre)
    if normalized_genre in {"cyberpunk", "sci_fi", "science_fiction"}:
        cyber_names = {
            "iron": "Ferrocarbon Alloy Shards",
            "steel": "Hardened Alloy Fragments",
            "silver": "Conductive Silver Filings",
            "wood": "Composite Handle Splinters",
            "cloth": "Synthetic Cloth Strips",
            "leather": "Polymer Weave Strips",
            "glass": "Optic Glass Shards",
            "paper": "Data-paper Scraps",
            "keenleaf": "Stimulant Herb Clippings",
            "lamp_oil": "Fuel Gel",
            "circuitry": "Reusable Circuitry",
            "power_cell": "Spent Power Cell",
        }
        return cyber_names.get(material_id, base)
    if normalized_genre in {"detective_noir", "modern_occult"}:
        noir_names = {
            "iron": "Usable Scrap Metal",
            "steel": "Tempered Scrap Steel",
            "silver": "Saint-marked Silver Filings",
            "wood": "Dry Wood Splinters",
            "cloth": "Torn Cloth Strips",
            "leather": "Leather Straps",
            "glass": "Bottle Glass Shards",
            "paper": "Torn Paper Scraps",
            "keenleaf": "Bitterroot Clippings",
            "lamp_oil": "Lamp Oil",
        }
        return noir_names.get(material_id, base)
    return base


def suggest_material_display_name(material: dict[str, Any], *, source_item: dict[str, Any] | None = None, genre: str | None = None) -> str:
    """Deterministic fallback display name for AI-free material fiction."""

    display_name = _genre_material_name(material, genre)
    theme_tags = [_norm(tag) for tag in _safe_list(material.get("theme_tags"))]
    if "moonlit" in theme_tags or "lunar" in theme_tags:
        if _text(material.get("material_id")) == "moon_essence":
            return "Crystallized Lunar Essence"
        return f"Moon-Touched {display_name}"
    if "holy" in theme_tags:
        return f"Saint-Blessed {display_name}"
    if "dragon" in theme_tags:
        return f"Dragon-Warmed {display_name}"
    source = _safe_dict(source_item)
    if _norm(source.get("rarity")) in {"rare", "epic", "legendary", "mythic"} and _text(source.get("name")):
        return f"{_text(source.get('name')).split()[0]}-Touched {display_name}"
    return display_name


def apply_material_fiction_proposal(
    material: dict[str, Any],
    proposal: dict[str, Any] | None,
    *,
    genre: str | None = None,
    source_item: dict[str, Any] | None = None,
) -> RpgMaterialFictionResult:
    """Apply AI display fiction without allowing AI to alter material mechanics."""

    compiled = deepcopy(_safe_dict(material))
    proposal = _safe_dict(proposal)
    ignored_fields: list[str] = []
    repairs: list[str] = []
    for key, value in proposal.items():
        if key in ENGINE_OWNED_MATERIAL_FIELDS:
            ignored_fields.append(key)
            repairs.append(f"ignored_engine_owned_field:{key}")
            continue
        if key not in AI_FICTION_MATERIAL_FIELDS:
            ignored_fields.append(key)
            repairs.append(f"ignored_unsupported_fiction_field:{key}")
            continue
        if key in {"display_name", "name"}:
            name = _text(value)
            if not name:
                repairs.append("ignored_blank_display_name")
                continue
            compiled["name"] = name[:100]
            display = _safe_dict(compiled.get("display"))
            display["name"] = name[:100]
            compiled["display"] = display
        elif key == "theme_tags":
            tags = [_text(tag)[:40] for tag in _safe_list(value) if _text(tag)]
            compiled["theme_tags"] = tags[:8]
        elif key == "salvage_narration":
            compiled["salvage_narration"] = _text(value)[:500]
        else:
            display = _safe_dict(compiled.get("display"))
            display[key] = _text(value)[:500]
            compiled["display"] = display
    if not _text(_safe_dict(compiled.get("display")).get("name") or compiled.get("name")):
        suggested = suggest_material_display_name(compiled, source_item=source_item, genre=genre)
        compiled["name"] = suggested
        compiled["display"] = {**_safe_dict(compiled.get("display")), "name": suggested}
        repairs.append("filled_display_name")
    compiled["fiction_source"] = "ai_material_fiction_proposal_v1" if proposal else "deterministic_material_fiction_v1"
    validation = validate_material_stack(compiled)
    if not validation.ok:
        repairs.append(f"fallback_canonical_material:{validation.error}")
        compiled = material_stack(_text(material.get("material_id"), "unknown_reagent"), int(material.get("quantity") or 1), theme_tags=_safe_list(material.get("theme_tags")))
        validation = validate_material_stack(compiled)
    return RpgMaterialFictionResult(ok=validation.ok, material=compiled, ignored_fields=ignored_fields, repairs=repairs, validation=validation.model_dump())


def _material_from_entry(entry: dict[str, Any], *, fallback_theme_tags: Sequence[str] | None = None) -> dict[str, Any]:
    material_id = _text(entry.get("material_id") or entry.get("id") or entry.get("item_id"), "unknown_reagent")
    quantity = _positive_quantity(entry.get("quantity") or entry.get("amount"), 1)
    theme_tags = list(fallback_theme_tags or []) + [_text(tag) for tag in _safe_list(entry.get("theme_tags")) if _text(tag)]
    return material_stack(material_id, quantity, theme_tags=theme_tags, display_name=_text(entry.get("display_name") or entry.get("name")) or None)


def _explicit_salvage_outputs(item: dict[str, Any]) -> list[dict[str, Any]]:
    salvage = item.get("salvage_outputs") or item.get("salvage") or item.get("materials")
    if not isinstance(salvage, list):
        return []
    return [_material_from_entry(_safe_dict(entry), fallback_theme_tags=_safe_list(item.get("theme_tags"))) for entry in salvage if _safe_dict(entry)]


def build_salvage_outputs(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive deterministic salvage outputs from explicit profile or item tags."""

    item = _safe_dict(item)
    explicit = _explicit_salvage_outputs(item)
    if explicit:
        return explicit

    item_type = _item_type(item)
    tags = _tags(item)
    outputs: list[dict[str, Any]] = []
    theme_tags = _safe_list(item.get("theme_tags"))

    def add(material_id: str, quantity: int) -> None:
        outputs.append(material_stack(material_id, quantity, theme_tags=theme_tags))

    if item_type == "weapon":
        weapon_type = _norm(item.get("weapon_type"))
        add("iron", 2 if weapon_type in {"dagger", "wand", "thrown"} else 3)
        if weapon_type in {"sword", "dagger", "axe", "mace", "spear", "bow", "crossbow"}:
            add("leather", 1)
        if weapon_type in {"bow", "crossbow", "staff"}:
            add("wood", 2)
    elif item_type == "armor":
        armor_type = _norm(item.get("armor_type"))
        if armor_type in {"light", "cloak", "robe", "boots", "gloves"}:
            add("leather", 2)
            add("cloth", 1)
        else:
            add("iron", 3)
            add("leather", 1)
    elif item_type in {"clothing", "camping"} or tags & {"cloth", "textile", "fabric", "banner", "bedroll"}:
        add("cloth", 2)
    elif tags & {"wood", "wooden", "chair", "branch"}:
        add("wood", 2)
    elif tags & {"glass", "bottle", "vial"}:
        add("glass", 1)
    elif tags & {"paper", "book", "document"}:
        add("paper", 1)
    elif tags & {"metal", "iron", "scrap"}:
        add("iron", 2)
    elif tags & {"leather", "hide"}:
        add("leather", 1)
    elif tags & {"herb", "plant"}:
        add("keenleaf", 1)
    elif tags & {"circuit", "electronics", "tech"}:
        add("circuitry", 2)
    elif tags & {"power", "battery"}:
        add("power_cell", 1)
    return outputs


def salvage_item(
    item: dict[str, Any],
    *,
    genre: str = "classic_fantasy",
    fiction_proposals: dict[str, dict[str, Any]] | None = None,
) -> RpgSalvageResult:
    """Break an item into deterministic materials with optional AI display fiction."""

    item = _safe_dict(item)
    item_id = _item_id(item)
    item_name = _item_name(item)
    tags = _tags(item)
    if _item_type(item) in {"quest"} or "protected" in tags or item.get("protected") is True:
        return RpgSalvageResult(ok=False, error="protected_item_not_salvageable", detail=f"{item_name} cannot be salvaged.", source_item_id=item_id, source_item_name=item_name)
    outputs = build_salvage_outputs(item)
    if not outputs:
        return RpgSalvageResult(ok=False, error="item_not_salvageable", detail=f"{item_name} has no deterministic salvage profile.", source_item_id=item_id, source_item_name=item_name)

    proposals = fiction_proposals or {}
    decorated_outputs: list[dict[str, Any]] = []
    repairs: list[str] = []
    for output in outputs:
        material_id = _text(output.get("material_id"))
        proposal = proposals.get(material_id) or proposals.get("*") or {}
        result = apply_material_fiction_proposal(output, proposal, genre=genre, source_item=item)
        decorated = result.material
        if not proposal:
            suggested = suggest_material_display_name(decorated, source_item=item, genre=genre)
            decorated["name"] = suggested
            decorated["display"] = {**_safe_dict(decorated.get("display")), "name": suggested}
        decorated_outputs.append(decorated)
        repairs.extend(result.repairs)

    consumed_quantity = 1
    consumed = [{"item_id": item_id, "name": item_name, "quantity": consumed_quantity}]
    trace = {
        "event": "item_salvaged",
        "source_item_id": item_id,
        "source_item_name": item_name,
        "outputs": [
            {
                "material_id": output.get("material_id"),
                "material_role": output.get("material_role"),
                "quantity": output.get("quantity"),
                "display_name": _safe_dict(output.get("display")).get("name") or output.get("name"),
            }
            for output in decorated_outputs
        ],
        "mechanics_source": "engine_salvage_v1",
    }
    return RpgSalvageResult(
        ok=True,
        detail=f"Salvaged {item_name} into {len(decorated_outputs)} material stack(s).",
        source_item_id=item_id,
        source_item_name=item_name,
        outputs=decorated_outputs,
        consumed_items=consumed,
        repairs=repairs,
        trace=trace,
    )
