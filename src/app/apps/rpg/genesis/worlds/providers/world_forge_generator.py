"""Provider-backed World Forge generation implementation."""

from __future__ import annotations
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from typing import Any, Callable, Mapping

from app.providers.base import BaseProvider, ChatMessage
from app.providers.structured import StructuredOutputGateway, StructuredRetryBudget
from app.persistence.device_permits import device_permit_slot
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.providers.world_forge_foundation import (
    WorldForgeEntityRegistryItem,
    WorldForgeEntityRegistryResponse,
    WorldForgeProviderConfig,
    WorldForgeTopicResponse,
    _ConfiguredProviderView,
    _ENTITY_ID_PREFIXES,
    _LOGGER,
    _entity_registry_contract,
    _entity_registry_payload,
    _entity_registry_system_prompt,
    _model_rows,
    _payload,
    _system_prompt,
    _token_estimate,
    _topic_contract,
)


class ProviderWorldForgeTopicGenerator:
    """Generate one validated canon topic through a request-local gateway."""

    def __init__(self, provider: BaseProvider, config: WorldForgeProviderConfig) -> None:
        self.transport_provider = provider
        self.provider = _ConfiguredProviderView(provider, config.provider)
        self.config = config
        self._progress_callback: Callable[[Mapping[str, Any]], None] | None = None

    def set_progress_callback(
        self,
        callback: Callable[[Mapping[str, Any]], None] | None,
    ) -> Callable[[Mapping[str, Any]], None] | None:
        """Install a request-local batch checkpoint hook and return the prior hook."""

        previous = self._progress_callback
        self._progress_callback = callback
        return previous

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        batch_size = min(self.config.entity_batch_size, node.target_count)
        if node.target_count <= batch_size:
            value, trusted, prompt_tokens, completion_tokens = self._generate_response(
                node,
                seed=seed,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )
            generated = self._to_generated_topic(
                node,
                values=(value,),
                diagnostics=(trusted,),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            self._emit_progress(
                node,
                batch_current=1,
                batch_total=1,
                trusted=trusted,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            return generated

        batch_count = (node.target_count + batch_size - 1) // batch_size
        values: list[WorldForgeTopicResponse] = []
        registry, registry_diagnostics, prompt_tokens, completion_tokens = (
            self._generate_entity_registry(
                node,
                seed=seed,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )
        )
        registry_slots = {row.id: row for row in registry.entities}
        diagnostics: list[Mapping[str, Any]] = [registry_diagnostics]
        existing_entities: list[Mapping[str, str]] = []
        batch_workers = min(self.config.entity_batch_workers, batch_count)
        for wave_start in range(0, batch_count, batch_workers):
            wave_indexes = tuple(
                range(wave_start, min(batch_count, wave_start + batch_workers))
            )
            # Batches in one wave intentionally share the same completed-entity
            # context.  The next wave receives every entity from this wave, keeping
            # prompts bounded while maintaining a two-call local-model pipeline.
            completed_entities = tuple(existing_entities)
            with ThreadPoolExecutor(max_workers=len(wave_indexes)) as executor:
                futures = {
                    batch_index: executor.submit(
                        self._generate_entity_batch,
                        node,
                        batch_index=batch_index,
                        batch_count=batch_count,
                        seed=seed,
                        campaign_context=campaign_context,
                        dependency_topics=dependency_topics,
                        existing_entities=completed_entities,
                        registry_slots=registry_slots,
                    )
                    for batch_index in wave_indexes
                }
                wave_results = {
                    batch_index: future.result()
                    for batch_index, future in futures.items()
                }
            # A batch response is already checked against its assigned registry
            # slot by ``_topic_contract``.  Cross-batch comparison is needed only
            # for genuinely concurrent waves; applying it to a serial wave can
            # falsely reject a valid allocated ID carried in local-model output.
            if len(wave_indexes) > 1:
                self._validate_distinct_batch_entities(
                    tuple(value for value, _, _, _ in wave_results.values()),
                    existing_entities=completed_entities,
                )
            for batch_index in wave_indexes:
                value, trusted, batch_prompt_tokens, batch_completion_tokens = (
                    wave_results[batch_index]
                )
                values.append(value)
                diagnostics.append(trusted)
                prompt_tokens += batch_prompt_tokens
                completion_tokens += batch_completion_tokens
                self._emit_progress(
                    node,
                    batch_current=batch_index + 1,
                    batch_total=batch_count,
                    trusted=self._aggregate_usage(
                        tuple(dict(row.get("usage") or {}) for row in diagnostics)
                    ),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                existing_entities.extend(self._entity_identity_rows(value))
        self._validate_registry_alignment(values, registry.entities)
        return self._to_generated_topic(
            node,
            values=tuple(values),
            diagnostics=tuple(diagnostics),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            batch_size=batch_size,
            entity_registry=tuple(
                row.model_dump(mode="python") for row in registry.entities
            ),
        )

    def _generate_entity_batch(
        self,
        node: CampaignTopicNode,
        *,
        batch_index: int,
        batch_count: int,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
        existing_entities: tuple[Mapping[str, str], ...],
        registry_slots: Mapping[str, WorldForgeEntityRegistryItem],
    ) -> tuple[WorldForgeTopicResponse, Mapping[str, Any], int, int]:
        requested_count = min(
            self.config.entity_batch_size,
            node.target_count - batch_index * self.config.entity_batch_size,
        )
        assigned_entity_ids = self._assigned_entity_ids(
            node,
            batch_index=batch_index,
            requested_count=requested_count,
        )
        assigned_entities = tuple(
            registry_slots[entity_id].model_dump(mode="python")
            for entity_id in assigned_entity_ids
        )
        return self._generate_response(
            replace(node, target_count=requested_count),
            seed=seed + batch_index,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
            expected_entity_count=requested_count,
            expected_entity_ids=assigned_entity_ids,
            batch_index=batch_index,
            batch_count=batch_count,
            existing_entities=existing_entities,
            assigned_entity_ids=assigned_entity_ids,
            assigned_entities=assigned_entities,
        )

    def _generate_entity_registry(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> tuple[WorldForgeEntityRegistryResponse, Mapping[str, Any], int, int]:
        assigned_entity_ids = self._allocated_entity_ids(node)
        messages = [
            ChatMessage(role="system", content=_entity_registry_system_prompt(node)),
            ChatMessage(
                role="user",
                content=json.dumps(
                    _entity_registry_payload(
                        node,
                        seed=seed,
                        campaign_context=campaign_context,
                        dependency_topics=dependency_topics,
                        assigned_entity_ids=assigned_entity_ids,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        total_calls = max(1, self.config.max_retries + 2)
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        provider_key = self.config.provider.strip().casefold().removeprefix("llm:")
        call_limiter = (
            device_permit_slot("llm-local", priority="batch")
            if provider_key == "lmstudio"
            else nullcontext()
        )
        with call_limiter:
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    _entity_registry_contract(
                        node.topic_id,
                        expected_entity_ids=assigned_entity_ids,
                    ),
                    temperature=self.config.temperature,
                    max_tokens=min(self.config.max_tokens, 2048),
                ),
                model=self.config.model or None,
                retry_budget=StructuredRetryBudget(
                    max_provider_calls=total_calls,
                    max_transport_retries=self.config.max_retries,
                    max_format_downgrades=(
                        1 if self.config.lmstudio_schema_fallback else 0
                    ),
                    max_validation_regenerations=self.config.max_retries,
                    deadline_seconds=float(self.config.timeout_seconds),
                ),
            )
        diagnostics = outcome.diagnostics
        if outcome.error is not None:
            raise RuntimeError(
                f"structured World Forge registry provider failed for {node.topic_id} "
                f"after {diagnostics.provider_calls or total_calls} attempts: "
                f"{type(outcome.error).__name__}: {outcome.error}"
            ) from outcome.error
        assert outcome.value is not None
        registry_payload = json.dumps(
            outcome.value.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
        )
        prompt_tokens = sum(_token_estimate(message.content) for message in messages)
        completion_tokens = _token_estimate(registry_payload)
        return outcome.value, diagnostics.as_dict(), prompt_tokens, completion_tokens

    def _allocated_entity_ids(self, node: CampaignTopicNode) -> tuple[str, ...]:
        return self._assigned_entity_ids(
            node,
            batch_index=0,
            requested_count=node.target_count,
        )

    def _assigned_entity_ids(
        self,
        node: CampaignTopicNode,
        *,
        batch_index: int,
        requested_count: int,
    ) -> tuple[str, ...]:
        targeted = node.metadata.get("entity_dossier_regeneration")
        if isinstance(targeted, Mapping) and requested_count == 1:
            target_id = str(targeted.get("entity_id") or "").strip()
            if target_id:
                return (target_id,)
        prefix = _ENTITY_ID_PREFIXES.get(node.topic_id, node.topic_id)
        first_index = batch_index * self.config.entity_batch_size + 1
        return tuple(
            f"ent:{prefix}:{entity_index:03d}"
            for entity_index in range(first_index, first_index + requested_count)
        )

    @staticmethod
    def _entity_identity_rows(
        value: WorldForgeTopicResponse,
    ) -> list[dict[str, str]]:
        return [
            {
                "id": str(row.get("id") or row.get("entity_id") or ""),
                "name": str(row.get("name") or row.get("title") or ""),
            }
            for row in _model_rows(value.entities)
        ]

    @classmethod
    def _validate_distinct_batch_entities(
        cls,
        values: tuple[WorldForgeTopicResponse, ...],
        *,
        existing_entities: tuple[Mapping[str, str], ...],
    ) -> None:
        seen = {
            (str(row.get("id") or "").casefold(), str(row.get("name") or "").casefold())
            for row in existing_entities
        }
        for value in values:
            for row in cls._entity_identity_rows(value):
                identity = (row["id"].casefold(), row["name"].casefold())
                if identity in seen:
                    raise RuntimeError(
                        "structured World Forge provider returned duplicate entity "
                        f"across concurrent batches: {row['id'] or row['name'] or '<unnamed>'}"
                    )
                seen.add(identity)

    @staticmethod
    def _validate_registry_alignment(
        values: list[WorldForgeTopicResponse],
        registry: list[WorldForgeEntityRegistryItem],
    ) -> None:
        expected = {row.id: row.name.strip() for row in registry}
        actual = {
            str(row.get("id") or row.get("entity_id") or ""): str(
                row.get("name") or row.get("title") or ""
            ).strip()
            for value in values
            for row in _model_rows(value.entities)
        }
        if actual != expected:
            raise RuntimeError(
                "structured World Forge merge does not match the entity registry"
            )

    @staticmethod
    def _apply_registry_slots(
        value: WorldForgeTopicResponse,
        assigned_entities: tuple[Mapping[str, str], ...],
    ) -> WorldForgeTopicResponse:
        """Make the compact registry authoritative for merge-critical identity.

        Some local models place a label only inside a dossier or summary.  The
        registry already validated a unique canonical name, so apply that value
        rather than rejecting otherwise valid dossier content for omitting a
        duplicate top-level field.
        """

        if not assigned_entities:
            return value
        slots = {str(row["id"]): row for row in assigned_entities}
        payload = value.model_dump(mode="python")
        for entity in payload["entities"]:
            entity_id = str(entity.get("id") or entity.get("entity_id") or "")
            slot = slots.get(entity_id)
            if slot is None:
                raise RuntimeError(
                    f"structured World Forge entity is outside its registry slot: {entity_id}"
                )
            entity["id"] = slot["id"]
            entity["entity_id"] = slot["id"]
            entity["name"] = slot["name"]
            entity["registry_role"] = slot["role"]
            entity["registry_distinction"] = slot["distinction"]
        return WorldForgeTopicResponse.model_validate(payload)

    def _emit_progress(
        self,
        node: CampaignTopicNode,
        *,
        batch_current: int,
        batch_total: int,
        trusted: Mapping[str, Any],
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        callback = self._progress_callback
        if callback is None:
            return
        usage = dict(trusted.get("usage") or trusted)
        provider_total = _token_usage_value(usage, "total_tokens", "total")
        provider_prompt = _token_usage_value(usage, "prompt_tokens", "input_tokens")
        provider_completion = _token_usage_value(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        if provider_total:
            resolved_prompt = provider_prompt or prompt_tokens
            resolved_completion = provider_completion or completion_tokens
            total_tokens = provider_total
            source = "provider_reported"
        else:
            resolved_prompt = prompt_tokens
            resolved_completion = completion_tokens
            total_tokens = prompt_tokens + completion_tokens
            source = "estimated"
        try:
            callback(
                {
                    "topic_id": node.topic_id,
                    "batch_current": batch_current,
                    "batch_total": batch_total,
                    "token_usage": {
                        "prompt_tokens": resolved_prompt,
                        "completion_tokens": resolved_completion,
                        "total_tokens": total_tokens,
                        "source": source,
                    },
                }
            )
        except Exception:
            _LOGGER.warning(
                "world_forge_batch_progress_checkpoint_failed",
                exc_info=True,
                extra={"topic_id": node.topic_id},
            )

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
        messages = [
            ChatMessage(
                role="system",
                content=_system_prompt(
                    node,
                    batch_index=batch_index,
                    batch_count=batch_count,
                    existing_entities=existing_entities,
                    assigned_entity_ids=assigned_entity_ids,
                    assigned_entities=assigned_entities,
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    _payload(
                        node,
                        seed=seed,
                        campaign_context=campaign_context,
                        dependency_topics=dependency_topics,
                        batch_index=batch_index,
                        batch_count=batch_count,
                        existing_entities=existing_entities,
                        assigned_entity_ids=assigned_entity_ids,
                        assigned_entities=assigned_entities,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        total_calls = max(1, self.config.max_retries + 2)
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        provider_key = self.config.provider.strip().casefold().removeprefix("llm:")
        call_limiter = (
            device_permit_slot("llm-local", priority="batch")
            if provider_key == "lmstudio"
            else nullcontext()
        )
        with call_limiter:
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    _topic_contract(
                    node.topic_id,
                    expected_entity_count=expected_entity_count,
                    expected_entity_ids=expected_entity_ids,
                    expected_entity_names=expected_entity_names,
                    ),
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                ),
                model=self.config.model or None,
                retry_budget=StructuredRetryBudget(
                    max_provider_calls=total_calls,
                    max_transport_retries=self.config.max_retries,
                    max_format_downgrades=(
                        1 if self.config.lmstudio_schema_fallback else 0
                    ),
                    max_validation_regenerations=self.config.max_retries,
                    deadline_seconds=float(self.config.timeout_seconds),
                ),
            )
        diagnostics = outcome.diagnostics
        if outcome.error is not None:
            batch_label = (
                f" batch {batch_index + 1}/{batch_count}"
                if batch_index is not None and batch_count is not None
                else ""
            )
            raise RuntimeError(
                f"structured World Forge provider failed for {node.topic_id}{batch_label} "
                f"after {diagnostics.provider_calls or total_calls} attempts: "
                f"{type(outcome.error).__name__}: {outcome.error}"
            ) from outcome.error
        assert outcome.value is not None
        value = self._apply_registry_slots(outcome.value, assigned_entities)
        trusted = diagnostics.as_dict()
        generated_payload = json.dumps(
            value.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
        )
        prompt_tokens_estimate = sum(_token_estimate(message.content) for message in messages)
        completion_tokens_estimate = _token_estimate(generated_payload)
        return value, trusted, prompt_tokens_estimate, completion_tokens_estimate

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
        assert values and diagnostics
        trusted = diagnostics[-1]
        provider_provenance = dict(values[0].provenance)
        batch_count = len(values)
        if batch_count > 1:
            batch_receipts = [
                dict(value.provenance).get("authoritative_contract_receipt")
                for value in values
            ]
            receipt = dict(
                provider_provenance.get("authoritative_contract_receipt") or {}
            )
            receipt["authored_draft_hash"] = "sha256:" + hashlib.sha256(
                json.dumps(
                    [
                        dict(row).get("authored_draft_hash")
                        for row in batch_receipts
                        if isinstance(row, Mapping)
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            receipt["materialized_batch_count"] = batch_count
            provider_provenance["authoritative_contract_receipt"] = receipt
            provider_provenance["entity_batches"] = {
                "strategy": "sequential_entity_batches",
                "batch_count": batch_count,
                "batch_size": batch_size,
                "target_count": node.target_count,
            }
        if entity_registry:
            provider_provenance["entity_registry"] = {
                "contract": "rpg_world_forge_entity_registry_v1",
                "entities": [dict(row) for row in entity_registry],
            }
        usage = self._aggregate_usage(
            tuple(dict(row.get("usage") or {}) for row in diagnostics)
        )
        return GeneratedTopic(
            topic_id=node.topic_id,
            documents=tuple(
                row for value in values for row in _model_rows(value.documents)
            ),
            entities=tuple(
                row for value in values for row in _model_rows(value.entities)
            ),
            facts=tuple(row for value in values for row in _model_rows(value.facts)),
            relationships=tuple(
                row for value in values for row in _model_rows(value.relationships)
            ),
            knowledge_rules=tuple(
                row for value in values for row in _model_rows(value.knowledge_rules)
            ),
            story_threads=tuple(
                row for value in values for row in _model_rows(value.story_threads)
            ),
            provenance={
                **provider_provenance,
                "generator": "structured_world_forge_provider_v1",
                "provider_contract": "rpg_world_forge_topic_request_v3",
                "provider": self.config.provider,
                "model": self.config.model,
                "attempt_count": sum(
                    int(row.get("provider_calls") or 1) for row in diagnostics
                ),
                "latency_ms": round(
                    sum(float(row.get("latency_ms") or 0.0) for row in diagnostics),
                    3,
                ),
                "usage": usage,
                "token_estimate": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "method": "characters_divided_by_4",
                },
                "finish_reason": str(trusted.get("finish_reason") or ""),
                "entity_dossier_schema": "rpg_world_entity_dossier_v1",
                "response_format": str(trusted.get("selected_mode") or ""),
                "structured_contract": "rpg.world_forge.topic.v3",
                "prompt_version": self.config.prompt_version,
                "schema_hash": str(trusted.get("schema_hash") or ""),
                "provider_schema_hash": str(
                    trusted.get("provider_schema_hash") or ""
                ),
                "canonical_contract_hash": str(
                    trusted.get("canonical_contract_hash") or ""
                ),
                "strategy_identity": str(trusted.get("strategy_identity") or ""),
                "max_tokens": self.config.max_tokens,
            },
        )

    @staticmethod
    def _aggregate_usage(values: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
        totals: dict[str, Any] = {}
        for value in values:
            for key, item in value.items():
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    totals[key] = totals.get(key, 0) + item
        return totals


def _token_usage_value(usage: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            value = max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0
