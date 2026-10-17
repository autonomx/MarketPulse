"""Registry-generation mixin for bounded same-model World Forge recovery."""
from __future__ import annotations

import json
from typing import Any, Mapping, TYPE_CHECKING

from app.providers.base import ChatMessage
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.generation_first_pass_provider import (
    _authored_registry_system_prompt,
    _identity_contract,
    _strict_registry_contract,
)
from app.apps.rpg.genesis.worlds.providers.world_forge import (
    WorldForgeEntityRegistryItem,
    WorldForgeEntityRegistryResponse,
    _entity_registry_payload,
    _token_estimate,
)


class StructuredRegistryRecoveryMixin:
    """Generate a registry and use the configured model to restructure it once."""

    if TYPE_CHECKING:
        # Provided by the generator class this mixes into.
        def __getattr__(self, name: str) -> Any: ...

    def _generate_entity_registry(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> tuple[WorldForgeEntityRegistryResponse, Mapping[str, Any], int, int]:
        ids = self._allocated_entity_ids(node)
        request = _entity_registry_payload(
            node,
            seed=seed,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
            assigned_entity_ids=ids,
        )
        request["required_output"]["identity_contract"] = _identity_contract(
            node.topic_id,
            ids,
        )
        request["required_output"].pop("provenance", None)
        messages = [
            ChatMessage(
                role="system",
                content=_authored_registry_system_prompt(node, ids),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(request, ensure_ascii=False, sort_keys=True),
            ),
        ]
        contract = _strict_registry_contract(
            node.topic_id,
            expected_entity_ids=ids,
        )
        max_tokens = min(self.config.max_tokens, 2048)
        outcome, raw_text = self._provider_call(
            messages,
            contract=contract,
            max_tokens=max_tokens,
            temperature=self.config.temperature,
        )
        if outcome.error is None and outcome.value is not None:
            recovered = self._recovered_value_type(
                outcome.value,
                outcome.diagnostics.as_dict(),
                sum(_token_estimate(message.content) for message in messages),
                _token_estimate(raw_text),
            )
        else:
            recovered = self._recover(
                contract=contract,
                outcome=outcome,
                raw_text=raw_text,
                original_messages=messages,
                expected_topic_id=node.topic_id,
                allocated_entity_ids=ids,
                allowed_reference_ids=frozenset(ids),
                expected_entity_kind="",
                field_definitions=(),
                max_tokens=max_tokens,
                retain_invalid_kind="registry",
            )
        canonical = WorldForgeEntityRegistryResponse(
            topic_id=node.topic_id,
            entities=[
                WorldForgeEntityRegistryItem.model_validate(
                    row.model_dump(mode="python")
                )
                for row in recovered.value.entities
            ],
            provenance={"materialized_by": "omnix_entity_registry_v2"},
        )
        return (
            canonical,
            recovered.diagnostics,
            recovered.prompt_tokens,
            recovered.completion_tokens,
        )


__all__ = ["StructuredRegistryRecoveryMixin"]
