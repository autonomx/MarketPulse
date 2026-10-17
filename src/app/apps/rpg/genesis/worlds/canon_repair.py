"""Representation-only normalization and fail-closed publication validation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from app.apps.rpg.genesis.forge.canon_audit import audit_generated_canon
from app.apps.rpg.genesis.forge.canon_relationships import compile_cross_domain_relationships
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicGraph
from app.apps.rpg.genesis.forge.world_forge_fact_pipeline_publication import (
    validate_or_compile_structured_entity_facts,
)
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeGenerationResult,
)
from app.apps.rpg.genesis.forge.world_forge_integrity import (
    WorldForgeIntegrityError,
    WorldForgeIntegrityIssue,
)
from app.apps.rpg.genesis.forge.world_forge_presentation_trusted import (
    render_fact_derived_presentations,
)
from app.apps.rpg.genesis.forge.world_forge_quality import apply_world_forge_quality_audit
from app.apps.rpg.genesis.forge.world_forge_semantic_quality import (
    require_topic_semantic_quality,
)

_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_entity(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    entity_id = _text(normalized.get("id") or normalized.get("entity_id"))
    if entity_id:
        normalized["id"] = entity_id
    if "name" not in normalized and normalized.get("title"):
        normalized["name"] = _text(normalized.get("title"))
    return normalized


def _normalize_document(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    document_id = _text(normalized.get("document_id") or normalized.get("id"))
    if document_id:
        normalized["document_id"] = document_id
    if "full_text" not in normalized and normalized.get("content") is not None:
        normalized["full_text"] = _text(normalized.get("content"))
    return normalized


def _normalize_fact(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    fact_id = _text(
        normalized.get("id")
        or normalized.get("fact_id")
        or normalized.get("evidence_id")
    )
    if fact_id:
        normalized["id"] = fact_id
    if "content" not in normalized and normalized.get("statement") is not None:
        normalized["content"] = _text(normalized.get("statement"))
    return normalized


def _normalize_relationship(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    relationship_id = _text(
        normalized.get("id") or normalized.get("relationship_id")
    )
    if relationship_id:
        normalized["id"] = relationship_id
    source_id = _text(
        normalized.get("source_id") or normalized.get("source_entity_id")
    )
    target_id = _text(
        normalized.get("target_id") or normalized.get("target_entity_id")
    )
    if source_id:
        normalized["source_id"] = source_id
    if target_id:
        normalized["target_id"] = target_id
    return normalized


def _item_id(row: Mapping[str, Any], collection: str, index: int) -> str:
    return str(
        row.get("id")
        or row.get("entity_id")
        or row.get("document_id")
        or row.get("fact_id")
        or row.get("relationship_id")
        or f"{collection}:{index}"
    )


def _normalize_rows(
    rows: tuple[Mapping[str, Any], ...],
    *,
    collection: str,
    normalizer: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], list[dict[str, Any]]]:
    normalized_rows: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        before = dict(source)
        after = normalizer(before)
        normalized_rows.append(after)
        item_id = _item_id(after, collection, index)
        for field_id in sorted(set(before) | set(after)):
            if before.get(field_id) == after.get(field_id):
                continue
            actions.append(
                {
                    "operation": "representation_alias_normalization",
                    "collection": collection,
                    "item_id": item_id,
                    "field": field_id,
                    "before": before.get(field_id),
                    "after": after.get(field_id),
                    "semantic_change": False,
                }
            )
    return tuple(normalized_rows), actions


def normalize_generation_contracts(
    generation: WorldForgeGenerationResult,
) -> WorldForgeGenerationResult:
    """Normalize declared field aliases without changing semantic meaning."""

    topics: list[GeneratedTopic] = []
    for topic in generation.topics:
        documents, document_actions = _normalize_rows(
            topic.documents,
            collection="documents",
            normalizer=_normalize_document,
        )
        entities, entity_actions = _normalize_rows(
            topic.entities,
            collection="entities",
            normalizer=_normalize_entity,
        )
        facts, fact_actions = _normalize_rows(
            topic.facts,
            collection="facts",
            normalizer=_normalize_fact,
        )
        relationships, relationship_actions = _normalize_rows(
            topic.relationships,
            collection="relationships",
            normalizer=_normalize_relationship,
        )
        actions = [
            *document_actions,
            *entity_actions,
            *fact_actions,
            *relationship_actions,
        ]
        topics.append(
            replace(
                topic,
                documents=documents,
                entities=entities,
                facts=facts,
                relationships=relationships,
                provenance={
                    **dict(topic.provenance),
                    "publication_normalization": "representation_only_v1",
                    "publication_normalization_actions": actions,
                    "publication_normalization_action_count": len(actions),
                },
            )
        )
    return replace(generation, topics=tuple(topics))


def _profile_revalidated_generation(
    generation: WorldForgeGenerationResult,
    topic_graph: CampaignTopicGraph,
    generation_context: Mapping[str, Any],
) -> WorldForgeGenerationResult:
    """Re-run Phase 4-7 gates from the pinned graph before publication."""

    source_topics = {topic.topic_id: topic for topic in generation.topics}
    processed: dict[str, GeneratedTopic] = {}
    for node in topic_graph.topological_order():
        if node.category in _NON_GENERATION_CATEGORIES:
            continue
        topic = source_topics.get(node.topic_id)
        if topic is None:
            continue
        dependencies = {
            dependency_id: processed[dependency_id]
            for dependency_id in node.dependencies
            if dependency_id in processed
        }
        validation_stages: list[str] = []
        if node.metadata.get("field_definitions"):
            topic = validate_or_compile_structured_entity_facts(
                node,
                topic,
                dependencies,
            )
            validation_stages.append("structured_facts_validated")
            semantic_report = require_topic_semantic_quality(
                node,
                topic,
                generation_context,
            )
            validation_stages.append("semantic_quality_validated")
            topic = replace(
                topic,
                provenance={
                    **dict(topic.provenance),
                    "publication_semantic_quality": semantic_report.as_dict(),
                },
            )
            topic = render_fact_derived_presentations(node, topic)
            validation_stages.append("presentation_rebuilt_from_facts")
            topic = replace(
                topic,
                provenance={
                    **dict(topic.provenance),
                    "publication_validation_stages": validation_stages,
                },
            )
        processed[node.topic_id] = topic

    ordered_topics = tuple(
        processed.get(topic.topic_id, topic)
        for topic in generation.topics
    )
    return replace(generation, topics=ordered_topics)


def validate_generation_contracts(
    generation: WorldForgeGenerationResult,
    *,
    topic_graph: CampaignTopicGraph | None = None,
    generation_context: Mapping[str, Any] | None = None,
) -> WorldForgeGenerationResult:
    validated = generation
    if topic_graph is not None:
        validated = _profile_revalidated_generation(
            validated,
            topic_graph,
            dict(generation_context or {}),
        )

    relationships = compile_cross_domain_relationships(validated.topics)
    report = apply_world_forge_quality_audit(
        validated.topics,
        audit_generated_canon(
            validated.topics,
            compiled_relationships=relationships,
        ),
    )
    if report.passed:
        return validated
    issues = tuple(
        WorldForgeIntegrityIssue(
            code=issue.code,
            topic_id="publication",
            item_id=issue.item_id,
            field="",
            supplied_value="",
            message=issue.message,
        )
        for issue in report.issues
        if issue.severity == "error"
    )
    raise WorldForgeIntegrityError(issues)


def repair_generation_contracts(
    generation: WorldForgeGenerationResult,
    *,
    starting_location: str,
    topic_graph: CampaignTopicGraph | None = None,
    generation_context: Mapping[str, Any] | None = None,
) -> WorldForgeGenerationResult:
    """Deprecated repair entry point retained as a strict publication gate.

    ``starting_location`` is intentionally ignored. Publication may not move an
    entity or choose a fallback location to make a proposal appear valid.
    """

    del starting_location
    normalized = normalize_generation_contracts(generation)
    return validate_generation_contracts(
        normalized,
        topic_graph=topic_graph,
        generation_context=generation_context,
    )
