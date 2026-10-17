"""Versioned editorial dossier contracts for generated world entities.

Dossiers organize and explain canonical data for reading. They never own entity
identity, mechanics, or reference integrity. Compact summaries remain suitable
for catalogues while ordered sections power long-form lore pages.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract', "1", "Two or three sentences for catalogue cards and search results.")
_PROMPT_2 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_2', "1", "Keep mechanics and reference fields outside editorial prose.")
_PROMPT_3 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_3', "1", "Avoid repeating the same paragraph across sections.")
_PROMPT_4 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_4', "1", "Use readable prose rather than field-label fragments.")
_PROMPT_5 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_5', "1", "Use the supplied section titles; never substitute numbered labels such as 'Section 1'.")
_PROMPT_6 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_6', "1", "Use only IDs present in dependencies or this topic.")
_PROMPT_7 = prompt_template('rpg.session_genesis_world_forge_dossiers.dossier_prompt_contract_7', "1", "One to three substantial paragraphs grounded in canon.")

DOSSIER_SCHEMA_VERSION = "rpg_world_entity_dossier_v1"

SectionSpec = tuple[str, str, tuple[str, ...]]

_SECTION_TEMPLATES: Mapping[str, tuple[SectionSpec, ...]] = {
    "setting_rules": (
        ("overview", "Overview", ("description", "summary", "rule")),
        ("foundations", "Foundations", ("foundations", "origins", "principles")),
        (
            "lived_experience",
            "Lived Experience",
            ("observable_consequences", "daily_life", "social_effects"),
        ),
        ("boundaries", "Boundaries and Constraints", ("boundaries", "limits", "exceptions")),
        ("consequences", "Consequences", ("consequences", "conflicts", "pressures")),
    ),
    "regions": (
        ("overview", "Overview", ("description", "summary")),
        ("geography", "Geography", ("geography", "terrain", "climate")),
        ("history", "History", ("history", "origins")),
        ("peoples", "Peoples and Settlements", ("peoples", "cultures", "settlements")),
        ("powers", "Powers and Conflicts", ("factions", "faction_ids", "conflicts")),
        ("landmarks", "Landmarks and Travel", ("landmarks", "locations", "location_ids", "routes")),
        ("dangers", "Dangers and Opportunities", ("dangers", "hazards", "hooks")),
    ),
    "locations": (
        ("overview", "Overview", ("description", "purpose")),
        ("geography", "Geography and Setting", ("geography", "region_id", "location_type")),
        ("atmosphere", "Atmosphere", ("sensory_profile", "atmosphere")),
        ("history", "History", ("history", "origin")),
        ("inhabitants", "Inhabitants", ("inhabitants", "npc_ids", "faction_ids")),
        ("landmarks", "Landmarks", ("landmarks", "points_of_interest")),
        ("dangers", "Dangers", ("dangers", "hazards", "threats")),
        ("secrets", "Secrets", ("secrets",)),
        ("hooks", "Adventure Hooks", ("hooks",)),
        ("connections", "Connections", ("routes", "neighbor_ids", "region_id")),
    ),
    "points_of_interest": (
        ("overview", "Overview", ("description", "purpose")),
        ("appearance", "Appearance and Atmosphere", ("sensory_profile", "appearance")),
        ("history", "History", ("history", "origin")),
        ("significance", "Significance", ("significance", "function")),
        ("dangers", "Dangers and Complications", ("dangers", "complications")),
        ("hooks", "Adventure Hooks", ("hooks",)),
        ("connections", "Connections", ("location_id", "region_id", "faction_ids")),
    ),
    "places": (
        ("overview", "Overview", ("description", "summary", "purpose")),
        ("setting", "Setting and Role", ("region_id", "location_type", "registry_role", "function")),
        ("access", "Access and Security", ("access_routes", "security_protocols", "access_restrictions")),
        ("atmosphere", "Atmosphere and Evidence", ("observable_evidence", "sensory_profile", "appearance", "atmosphere")),
        ("pressures", "Current Pressures", ("current_pressure", "pressures", "conflicts", "threats")),
        ("distinction", "Why It Matters", ("registry_distinction", "significance", "secrets", "hooks")),
    ),
    "npcs": (
        ("overview", "Overview", ("description", "role", "personality")),
        ("appearance", "Appearance", ("appearance",)),
        ("personality", "Personality", ("personality", "mannerisms")),
        ("backstory", "Backstory", ("backstory", "history")),
        ("goals", "Goals and Motives", ("goals", "motives")),
        ("relationships", "Relationships", ("relationships", "faction_ids", "location_id")),
        ("knowledge", "Knowledge and Secrets", ("known_facts", "secrets")),
        ("speech", "Speech Style", ("speech_style",)),
        ("situation", "Current Situation", ("current_situation", "mobility_status")),
    ),
    "races": (
        ("overview", "Overview", ("description", "summary")),
        ("origin", "Origin", ("origin", "origins", "history")),
        ("appearance", "Appearance", ("appearance", "physical_traits")),
        ("culture", "Culture", ("cultures", "traditions", "values")),
        ("abilities", "Abilities", ("abilities", "traits")),
        ("lifecycle", "Lifecycle and Lifespan", ("lifecycle", "lifespan")),
        ("society", "Society", ("society", "organization", "homelands")),
        ("strengths", "Strengths and Weaknesses", ("strengths", "weaknesses")),
        ("figures", "Notable Figures", ("notable_figures", "npc_ids")),
        ("connections", "Related Entries", ("homelands", "faction_ids", "class_ids")),
    ),
    "classes": (
        ("overview", "Overview", ("description", "role")),
        ("tradition", "Tradition and Philosophy", ("tradition", "philosophy")),
        ("training", "Training", ("training", "institution_ids")),
        ("capabilities", "Capabilities", ("capabilities",)),
        ("progression", "Progression", ("progression",)),
        ("equipment", "Equipment", ("equipment",)),
        ("society", "Place in Society", ("social_role", "reputation")),
        ("limitations", "Limitations", ("limitations", "weaknesses")),
    ),
    "factions": (
        ("overview", "Overview", ("description", "summary")),
        ("origins", "Origins", ("origins", "history")),
        ("ideology", "Ideology and Values", ("ideology", "values")),
        ("organization", "Organization", ("organization", "structure")),
        ("leadership", "Leadership", ("leadership", "leader_ids", "npc_ids")),
        ("territory", "Territory and Resources", ("territory", "region_ids", "location_ids", "resources")),
        ("relations", "Allies and Rivals", ("allies", "rivals", "relationships")),
        ("goals", "Goals", ("goals",)),
        ("methods", "Methods", ("methods", "tactics")),
        ("secrets", "Secrets", ("secrets",)),
    ),
    "monsters": (
        ("overview", "Overview", ("description", "summary")),
        ("appearance", "Appearance", ("appearance",)),
        ("habitat", "Habitat", ("habitats", "region_ids")),
        ("behaviour", "Behaviour", ("behaviour", "behavior")),
        ("abilities", "Abilities", ("abilities",)),
        ("weaknesses", "Weaknesses", ("weaknesses",)),
        ("ecology", "Ecology", ("ecology", "diet", "lifecycle")),
        ("legends", "Legends and Lore", ("legends", "history")),
        ("encounters", "Encounter Guidance", ("encounter_guidance", "tactics", "threat_level")),
    ),
    "items": (
        ("overview", "Overview", ("description", "summary")),
        ("appearance", "Appearance", ("appearance", "materials")),
        ("origin", "Origin", ("origin", "origin_ids", "history")),
        ("properties", "Properties and Effects", ("effects", "properties")),
        ("use", "Use and Limitations", ("uses", "limitations", "costs")),
        ("ownership", "Ownership and Reputation", ("owners", "owner_ids", "reputation")),
    ),
    "spells": (
        ("overview", "Overview", ("description", "summary")),
        ("principle", "Magical Principle", ("principle", "school")),
        ("casting", "Casting", ("casting", "costs", "range")),
        ("effects", "Effects", ("effects",)),
        ("limitations", "Limitations and Risks", ("limitations", "risks")),
        ("history", "History and Practitioners", ("history", "institution_ids", "practitioner_ids")),
    ),
    "feats": (
        ("overview", "Overview", ("description", "summary")),
        ("requirements", "Prerequisites", ("prerequisites", "class_ids")),
        ("benefits", "Benefits", ("benefits",)),
        ("practice", "Practice and Expression", ("practice", "training")),
        ("limitations", "Limitations", ("limitations",)),
    ),
    "quests": (
        ("overview", "Overview", ("description", "premise", "stakes")),
        ("background", "Background", ("background", "history")),
        ("objectives", "Objectives", ("objectives",)),
        ("people", "People and Factions", ("giver_id", "npc_ids", "faction_ids")),
        ("locations", "Locations", ("location_ids",)),
        ("complications", "Complications", ("complications", "risks")),
        ("outcomes", "Outcomes and Rewards", ("outcomes", "rewards")),
    ),
    "encounter_seeds": (
        ("overview", "Overview", ("description", "setup")),
        ("situation", "Situation", ("situation", "setup")),
        ("actors", "Actors and Threats", ("actor_ids", "threat_ids")),
        ("complications", "Complications", ("complications",)),
        ("outcomes", "Possible Outcomes", ("outcomes",)),
    ),
    "one_shots": (
        ("overview", "Overview", ("description", "premise")),
        ("cast", "Cast and Locations", ("actor_ids", "location_ids")),
        ("beats", "Story Beats", ("beats",)),
        ("choices", "Key Choices", ("choices", "complications")),
        ("outcomes", "Outcomes and Rewards", ("outcomes", "rewards")),
    ),
    "opening_scenarios": (
        ("overview", "Overview", ("description", "premise")),
        ("opening", "Opening Situation", ("opening", "starting_location_id")),
        ("cast", "Initial Cast", ("initial_npc_ids",)),
        ("threads", "Opening Threads", ("opening_seed_ids", "objectives")),
        ("resources", "Starting Resources", ("starting_resources",)),
        ("stakes", "Stakes and Direction", ("stakes", "outcomes")),
    ),
}

_GENERIC_TEMPLATE: tuple[SectionSpec, ...] = (
    ("overview", "Overview", ("description", "summary", "purpose", "premise")),
    ("history", "History and Context", ("history", "origin", "origins", "backstory")),
    ("details", "Details", ("details", "traits", "values", "goals", "effects")),
    ("connections", "Connections", ("relationships", "related_entity_ids")),
)

# The profile-based forge uses these domain IDs while the authoring card variants
# use the corresponding canonical dossier shapes.
_SECTION_TEMPLATE_ALIASES: Mapping[str, str] = {
    "actors": "npcs",
    "groups": "factions",
    "technology_augmentations": "items",
    "equipment_vehicles": "items",
    "roles_archetypes": "classes",
    "threats": "monsters",
}

_QUICK_FACT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Lifespan", "lifespan"),
    ("Homeland", "homeland"),
    ("Region", "region_id"),
    ("Location", "location_id"),
    ("Threat", "threat_level"),
    ("Rarity", "rarity"),
    ("Type", "item_type"),
    ("Value", "value"),
    ("School", "school"),
    ("Tier", "tier"),
    ("Range", "range"),
    ("Mobility", "mobility_status"),
    ("Visibility", "visibility"),
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_NON_ID = re.compile(r"[^a-z0-9]+")
_PLACEHOLDER_SECTION_TITLE = re.compile(
    r"^(?:section|part)(?:[\s._:-]*(?:\d+|[ivxlcdm]+))?$",
    re.IGNORECASE,
)


def text(value: Any, fallback: str = "") -> str:
    return str(value).strip() if value is not None and str(value).strip() else fallback


def _section_id(value: Any, fallback: str) -> str:
    candidate = _NON_ID.sub("-", text(value).casefold()).strip("-")
    return candidate or fallback


def _display(value: Any) -> str:
    if value in (None, "", [], (), {}):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Mapping):
        label = value.get("label") or value.get("name") or value.get("resource") or value.get("type") or value.get("id")
        amount = value.get("value") or value.get("amount") or value.get("count") or value.get("quantity")
        if label is not None and amount is not None:
            return f"{_display(label)}: {_display(amount)}"
        if label is not None:
            return _display(label)
        return "; ".join(
            f"{str(key).replace('_', ' ').title()}: {_display(item)}"
            for key, item in value.items()
            if _display(item)
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return ", ".join(item for item in (_display(row) for row in value) if item)
    rendered = text(value)
    if ":" in rendered and " " not in rendered:
        rendered = rendered.split(":", 1)[-1]
    return rendered.replace("_", " ")


def _paragraphs(value: Any) -> list[str]:
    """Normalize prose while preserving every explicit paragraph boundary."""
    if value in (None, "", [], (), {}):
        return []
    if isinstance(value, Mapping):
        rendered = []
        for key, item in value.items():
            item_text = _display(item)
            if item_text:
                rendered.append(f"{str(key).replace('_', ' ').title()}: {item_text}.")
        return rendered
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        paragraphs: list[str] = []
        for item in value:
            if isinstance(item, str):
                paragraphs.extend(
                    part.strip()
                    for part in re.split(r"\n\s*\n", item)
                    if part.strip()
                )
            else:
                paragraph = _display(item)
                if paragraph:
                    paragraphs.append(paragraph.rstrip(".") + ".")
        return paragraphs
    raw = text(value)
    return [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]


def compact_summary(value: Any, *, limit: int = 420) -> str:
    raw = " ".join(text(value).split())
    if not raw:
        return "No overview has been written yet."
    sentences = [sentence.strip() for sentence in _SENTENCE.split(raw) if sentence.strip()]
    candidate = " ".join(sentences[:2]) if sentences else raw
    if len(candidate) <= limit:
        return candidate
    return candidate[: max(1, limit - 1)].rstrip(" ,;:-") + "…"


def _linked_document(content: Mapping[str, Any], entity_id: str) -> Mapping[str, Any]:
    documents = content.get("documents")
    if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
        return {}
    for value in documents:
        if not isinstance(value, Mapping):
            continue
        references = {str(item) for item in value.get("entities") or ()}
        if entity_id in references:
            return value
    return {}


def _source_summary(row: Mapping[str, Any], content: Mapping[str, Any], entity_id: str) -> str:
    document = _linked_document(content, entity_id)
    for value in (
        row.get("short_summary"), row.get("summary"), row.get("description"),
        document.get("summary_120"), document.get("summary_500"), document.get("full_text"),
        row.get("personality"), row.get("sensory_profile"), row.get("purpose"),
        row.get("premise"), row.get("setup"),
    ):
        rendered = text(value)
        if rendered:
            return compact_summary(rendered)
    return "No overview has been written yet."


def _normalize_quote(value: Any) -> dict[str, str] | None:
    if isinstance(value, Mapping):
        quote_text = text(value.get("text") or value.get("quote"))
        if quote_text:
            return {"text": quote_text, "attribution": text(value.get("attribution") or value.get("source"))}
    quote_text = text(value)
    return {"text": quote_text, "attribution": ""} if quote_text else None


def _normalize_quick_facts(value: Any, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            label = text(item.get("label") or item.get("name"))
            fact_value = item.get("value")
            if label and fact_value not in (None, "", [], {}):
                facts.append({"label": label, "value": fact_value})
    if facts:
        return facts[:12]
    for label, field in _QUICK_FACT_FIELDS:
        fact_value = row.get(field)
        if fact_value not in (None, "", [], {}):
            facts.append({"label": label, "value": fact_value})
    return facts[:8]


def _section_title(value: Any, *, index: int, card_type: str) -> str:
    """Return a readable heading when a provider supplies a numbered placeholder."""

    title = text(value)
    if title and not _PLACEHOLDER_SECTION_TITLE.fullmatch(title):
        return title
    template = _section_template(card_type)
    if index < len(template):
        return template[index][1]
    return f"Additional Details {index - len(template) + 1}"


def _section_template(card_type: str) -> tuple[SectionSpec, ...]:
    return _SECTION_TEMPLATES.get(
        _SECTION_TEMPLATE_ALIASES.get(card_type, card_type),
        _GENERIC_TEMPLATE,
    )


def _normalize_sections(value: Any, *, card_type: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    sections: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        title = _section_title(item.get("title"), index=index, card_type=card_type)
        section_id = _section_id(item.get("id") or title, f"section-{index + 1}")
        base_id = section_id
        collision = 2
        while section_id in used:
            section_id = f"{base_id}-{collision}"
            collision += 1
        paragraphs = _paragraphs(item.get("paragraphs") or item.get("body") or item.get("text"))
        if not paragraphs:
            continue
        used.add(section_id)
        sections.append({"id": section_id, "title": title, "paragraphs": paragraphs[:6]})
    return sections


def placeholder_section_title_count(value: Any) -> int:
    """Count blank or numbered placeholder titles in a stored raw dossier."""

    if not isinstance(value, Mapping):
        return 0
    sections = value.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)):
        return 0
    return sum(
        1
        for section in sections
        if isinstance(section, Mapping)
        and (
            not text(section.get("title"))
            or bool(_PLACEHOLDER_SECTION_TITLE.fullmatch(text(section.get("title"))))
        )
    )


def _fallback_sections(
    row: Mapping[str, Any],
    *,
    card_type: str,
    content: Mapping[str, Any],
    entity_id: str,
    short_summary: str,
) -> list[dict[str, Any]]:
    template = _section_template(card_type)
    document = _linked_document(content, entity_id)
    sections: list[dict[str, Any]] = []
    consumed: set[str] = set()
    for section_id, title, fields in template:
        paragraphs: list[str] = []
        for field in fields:
            source = row.get(field)
            if source in (None, "", [], (), {}):
                continue
            consumed.add(field)
            paragraphs.extend(_paragraphs(source))
        if section_id == "overview":
            full_text = text(document.get("full_text") or document.get("body"))
            if full_text:
                paragraphs = [*_paragraphs(full_text), *paragraphs]
            if not paragraphs:
                paragraphs = [short_summary]
            elif compact_summary(paragraphs[0]) != short_summary:
                paragraphs.insert(0, short_summary)
        paragraphs = list(dict.fromkeys(paragraph for paragraph in paragraphs if paragraph))
        if paragraphs:
            sections.append({"id": section_id, "title": title, "paragraphs": paragraphs[:6]})

    if len(sections) < 2:
        excluded = {
            "id", "entity_id", "name", "title", "label", "kind", "summary",
            "short_summary", "description", "dossier", "visibility", "schema_version",
            *consumed,
        }
        extra = []
        for key, value in row.items():
            if key in excluded or value in (None, "", [], (), {}) or key.endswith(("_hash", "_version")):
                continue
            rendered = _display(value)
            if rendered:
                extra.append(f"{key.replace('_', ' ').title()}: {rendered}.")
        if extra:
            sections.append({"id": "canon-details", "title": "Canon Details", "paragraphs": extra[:6]})
    return sections or [{"id": "overview", "title": "Overview", "paragraphs": [short_summary]}]


def _related_entity_ids(row: Mapping[str, Any], explicit: Any, entity_id: str) -> list[str]:
    values: list[str] = []
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        values.extend(text(item) for item in explicit)
    for key, value in row.items():
        if key == "id" or not (key.endswith("_id") or key.endswith("_ids") or key in {"homelands", "relationships"}):
            continue
        source = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
        for item in source:
            if isinstance(item, Mapping):
                item = item.get("id") or item.get("entity_id")
            candidate = text(item)
            if ":" in candidate:
                values.append(candidate)
    return list(dict.fromkeys(value for value in values if value and value != entity_id))[:40]


def project_entity_dossier(
    row: Mapping[str, Any],
    *,
    card_type: str,
    content: Mapping[str, Any] | None = None,
    entity_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """Return compact catalogue prose plus a normalized long-form dossier."""
    source = dict(row)
    canon = dict(content or {})
    resolved_id = entity_id or text(source.get("id") or source.get("entity_id"))
    short_summary = _source_summary(source, canon, resolved_id)
    raw_dossier = source.get("dossier")
    raw = dict(raw_dossier) if isinstance(raw_dossier, Mapping) else {}
    sections = _normalize_sections(raw.get("sections"), card_type=card_type)
    generated_from_legacy = not bool(sections)
    if not sections:
        sections = _fallback_sections(
            source,
            card_type=card_type,
            content=canon,
            entity_id=resolved_id,
            short_summary=short_summary,
        )
    dossier = {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "subtitle": text(raw.get("subtitle") or source.get("subtitle")),
        "quote": _normalize_quote(raw.get("quote") or source.get("quote")),
        "quick_facts": _normalize_quick_facts(raw.get("quick_facts"), source),
        "sections": sections,
        "related_entity_ids": _related_entity_ids(
            source,
            raw.get("related_entity_ids") or source.get("related_entity_ids"),
            resolved_id,
        ),
        "generated_from_legacy": generated_from_legacy,
    }
    return short_summary, dossier


def validate_entity_dossier(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ("dossier_must_be_object",)
    issues: list[str] = []
    if text(value.get("schema_version")) != DOSSIER_SCHEMA_VERSION:
        issues.append("unsupported_dossier_schema")
    sections = value.get("sections")
    if not isinstance(sections, Sequence) or isinstance(sections, (str, bytes)) or not sections:
        issues.append("dossier_sections_required")
        return tuple(issues)
    ids: set[str] = set()
    for index, item in enumerate(sections):
        if not isinstance(item, Mapping):
            issues.append(f"invalid_section:{index}")
            continue
        section_id = text(item.get("id"))
        if not section_id:
            issues.append(f"section_id_required:{index}")
        elif section_id in ids:
            issues.append(f"duplicate_section_id:{section_id}")
        ids.add(section_id)
        if not text(item.get("title")):
            issues.append(f"section_title_required:{index}")
        paragraphs = item.get("paragraphs")
        if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)) or not paragraphs:
            issues.append(f"section_paragraphs_required:{section_id or index}")
    return tuple(dict.fromkeys(issues))


def dossier_prompt_contract(topic_id: str) -> dict[str, Any]:
    template = _section_template(topic_id)
    importance = "minor" if topic_id in {"feats", "items", "spells"} else "standard"
    return {
        "schema_version": DOSSIER_SCHEMA_VERSION,
        "entity_fields": {
            "short_summary": _PROMPT_1.text,
            "dossier": {
                "schema_version": DOSSIER_SCHEMA_VERSION,
                "subtitle": "Optional evocative subtitle.",
                "quote": {"text": "Optional in-world quotation.", "attribution": "Speaker or source."},
                "quick_facts": [{"label": "Readable label", "value": "Canonical value"}],
                "sections": [
                    {
                        "id": section_id,
                        "title": title,
                        "paragraphs": [_PROMPT_7.text],
                    }
                    for section_id, title, _fields in template
                ],
                "related_entity_ids": [_PROMPT_6.text],
            },
        },
        "content_targets": {
            "importance": importance,
            "major_words": "700-1400",
            "standard_words": "350-800",
            "minor_or_mechanical_words": "150-400",
            "paragraphs_per_substantive_section": "1-3",
        },
        "rules": [
            _PROMPT_2.text,
            "Do not change or invent unresolved canonical IDs.",
            _PROMPT_3.text,
            _PROMPT_4.text,
            _PROMPT_5.text,
        ],
    }
