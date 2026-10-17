"""Schema-pinned first-pass World Forge provider contracts.

The provider receives two different identity namespaces in the same response:
the root topic ID and one or more allocated entity IDs.  Keep those identities
in the JSON Schema itself so guided decoding cannot place an entity ID in the
root ``topic_id`` field.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
import math
from typing import Any, Mapping

from pydantic import ConfigDict, Field, create_model

from app.providers.base import ChatMessage
from app.providers.structured import StructuredContract, StructuredOutputGateway
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_dossier_quality import content_target
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.generation_contract_bundle import (
    TopicContractBundle,
    build_topic_contract_bundle,
)
from app.apps.rpg.genesis.worlds.generation_strategy import world_forge_strategy_identity
from app.apps.rpg.genesis.worlds.generation_failure_artifact import build_failure_artifact
from app.apps.rpg.genesis.worlds.providers.world_forge import (
    WorldForgeEntityRegistryItem,
    WorldForgeEntityRegistryResponse,
    WorldForgeTopicResponse,
    _entity_registry_payload,
    _payload,
    _token_estimate,
)
from app.apps.rpg.genesis.worlds.providers.single_pass import (
    SinglePassProviderWorldForgeTopicGenerator,
    SinglePassWorldForgeProviderError,
    _field_contract,
    _literal,
    _one_call_budget,
)
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.worlds_generation_first_pass_provider.authored_system_prompt', "1", 'You are the Omnix Campaign World Forge. Author rich, internally consistent campaign canon for exactly one topic, not player-facing turn narration. The campaign_context.world_brief and supplied dependencies are authoritative. Ground every name, institution, conflict, technology, culture, creature, and location in that brief. Do not fall back to generic genre conventions unless the brief supports them. Return exactly one bare JSON object: no markdown fences, commentary, or reasoning. The JSON Schema at required_output.authored_draft_schema is the sole output contract; unknown fields are forbidden. The root keys must be exactly topic_id, documents, entities, relationships, knowledge_rules, and story_threads. Include every root key even when its array is empty. Never return provenance or facts. Omnix materializes canonical IDs, facts, authority, visibility, provenance, and dossier display metadata after validation. Use the exact root topic_id {v0!r}. Never place an entity ID in root topic_id. Use each allocated entity ID exactly once and only at entities[].id: {v1}. Every entity must include the schema-required profile fields, short_summary, and dossier. The dossier object must contain subtitle, quote, quick_facts, sections, and related_entity_ids. Dossier section keys must be nested under entities[].dossier.sections, never directly under dossier. The exact dossier sections fragment is {v2}. Each section object contains only paragraphs; never return section id or title fields. Use short_summary for cards and substantive dossier paragraphs for lore. Put mechanics and references in their schema-defined fields. Never invent an unresolved dependency ID. This is entity batch {v3} of {v4}. Earlier entities that must not be duplicated: {v5}. Preserve assigned registry names, roles, and distinctions: {v6}.')
_PROMPT_2 = prompt_template('rpg.worlds_generation_first_pass_provider.authored_registry_system_prompt', "1", 'You are the Omnix Campaign World Forge planner. Return exactly one bare JSON object with no markdown, commentary, or reasoning. The root keys are exactly topic_id and entities; never return provenance. Set root topic_id to {v0!r}. Create exactly {v1} compact registry entries, one for every allocated ID, using each exactly once: {v2}. Each entity contains exactly id, name, role, and distinction. Names and distinctions must be unique, substantive, and grounded in the world brief and dependencies. Do not return dossiers, facts, documents, or authorship metadata.')
_PROMPT_3 = prompt_template('rpg.worlds_generation_first_pass_provider.targeted_instruction', "1", ' This is a dossier-only regeneration for the existing canonical entity {v0!r} named {v1!r}. Return exactly that entity and preserve its ID, name, structured facts, references, mechanics, and all schema-required profile fields from campaign_context.entity_dossier_regeneration. Author new prose only for short_summary and dossier. The dossier must contain at least {v2} words across all required sections (the validator requires {v3}, and this safety margin is intentional), use at least {v4} substantive sections, give every paragraph at least 24 words, and never repeat a paragraph. Keep each section distinct and grounded in the supplied canonical entity and quality issues.')

_SAFE_MODEL = re.compile(r"[^A-Za-z0-9_]+")


def _safe_name(value: str) -> str:
    return _SAFE_MODEL.sub("_", value).strip("_") or "topic"


def _authored_contract(bundle: TopicContractBundle) -> StructuredContract[Any]:
    return StructuredContract(
        contract_id=bundle.contract_id,
        version=6,
        output_model=bundle.authored_draft_model,
        semantic_validator=bundle.semantic_validator,
        schema_profile="canon_strict",
        schema_name=f"rpg_world_forge_authored_{_safe_name(bundle.contract_id)}",
        regenerate_on_semantic_failure=False,
        exact_json_object=True,
        max_raw_bytes=bundle.limits.max_raw_bytes,
        max_json_depth=bundle.limits.max_depth,
        max_json_nodes=bundle.limits.max_nodes,
        max_json_string_length=bundle.limits.max_string_length,
        max_json_array_length=bundle.limits.max_collection_rows,
    )


def _authored_system_prompt(
    node: CampaignTopicNode,
    bundle: TopicContractBundle,
    *,
    batch_index: int,
    batch_count: int,
    existing_entities: tuple[Mapping[str, str], ...],
    assigned_entity_ids: tuple[str, ...],
    assigned_entities: tuple[Mapping[str, str], ...],
) -> str:
    section_keys = tuple(
        section_id for section_id, _title in bundle.dossier_template
    )
    sections_fragment = {
        section_id: {"paragraphs": ["Write 1-3 substantive paragraphs."]}
        for section_id in section_keys
    }
    assigned_slot_text = "; ".join(
        f"{row['id']} = {row['name']} ({row['role']}; {row['distinction']})"
        for row in assigned_entities
    )
    exclusions = ", ".join(
        f"{row.get('id') or '<unknown>'} ({row.get('name') or 'unnamed'})"
        for row in existing_entities
    )
    targeted = node.metadata.get("entity_dossier_regeneration")
    targeted_instruction = ""
    if isinstance(targeted, Mapping):
        target_id = str(targeted.get("entity_id") or "")
        target_name = str(targeted.get("entity_name") or "")
        minimum_words, minimum_sections = content_target(node.topic_id)
        requested_words = math.ceil(minimum_words * 1.25)
        targeted_instruction = (
            _PROMPT_3.format(v0=(target_id), v1=(target_name), v2=(requested_words), v3=(minimum_words), v4=(minimum_sections))
        )
    return (
        _PROMPT_1.format(v0=(node.topic_id), v1=(', '.join(assigned_entity_ids) or 'none'), v2=(json.dumps(sections_fragment, ensure_ascii=False, sort_keys=True)), v3=(batch_index + 1), v4=(batch_count), v5=(exclusions or 'none'), v6=(assigned_slot_text or 'none'))
        + targeted_instruction
    )


def _authored_registry_system_prompt(
    node: CampaignTopicNode,
    entity_ids: tuple[str, ...],
) -> str:
    return (
        _PROMPT_2.format(v0=(node.topic_id), v1=(len(entity_ids)), v2=(', '.join(entity_ids) or 'none'))
    )


def _strict_registry_contract(
    expected_topic_id: str,
    *,
    expected_entity_ids: tuple[str, ...],
) -> StructuredContract[Any]:
    safe = _safe_name(expected_topic_id)
    item_model = create_model(
        f"WorldForgeStrictRegistryItem_{safe}",
        __base__=WorldForgeEntityRegistryItem,
        id=(_literal(expected_entity_ids), ...),
    )
    response_model = create_model(
        f"WorldForgeStrictRegistryResponse_{safe}",
        __config__=ConfigDict(extra="forbid"),
        topic_id=(_literal((expected_topic_id,)), ...),
        entities=(
            list[item_model],  # type: ignore[valid-type]
            Field(
                min_length=len(expected_entity_ids),
                max_length=len(expected_entity_ids),
            ),
        ),
    )
    def validate_registry(value: Any) -> None:
        actual_ids = tuple(str(row.id) for row in value.entities)
        if len(actual_ids) != len(expected_entity_ids):
            raise ValueError("world_forge_registry_entity_count_mismatch")
        if set(actual_ids) != set(expected_entity_ids) or len(actual_ids) != len(
            set(actual_ids)
        ):
            raise ValueError("world_forge_registry_entity_id_set_mismatch")

    return StructuredContract(
        contract_id=f"rpg.world_forge.entity_registry.{expected_topic_id}",
        version=2,
        output_model=response_model,
        semantic_validator=validate_registry,
        schema_profile="canon_strict",
        schema_name=f"rpg_world_forge_registry_strict_{safe}",
        regenerate_on_semantic_failure=False,
        exact_json_object=True,
        max_raw_bytes=65_536,
        max_json_depth=8,
        max_json_nodes=1_024,
        max_json_string_length=4_096,
        max_json_array_length=max(1, len(expected_entity_ids)),
    )


def _identity_contract(topic_id: str, entity_ids: tuple[str, ...]) -> dict[str, Any]:
    return {
        "root_topic_id": topic_id,
        "entity_id_path": "entities[].id",
        "allocated_entity_ids": list(entity_ids),
        "root_topic_id_must_not_be_an_entity_id": True,
    }


class FirstPassWorldForgeTopicGenerator(SinglePassProviderWorldForgeTopicGenerator):
    """Single-call generation with schema-pinned root and entity identities."""

    def _generate_response(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
        expected_entity_count: int | None = None,
        expected_entity_ids: tuple[str, ...] = (),
        expected_entity_names: tuple[str, ...] = (),
        batch_index: int | None = None,
        batch_count: int | None = None,
        existing_entities: tuple[Mapping[str, str], ...] = (),
        assigned_entity_ids: tuple[str, ...] = (),
        assigned_entities: tuple[Mapping[str, str], ...] = (),
    ) -> tuple[WorldForgeTopicResponse, Mapping[str, Any], int, int]:
        count = expected_entity_count or node.target_count
        ids = expected_entity_ids or self._assigned_entity_ids(
            node,
            batch_index=batch_index or 0,
            requested_count=count,
        )
        index = batch_index if batch_index is not None else 0
        total = batch_count if batch_count is not None else 1
        bundle = build_topic_contract_bundle(
            node,
            allocated_entity_ids=ids,
            dependencies=dependency_topics,
            expected_entity_count=count,
        )
        prompt = _authored_system_prompt(
            node,
            bundle,
            batch_index=index,
            batch_count=total,
            existing_entities=existing_entities,
            assigned_entity_ids=ids,
            assigned_entities=assigned_entities,
        )
        request = _payload(
            node,
            seed=seed,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
            batch_index=index,
            batch_count=total,
            existing_entities=existing_entities,
            assigned_entity_ids=ids,
            assigned_entities=assigned_entities,
        )
        request["required_output"] = {
            **dict(bundle.prompt_contract),
            "identity_contract": _identity_contract(node.topic_id, ids),
            "profile_field_contract": _field_contract(
                node,
                allocated_ids=ids,
                dependencies=dependency_topics,
            ),
        }
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(
                role="user",
                content=json.dumps(request, ensure_ascii=False, sort_keys=True),
            ),
        ]
        contract = _authored_contract(bundle)
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        with self._limiter():
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    contract,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                ),
                model=self.config.model or None,
                retry_budget=_one_call_budget(self.config.timeout_seconds),
            )
        if outcome.error is not None:
            diagnostics = {
                **bundle.descriptor(),
                **outcome.diagnostics.as_dict(),
            }
            artifact = build_failure_artifact(
                topic_id=node.topic_id,
                stage="provider_validation",
                error=outcome.error,
                raw_text="",
                diagnostics=diagnostics,
            )
            diagnostics["failure_artifact"] = artifact.model_dump(mode="json")
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                outcome.error,
                diagnostics,
                unit="topic",
            ) from outcome.error
        assert outcome.value is not None
        outcome_diagnostics = outcome.diagnostics.as_dict()
        try:
            value = bundle.materializer(outcome.value)
        except Exception as exc:
            diagnostics = {**bundle.descriptor(), **outcome_diagnostics}
            artifact = build_failure_artifact(
                topic_id=node.topic_id,
                stage="materialization",
                error=exc,
                raw_text="",
                diagnostics=diagnostics,
            )
            diagnostics["failure_artifact"] = artifact.model_dump(mode="json")
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                exc,
                diagnostics,
                unit="topic",
            ) from exc
        strategy_identity = world_forge_strategy_identity(
            provider=str(outcome_diagnostics.get("provider") or self.config.provider),
            model=str(outcome_diagnostics.get("model") or self.config.model),
            selected_mode=str(outcome_diagnostics.get("selected_mode") or ""),
            prompt_version=self.config.prompt_version,
            contract_descriptor=bundle.descriptor(),
        )
        provenance = dict(value.provenance)
        receipt = dict(provenance.get("authoritative_contract_receipt") or {})
        receipt["provider_wire_schema_hash"] = str(
            outcome_diagnostics.get("provider_schema_hash") or ""
        )
        receipt["strategy_identity"] = strategy_identity
        provenance.update(
            {
                "authoritative_contract_receipt": receipt,
                "strategy_identity": strategy_identity,
            }
        )
        value = value.model_copy(update={"provenance": provenance})
        value = self._apply_registry_slots(value, assigned_entities)
        rendered = json.dumps(
            value.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            value,
            {
                **bundle.descriptor(),
                **outcome_diagnostics,
                "strategy_identity": strategy_identity,
            },
            sum(_token_estimate(message.content) for message in messages),
            _token_estimate(rendered),
        )

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
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        with self._limiter():
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    _strict_registry_contract(
                        node.topic_id,
                        expected_entity_ids=ids,
                    ),
                    temperature=self.config.temperature,
                    max_tokens=min(self.config.max_tokens, 2048),
                ),
                model=self.config.model or None,
                retry_budget=_one_call_budget(self.config.timeout_seconds),
            )
        if outcome.error is not None:
            diagnostics = outcome.diagnostics.as_dict()
            artifact = build_failure_artifact(
                topic_id=node.topic_id,
                stage="provider_validation",
                error=outcome.error,
                raw_text="",
                diagnostics=diagnostics,
            )
            diagnostics["failure_artifact"] = artifact.model_dump(mode="json")
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                outcome.error,
                diagnostics,
                unit="registry",
            ) from outcome.error
        assert outcome.value is not None
        canonical_registry = WorldForgeEntityRegistryResponse(
            topic_id=node.topic_id,
            entities=[
                WorldForgeEntityRegistryItem.model_validate(
                    row.model_dump(mode="python")
                )
                for row in outcome.value.entities
            ],
            provenance={"materialized_by": "omnix_entity_registry_v2"},
        )
        rendered = json.dumps(
            canonical_registry.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            canonical_registry,
            outcome.diagnostics.as_dict(),
            sum(_token_estimate(message.content) for message in messages),
            _token_estimate(rendered),
        )


__all__ = ["FirstPassWorldForgeTopicGenerator"]
