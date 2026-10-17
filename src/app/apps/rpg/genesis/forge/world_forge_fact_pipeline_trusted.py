"""Compile machine-readable canon facts without inventing presentation prose."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline import (
    StructuredFactIssue,
    StructuredFactValidationError,
    _canonical_fact_signature,
    _entity_id,
    _fact_id,
    _field_definitions,
    _reference_values,
    validate_structured_entity_records,
)
from .world_forge_generation import GeneratedTopic


def compile_structured_entity_facts(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    """Emit structured subject/predicate/object facts and preserve only authored prose.

    The compiler may create IDs, references, authority, and machine values. It must not
    synthesize ``content``, ``display_text``, or ``expanded_description``. Provider- or
    human-authored presentation already attached to an identical fact is retained.
    Empty optional containers are omitted rather than promoted into meaningless canon.
    """

    definitions = _field_definitions(node)
    if not definitions:
        return topic
    validate_structured_entity_records(node, topic, dependencies)
    existing = {str(row.get("id") or ""): dict(row) for row in topic.facts}
    fact_ids: list[str] = []
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        visibility = str(entity.get("visibility") or node.visibility)
        for definition in definitions:
            field_id = str(definition.get("field_id") or "")
            if field_id not in entity or entity.get(field_id) in (None, "", [], (), {}):
                continue
            value = entity[field_id]
            fact_id = _fact_id(entity_id, field_id)
            references = [
                entity_id,
                *_reference_values(value, str(definition.get("value_type") or "")),
            ]
            row: dict[str, Any] = {
                "id": fact_id,
                "subject": entity_id,
                "predicate": field_id,
                "object": value,
                "authority": "generated_proposal",
                "approved_authority": "objective_canon",
                "visibility": visibility,
                "entity_refs": list(dict.fromkeys(references)),
                "topic_id": node.topic_id,
                "field_id": field_id,
                "value_type": str(definition.get("value_type") or ""),
                "semantic_role": str(definition.get("semantic_role") or ""),
                "source": "profile_structured_fact_compiler_v2",
                "authorship_class": "machine_structured",
            }
            current = existing.get(fact_id)
            if current is not None:
                current_signature = _canonical_fact_signature(current)
                expected_signature = _canonical_fact_signature(row)
                current_signature["source"] = expected_signature["source"]
                if current_signature != expected_signature:
                    raise StructuredFactValidationError(
                        (
                            StructuredFactIssue(
                                "conflicting_structured_fact",
                                node.topic_id,
                                entity_id,
                                field_id,
                                "Existing fact conflicts with the validated structured field.",
                                current,
                            ),
                        )
                    )
                authored = {
                    key: current[key]
                    for key in ("content", "display_text", "expanded_description")
                    if isinstance(current.get(key), str)
                    and str(current.get(key)).strip()
                    and str(current.get("source") or "")
                    != "profile_structured_fact_compiler_v1"
                }
                row.update(authored)
                for key in ("lookup", "lookup_schema"):
                    if key in current:
                        row[key] = current[key]
            existing[fact_id] = row
            fact_ids.append(fact_id)
    return replace(
        topic,
        facts=tuple(existing[key] for key in sorted(existing)),
        provenance={
            **dict(topic.provenance),
            "structured_fact_schema": "rpg_profile_structured_facts_v2",
            "structured_facts_validated": True,
            "structured_fact_ids": sorted(fact_ids),
            "presentation_derived_from_structured_facts": False,
        },
    )


__all__ = ["compile_structured_entity_facts"]
