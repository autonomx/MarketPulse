"""Bounded targeted regeneration for live World Forge topic proposals."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline import (
    StructuredFactIssue,
    StructuredFactValidationError,
)
from .world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from .world_forge_integrity import WorldForgeIntegrityError
from .world_forge_lore_scoring import WorldForgeLoreQualityError
from .world_forge_semantic_quality import WorldForgeSemanticQualityError


@dataclass(frozen=True)
class RegenerationRequest:
    topic_id: str
    attempt: int
    reason_codes: tuple[str, ...]
    entity_ids: tuple[str, ...]
    fields: tuple[str, ...]
    scope: str
    instructions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "attempt": self.attempt,
            "reason_codes": list(self.reason_codes),
            "entity_ids": list(self.entity_ids),
            "fields": list(self.fields),
            "scope": self.scope,
            "instructions": list(self.instructions),
        }


def regeneration_request_from_error(
    node: CampaignTopicNode,
    error: Exception,
    *,
    attempt: int,
) -> RegenerationRequest | None:
    codes: set[str] = set()
    entity_ids: set[str] = set()
    fields: set[str] = set()
    instructions: set[str] = set()
    scopes: set[str] = set()

    if isinstance(error, WorldForgeIntegrityError):
        for issue in error.issues:
            codes.add(issue.code)
            if issue.field in {"source_id", "target_id", "entity_refs", "entities"}:
                scopes.add("topic")
            else:
                if issue.item_id:
                    entity_ids.add(issue.item_id)
                scopes.add("entity_fields" if issue.field else "topic")
            if issue.field:
                fields.add(issue.field)
            instructions.add(
                issue.message
                or f"Resolve {issue.field or 'reference'} without fallback substitution."
            )
    elif isinstance(error, StructuredFactValidationError):
        for structured_fact_issue in error.issues:
            codes.add(structured_fact_issue.code)
            if structured_fact_issue.entity_id:
                entity_ids.add(structured_fact_issue.entity_id)
            if structured_fact_issue.field_id:
                fields.add(structured_fact_issue.field_id)
            instructions.add(structured_fact_issue.message)
            scopes.add("entity_fields")
    elif isinstance(error, WorldForgeSemanticQualityError):
        for semantic_quality_issue in error.report.issues:
            if semantic_quality_issue.severity != "error":
                continue
            codes.add(semantic_quality_issue.code)
            entity_ids.update(semantic_quality_issue.entity_ids)
            fields.update(semantic_quality_issue.fields)
            instructions.add(semantic_quality_issue.reason)
            scopes.add(semantic_quality_issue.regeneration_scope)
    else:
        return None

    scope = (
        "topic"
        if "topic" in scopes
        else ("entities" if "entities" in scopes else "entity_fields")
    )
    return RegenerationRequest(
        topic_id=node.topic_id,
        attempt=attempt,
        reason_codes=tuple(sorted(codes)),
        entity_ids=tuple(sorted(value for value in entity_ids if value)),
        fields=tuple(sorted(value for value in fields if value)),
        scope=scope,
        instructions=tuple(sorted(value for value in instructions if value)),
    )


def _entity_id(entity: Mapping[str, Any]) -> str:
    return str(entity.get("id") or entity.get("entity_id") or "").strip()


def _entity_snapshot(
    topic: GeneratedTopic,
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        if selected_ids and entity_id not in selected_ids:
            continue
        rows.append(dict(entity))
    return rows


def targeted_regeneration_context(
    campaign_context: Mapping[str, Any],
    request: RegenerationRequest,
    prior_topic: GeneratedTopic,
) -> dict[str, Any]:
    all_entity_ids = tuple(
        _entity_id(entity)
        for entity in prior_topic.entities
        if _entity_id(entity)
    )
    selected_ids = set(request.entity_ids)
    if request.scope == "topic" or not selected_ids:
        selected_ids = set(all_entity_ids)
    return {
        **dict(campaign_context),
        "targeted_regeneration": {
            **request.as_dict(),
            "required_behavior": (
                "Regenerate only the identified topic, entities, or fields. Preserve "
                "stable IDs and all unaffected structured values. Do not choose fallback "
                "references, add generic filler, or alter unrelated canon."
            ),
            "prior_failing_entities": _entity_snapshot(prior_topic, selected_ids),
            "preserve_entity_ids": sorted(set(all_entity_ids) - selected_ids),
        },
    }


def _regeneration_invariant_error(
    request: RegenerationRequest,
    *,
    entity_id: str,
    field_id: str,
    message: str,
    supplied_value: Any = None,
) -> StructuredFactValidationError:
    return StructuredFactValidationError(
        (
            StructuredFactIssue(
                code="targeted_regeneration_invariant",
                topic_id=request.topic_id,
                entity_id=entity_id,
                field_id=field_id,
                message=message,
                supplied_value=supplied_value,
            ),
        )
    )


def enforce_targeted_regeneration(
    prior_topic: GeneratedTopic,
    candidate: GeneratedTopic,
    request: RegenerationRequest,
) -> GeneratedTopic:
    """Deterministically preserve every value outside the requested retry scope."""

    if candidate.topic_id != prior_topic.topic_id or candidate.topic_id != request.topic_id:
        raise _regeneration_invariant_error(
            request,
            entity_id="",
            field_id="topic_id",
            message="Targeted regeneration may not change the topic ID.",
            supplied_value=candidate.topic_id,
        )

    prior_entities = {
        _entity_id(entity): dict(entity)
        for entity in prior_topic.entities
        if _entity_id(entity)
    }
    candidate_entities = {
        _entity_id(entity): dict(entity)
        for entity in candidate.entities
        if _entity_id(entity)
    }
    selected_ids = set(request.entity_ids)

    if request.scope == "topic":
        if prior_entities and set(candidate_entities) != set(prior_entities):
            raise _regeneration_invariant_error(
                request,
                entity_id="",
                field_id="id",
                message=(
                    "Topic regeneration must preserve the complete stable entity ID set."
                ),
                supplied_value=sorted(candidate_entities),
            )
        return replace(
            candidate,
            provenance={
                **dict(candidate.provenance),
                "targeted_regeneration_enforced": True,
                "targeted_regeneration_scope": request.scope,
            },
        )

    missing = selected_ids.difference(candidate_entities)
    if missing:
        raise _regeneration_invariant_error(
            request,
            entity_id=sorted(missing)[0],
            field_id="id",
            message="Regenerated output omitted a selected stable entity ID.",
            supplied_value=sorted(candidate_entities),
        )

    merged_entities: list[dict[str, Any]] = []
    for entity in prior_topic.entities:
        entity_id = _entity_id(entity)
        prior = dict(entity)
        if entity_id not in selected_ids:
            merged_entities.append(prior)
            continue
        regenerated = candidate_entities[entity_id]
        if request.scope == "entities":
            merged_entities.append(regenerated)
            continue
        merged = dict(prior)
        for field_id in request.fields:
            if field_id not in regenerated:
                raise _regeneration_invariant_error(
                    request,
                    entity_id=entity_id,
                    field_id=field_id,
                    message="Regenerated output omitted a selected field.",
                )
            merged[field_id] = regenerated[field_id]
        merged_entities.append(merged)

    return replace(
        candidate,
        documents=prior_topic.documents,
        entities=tuple(merged_entities),
        facts=prior_topic.facts,
        relationships=prior_topic.relationships,
        knowledge_rules=prior_topic.knowledge_rules,
        story_threads=prior_topic.story_threads,
        provenance={
            **dict(candidate.provenance),
            "targeted_regeneration_enforced": True,
            "targeted_regeneration_scope": request.scope,
            "targeted_regeneration_preserved_entity_ids": sorted(
                set(prior_entities) - selected_ids
            ),
            "targeted_regeneration_updated_entity_ids": sorted(selected_ids),
            "targeted_regeneration_updated_fields": list(request.fields),
        },
    )


def _provider_generated(topic: GeneratedTopic) -> bool:
    return str(dict(topic.provenance).get("generator") or "").startswith(
        "structured_world_forge_provider_"
    )


def _quality_attempt_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return exactly one scored report per provider attempt."""

    return [
        dict(row)
        for row in history
        if row.get("quality_status") is not None
    ]


