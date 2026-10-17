"""Structured contracts, fallback generation, and reference normalization for world domains."""
from __future__ import annotations

from dataclasses import dataclass, replace
import random
import re
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_dossiers import project_entity_dossier, validate_entity_dossier
from .world_forge_generation import GeneratedTopic


@dataclass(frozen=True)
class DomainSpec:
    kind: str
    id_prefix: str
    required_scalars: tuple[str, ...]
    required_lists: tuple[str, ...]
    reference_fields: Mapping[str, tuple[str, ...]]


DOMAIN_SPECS: Mapping[str, DomainSpec] = {
    "points_of_interest": DomainSpec(
        "point_of_interest", "poi",
        ("name", "location_id", "region_id", "purpose", "sensory_profile"),
        ("hooks",),
        {"location_id": ("location",), "region_id": ("region",)},
    ),
    "quests": DomainSpec(
        "quest", "quest", ("name", "giver_id", "stakes"),
        ("location_ids", "faction_ids", "objectives", "rewards"),
        {"giver_id": ("npc",), "location_ids": ("location",), "faction_ids": ("faction",)},
    ),
    "monsters": DomainSpec(
        "monster", "monster", ("name", "threat_level"),
        ("region_ids", "habitats", "abilities", "weaknesses"),
        {"region_ids": ("region",)},
    ),
    "items": DomainSpec(
        "item", "item", ("name", "item_type", "rarity", "value"),
        ("effects", "origin_ids"),
        {"origin_ids": ("faction", "location")},
    ),
    "races": DomainSpec(
        "race", "race", ("name", "lifespan"),
        ("homelands", "cultures", "traits", "languages"),
        {"homelands": ("region",)},
    ),
    "classes": DomainSpec(
        "class", "class", ("name",),
        ("capabilities", "progression", "equipment", "institution_ids"),
        {"institution_ids": ("institution", "lore_topic")},
    ),
    "spells": DomainSpec(
        "spell", "spell", ("name", "school", "tier", "range"),
        ("costs", "effects"),
        {"institution_ids": ("institution", "lore_topic")},
    ),
    "feats": DomainSpec(
        "feat", "feat", ("name",),
        ("prerequisites", "benefits", "limitations"),
        {"class_ids": ("class",)},
    ),
    "encounter_seeds": DomainSpec(
        "encounter_seed", "encounter", ("name", "setup"),
        ("location_ids", "actor_ids", "threat_ids", "complications", "outcomes"),
        {"location_ids": ("location",), "actor_ids": ("npc",), "threat_ids": ("monster",)},
    ),
    "one_shots": DomainSpec(
        "one_shot", "one-shot", ("name", "premise"),
        ("location_ids", "actor_ids", "quest_ids", "beats", "rewards"),
        {"location_ids": ("location",), "actor_ids": ("npc",), "quest_ids": ("quest",)},
    ),
    "opening_scenarios": DomainSpec(
        "opening_scenario", "opening", ("name", "starting_location_id", "premise"),
        ("initial_npc_ids", "opening_seed_ids", "starting_resources"),
        {
            "starting_location_id": ("location",),
            "initial_npc_ids": ("npc",),
            "opening_seed_ids": ("quest", "one_shot", "encounter_seed"),
        },
    ),
}

_REFERENCE_HINTS: Mapping[str, tuple[str, ...]] = {
    "institution_ids": ("topic:institutions",),
    "giver_id": ("npc:",),
    "starting_location_id": ("location:",),
}

_WORLD_BRIEF_STOP_WORDS = frozenset(
    {
        "about",
        "after",
        "alternate",
        "before",
        "built",
        "civilization",
        "description",
        "humanity",
        "people",
        "their",
        "there",
        "these",
        "they",
        "this",
        "through",
        "where",
        "which",
        "world",
    }
)


def is_structured_domain(topic_id: str) -> bool:
    return topic_id in DOMAIN_SPECS


def _slug(value: str) -> str:
    return "_".join("".join(ch.casefold() if ch.isalnum() else " " for ch in value).split()) or "entry"


