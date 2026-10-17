"""Stable page, card, and rich dossier schemas for reusable-world authoring."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_dossiers import project_entity_dossier

SYSTEM_SECTIONS: tuple[dict[str, Any], ...] = (
    {"id": "overview", "label": "Overview", "group": "workspace", "page_kind": "document"},
    {"id": "generation", "label": "World Generation", "group": "workspace", "page_kind": "document"},
    {"id": "images", "label": "Images", "group": "workspace", "page_kind": "collection"},
    {"id": "map", "label": "Map", "group": "world", "page_kind": "document"},
    {"id": "scenarios", "label": "Scenarios", "group": "game-master", "page_kind": "collection"},
    {"id": "map_blueprints", "label": "Map Blueprints", "group": "game-master", "page_kind": "collection"},
    {"id": "validation", "label": "Validation", "group": "game-master", "page_kind": "document"},
    {"id": "releases", "label": "Releases", "group": "game-master", "page_kind": "collection"},
    {"id": "history_revisions", "label": "Revision History", "group": "game-master", "page_kind": "collection"},
    {"id": "advanced", "label": "Advanced", "group": "game-master", "page_kind": "document"},
)

WORLD_COLLECTION_CATEGORIES = {
    "regions",
    "factions",
    "locations",
    "npcs",
    "points_of_interest",
    "races",
    "classes",
    "monsters",
    "items",
    "spells",
    "feats",
    "places",
    "groups",
    "cultures",
    "actors",
    "networks",
    "technology_augmentations",
    "equipment_vehicles",
    "roles_archetypes",
    "threats",
}
LORE_COLLECTION_CATEGORIES = {"economy_law", "pressures"}
GAME_MASTER_COLLECTION_CATEGORIES = {
    "quests",
    "encounter_seeds",
    "one_shots",
    "opening_threads",
    "opening_scenarios",
}
COLLECTION_CATEGORIES = (
    WORLD_COLLECTION_CATEGORIES
    | LORE_COLLECTION_CATEGORIES
    | GAME_MASTER_COLLECTION_CATEGORIES
)
PIPELINE_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}

_SECTION_LABELS = {
    "realm": "Realm Overview",
    "locations": "Areas",
    "npcs": "Characters",
    "actors": "Actors and NPCs",
    "places": "Places and Points of Interest",
    "groups": "Organisations and Institutions",
    "one_shots": "One-Shots",
    "opening_scenarios": "Opening Scenarios",
    "encounter_seeds": "Encounter Seeds",
    "points_of_interest": "Points of Interest",
    "history_revisions": "Revision History",
    "map_blueprints": "Map Blueprints",
}

_CARD_SPECS: Mapping[str, Mapping[str, Any]] = {
    "regions": {
        "eyebrow": "Area / Region",
        "badges": ("visibility",),
        "highlights": (("Realm", "realm_id"),),
    },
    "locations": {
        "eyebrow": "Area",
        "badges": ("dossier_status", "visibility"),
        "highlights": (("Region", "region_id"),),
    },
    "places": {
        "eyebrow": "Place / Point of Interest",
        "badges": ("dossier_status", "visibility"),
        "highlights": (("Region", "region_id"), ("Parent", "parent_place_id")),
        "groups": (("Access Routes", "access_routes", "list"),),
    },
    "points_of_interest": {
        "eyebrow": "Point of Interest",
        "badges": ("visibility",),
        "highlights": (("Location", "location_id"), ("Region", "region_id")),
        "groups": (("Hooks", "hooks", "list"),),
    },
    "npcs": {
        "eyebrow": "Character",
        "badges": ("dossier_status", "visibility"),
        "highlights": (
            ("Location", "location_id"),
            ("Mobility", "mobility_status"),
            ("Speech", "speech_style"),
        ),
        "groups": (
            ("Factions", "faction_ids", "chips"),
            ("Goals", "goals", "list"),
            ("Motives", "motives", "chips"),
        ),
    },
    "actors": {
        "eyebrow": "Actor / NPC",
        "badges": ("dossier_status", "visibility"),
        "highlights": (("Location", "location_id"), ("Goal", "goal")),
        "groups": (
            ("Groups", "group_ids", "chips"),
            ("Reaction Conditions", "reaction_conditions", "list"),
            ("Knowledge Limits", "knowledge_limits", "list"),
        ),
    },
    "races": {
        "eyebrow": "Race / Ancestry",
        "badges": ("visibility",),
        "highlights": (("Lifespan", "lifespan"),),
        "groups": (
            ("Homelands", "homelands", "chips"),
            ("Cultures", "cultures", "chips"),
            ("Traits", "traits", "list"),
            ("Languages", "languages", "chips"),
        ),
    },
    "cultures": {
        "eyebrow": "Culture / Subculture",
        "badges": ("visibility",),
        "groups": (
            ("Regions", "region_ids", "chips"),
            ("Groups", "group_ids", "chips"),
            ("Values", "values", "list"),
            ("Customs", "customs", "list"),
        ),
    },
    "classes": {
        "eyebrow": "Class / Discipline",
        "badges": ("visibility",),
        "groups": (
            ("Capabilities", "capabilities", "list"),
            ("Progression", "progression", "chips"),
            ("Equipment", "equipment", "chips"),
            ("Institutions", "institution_ids", "chips"),
        ),
    },
    "roles_archetypes": {
        "eyebrow": "Role / Archetype",
        "badges": ("visibility",),
        "groups": (
            ("Capabilities", "capabilities", "list"),
            ("Progression", "progression", "list"),
            ("Equipment", "equipment_ids", "chips"),
            ("Groups", "group_ids", "chips"),
        ),
    },
    "factions": {
        "eyebrow": "Faction",
        "badges": ("visibility",),
        "groups": (("Values", "values", "chips"), ("Goals", "goals", "list")),
    },
    "groups": {
        "eyebrow": "Organisation / Institution",
        "badges": ("visibility",),
        "highlights": (("Objective", "current_objective"),),
        "groups": (
            ("Controlled Places", "controlled_place_ids", "chips"),
            ("Resources", "resources", "list"),
            ("Internal Divisions", "internal_divisions", "list"),
        ),
    },
    "networks": {
        "eyebrow": "Network / Digital Space",
        "badges": ("visibility",),
        "highlights": (("Security Pressure", "security_pressure"),),
        "groups": (
            ("Controllers", "controller_group_ids", "chips"),
            ("Access Conditions", "access_conditions", "list"),
        ),
    },
    "monsters": {
        "eyebrow": "Monster / Creature",
        "badges": ("threat_level", "visibility"),
        "groups": (
            ("Regions", "region_ids", "chips"),
            ("Habitats", "habitats", "chips"),
            ("Abilities", "abilities", "list"),
            ("Weaknesses", "weaknesses", "list"),
        ),
    },
    "threats": {
        "eyebrow": "Threat / Hostile Entity",
        "badges": ("threat_level", "visibility"),
        "highlights": (("Behaviour", "behaviour"),),
        "groups": (
            ("Places", "place_ids", "chips"),
            ("Groups", "group_ids", "chips"),
            ("Capabilities", "capabilities", "list"),
            ("Weaknesses", "weaknesses", "list"),
        ),
    },
    "items": {
        "eyebrow": "Item / Relic",
        "badges": ("item_type", "rarity"),
        "highlights": (("Value", "value"),),
        "groups": (("Effects", "effects", "list"), ("Origins", "origin_ids", "chips")),
    },
    "technology_augmentations": {
        "eyebrow": "Technology / Augmentation",
        "badges": ("visibility",),
        "highlights": (("Capability", "capability"), ("Cost", "cost")),
        "groups": (("Sources", "source_group_ids", "chips"), ("Failure Mode", "failure_mode", "list")),
    },
    "equipment_vehicles": {
        "eyebrow": "Equipment / Vehicle / Commodity",
        "badges": ("visibility",),
        "highlights": (("Function", "function"), ("Availability", "availability"), ("Cost", "cost")),
        "groups": (("Producers", "producer_group_ids", "chips"), ("Limitations", "limitations", "list")),
    },
    "spells": {
        "eyebrow": "Spell / Ritual",
        "badges": ("school", "tier"),
        "highlights": (("Range", "range"),),
        "groups": (
            ("Costs", "costs", "list"),
            ("Effects", "effects", "list"),
            ("Institutions", "institution_ids", "chips"),
        ),
    },
    "feats": {
        "eyebrow": "Feat / Talent",
        "badges": ("visibility",),
        "groups": (
            ("Prerequisites", "prerequisites", "list"),
            ("Benefits", "benefits", "list"),
            ("Limitations", "limitations", "list"),
            ("Classes", "class_ids", "chips"),
        ),
    },
    "economy_law": {
        "eyebrow": "Economy / Law / Service",
        "badges": ("visibility",),
        "highlights": (("Failure Effect", "failure_effect"),),
        "groups": (
            ("Controllers", "controller_group_ids", "chips"),
            ("Affected Places", "affected_place_ids", "chips"),
            ("Rules", "rules", "list"),
            ("Services", "services", "list"),
        ),
    },
    "pressures": {
        "eyebrow": "Conflict / Pressure",
        "badges": ("visibility",),
        "highlights": (("Current State", "current_state"), ("Next Change", "next_tick_change")),
        "groups": (
            ("Actors", "actor_ids", "chips"),
            ("Groups", "group_ids", "chips"),
            ("Places", "place_ids", "chips"),
        ),
    },
    "quests": {
        "eyebrow": "Quest",
        "badges": ("visibility",),
        "highlights": (("Giver", "giver_id"), ("Stakes", "stakes")),
        "groups": (
            ("Locations", "location_ids", "chips"),
            ("Factions", "faction_ids", "chips"),
            ("Objectives", "objectives", "list"),
            ("Rewards", "rewards", "chips"),
        ),
    },
    "encounter_seeds": {
        "eyebrow": "Encounter Seed",
        "badges": ("visibility",),
        "groups": (
            ("Locations", "location_ids", "chips"),
            ("Actors", "actor_ids", "chips"),
            ("Threats", "threat_ids", "chips"),
            ("Complications", "complications", "list"),
            ("Outcomes", "outcomes", "list"),
        ),
    },
    "opening_threads": {
        "eyebrow": "Opening Thread",
        "badges": ("visibility",),
        "groups": (
            ("Actors", "actor_ids", "chips"),
            ("Places", "place_ids", "chips"),
            ("Pressures", "pressure_ids", "chips"),
            ("Player Choices", "player_choices", "list"),
        ),
    },
    "one_shots": {
        "eyebrow": "One-Shot",
        "badges": ("visibility",),
        "groups": (
            ("Locations", "location_ids", "chips"),
            ("Characters", "actor_ids", "chips"),
            ("Quests", "quest_ids", "chips"),
            ("Beats", "beats", "chips"),
            ("Rewards", "rewards", "chips"),
        ),
    },
    "opening_scenarios": {
        "eyebrow": "Opening Scenario",
        "badges": ("visibility",),
        "highlights": (("Starting Location", "starting_location_id"), ("Starting Place", "starting_place_id")),
        "groups": (
            ("Initial Characters", "initial_npc_ids", "chips"),
            ("Initial Actors", "initial_actor_ids", "chips"),
            ("Opening Seeds", "opening_seed_ids", "chips"),
            ("Opening Threads", "opening_thread_ids", "chips"),
            ("Starting Resources", "starting_resources", "list"),
        ),
    },
    "scenarios": {
        "eyebrow": "Scenario",
        "badges": ("status",),
        "highlights": (("World", "world_id"),),
    },
    "map_blueprints": {
        "eyebrow": "Map Blueprint",
        "badges": ("status",),
        "highlights": (("Map", "map_id"), ("Revision", "blueprint_revision")),
    },
    "releases": {
        "eyebrow": "World Release",
        "highlights": (("Release", "release"), ("World Revision", "world_revision")),
    },
    "history_revisions": {
        "eyebrow": "World Revision",
        "highlights": (("Revision", "revision"), ("Content Hash", "content_hash")),
    },
}

_SUMMARY_FIELDS: Mapping[str, tuple[str, ...]] = {
    "points_of_interest": ("purpose", "sensory_profile", "description"),
    "places": ("current_pressure", "description", "sensory_profile"),
    "npcs": ("personality", "appearance", "backstory", "description"),
    "actors": ("goal", "current_pressure", "personality", "description"),
    "locations": ("sensory_profile", "description"),
    "races": ("description", "lifespan"),
    "cultures": ("description", "values"),
    "classes": ("description",),
    "roles_archetypes": ("description", "capabilities"),
    "monsters": ("description",),
    "threats": ("behaviour", "description"),
    "items": ("description",),
    "technology_augmentations": ("capability", "failure_mode", "description"),
    "equipment_vehicles": ("function", "availability", "description"),
    "networks": ("security_pressure", "description"),
    "economy_law": ("failure_effect", "description"),
    "pressures": ("current_state", "next_tick_change", "description"),
    "spells": ("description",),
    "feats": ("description",),
    "quests": ("stakes", "description"),
    "encounter_seeds": ("setup", "description"),
    "opening_threads": ("initial_evidence", "description"),
    "one_shots": ("premise", "description"),
    "opening_scenarios": ("premise", "description"),
}


def text(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value is not None and str(value).strip() else fallback


def rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]


def section_label(topic_id: str, fallback: str = "") -> str:
    return _SECTION_LABELS.get(topic_id, text(fallback, topic_id.replace("_", " ").title()))


def section_group(category: str) -> str:
    if category in PIPELINE_CATEGORIES or category in GAME_MASTER_COLLECTION_CATEGORIES:
        return "game-master"
    if category in WORLD_COLLECTION_CATEGORIES:
        return "world"
    return "lore"


def section_page_kind(category: str) -> str:
    return "collection" if category in COLLECTION_CATEGORIES else "document"


def _identity(row: Mapping[str, Any], card_type: str, index: int) -> str:
    return text(
        row.get("id")
        or row.get("entity_id")
        or row.get("location_id")
        or row.get("npc_id")
        or row.get("faction_id")
        or row.get("scenario_id")
        or row.get("map_id")
        or row.get("release_hash")
        or row.get("content_hash"),
        f"{card_type}:{index + 1}",
    )


def _title(row: Mapping[str, Any], entity_id: str, card_type: str) -> str:
    document = raw_document if isinstance(raw_document := row.get("document"), Mapping) else {}
    dossier = raw_dossier if isinstance(raw_dossier := row.get("dossier"), Mapping) else {}
    quick_facts = rows(dossier.get("quick_facts"))
    readable_label = next(
        (
            text(fact.get("value"))
            for fact in quick_facts
            if text(fact.get("label")).lower() in {"readable label", "display name"}
            and text(fact.get("value"))
        ),
        "",
    )
    identifier = entity_id.split(":", 1)[-1].replace("_", " ").title()
    fallback = section_label(card_type) if identifier.isdecimal() else identifier
    return text(
        row.get("name")
        or row.get("title")
        or row.get("label")
        or row.get("scenario_name")
        or document.get("title")
        or readable_label
        or dossier.get("title")
        or dossier.get("name")
        or dossier.get("subtitle"),
        fallback if ":" in entity_id else f"{section_label(card_type)} {entity_id}",
    )


def _linked_summary(content: Mapping[str, Any], entity_id: str) -> str:
    for document in rows(content.get("documents")):
        references = {str(value) for value in document.get("entities") or ()}
        if entity_id not in references:
            continue
        summary = text(
            document.get("summary_120")
            or document.get("summary_500")
            or document.get("full_text")
            or document.get("body")
        )
        if summary:
            return summary
    for fact in rows(content.get("facts")):
        references = {str(value) for value in fact.get("entity_refs") or ()}
        if entity_id in references:
            summary = text(fact.get("content") or fact.get("object"))
            if summary:
                return summary
    return ""


def _summary(row: Mapping[str, Any], card_type: str, content: Mapping[str, Any], entity_id: str) -> str:
    candidates = (
        "short_summary",
        "summary",
        "description",
        "role",
        *_SUMMARY_FIELDS.get(card_type, ()),
        "personality",
        "sensory_profile",
        "premise",
        "setup",
    )
    for field in candidates:
        value = text(row.get(field))
        if value:
            return value
    return _linked_summary(content, entity_id) or "No summary yet."


def _items(value: Any) -> list[Any]:
    if value in (None, "", [], (), {}):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, Mapping):
        return [{"label": str(key), "value": item} for key, item in value.items()]
    return [value]


def _presentation(row: Mapping[str, Any], card_type: str) -> dict[str, Any]:
    spec = dict(_CARD_SPECS.get(card_type) or {})
    badges = [row.get(field) for field in spec.get("badges") or () if row.get(field) not in (None, "", [], {})]
    highlights = [
        {"label": label, "value": row.get(field)}
        for label, field in spec.get("highlights") or ()
        if row.get(field) not in (None, "", [], {})
    ]
    groups = [
        {"label": label, "items": items, "style": style}
        for label, field, style in spec.get("groups") or ()
        if (items := _items(row.get(field)))
    ]
    return {
        "variant": card_type,
        "eyebrow": text(spec.get("eyebrow"), section_label(card_type)),
        "badges": badges,
        "highlights": highlights,
        "groups": groups,
    }


def entity_card(
    row: Mapping[str, Any],
    *,
    card_type: str,
    kind: str,
    index: int,
    content: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entity_id = _identity(row, card_type, index)
    source = dict(row)
    canon = dict(content or {})
    short_summary, dossier = project_entity_dossier(
        source,
        card_type=card_type,
        content=canon,
        entity_id=entity_id,
    )
    if short_summary == "No overview has been written yet.":
        short_summary = _summary(source, card_type, canon, entity_id)
    return {
        "id": entity_id,
        "title": _title(source, entity_id, card_type),
        "summary": short_summary,
        "short_summary": short_summary,
        "dossier": dossier,
        "kind": kind,
        "card_type": card_type,
        "image_target_id": f"{kind}:{entity_id}",
        "presentation": _presentation(source, card_type),
        "metadata": source,
    }
