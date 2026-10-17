"""Attach manual-review provenance when automatic structured recovery was used."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.generation_structured_recovery import recovery_review
from app.apps.rpg.genesis.worlds.providers.world_forge import WorldForgeTopicResponse


class StructuredRecoveryReviewMixin:
    """Mark every automatically recovered candidate for explicit review."""

    def _to_generated_topic(
        self,
        node: CampaignTopicNode,
        *,
        values: tuple[WorldForgeTopicResponse, ...],
        diagnostics: tuple[Mapping[str, Any], ...],
        prompt_tokens: int,
        completion_tokens: int,
        batch_size: int | None = None,
        entity_registry: tuple[Mapping[str, Any], ...] = (),
    ) -> GeneratedTopic:
        topic = super()._to_generated_topic(  # type: ignore[misc]  # mixin over the generator
            node,
            values=values,
            diagnostics=diagnostics,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            batch_size=batch_size,
            entity_registry=entity_registry,
        )
        records = tuple(
            dict(record)
            for row in diagnostics
            for record in (row.get("structured_recovery"),)
            if isinstance(record, Mapping)
        )
        if not records:
            return topic
        return replace(
            topic,
            provenance={
                **dict(topic.provenance),
                "structured_recovery": {"records": [dict(row) for row in records]},
                "generation_status": "needs_review",
                "generation_review": recovery_review(node.topic_id, records),
            },
        )


__all__ = ["StructuredRecoveryReviewMixin"]