def _known_entities(dependencies: Mapping[str, GeneratedTopic]) -> dict[str, dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    for dependency_topic_id, topic in dependencies.items():
        for entity in topic.entities:
            entity_id = str(entity.get("id") or "")
            if not entity_id:
                continue
            known[entity_id] = {
                **dict(entity),
                "_source_topic_id": dependency_topic_id,
            }
    return known


def _reference_candidates(
    field: str,
    known: Mapping[str, Mapping[str, Any]],
    kinds: Sequence[str],
) -> list[str]:
    allowed = set(kinds)

    def matches_kind(entity_id: str, entity: Mapping[str, Any]) -> bool:
        entity_kind = str(entity.get("kind") or "").rstrip("s")
        source_topic_id = str(entity.get("_source_topic_id") or "").rstrip("s")
        id_prefix = entity_id.split(":", 2)[1] if entity_id.startswith("ent:") else entity_id.split(":", 1)[0]
        return bool(allowed & {entity_kind, source_topic_id, id_prefix.rstrip("s")})

    candidates = sorted(
        entity_id
        for entity_id, entity in known.items()
        if matches_kind(entity_id, entity)
    )
    hints = _REFERENCE_HINTS.get(field, ())
    if hints:
        preferred = [
            entity_id
            for entity_id in candidates
            if any(entity_id == hint or entity_id.startswith(hint) for hint in hints)
        ]
        if preferred:
            return preferred
    return candidates


def _name(node: CampaignTopicNode, index: int) -> str:
    return f"{node.title.rstrip('s')} {index + 1}"


def _world_brief_keywords(campaign_context: Mapping[str, Any]) -> tuple[str, ...]:
    brief = campaign_context.get("world_brief")
    if not isinstance(brief, Mapping):
        return ()
    text = " ".join(
        str(brief.get(field) or "") for field in ("title", "description")
    )
    return tuple(
        sorted(
            {
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9]{4,}", text)
                if token.casefold() not in _WORLD_BRIEF_STOP_WORDS
            }
        )
    )


def _topic_text(topic: GeneratedTopic) -> str:
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for nested in value:
                collect(nested)
        elif isinstance(value, str):
            values.append(value)

    collect(topic.documents)
    collect(topic.entities)
    collect(topic.facts)
    collect(topic.relationships)
    collect(topic.knowledge_rules)
    collect(topic.story_threads)
    return "\n".join(values).casefold()


def validate_world_brief_grounding(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any],
) -> None:
    """Reject placeholders and content that ignores a supplied world brief."""
    placeholder_name = re.compile(
        rf"^{re.escape(node.title.rstrip('s').casefold())}\s+\d+$"
    )
    for entity in topic.entities:
        if placeholder_name.fullmatch(str(entity.get("name") or "").strip().casefold()):
            raise ValueError(
                f"world_generation_placeholder_entity:{node.topic_id}:{entity.get('name')}"
            )

    keywords = _world_brief_keywords(campaign_context)
    if not keywords:
        return
    generated_text = _topic_text(topic)
    matches = tuple(keyword for keyword in keywords if keyword in generated_text)
    required_matches = min(3, len(keywords))
    if len(matches) < required_matches:
        raise ValueError(
            f"world_brief_grounding:{node.topic_id}:{len(matches)}:{required_matches}"
        )


def _scalar_default(field: str, *, name: str, index: int, spec: DomainSpec) -> Any:
    values: Mapping[str, Any] = {
        "name": name,
        "purpose": f"A consequential {spec.kind.replace('_', ' ')} tied to active world pressures.",
        "sensory_profile": f"{name} has a distinct atmosphere, material history, and signs of recent activity.",
        "stakes": f"Failure changes local power, safety, or access around {name}.",
        "threat_level": ("minor", "dangerous", "elite", "catastrophic")[index % 4],
        "item_type": ("weapon", "armor", "tool", "relic")[index % 4],
        "rarity": ("common", "uncommon", "rare", "legendary")[index % 4],
        "value": (index + 1) * 25,
        "lifespan": "Adulthood, aging, and longevity vary by lineage and environment.",
        "school": ("evocation", "warding", "divination", "transmutation")[index % 4],
        "tier": index % 5 + 1,
        "range": ("self", "touch", "near", "far")[index % 4],
        "setup": f"The party encounters {name} while another force acts under time pressure.",
        "premise": f"A self-contained adventure centered on {name}, a clear choice, and a lasting consequence.",
    }
    return values.get(field, f"Defined {field.replace('_', ' ')} for {name}.")


