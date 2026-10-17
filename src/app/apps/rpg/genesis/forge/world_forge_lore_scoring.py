"""Score live provider-authored lore without synthesising replacement prose.

Hard schema, reference, and contradiction failures are rejected by earlier validation
layers. This module handles readable-lore quality: it records a durable 0-100 score,
requests bounded LLM retries below the preferred threshold, and lets the retry
coordinator retain the best structurally valid candidate for Game Master review.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import fmean
from typing import Any, Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic
from .world_forge_integrity import WorldForgeIntegrityError, WorldForgeIntegrityIssue
from .world_forge_lore_quality import provider_lore_quality_issues
from .world_forge_timeline_quality import timeline_lore_quality_issues

_DEFAULT_THRESHOLD = 80

_ISSUE_WEIGHTS: Mapping[str, int] = {
    "provider_lore_summary_too_short": 8,
    "provider_lore_summary_fragment": 6,
    "provider_lore_required_sections_missing": 18,
    "provider_lore_duplicate_section_heading": 8,
    "provider_lore_empty_section": 12,
    "provider_lore_paragraph_too_short": 5,
    "provider_lore_paragraph_fragment": 5,
    "provider_lore_field_label_fragment": 12,
    "provider_lore_generic_or_deterministic_filler": 18,
    "provider_lore_duplicate_paragraph": 12,
    "provider_lore_total_too_short": 20,
    "provider_lore_reference_not_explained": 10,
    "provider_lore_field_not_explained": 8,
    "provider_lore_world_brief_echo_name": 20,
    "provider_lore_duplicate_entity_prose": 20,
    "provider_timeline_document_too_short": 25,
    "provider_timeline_entries_missing": 30,
    "provider_timeline_temporal_marker_missing": 18,
    "provider_timeline_duplicate_temporal_marker": 8,
    "provider_timeline_entry_too_short": 18,
    "provider_timeline_entry_insufficient_narrative": 12,
    "provider_timeline_cause_missing": 12,
    "provider_timeline_event_missing": 14,
    "provider_timeline_participants_missing": 15,
    "provider_timeline_consequences_missing": 14,
    "provider_timeline_legacy_missing": 14,
    "provider_timeline_sources_or_uncertainty_missing": 10,
    "provider_timeline_timekeeping_missing": 14,
    "provider_timeline_cycle_missing": 12,
    "provider_timeline_observance_missing": 12,
    "provider_timeline_social_impact_missing": 12,
}

_DIMENSION_CODES: Mapping[str, frozenset[str]] = {
    "structure": frozenset(
        {
            "provider_lore_required_sections_missing",
            "provider_lore_duplicate_section_heading",
            "provider_lore_empty_section",
            "provider_lore_paragraph_fragment",
            "provider_lore_summary_fragment",
            "provider_timeline_entries_missing",
            "provider_timeline_temporal_marker_missing",
            "provider_timeline_duplicate_temporal_marker",
        }
    ),
    "detail": frozenset(
        {
            "provider_lore_summary_too_short",
            "provider_lore_paragraph_too_short",
            "provider_lore_total_too_short",
            "provider_timeline_document_too_short",
            "provider_timeline_entry_too_short",
            "provider_timeline_entry_insufficient_narrative",
        }
    ),
    "natural_prose": frozenset(
        {
            "provider_lore_field_label_fragment",
            "provider_lore_generic_or_deterministic_filler",
        }
    ),
    "canon_coverage": frozenset(
        {
            "provider_lore_reference_not_explained",
            "provider_lore_field_not_explained",
            "provider_timeline_cause_missing",
            "provider_timeline_event_missing",
            "provider_timeline_participants_missing",
            "provider_timeline_consequences_missing",
            "provider_timeline_legacy_missing",
            "provider_timeline_sources_or_uncertainty_missing",
            "provider_timeline_timekeeping_missing",
            "provider_timeline_cycle_missing",
            "provider_timeline_observance_missing",
            "provider_timeline_social_impact_missing",
        }
    ),
    "distinctiveness": frozenset(
        {
            "provider_lore_duplicate_paragraph",
            "provider_lore_duplicate_entity_prose",
            "provider_lore_world_brief_echo_name",
        }
    ),
}


@dataclass(frozen=True)
class LoreQualityAssessment:
    score: int
    threshold: int
    passed: bool
    status: str
    issues: tuple[WorldForgeIntegrityIssue, ...]
    entity_scores: Mapping[str, int]
    dimensions: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "rpg_provider_lore_quality_v2",
            "score": self.score,
            "threshold": self.threshold,
            "passed": self.passed,
            "status": self.status,
            "issues": [issue.as_dict() for issue in self.issues],
            "issue_codes": sorted({issue.code for issue in self.issues}),
            "entity_scores": dict(self.entity_scores),
            "dimensions": dict(self.dimensions),
        }


class WorldForgeLoreQualityError(WorldForgeIntegrityError):
    """Retryable soft-quality failure carrying the usable scored candidate."""

    def __init__(
        self,
        assessment: LoreQualityAssessment,
        candidate_topic: GeneratedTopic,
    ) -> None:
        self.assessment = assessment
        self.candidate_topic = candidate_topic
        super().__init__(assessment.issues)


def _entity_ids(topic: GeneratedTopic) -> tuple[str, ...]:
    values = tuple(
        str(row.get("id") or row.get("entity_id") or "").strip()
        for row in topic.entities
    )
    return tuple(value for value in values if value)


def _threshold(node: CampaignTopicNode) -> int:
    raw = node.metadata.get("lore_quality")
    if isinstance(raw, Mapping):
        try:
            return max(50, min(100, int(raw.get("preferred_score") or _DEFAULT_THRESHOLD)))
        except (TypeError, ValueError):
            pass
    return _DEFAULT_THRESHOLD


def assess_provider_lore_quality(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> LoreQualityAssessment:
    issues = (
        *provider_lore_quality_issues(node, topic, campaign_context),
        *timeline_lore_quality_issues(node, topic),
    )
    entity_ids = _entity_ids(topic)
    penalties = {entity_id: 0 for entity_id in entity_ids}
    global_penalty = 0
    for issue in issues:
        weight = _ISSUE_WEIGHTS.get(issue.code, 8)
        if issue.item_id in penalties:
            penalties[issue.item_id] += weight
        else:
            global_penalty += weight

    if penalties:
        entity_scores = {
            entity_id: max(0, 100 - penalty - global_penalty)
            for entity_id, penalty in penalties.items()
        }
        score = round(fmean(entity_scores.values()))
    else:
        entity_scores = {}
        score = max(0, 100 - global_penalty)

    dimensions: dict[str, int] = {}
    for dimension, codes in _DIMENSION_CODES.items():
        penalty = sum(
            _ISSUE_WEIGHTS.get(issue.code, 8)
            for issue in issues
            if issue.code in codes
        )
        dimensions[dimension] = max(0, 100 - penalty)

    threshold = _threshold(node)
    passed = score >= threshold
    return LoreQualityAssessment(
        score=score,
        threshold=threshold,
        passed=passed,
        status="accepted" if passed else "needs_review",
        issues=issues,
        entity_scores=entity_scores,
        dimensions=dimensions,
    )


def attach_lore_quality(
    topic: GeneratedTopic,
    assessment: LoreQualityAssessment,
) -> GeneratedTopic:
    return replace(
        topic,
        provenance={
            **dict(topic.provenance),
            "lore_quality": assessment.as_dict(),
            "lore_quality_score": assessment.score,
            "lore_quality_threshold": assessment.threshold,
            "lore_quality_status": assessment.status,
            "lore_quality_needs_review": not assessment.passed,
        },
    )


def require_preferred_lore_quality(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    campaign_context: Mapping[str, Any] | None = None,
) -> GeneratedTopic:
    assessment = assess_provider_lore_quality(node, topic, campaign_context)
    scored = attach_lore_quality(topic, assessment)
    if not assessment.passed:
        raise WorldForgeLoreQualityError(assessment, scored)
    return scored


__all__ = [
    "LoreQualityAssessment",
    "WorldForgeLoreQualityError",
    "assess_provider_lore_quality",
    "attach_lore_quality",
    "require_preferred_lore_quality",
]
