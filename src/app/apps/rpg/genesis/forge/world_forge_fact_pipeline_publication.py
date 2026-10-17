"""Idempotent structured-fact validation for publication.

Publication must never regenerate presentation prose. Already compiled facts are
checked against current structured entity values while compiler-version metadata is
ignored; uncompiled topics use the trusted machine-only compiler. Every validated
structured fact receives a compact machine lookup so generic canon consumers do not
mistake a prose-free typed fact for an incomplete narrative fact.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_canon_lookup_trusted import attach_structured_canon_lookup
from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline import (
    StructuredFactIssue,
    StructuredFactValidationError,
    _entity_id,
    _fact_id,
    _field_definitions,
    _reference_values,
    validate_structured_entity_records,
)
from .world_forge_fact_pipeline_trusted import (
    compile_structured_entity_facts as _compile_machine_facts,
)
from .world_forge_generation import GeneratedTopic


def _signature(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return semantic fact values; compiler source and prose are not canon."""

    return {
        "id": str(row.get("id") or ""),
        "subject": str(row.get("subject") or ""),
        "predicate": str(row.get("predicate") or row.get("field_id") or ""),
        "object": row.get("object"),
        "authority": str(row.get("authority") or ""),
        "approved_authority": str(row.get("approved_authority") or ""),
        "visibility": str(row.get("visibility") or ""),
        "entity_refs": list(row.get("entity_refs") or ()),
        "topic_id": str(row.get("topic_id") or ""),
        "field_id": str(row.get("field_id") or ""),
        "value_type": str(row.get("value_type") or ""),
        "semantic_role": str(row.get("semantic_role") or ""),
    }


def validate_or_compile_structured_entity_facts(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    definitions = _field_definitions(node)
    if not definitions:
        return topic
    validate_structured_entity_records(node, topic, dependencies)
    if not bool(dict(topic.provenance).get("structured_facts_validated")):
        return attach_structured_canon_lookup(
            _compile_machine_facts(node, topic, dependencies)
        )

    existing = {str(row.get("id") or ""): dict(row) for row in topic.facts}
    fact_ids: list[str] = []
    issues: list[StructuredFactIssue] = []
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        visibility = str(entity.get("visibility") or node.visibility)
        for definition in definitions:
            field_id = str(definition.get("field_id") or "")
            if field_id not in entity or entity.get(field_id) in (None, ""):
                continue
            value = entity[field_id]
            fact_id = _fact_id(entity_id, field_id)
            references = [
                entity_id,
                *_reference_values(value, str(definition.get("value_type") or "")),
            ]
            expected = {
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
            }
            current = existing.get(fact_id)
            if current is None or _signature(current) != _signature(expected):
                issues.append(
                    StructuredFactIssue(
                        "conflicting_structured_fact",
                        node.topic_id,
                        entity_id,
                        field_id,
                        "Existing fact conflicts with the validated structured field.",
                        current,
                    )
                )
            fact_ids.append(fact_id)
    if issues:
        raise StructuredFactValidationError(issues)
    validated = replace(
        topic,
        provenance={
            **dict(topic.provenance),
            "structured_facts_validated": True,
            "structured_fact_ids": sorted(fact_ids),
            "publication_structured_fact_validation": "semantic_signature_v1",
        },
    )
    return attach_structured_canon_lookup(validated)


__all__ = ["validate_or_compile_structured_entity_facts"]
