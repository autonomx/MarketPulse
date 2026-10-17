"""Reference-safe adapter shared by explicit test and live World Forge generation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_canon_lookup_trusted import attach_structured_canon_lookup
from .world_forge_contract import CampaignTopicNode
from .world_forge_deterministic import DeterministicWorldForgeGenerator
from .world_forge_deterministic_completion import complete_deterministic_references
from .world_forge_dossier_quality import (
    dossier_word_count,
    enrich_fallback_dossier,
    validate_dossier_quality,
)
from .world_forge_dossiers import validate_entity_dossier
from .world_forge_dossiers_trusted import project_entity_dossier
from .world_forge_domains import (
    normalize_structured_domain,
    validate_world_brief_grounding,
)
from .world_forge_fact_pipeline_fixture import compile_deterministic_fixture_facts
from .world_forge_fact_pipeline_trusted import (
    compile_structured_entity_facts as compile_trusted_structured_entity_facts,
)
from .world_forge_generation import GeneratedTopic, WorldForgeTopicGenerator
from .world_forge_integrity import validate_and_normalize_provider_topic
from .world_forge_lore_scoring import require_preferred_lore_quality
from .world_forge_presentation_trusted import render_fact_derived_presentations
from .world_forge_profile_deterministic import generate_deterministic_profile_topic
from .world_forge_regeneration import (
    RegenerationRequest,
    enforce_targeted_regeneration,
)
from .world_forge_regeneration_trusted import generate_with_targeted_regeneration
from .world_forge_semantic_quality import require_topic_semantic_quality


class ReferenceSafeWorldForgeGenerator:
    """Validate a provider candidate and apply bounded, path-restricted repair."""

    def __init__(self, generator: WorldForgeTopicGenerator) -> None:
        if generator is None:
            raise ValueError("world_forge_generator_required")
        self.generator = generator

    @staticmethod
    def _provider_generated(topic: GeneratedTopic) -> bool:
        return str(dict(topic.provenance).get("generator") or "").startswith(
            "structured_world_forge_provider_"
        )

    @staticmethod
    def _manual_retry_config(
        campaign_context: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        directives = campaign_context.get("topic_directives")
        directives = directives if isinstance(directives, Mapping) else {}
        retry = directives.get("manual_retry")
        return retry if isinstance(retry, Mapping) else None

    @classmethod
    def _manual_retry_candidate(
        cls,
        topic: GeneratedTopic,
        campaign_context: Mapping[str, Any],
    ) -> GeneratedTopic:
        retry = cls._manual_retry_config(campaign_context)
        if retry is None:
            return topic
        prior_value = retry.get("prior_candidate")
        if not isinstance(prior_value, Mapping):
            return topic
        prior = GeneratedTopic.from_dict(prior_value)
        request = RegenerationRequest(
            topic_id=topic.topic_id,
            attempt=1,
            reason_codes=tuple(str(value) for value in retry.get("reason_codes") or ()),
            entity_ids=tuple(str(value) for value in retry.get("entity_ids") or ()),
            fields=tuple(str(value) for value in retry.get("fields") or ()),
            scope=str(retry.get("scope") or "topic"),
            instructions=tuple(str(value) for value in retry.get("instructions") or ()),
        )
        return enforce_targeted_regeneration(prior, topic, request)

    @classmethod
    def _mark_manual_decision_required(
        cls,
        node: CampaignTopicNode,
        topic: GeneratedTopic,
        campaign_context: Mapping[str, Any],
    ) -> GeneratedTopic:
        retry = cls._manual_retry_config(campaign_context)
        if retry is None or not isinstance(retry.get("prior_candidate"), Mapping):
            return topic
        return replace(
            topic,
            provenance={
                **dict(topic.provenance),
                "generation_status": "needs_review",
                "manual_retry_pending_decision": True,
                "manual_retry_parent_run_id": str(retry.get("parent_run_id") or ""),
                "generation_review": {
                    "schema_version": "rpg_world_generation_review_v1",
                    "status": "needs_review",
                    "blocking": True,
                    "error_type": "ManualRetryDecisionRequired",
                    "reason_codes": ["manual_retry_decision_required"],
                    "issues": [
                        {
                            "code": "manual_retry_decision_required",
                            "topic_id": node.topic_id,
                            "entity_id": "",
                            "field_id": "",
                            "message": (
                                "The retry candidate passed validation and requires an explicit "
                                "Game Master keep or replace decision."
                            ),
                        }
                    ],
                    "summary": "Valid retry candidate awaits explicit Game Master promotion.",
                },
            },
        )

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        def process(topic: GeneratedTopic) -> GeneratedTopic:
            scoped = self._manual_retry_candidate(topic, campaign_context)
            return self._process_topic(
                node,
                scoped,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )

        if isinstance(self.generator, DeterministicWorldForgeGenerator):
            generated = (
                generate_deterministic_profile_topic(
                    node,
                    campaign_context=campaign_context,
                    dependency_topics=dependency_topics,
                )
                if node.metadata.get("field_definitions")
                else self.generator.generate(
                    node,
                    seed=seed,
                    campaign_context=campaign_context,
                    dependency_topics=dependency_topics,
                )
            )
            processed = process(generated)
        else:
            processed = generate_with_targeted_regeneration(
                self.generator,
                node,
                seed=seed,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
                process=process,
                max_attempts=3,
            )
        processed = self._mark_manual_decision_required(
            node,
            processed,
            campaign_context,
        )
        return attach_structured_canon_lookup(processed)

    def _process_topic(
        self,
        node: CampaignTopicNode,
        topic: GeneratedTopic,
        *,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        provider_generated = self._provider_generated(topic)
        profile_defined = bool(node.metadata.get("field_definitions"))
        if provider_generated and not profile_defined:
            aliases = campaign_context.get("reference_aliases")
            topic = validate_and_normalize_provider_topic(
                node,
                topic,
                dependency_topics,
                aliases=dict(aliases) if isinstance(aliases, Mapping) else None,
            )

        if not profile_defined:
            topic = normalize_structured_domain(
                node,
                topic,
                dependency_topics,
                allow_synthetic_completion=not provider_generated,
            )

        if not provider_generated and profile_defined:
            topic = generate_deterministic_profile_topic(
                node,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )
        elif not provider_generated:
            topic = complete_deterministic_references(
                node,
                topic,
                dependency_topics,
            )

        topic = (
            compile_trusted_structured_entity_facts(
                node,
                topic,
                dependency_topics,
            )
            if provider_generated
            else compile_deterministic_fixture_facts(
                node,
                topic,
                dependency_topics,
            )
        )
        if provider_generated:
            semantic_report = require_topic_semantic_quality(
                node,
                topic,
                campaign_context,
            )
            topic = replace(
                topic,
                provenance={
                    **dict(topic.provenance),
                    "semantic_quality": semantic_report.as_dict(),
                },
            )
        validate_world_brief_grounding(node, topic, campaign_context)
        if profile_defined:
            rendered = render_fact_derived_presentations(node, topic)
            if provider_generated:
                rendered = require_preferred_lore_quality(
                    node,
                    rendered,
                    campaign_context,
                )
            return rendered
        return self._normalize_entity_dossiers(
            node,
            topic,
            allow_fixture_enrichment=not provider_generated,
        )

    @staticmethod
    def _normalize_entity_dossiers(
        node: CampaignTopicNode,
        topic: GeneratedTopic,
        *,
        allow_fixture_enrichment: bool,
    ) -> GeneratedTopic:
        if not topic.entities:
            return topic
        content = topic.as_dict()
        entities: list[dict[str, Any]] = []
        word_counts: dict[str, int] = {}
        for entity in topic.entities:
            row = dict(entity)
            entity_id = str(row.get("id") or row.get("entity_id") or "")
            short_summary, dossier = project_entity_dossier(
                row,
                card_type=node.topic_id,
                content=content,
                entity_id=entity_id,
            )
            quality_issues = validate_dossier_quality(dossier, topic_id=node.topic_id)
            if quality_issues and allow_fixture_enrichment:
                dossier = enrich_fallback_dossier(
                    row,
                    dossier,
                    topic_id=node.topic_id,
                )
                quality_issues = validate_dossier_quality(
                    dossier,
                    topic_id=node.topic_id,
                )
            schema_issues = validate_entity_dossier(dossier)
            issues = (*schema_issues, *quality_issues)
            if issues:
                raise ValueError(
                    f"world_entity_dossier_quality:{node.topic_id}:{entity_id}:"
                    + ",".join(issues)
                )
            row["short_summary"] = short_summary
            row["dossier"] = dossier
            entities.append(row)
            if entity_id:
                word_counts[entity_id] = dossier_word_count(dossier)
        return replace(
            topic,
            entities=tuple(entities),
            provenance={
                **dict(topic.provenance),
                "entity_dossier_schema": "rpg_world_entity_dossier_v1",
                "entity_dossier_quality_validated": True,
                "entity_dossier_word_counts": word_counts,
                "deterministic_fixture_enrichment": bool(allow_fixture_enrichment),
            },
        )