def _list_default(field: str, *, name: str) -> list[Any]:
    values: Mapping[str, list[Any]] = {
        "hooks": [f"A rumor points toward {name}", f"A rival reaches {name} first"],
        "objectives": [f"Investigate {name}", "Choose which interested party to support"],
        "rewards": ["currency", "reputation", "a durable lead"],
        "habitats": ["a region-specific lair", "a contested travel route"],
        "abilities": ["a signature attack", "an environmental adaptation"],
        "weaknesses": ["a discoverable behavioral or material weakness"],
        "effects": ["a bounded mechanical effect", "a narrative consequence"],
        "traits": ["a physical adaptation", "a social tradition"],
        "languages": ["Common", "a regional language"],
        "cultures": ["a named regional culture"],
        "progression": ["novice", "adept", "master"],
        "capabilities": ["an exploration capability", "a conflict capability"],
        "equipment": ["a signature tool", "travel gear"],
        "costs": ["time", "focus or material components"],
        "prerequisites": ["a related class, skill, or story achievement"],
        "benefits": ["one bounded mechanical advantage"],
        "limitations": ["one explicit restriction or tradeoff"],
        "complications": ["a third party intervenes", "the environment changes"],
        "outcomes": ["success changes local state", "failure advances an opposing clock"],
        "beats": ["hook", "escalation", "choice", "climax", "aftermath"],
        "starting_resources": [
            {"resource": "currency", "amount": 25},
            {"resource": "rations", "amount": 3},
        ],
    }
    return list(values.get(field, [f"Defined {field.replace('_', ' ')} for {name}"]))


def _normalize_reference(
    field: str,
    value: Any,
    *,
    known: Mapping[str, Mapping[str, Any]],
    kinds: Sequence[str],
    multiple: bool,
) -> Any:
    candidates = _reference_candidates(field, known, kinds)
    raw_values = value if isinstance(value, list) else [value] if value else []
    candidate_names = {
        str(entity.get("name") or entity.get("title") or "").strip().casefold(): entity_id
        for entity_id, entity in known.items()
        if entity_id in candidates
        and str(entity.get("name") or entity.get("title") or "").strip()
    }
    resolved: list[str] = []
    for item in raw_values:
        reference = str(item).strip()
        if reference in candidates:
            resolved.append(reference)
            continue
        matched = candidate_names.get(reference.casefold())
        if matched:
            resolved.append(matched)
    if not resolved and candidates:
        resolved = [candidates[0]]
    if not resolved and not known:
        prefix = next(iter(kinds), "entity")
        resolved = [f"{prefix}:pending"]
    return resolved if multiple else (resolved[0] if resolved else "")