def _selected_best_candidate(
    candidates: list[tuple[int, int, GeneratedTopic]],
    *,
    attempts: int,
    history: list[dict[str, Any]],
) -> GeneratedTopic:
    score, selected_attempt, topic = max(
        candidates,
        key=lambda value: (value[0], -value[1]),
    )
    return replace(
        topic,
        provenance={
            **dict(topic.provenance),
            "lore_quality_status": "needs_review",
            "lore_quality_needs_review": True,
            "lore_quality_selected_below_threshold": True,
            "lore_quality_selected_attempt": selected_attempt,
            "lore_quality_selected_score": score,
            "lore_quality_total_attempts": attempts,
            "lore_quality_retry_count": max(0, attempts - 1),
            "lore_quality_attempts": _quality_attempt_rows(history),
            "targeted_regeneration_history": history,
            "targeted_regeneration_succeeded": False,
        },
    )


def generate_with_targeted_regeneration(
    generator: WorldForgeTopicGenerator,
    node: CampaignTopicNode,
    *,
    seed: int,
    campaign_context: Mapping[str, Any],
    dependency_topics: Mapping[str, GeneratedTopic],
    process: Callable[[GeneratedTopic], GeneratedTopic],
    max_attempts: int = 2,
) -> GeneratedTopic:
    """Generate, score, and retry live-provider output without losing usable lore.

    Hard-invalid candidates never become canon. Structurally valid candidates that
    miss the preferred prose score are retained while the provider receives one
    targeted retry. If neither reaches the threshold, the best score is returned
    with durable ``needs_review`` provenance for later Game Master regeneration.
    """

    attempts = max(1, min(int(max_attempts), 2))
    context = dict(campaign_context)
    history: list[dict[str, Any]] = []
    last_error: Exception | None = None
    prior_failed_topic: GeneratedTopic | None = None
    pending_request: RegenerationRequest | None = None
    quality_candidates: list[tuple[int, int, GeneratedTopic]] = []

    for attempt in range(1, attempts + 1):
        generated = generator.generate(
            node,
            seed=seed,
            campaign_context=context,
            dependency_topics=dependency_topics,
        )
        topic = generated
        try:
            if pending_request is not None and prior_failed_topic is not None:
                topic = enforce_targeted_regeneration(
                    prior_failed_topic,
                    generated,
                    pending_request,
                )
            processed = process(topic)
        except Exception as error:
            last_error = error
            # Keep the uncompiled provider candidate as the next retry baseline.
            # The processed/scored candidate remains eligible for best-of selection,
            # but must not be fed back through compilation as provider input.
            failing_topic = topic
            if isinstance(error, WorldForgeLoreQualityError):
                quality_candidates.append(
                    (error.assessment.score, attempt, error.candidate_topic)
                )
                history.append(
                    {
                        "attempt": attempt,
                        "quality_score": error.assessment.score,
                        "quality_threshold": error.assessment.threshold,
                        "quality_status": error.assessment.status,
                        "quality_issue_codes": sorted(
                            {issue.code for issue in error.assessment.issues}
                        ),
                        "quality_entity_scores": dict(
                            error.assessment.entity_scores
                        ),
                        "quality_dimensions": dict(error.assessment.dimensions),
                    }
                )

            if not _provider_generated(generated):
                raise
            if attempt >= attempts:
                if quality_candidates:
                    return _selected_best_candidate(
                        quality_candidates,
                        attempts=attempt,
                        history=history,
                    )
                raise

            request = regeneration_request_from_error(
                node,
                error,
                attempt=attempt + 1,
            )
            if request is None:
                raise
            request_row = request.as_dict()
            request_row["source_attempt"] = attempt
            history.append(request_row)
            if pending_request is None:
                prior_failed_topic = failing_topic
            context = targeted_regeneration_context(
                context,
                request,
                prior_failed_topic or failing_topic,
            )
            pending_request = request
            continue

        assessment = dict(processed.provenance).get("lore_quality")
        if isinstance(assessment, Mapping):
            history.append(
                {
                    "attempt": attempt,
                    "quality_score": int(assessment.get("score") or 0),
                    "quality_threshold": int(assessment.get("threshold") or 0),
                    "quality_status": str(assessment.get("status") or "accepted"),
                    "quality_issue_codes": list(assessment.get("issue_codes") or ()),
                    "quality_entity_scores": dict(
                        assessment.get("entity_scores") or {}
                    ),
                    "quality_dimensions": dict(assessment.get("dimensions") or {}),
                }
            )
        if history:
            processed = replace(
                processed,
                provenance={
                    **dict(processed.provenance),
                    "targeted_regeneration_attempt_count": attempt,
                    "targeted_regeneration_history": history,
                    "targeted_regeneration_succeeded": True,
                    "lore_quality_attempts": _quality_attempt_rows(history),
                    "lore_quality_selected_attempt": attempt,
                    "lore_quality_total_attempts": attempt,
                    "lore_quality_retry_count": max(0, attempt - 1),
                },
            )
        return processed

    if quality_candidates:
        return _selected_best_candidate(
            quality_candidates,
            attempts=attempts,
            history=history,
        )
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"world_forge_regeneration_exhausted:{node.topic_id}")
