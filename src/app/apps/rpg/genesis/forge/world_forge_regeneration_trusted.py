"""Bounded, lineage-preserving targeted LLM repair for World Forge lore."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from .world_forge_lore_scoring import WorldForgeLoreQualityError
from .world_forge_regeneration import (
    RegenerationRequest,
    _provider_generated,
    _quality_attempt_rows,
    _selected_best_candidate,
    enforce_targeted_regeneration,
    regeneration_request_from_error as _base_request_from_error,
    targeted_regeneration_context,
)
from .world_forge_review import is_reviewable_candidate_error, mark_needs_review


def regeneration_request_from_error(
    node: CampaignTopicNode,
    error: Exception,
    *,
    attempt: int,
) -> RegenerationRequest | None:
    request = _base_request_from_error(node, error, attempt=attempt)
    if request is not None:
        return request
    if not isinstance(error, WorldForgeLoreQualityError):
        return None
    entity_ids = sorted(
        {
            str(issue.item_id or "")
            for issue in error.assessment.issues
            if str(issue.item_id or "")
        }
    )
    reason_codes = sorted({issue.code for issue in error.assessment.issues})
    instructions = sorted(
        {
            issue.message
            or "Improve specificity, substance, distinctiveness, and canon coverage."
            for issue in error.assessment.issues
        }
    )
    return RegenerationRequest(
        topic_id=node.topic_id,
        attempt=attempt,
        reason_codes=tuple(reason_codes),
        entity_ids=tuple(entity_ids),
        fields=("short_summary", "dossier") if entity_ids else (),
        scope="entity_fields" if entity_ids else "topic",
        instructions=tuple(instructions),
    )


def _repair_context(
    campaign_context: Mapping[str, Any],
    request: RegenerationRequest,
    prior_topic: GeneratedTopic,
) -> dict[str, Any]:
    context = targeted_regeneration_context(campaign_context, request, prior_topic)
    repair = dict(context.get("targeted_regeneration") or {})
    allowed_paths: list[str] = []
    immutable_paths: list[str] = []
    for entity_id in request.entity_ids:
        prefix = f"entities[{entity_id}]"
        if request.scope == "entity_fields":
            allowed_paths.extend(f"{prefix}.{field}" for field in request.fields)
        else:
            allowed_paths.append(prefix)
        immutable_paths.extend(
            (
                f"{prefix}.id",
                f"{prefix}.entity_id",
                f"{prefix}.name",
                f"{prefix}.location_id",
                f"{prefix}.faction_id",
                f"{prefix}.faction_ids",
                f"{prefix}.structured_facts",
            )
        )
    repair.update(
        {
            "repair_type": "targeted_llm_repair",
            "allowed_paths": allowed_paths,
            "immutable_paths": immutable_paths,
            "instruction": (
                "Change only allowed paths. Preserve every established fact, ID, name, "
                "reference, relationship, and mechanical value outside those paths."
            ),
        }
    )
    context["targeted_regeneration"] = repair
    return context


def _review_candidate(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    error: Exception,
    *,
    attempt: int,
    history: list[dict[str, Any]],
) -> GeneratedTopic:
    reviewed = mark_needs_review(node, topic, error)
    return replace(
        reviewed,
        provenance={
            **dict(reviewed.provenance),
            "targeted_regeneration_attempt_count": attempt,
            "targeted_regeneration_history": list(history),
            "targeted_regeneration_succeeded": False,
            "lore_quality_attempts": _quality_attempt_rows(history),
            "lore_quality_selected_attempt": attempt,
            "lore_quality_total_attempts": attempt,
            "lore_quality_retry_count": max(0, attempt - 1),
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
    max_attempts: int = 3,
) -> GeneratedTopic:
    """Run one initial generation plus at most two narrowly scoped LLM repairs.

    Provider candidates that are structurally usable but fail a reviewable grounding,
    placeholder, or dossier-quality check are retained as ``needs_review`` when no
    safe targeted repair request can be derived. They never become ready canon, but
    their provider-authored lore is not discarded or replaced by application prose.
    """

    attempts = max(1, min(int(max_attempts), 3))
    context = dict(campaign_context)
    history: list[dict[str, Any]] = []
    last_error: Exception | None = None
    prior_failed_topic: GeneratedTopic | None = None
    pending_request: RegenerationRequest | None = None
    quality_candidates: list[tuple[int, int, GeneratedTopic]] = []

    for attempt in range(1, attempts + 1):
        generated = generator.generate(
            node,
            seed=seed + attempt - 1,
            campaign_context=context,
            dependency_topics=dependency_topics,
        )
        candidate = generated
        try:
            if pending_request is not None and prior_failed_topic is not None:
                candidate = enforce_targeted_regeneration(
                    prior_failed_topic,
                    generated,
                    pending_request,
                )
            processed = process(candidate)
        except Exception as error:
            last_error = error
            failing_topic = candidate
            if isinstance(error, WorldForgeLoreQualityError):
                quality_candidates.append(
                    (error.assessment.score, attempt, error.candidate_topic)
                )
                history.append(
                    {
                        "candidate_id": f"candidate:{node.topic_id}:attempt:{attempt}",
                        "parent_candidate_id": (
                            f"candidate:{node.topic_id}:attempt:{attempt - 1}"
                            if attempt > 1
                            else ""
                        ),
                        "generation_type": (
                            "initial" if attempt == 1 else "targeted_repair"
                        ),
                        "attempt": attempt,
                        "quality_score": error.assessment.score,
                        "quality_threshold": error.assessment.threshold,
                        "quality_status": error.assessment.status,
                        "quality_issue_codes": sorted(
                            {issue.code for issue in error.assessment.issues}
                        ),
                        "quality_entity_scores": dict(error.assessment.entity_scores),
                        "quality_dimensions": dict(error.assessment.dimensions),
                    }
                )

            if not _provider_generated(generated):
                raise

            request = regeneration_request_from_error(
                node,
                error,
                attempt=attempt + 1,
            )
            if request is None:
                if is_reviewable_candidate_error(error):
                    history.append(
                        {
                            "candidate_id": f"candidate:{node.topic_id}:attempt:{attempt}",
                            "parent_candidate_id": (
                                f"candidate:{node.topic_id}:attempt:{attempt - 1}"
                                if attempt > 1
                                else ""
                            ),
                            "generation_type": (
                                "initial" if attempt == 1 else "targeted_repair"
                            ),
                            "attempt": attempt,
                            "review_status": "needs_review",
                            "review_reason": str(error).split(":", 1)[0],
                            "repair_available": False,
                        }
                    )
                    return _review_candidate(
                        node,
                        failing_topic,
                        error,
                        attempt=attempt,
                        history=history,
                    )
                raise

            if attempt >= attempts:
                if quality_candidates:
                    return _selected_best_candidate(
                        quality_candidates,
                        attempts=attempt,
                        history=history,
                    )
                if is_reviewable_candidate_error(error):
                    return _review_candidate(
                        node,
                        failing_topic,
                        error,
                        attempt=attempt,
                        history=history,
                    )
                raise

            request_row = request.as_dict()
            request_row.update(
                {
                    "candidate_id": f"candidate:{node.topic_id}:attempt:{attempt + 1}",
                    "parent_candidate_id": f"candidate:{node.topic_id}:attempt:{attempt}",
                    "generation_type": "targeted_repair",
                    "source_attempt": attempt,
                }
            )
            history.append(request_row)
            if pending_request is None:
                prior_failed_topic = failing_topic
            context = _repair_context(
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
                    "candidate_id": f"candidate:{node.topic_id}:attempt:{attempt}",
                    "parent_candidate_id": (
                        f"candidate:{node.topic_id}:attempt:{attempt - 1}"
                        if attempt > 1
                        else ""
                    ),
                    "generation_type": (
                        "initial" if attempt == 1 else "targeted_repair"
                    ),
                    "attempt": attempt,
                    "quality_score": int(assessment.get("score") or 0),
                    "quality_threshold": int(assessment.get("threshold") or 0),
                    "quality_status": str(assessment.get("status") or "accepted"),
                    "quality_issue_codes": list(assessment.get("issue_codes") or ()),
                    "quality_entity_scores": dict(assessment.get("entity_scores") or {}),
                    "quality_dimensions": dict(assessment.get("dimensions") or {}),
                }
            )
        return replace(
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

    if quality_candidates:
        return _selected_best_candidate(
            quality_candidates,
            attempts=attempts,
            history=history,
        )
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"world_forge_regeneration_exhausted:{node.topic_id}")


__all__ = [
    "RegenerationRequest",
    "enforce_targeted_regeneration",
    "generate_with_targeted_regeneration",
    "regeneration_request_from_error",
]