def _normalize_entity(
    node: CampaignTopicNode,
    source: Mapping[str, Any],
    *,
    index: int,
    known: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    spec = DOMAIN_SPECS[node.topic_id]
    entity = dict(source)
    name = str(entity.get("name") or entity.get("title") or _name(node, index)).strip()
    entity_id = str(entity.get("id") or entity.get("entity_id") or f"{spec.id_prefix}:{_slug(name)}")
    if not entity_id.startswith(f"{spec.id_prefix}:"):
        entity_id = f"{spec.id_prefix}:{_slug(entity_id)}"
    entity.update(
        {
            "id": entity_id,
            "name": name,
            "kind": spec.kind,
            "visibility": str(entity.get("visibility") or node.visibility),
        }
    )
    for field in spec.required_scalars:
        value = entity.get(field)
        if field in spec.reference_fields:
            value = _normalize_reference(
                field, value, known=known, kinds=spec.reference_fields[field], multiple=False
            )
        if value in (None, ""):
            value = _scalar_default(field, name=name, index=index, spec=spec)
        entity[field] = value
    for field in spec.required_lists:
        value = entity.get(field)
        if field in spec.reference_fields:
            value = _normalize_reference(
                field, value, known=known, kinds=spec.reference_fields[field], multiple=True
            )
        elif field == "starting_resources" and isinstance(value, Mapping):
            value = [dict(value)]
        elif not isinstance(value, list):
            value = [value] if value not in (None, "") else []
        if not value:
            value = _list_default(field, name=name)
        entity[field] = value
    entity.setdefault(
        "description",
        f"{name} is structured {spec.kind.replace('_', ' ')} canon for the campaign world.",
    )
    entity.setdefault(
        "schema_version",
        str(node.metadata.get("schema_version") or f"rpg_world_{node.topic_id}_v1"),
    )
    short_summary, dossier = project_entity_dossier(
        entity,
        card_type=node.topic_id,
        entity_id=entity_id,
    )
    entity.setdefault("short_summary", short_summary)
    entity["dossier"] = dossier
    issues = validate_entity_dossier(dossier)
    if issues:
        raise ValueError(
            f"structured_domain_dossier:{node.topic_id}:{entity_id}:" + ",".join(issues)
        )
    return entity


def _dossier_text(entity: Mapping[str, Any]) -> str:
    dossier = raw_dossier if isinstance(raw_dossier := entity.get("dossier"), Mapping) else {}
    sections = dossier.get("sections") if isinstance(dossier, Mapping) else []
    paragraphs: list[str] = []
    if isinstance(sections, Sequence) and not isinstance(sections, (str, bytes)):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            title = str(section.get("title") or "").strip()
            values = section.get("paragraphs")
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                continue
            rendered = [str(value).strip() for value in values if str(value).strip()]
            if not rendered:
                continue
            if title:
                paragraphs.append(title)
            paragraphs.extend(rendered)
    return "\n\n".join(paragraphs)


def _document(node: CampaignTopicNode, entity: Mapping[str, Any]) -> dict[str, Any]:
    description = str(entity.get("description") or "")
    short_summary = str(entity.get("short_summary") or description)
    full_text = _dossier_text(entity) or description
    return {
        "document_id": f"lore:{node.topic_id}:{_slug(str(entity['id']))}",
        "topic_id": node.topic_id,
        "title": str(entity["name"]),
        "full_text": full_text,
        "summary_500": short_summary[:500],
        "summary_120": short_summary[:120],
        "facts": [],
        "entities": [str(entity["id"])],
        "relationships": [],
        "keywords": [node.topic_id, str(entity.get("kind") or "")],
        "visibility": str(entity.get("visibility") or node.visibility),
        "canon_revision": 0,
    }


def _fact(node: CampaignTopicNode, entity: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"fact:{_slug(str(entity['id']))}:definition",
        "subject": str(entity["id"]),
        "predicate": "defined_as",
        "object": str(entity.get("description") or ""),
        "content": str(entity.get("description") or ""),
        "authority": "generated_proposal",
        "approved_authority": "objective_canon",
        "visibility": str(entity.get("visibility") or node.visibility),
        "entity_refs": [str(entity["id"])],
        "category": node.category,
    }


def normalize_structured_domain(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependency_topics: Mapping[str, GeneratedTopic],
    *,
    allow_synthetic_completion: bool = True,
) -> GeneratedTopic:
    if node.topic_id not in DOMAIN_SPECS:
        return topic
    known = _known_entities(dependency_topics)
    sources = list(topic.entities)
    if len(sources) < node.target_count and not allow_synthetic_completion:
        raise ValueError(
            f"structured_domain_count:{node.topic_id}:{len(sources)}:{node.target_count}"
        )
    while len(sources) < node.target_count:
        sources.append({"name": _name(node, len(sources))})
    entities = tuple(
        _normalize_entity(node, row, index=index, known=known)
        for index, row in enumerate(sources[: node.target_count])
    )
    documents = [dict(row) for row in topic.documents]
    documented = {
        str(entity_id)
        for row in documents
        for entity_id in row.get("entities") or ()
    }
    documents.extend(_document(node, entity) for entity in entities if str(entity["id"]) not in documented)
    facts = [dict(row) for row in topic.facts]
    fact_refs = {
        str(entity_id)
        for row in facts
        for entity_id in row.get("entity_refs") or ()
    }
    facts.extend(_fact(node, entity) for entity in entities if str(entity["id"]) not in fact_refs)
    normalized = replace(
        topic,
        entities=entities,
        documents=tuple(documents),
        facts=tuple(facts),
        provenance={
            **dict(topic.provenance),
            "domain_contract": str(
                node.metadata.get("schema_version") or f"rpg_world_{node.topic_id}_v1"
            ),
            "domain_normalized": True,
            "domain_entity_ids": sorted(str(entity["id"]) for entity in entities),
            "entity_dossier_schema": "rpg_world_entity_dossier_v1",
        },
    )
    validate_structured_domain(node, normalized, dependency_topics)
    return normalized


def validate_structured_domain(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependency_topics: Mapping[str, GeneratedTopic],
) -> None:
    if node.topic_id not in DOMAIN_SPECS:
        return
    spec = DOMAIN_SPECS[node.topic_id]
    known = _known_entities(dependency_topics)
    seen: set[str] = set()
    if len(topic.entities) < node.target_count:
        raise ValueError(
            f"structured_domain_count:{node.topic_id}:{len(topic.entities)}:{node.target_count}"
        )
    for entity in topic.entities:
        entity_id = str(entity.get("id") or "")
        if not entity_id.startswith(f"{spec.id_prefix}:") or entity_id in seen:
            raise ValueError(f"structured_domain_entity_id:{node.topic_id}:{entity_id}")
        seen.add(entity_id)
        if str(entity.get("kind") or "") != spec.kind:
            raise ValueError(f"structured_domain_kind:{node.topic_id}:{entity_id}")
        for field in spec.required_scalars:
            if entity.get(field) in (None, ""):
                raise ValueError(f"structured_domain_field:{node.topic_id}:{entity_id}:{field}")
        for field in spec.required_lists:
            if not isinstance(entity.get(field), list) or not entity.get(field):
                raise ValueError(f"structured_domain_list:{node.topic_id}:{entity_id}:{field}")
        dossier_issues = validate_entity_dossier(entity.get("dossier"))
        if dossier_issues:
            raise ValueError(
                f"structured_domain_dossier:{node.topic_id}:{entity_id}:" + ",".join(dossier_issues)
            )
        for field, kinds in spec.reference_fields.items():
            values = entity.get(field)
            values = values if isinstance(values, list) else [values] if values else []
            candidates = set(_reference_candidates(field, known, kinds))
            for value in values:
                if known and str(value) not in candidates:
                    raise ValueError(
                        f"structured_domain_reference:{node.topic_id}:{entity_id}:{field}:{value}"
                    )


def generate_deterministic_domain(
    node: CampaignTopicNode,
    *,
    template: str,
    campaign_context: Mapping[str, Any],
    dependency_topics: Mapping[str, GeneratedTopic],
    rng: random.Random,
) -> GeneratedTopic:
    del campaign_context
    entities = []
    adjectives = ("Ashen", "Silver", "Hidden", "Storm", "Verdant", "Glass")
    noun = node.title.split(" and ", 1)[0].rstrip("s")
    for index in range(node.target_count):
        entities.append({"name": f"{adjectives[rng.randrange(len(adjectives))]} {noun} {index + 1}"})
    return normalize_structured_domain(
        node,
        GeneratedTopic(
            topic_id=node.topic_id,
            entities=tuple(entities),
            provenance={
                "generator": "deterministic_structured_domain_v1",
                "template": template,
            },
        ),
        dependency_topics,
    )
