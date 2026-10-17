"""Compile generated World Forge topics into one retrieval-ready Campaign Bible."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .canon_audit import CanonAuditReport
from .world_forge_generation import WorldForgeGenerationResult


@dataclass(frozen=True)
class CanonCompilationResult:
    document: Mapping[str, Any]
    completeness: Mapping[str, Any]
    retrieval_index: Mapping[str, Any]
    launch_ready: bool
    missing_requirements: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": dict(self.document),
            "completeness": dict(self.completeness),
            "retrieval_index": dict(self.retrieval_index),
            "launch_ready": self.launch_ready,
            "missing_requirements": list(self.missing_requirements),
            "metadata": dict(self.metadata),
        }


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _unique(
    rows: Iterable[Mapping[str, Any]],
    *id_fields: str,
) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        item = dict(row)
        item_id = next(
            (
                str(item.get(id_field) or "")
                for id_field in id_fields
                if item.get(id_field)
            ),
            f"item:{index}",
        )
        values.setdefault(item_id, item)
    return [values[key] for key in sorted(values)]


def _words(value: Any) -> tuple[str, ...]:
    text = "".join(
        character.casefold() if character.isalnum() else " "
        for character in str(value or "")
    )
    return tuple(word for word in text.split() if len(word) >= 3)


def _document_cards(
    documents: list[dict[str, Any]],
    canon_revision: int,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for document in documents:
        authority = str(
            document.get("approved_authority")
            or document.get("authority")
            or "objective_canon"
        )
        if authority == "presentation_only":
            continue
        document_id = str(document.get("document_id") or "")
        title = str(document.get("title") or document_id)
        visibility = str(document.get("visibility") or "game_master_canon")
        entity_refs = [
            str(value) for value in document.get("entities") or () if str(value)
        ]
        keywords = [
            str(value) for value in document.get("keywords") or () if str(value)
        ]
        source_fact_ids = [
            str(value)
            for value in document.get("canonical_source_fact_ids") or ()
            if str(value)
        ]
        for size, field_name in (
            ("short", "summary_120"),
            ("medium", "summary_500"),
        ):
            content = str(document.get(field_name) or "").strip()
            if not content:
                continue
            cards.append(
                {
                    "id": f"card:{document_id}:{size}",
                    "document_id": document_id,
                    "title": title,
                    "content": content,
                    "summary_size": size,
                    "authority": authority,
                    "visibility": visibility,
                    "entity_refs": entity_refs,
                    "keywords": keywords,
                    "canonical_source_fact_ids": source_fact_ids,
                    "canon_revision": canon_revision,
                    "category": str(document.get("topic_id") or "lore"),
                }
            )
    return cards


def _fact_cards(
    facts: list[dict[str, Any]],
    canon_revision: int,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for fact in facts:
        fact_id = str(fact.get("id") or fact.get("evidence_id") or "")
        content = str(
            fact.get("content")
            or fact.get("statement")
            or fact.get("object")
            or ""
        ).strip()
        if not fact_id or not content:
            continue
        cards.append(
            {
                "id": f"card:{fact_id}",
                "fact_id": fact_id,
                "title": str(fact.get("title") or fact_id.replace("_", " ")),
                "content": content,
                "authority": str(
                    fact.get("approved_authority")
                    or fact.get("authority")
                    or "objective_canon"
                ),
                "visibility": str(
                    fact.get("visibility") or "game_master_canon"
                ),
                "known_by": list(fact.get("known_by") or ()),
                "entity_refs": list(fact.get("entity_refs") or ()),
                "keywords": sorted(set(_words(content))),
                "canon_revision": canon_revision,
                "category": str(fact.get("category") or "fact"),
            }
        )
    return cards


def _lexical_index(cards: list[dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, set[str]] = {}
    for card in cards:
        card_id = str(card.get("id") or "")
        tokens = set(_words(card.get("title")))
        tokens.update(_words(card.get("content")))
        tokens.update(
            str(value).casefold()
            for value in card.get("keywords") or ()
            if str(value)
        )
        for token in tokens:
            index.setdefault(token, set()).add(card_id)
    return {
        token: sorted(card_ids)
        for token, card_ids in sorted(index.items())
    }


def _entity_index(
    entities: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    cards_by_entity: dict[str, list[str]] = {}
    for card in cards:
        for entity_id in card.get("entity_refs") or ():
            cards_by_entity.setdefault(str(entity_id), []).append(
                str(card.get("id") or "")
            )
    return {
        str(entity.get("id")): {
            "name": str(entity.get("name") or entity.get("id") or ""),
            "kind": str(entity.get("kind") or "entity"),
            "visibility": str(
                entity.get("visibility") or "game_master_canon"
            ),
            "retrieval_card_ids": sorted(
                set(cards_by_entity.get(str(entity.get("id")), []))
            ),
        }
        for entity in entities
        if entity.get("id")
    }


def _discovery_state(
    documents: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    page_status: dict[str, str] = {}
    for document in documents:
        visibility = str(document.get("visibility") or "game_master_canon")
        status = {
            "public": "public_at_campaign_start",
            "player_known": "learned",
            "learned": "learned",
            "partially_known": "partially_known",
            "disputed": "disputed",
        }.get(visibility, "hidden_from_player")
        page_status[str(document.get("document_id") or "")] = status
    entity_status = {
        str(entity.get("id")): {
            "public": "public_at_campaign_start",
            "player_known": "learned",
            "learned": "learned",
            "partially_known": "partially_known",
            "disputed": "disputed",
        }.get(str(entity.get("visibility") or ""), "hidden_from_player")
        for entity in entities
        if entity.get("id")
    }
    return {
        "pages": page_status,
        "entities": entity_status,
        "discoveries": [],
    }


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _profile_opening_contract(
    topic_graph: Mapping[str, Any],
) -> tuple[set[str], set[str], set[str], bool]:
    """Derive opening place/actor kinds and actor location fields from a profile."""

    metadata = _record(topic_graph.get("metadata"))
    profile = _record(metadata.get("resolved_profile"))
    domains = _rows(profile.get("domains"))
    if not domains:
        return {"location"}, {"npc"}, {"location_id"}, False

    domain_map = {
        str(domain.get("domain_id") or ""): domain
        for domain in domains
        if str(domain.get("domain_id") or "")
    }
    actor_domains = tuple(
        domain
        for domain in domains
        if "initial_actors"
        in {str(role) for role in domain.get("semantic_roles") or ()}
    )
    actor_kinds = {
        str(domain.get("entity_kind") or "").strip()
        for domain in actor_domains
        if str(domain.get("entity_kind") or "").strip()
    }
    place_domain_ids: set[str] = set()
    actor_place_fields: set[str] = set()
    for domain in actor_domains:
        for definition in _rows(domain.get("fields")):
            field_id = str(definition.get("field_id") or "").strip()
            semantic_role = str(
                definition.get("semantic_role") or ""
            ).strip()
            value_type = str(definition.get("value_type") or "").strip()
            is_place_field = (
                semantic_role
                in {"current_location", "current_place", "starting_place"}
                or any(
                    marker in field_id
                    for marker in ("location", "place", "container")
                )
            )
            if value_type not in {"entity_ref", "entity_ref_list"}:
                continue
            if not is_place_field:
                continue
            actor_place_fields.add(field_id)
            place_domain_ids.update(
                str(value)
                for value in definition.get("allowed_target_domains") or ()
                if str(value)
            )
    place_kinds = {
        str(domain_map[domain_id].get("entity_kind") or "").strip()
        for domain_id in place_domain_ids
        if domain_id in domain_map
        and str(domain_map[domain_id].get("entity_kind") or "").strip()
    }
    return (
        place_kinds or {"location"},
        actor_kinds or {"npc"},
        actor_place_fields or {"location_id"},
        True,
    )


def _normalized_identity(value: Any) -> str:
    rendered = str(value or "").strip().casefold()
    if ":" in rendered:
        rendered = rendered.split(":", 1)[-1]
    return "_".join(
        "".join(
            character if character.isalnum() else " "
            for character in rendered
        ).split()
    )


def _reference_values(value: Any) -> set[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {str(item).strip() for item in value if str(item).strip()}
    rendered = str(value or "").strip()
    return {rendered} if rendered else set()


def _opening_ids(
    entities: list[dict[str, Any]],
    story_threads: list[dict[str, Any]],
    starting_location: str,
    topic_graph: Mapping[str, Any],
) -> tuple[set[str], set[str], dict[str, Any]]:
    place_kinds, actor_kinds, actor_place_fields, profile_driven = (
        _profile_opening_contract(topic_graph)
    )
    place_entities = [
        entity
        for entity in entities
        if str(entity.get("kind") or "") in place_kinds
    ]
    requested = _normalized_identity(starting_location)
    location_ids = {
        str(entity.get("id"))
        for entity in place_entities
        if requested
        and requested
        in {
            _normalized_identity(entity.get("id")),
            _normalized_identity(entity.get("name")),
        }
    }
    if not requested and place_entities:
        location_ids = {str(place_entities[0].get("id"))}
    elif not location_ids and not profile_driven and place_entities:
        location_ids = {str(place_entities[0].get("id"))}

    actor_ids: set[str] = set()
    for entity in entities:
        if str(entity.get("kind") or "") not in actor_kinds:
            continue
        present_at = set().union(
            *(
                _reference_values(entity.get(field_id))
                for field_id in actor_place_fields
            )
        )
        if location_ids.intersection(present_at):
            actor_ids.add(str(entity.get("id")))
    for thread in story_threads:
        thread_locations = {
            str(value) for value in thread.get("location_ids") or () if str(value)
        }
        if location_ids.intersection(thread_locations):
            actor_ids.update(
                str(value)
                for value in thread.get("actor_ids") or ()
                if str(value)
            )
    return (
        location_ids,
        actor_ids,
        {
            "profile_driven": profile_driven,
            "place_kinds": sorted(place_kinds),
            "actor_kinds": sorted(actor_kinds),
            "actor_place_fields": sorted(actor_place_fields),
            "requested_starting_location": starting_location,
        },
    )


def compile_campaign_bible(
    generation: WorldForgeGenerationResult,
    *,
    compiled_relationships: Iterable[Mapping[str, Any]],
    audit: CanonAuditReport,
    topic_graph: Mapping[str, Any],
    campaign_id: str,
    campaign_template: str,
    starting_location: str,
    canon_revision: int = 1,
) -> CanonCompilationResult:
    """Compile topic outputs into human, atomic, ACL, and retrieval forms."""

    topics = generation.topics
    documents = _unique(
        (row for topic in topics for row in topic.documents),
        "document_id",
        "id",
    )
    entities = _unique(
        (row for topic in topics for row in topic.entities),
        "id",
    )
    facts = _unique(
        (row for topic in topics for row in topic.facts),
        "id",
        "evidence_id",
    )
    relationships = _unique(
        [
            *(row for topic in topics for row in topic.relationships),
            *(dict(row) for row in compiled_relationships),
        ],
        "id",
    )
    knowledge_rules = _unique(
        (row for topic in topics for row in topic.knowledge_rules),
        "id",
    )
    story_threads = _unique(
        (row for topic in topics for row in topic.story_threads),
        "id",
    )
    for document in documents:
        document["canon_revision"] = canon_revision
    for row in [*facts, *relationships]:
        if row.get("authority") == "generated_proposal":
            row["authority"] = str(
                row.pop("approved_authority", "objective_canon")
            )
        row["canon_revision"] = canon_revision
    cards = _unique(
        [
            *_document_cards(documents, canon_revision),
            *_fact_cards(facts, canon_revision),
        ],
        "id",
    )
    lexical = _lexical_index(cards)
    entity_index = _entity_index(entities, cards)
    opening_locations, opening_actors, opening_contract = _opening_ids(
        entities,
        story_threads,
        starting_location,
        topic_graph,
    )
    entity_by_id = {str(row.get("id")): row for row in entities}
    missing: list[str] = []
    required_topics = {
        str(value)
        for value in topic_graph.get("launch_required_topic_ids") or ()
    }
    completed_topics = {topic.topic_id for topic in topics}
    pipeline_topics = {
        "relationships",
        "consistency_audit",
        "canon_compile",
        "retrieval_index",
        "opening_materialization",
    }
    for topic_id in sorted(
        required_topics.difference(completed_topics).difference(pipeline_topics)
    ):
        missing.append(f"topic:{topic_id}")
    if not documents:
        missing.append("lore_documents")
    if not facts:
        missing.append("atomic_facts")
    if not cards:
        missing.append("retrieval_cards")
    if not opening_locations:
        missing.append("starting_location_dossier")
    for actor_id in sorted(opening_actors):
        actor = entity_by_id.get(actor_id, {})
        if str(actor.get("dossier_status") or "") != "complete":
            missing.append(f"opening_actor_dossier:{actor_id}")
    if not audit.passed:
        missing.append("consistency_audit")
    completeness = {
        "score": round(
            max(0.0, 1.0 - len(missing) / max(1, len(required_topics))),
            4,
        ),
        "required_topics": len(required_topics),
        "completed_topics": len(completed_topics.intersection(required_topics)),
        "documents": len(documents),
        "entities": len(entities),
        "facts": len(facts),
        "relationships": len(relationships),
        "knowledge_rules": len(knowledge_rules),
        "retrieval_cards": len(cards),
        "opening_location_ids": sorted(opening_locations),
        "opening_actor_ids": sorted(opening_actors),
        "opening_contract": opening_contract,
        "missing_requirements": list(missing),
    }
    discovery = _discovery_state(documents, entities)
    for entity_id in sorted(opening_locations | opening_actors):
        discovery["entities"][entity_id] = "partially_known"
    retrieval_index = {
        "index_version": "rpg_campaign_retrieval_index_v1",
        "lexical": lexical,
        "entities": entity_index,
        "card_count": len(cards),
        "embedding_index": {
            "status": "not_built",
            "reason": "provider_independent_genesis",
        },
    }
    document = {
        "schema_version": "rpg_campaign_bible_v2",
        "campaign_id": campaign_id,
        "campaign_template": campaign_template,
        "canon_revision": canon_revision,
        "topic_graph": dict(topic_graph),
        "manifest": {
            "document_count": len(documents),
            "entity_count": len(entities),
            "fact_count": len(facts),
            "relationship_count": len(relationships),
            "retrieval_card_count": len(cards),
            "generation_jobs": [job.as_dict() for job in generation.jobs],
            "generation_order": [
                list(batch) for batch in generation.generation_order
            ],
        },
        "documents": documents,
        "entities": {
            str(row["id"]): row for row in entities if row.get("id")
        },
        "facts": facts,
        "relationships": relationships,
        "knowledge_rules": knowledge_rules,
        "story_threads": story_threads,
        "retrieval_cards": cards,
        "indexes": retrieval_index,
        "discovery_state": discovery,
        "consistency_report": audit.as_dict(),
        "completeness": completeness,
        "generation_provenance": {
            topic.topic_id: dict(topic.provenance) for topic in topics
        },
    }
    document["content_hash"] = _canonical_hash(document)
    return CanonCompilationResult(
        document=document,
        completeness=completeness,
        retrieval_index=retrieval_index,
        launch_ready=not missing,
        missing_requirements=tuple(missing),
        metadata={
            "compiler": "rpg_campaign_canon_compiler_v2",
            "content_hash": document["content_hash"],
        },
    )
