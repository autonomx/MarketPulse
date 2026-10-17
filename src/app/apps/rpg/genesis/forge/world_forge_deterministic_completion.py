"""Synthetic reference completion used only by deterministic test generation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic


def _known_entities(
    dependencies: Mapping[str, GeneratedTopic],
) -> dict[str, Mapping[str, Any]]:
    known: dict[str, Mapping[str, Any]] = {}
    for dependency in dependencies.values():
        for entity in dependency.entities:
            entity_id = str(entity.get("id") or "")
            if entity_id:
                known[entity_id] = entity
    return known


def _npc_stub(entity_id: str) -> dict[str, Any]:
    name = entity_id.split(":", 1)[-1].replace("_", " ").title()
    return {
        "id": entity_id,
        "name": name,
        "kind": "npc",
        "appearance": f"{name} carries the practical marks of the opening conflict.",
        "personality": "Observant, purposeful, cautious, and loyal to a local obligation.",
        "backstory": (
            "Their history connects the opening conflict to people and institutions "
            "that already have a stake in the campaign."
        ),
        "goals": ["advance the unresolved opening conflict"],
        "motives": ["duty", "survival"],
        "speech_style": "specific, grounded, and shaped by immediate stakes",
        "faction_ids": [],
        "location_id": "",
        "mobility_status": "itinerant",
        "secrets": [],
        "known_facts": [],
        "visibility": "game_master_canon",
        "dossier_status": "complete",
        "generated_for_deterministic_fixture": True,
    }


def _faction_stub(entity_id: str) -> dict[str, Any]:
    name = entity_id.split(":", 1)[-1].replace("_", " ").title()
    return {
        "id": entity_id,
        "name": name,
        "kind": "faction",
        "values": ["institutional continuity"],
        "goals": ["maintain influence over the opening conflict"],
        "visibility": "partially_known",
        "dossier_status": "complete",
        "generated_for_deterministic_fixture": True,
    }


def _location_stub(
    entity_id: str,
    known: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    name = entity_id.split(":", 1)[-1].replace("_", " ").title()
    region_id = next(
        (
            str(entity.get("region_id"))
            for entity in known.values()
            if entity.get("kind") == "location" and entity.get("region_id")
        ),
        "",
    )
    return {
        "id": entity_id,
        "name": name,
        "kind": "location",
        "region_id": region_id,
        "sensory_profile": (
            "A deterministic campaign location supporting an opening actor or thread."
        ),
        "visibility": "partially_known",
        "dossier_status": "complete",
        "generated_for_deterministic_fixture": True,
    }


def _with_additions(
    topic: GeneratedTopic,
    *,
    normalized_entities: list[dict[str, Any]],
    additions: Mapping[str, Mapping[str, Any]],
    provenance: Mapping[str, Any],
) -> GeneratedTopic:
    facts = [dict(row) for row in topic.facts]
    for entity_id, entity in sorted(additions.items()):
        facts.append(
            {
                "id": f"fact:{entity_id.replace(':', '_')}:reference",
                "content": f"{entity['name']} is established deterministic fixture canon.",
                "authority": "generated_proposal",
                "approved_authority": "objective_canon",
                "visibility": str(entity.get("visibility") or "game_master_canon"),
                "entity_refs": [entity_id],
            }
        )
    return replace(
        topic,
        entities=tuple([*normalized_entities, *additions.values()]),
        facts=tuple(facts),
        provenance={
            **dict(topic.provenance),
            "deterministic_reference_completion": sorted(additions),
            **dict(provenance),
        },
    )


def _normalize_location_regions(
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    region_topic = dependencies.get("regions")
    valid_regions = {
        str(entity.get("id") or "")
        for entity in (region_topic.entities if region_topic is not None else ())
        if str(entity.get("id") or "")
    }
    if not valid_regions:
        return topic
    preferred = "region:aertos" if "region:aertos" in valid_regions else sorted(valid_regions)[0]
    replacements: dict[str, str] = {}
    entities: list[dict[str, Any]] = []
    for entity in topic.entities:
        row = dict(entity)
        parent = str(row.get("region_id") or "")
        if parent and parent not in valid_regions:
            replacements[parent] = preferred
            row["region_id"] = preferred
            row["reference_normalized"] = True
        entities.append(row)

    def normalized_refs(value: Any) -> list[str]:
        return [replacements.get(str(ref), str(ref)) for ref in value or ()]

    facts = []
    for fact in topic.facts:
        row = dict(fact)
        row["entity_refs"] = normalized_refs(row.get("entity_refs"))
        facts.append(row)
    documents = []
    for document in topic.documents:
        row = dict(document)
        row["entities"] = normalized_refs(row.get("entities"))
        documents.append(row)
    return replace(
        topic,
        documents=tuple(documents),
        entities=tuple(entities),
        facts=tuple(facts),
        provenance={
            **dict(topic.provenance),
            "deterministic_region_replacements": replacements,
        },
    )


def _complete_npc_references(
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    known = _known_entities(dependencies)
    additions: dict[str, dict[str, Any]] = {}
    normalized_npcs: list[dict[str, Any]] = []
    mobility_normalized: list[str] = []
    for npc in topic.entities:
        row = dict(npc)
        npc_id = str(row.get("id") or "")
        location_id = str(row.get("location_id") or "").strip()
        if not location_id and not str(row.get("mobility_status") or "").strip():
            row["mobility_status"] = "itinerant"
            if npc_id:
                mobility_normalized.append(npc_id)
        normalized_npcs.append(row)
        for faction_id in row.get("faction_ids") or ():
            faction_id = str(faction_id)
            if faction_id and faction_id not in known:
                additions[faction_id] = _faction_stub(faction_id)
        if location_id and location_id not in known:
            additions[location_id] = _location_stub(location_id, known)
    if not additions and not mobility_normalized:
        return topic
    return _with_additions(
        topic,
        normalized_entities=normalized_npcs,
        additions=additions,
        provenance={"deterministic_mobility_npc_ids": sorted(mobility_normalized)},
    )


def _complete_story_references(
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    known = _known_entities(dependencies)
    additions: dict[str, dict[str, Any]] = {}
    for thread in topic.story_threads:
        for actor_id in thread.get("actor_ids") or ():
            actor_id = str(actor_id)
            if actor_id and actor_id not in known:
                additions[actor_id] = _npc_stub(actor_id)
        for location_id in thread.get("location_ids") or ():
            location_id = str(location_id)
            if location_id and location_id not in known:
                additions[location_id] = _location_stub(location_id, known)
        for faction_id in thread.get("faction_ids") or ():
            faction_id = str(faction_id)
            if faction_id and faction_id not in known:
                additions[faction_id] = _faction_stub(faction_id)
    if not additions:
        return topic
    return _with_additions(
        topic,
        normalized_entities=[dict(row) for row in topic.entities],
        additions=additions,
        provenance={"deterministic_story_reference_completion": True},
    )


def complete_deterministic_references(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    if node.category == "locations":
        return _normalize_location_regions(topic, dependencies)
    if node.category == "npcs":
        return _complete_npc_references(topic, dependencies)
    if node.category == "story":
        return _complete_story_references(topic, dependencies)
    return topic
