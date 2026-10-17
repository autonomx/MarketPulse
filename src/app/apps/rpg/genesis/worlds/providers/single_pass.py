"""Profile-typed, one-call provider adapter for durable World Forge jobs."""
from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import replace
from typing import Any, Literal, Mapping, Sequence, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    create_model,
)

from app.providers.base import ChatMessage
from app.persistence.device_permits import device_permit_slot
from app.providers.structured import (
    StructuredContract,
    StructuredOutputGateway,
    StructuredRetryBudget,
)
from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.providers.world_forge import (
    ProviderWorldForgeTopicGenerator,
    WorldForgeDossier,
    WorldForgeEntityRegistryResponse,
    WorldForgeTopicResponse,
    _entity_registry_contract,
    _entity_registry_payload,
    _entity_registry_system_prompt,
    _model_rows,
    _payload,
    _system_prompt,
    _token_estimate,
    _topic_contract,
)
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.worlds_providers_single_pass.prompt', "1", (
    " PROFILE_FIELD_CONTRACT is authoritative. Use the exact allocated ID "
            "and entity kind, include all required top-level fields, obey declared "
            "JSON types, and use only listed reference IDs. Unknown fields are forbidden."
))

_SAFE_MODEL = re.compile(r"[^A-Za-z0-9_]+")


class SinglePassWorldForgeProviderError(RuntimeError):
    """One failed provider generation unit with its structured diagnostics."""

    def __init__(
        self,
        topic_id: str,
        error: Exception,
        diagnostics: Mapping[str, Any],
        *,
        unit: str,
    ) -> None:
        self.topic_id = topic_id
        self.error = error
        self.diagnostics = dict(diagnostics)
        self.unit = unit
        super().__init__(
            f"world_forge_single_pass_{unit}_failed:{topic_id}:"
            f"{type(error).__name__}:{error}"
        )


def _literal(values: Sequence[str]) -> Any:
    unique = tuple(dict.fromkeys(str(value) for value in values if str(value)))
    return Literal.__getitem__(unique) if unique else StrictStr


def _definitions(node: CampaignTopicNode) -> tuple[dict[str, Any], ...]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _allocated_reference_ids(
    node: CampaignTopicNode,
    dependencies: Mapping[str, GeneratedTopic],
    own_ids: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    by_domain = {
        domain_id: tuple(
            str(row.get("id") or row.get("entity_id") or "")
            for row in topic.entities
            if str(row.get("id") or row.get("entity_id") or "")
        )
        for domain_id, topic in dependencies.items()
    }
    by_domain[node.topic_id] = own_ids
    return {
        str(definition.get("field_id") or ""): tuple(
            dict.fromkeys(
                entity_id
                for domain_id in definition.get("allowed_target_domains") or ()
                for entity_id in by_domain.get(str(domain_id), ())
                if entity_id
            )
        )
        for definition in _definitions(node)
    }


def _field_type(definition: Mapping[str, Any], references: tuple[str, ...]) -> Any:
    kind = str(definition.get("value_type") or "string")
    if kind == "string":
        return StrictStr
    if kind == "integer":
        return StrictInt
    if kind == "number":
        return StrictInt | StrictFloat
    if kind == "boolean":
        return StrictBool
    if kind == "enum":
        return _literal(tuple(str(value) for value in definition.get("enum_values") or ()))
    if kind == "entity_ref":
        return _literal(references)
    if kind == "entity_ref_list":
        return list[_literal(references)]  # type: ignore[misc]
    if kind == "structured_object":
        return dict[str, Any] | list[Any]
    return Any


def _entity_model(
    node: CampaignTopicNode,
    *,
    allocated_ids: tuple[str, ...],
    dependencies: Mapping[str, GeneratedTopic],
) -> type[BaseModel]:
    references = _allocated_reference_ids(node, dependencies, allocated_ids)
    fields: dict[str, tuple[Any, Any]] = {
        "id": (_literal(allocated_ids), ...),
        "kind": (_literal((str(node.metadata.get("entity_kind") or node.topic_id),)), ...),
    }
    for definition in _definitions(node):
        field_id = str(definition.get("field_id") or "").strip()
        if not field_id or field_id in fields:
            continue
        annotation = _field_type(definition, references.get(field_id, ()))
        required = bool(definition.get("required", False))
        fields[field_id] = (
            annotation if required else annotation | None,
            Field(
                default=... if required else None,
                description=str(definition.get("description") or ""),
            ),
        )
    # Identity aliases remain optional because approved profiles may use ``id`` or
    # ``entity_id``. Reading prose is mandatory for every generated entity.
    fields.setdefault("name", (StrictStr | None, None))
    fields.setdefault("entity_id", (StrictStr | None, None))
    fields["short_summary"] = (StrictStr, ...)
    fields["dossier"] = (WorldForgeDossier, ...)
    fields.setdefault("registry_role", (StrictStr | None, None))
    fields.setdefault("registry_distinction", (StrictStr | None, None))
    safe = _SAFE_MODEL.sub("_", node.topic_id).strip("_") or "topic"
    return cast(Any, create_model)(
        f"WorldForgeProfileEntity_{safe}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _profile_contract(
    node: CampaignTopicNode,
    *,
    expected_count: int,
    expected_ids: tuple[str, ...],
    expected_names: tuple[str, ...],
    dependencies: Mapping[str, GeneratedTopic],
) -> StructuredContract[Any]:
    safe = _SAFE_MODEL.sub("_", node.topic_id).strip("_") or "topic"
    entity_model = _entity_model(
        node,
        allocated_ids=expected_ids,
        dependencies=dependencies,
    )
    response_model = create_model(
        f"WorldForgeProfileTopicResponse_{safe}",
        __base__=WorldForgeTopicResponse,
        entities=(list[entity_model], ...),  # type: ignore[valid-type]
    )

    def validate(value: Any) -> None:
        if value.topic_id != node.topic_id:
            raise ValueError(f"topic_id_mismatch:{value.topic_id}:{node.topic_id}")
        if len(value.entities) != expected_count:
            raise ValueError(
                f"entity_count_mismatch:{len(value.entities)}:{expected_count}"
            )
        rows = _model_rows(value.entities)
        actual_ids = tuple(str(row.get("id") or "") for row in rows)
        if set(actual_ids) != set(expected_ids) or len(actual_ids) != len(set(actual_ids)):
            raise ValueError(f"entity_id_set_mismatch:{actual_ids}:{expected_ids}")
        if expected_names:
            actual_names = tuple(str(row.get("name") or "").strip() for row in rows)
            if actual_names != expected_names:
                raise ValueError(f"entity_name_set_mismatch:{actual_names}:{expected_names}")

    return StructuredContract(
        contract_id=f"rpg.world_forge.topic.{node.topic_id}",
        version=4,
        output_model=response_model,
        semantic_validator=validate,
        schema_profile="canon_strict",
        schema_name=f"rpg_world_forge_{safe}",
        regenerate_on_semantic_failure=False,
    )


def _field_contract(
    node: CampaignTopicNode,
    *,
    allocated_ids: tuple[str, ...],
    dependencies: Mapping[str, GeneratedTopic],
) -> dict[str, Any]:
    references = _allocated_reference_ids(node, dependencies, allocated_ids)
    return {
        "entity_kind": str(node.metadata.get("entity_kind") or node.topic_id),
        "allocated_entity_ids": list(allocated_ids),
        "extra_fields": "forbidden",
        "fields": [
            {
                **definition,
                "candidate_reference_ids": list(
                    references.get(str(definition.get("field_id") or ""), ())
                ),
            }
            for definition in _definitions(node)
        ],
    }


def _one_call_budget(timeout_seconds: int) -> StructuredRetryBudget:
    return StructuredRetryBudget(
        max_provider_calls=1,
        max_transport_retries=0,
        max_format_downgrades=0,
        max_validation_regenerations=0,
        deadline_seconds=float(timeout_seconds),
    )


class SinglePassProviderWorldForgeTopicGenerator(ProviderWorldForgeTopicGenerator):
    """Use one provider call for each planned registry or entity generation unit."""

    def _limiter(self):
        provider = self.config.provider.strip().casefold().removeprefix("llm:")
        return device_permit_slot("llm-local", priority="batch") if provider == "lmstudio" else nullcontext()

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
        prompt = _system_prompt(
            node,
            batch_index=index,
            batch_count=total,
            existing_entities=existing_entities,
            assigned_entity_ids=ids,
            assigned_entities=assigned_entities,
        ) + (
            _PROMPT_1.text
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
        request["required_output"]["profile_field_contract"] = _field_contract(
            node,
            allocated_ids=ids,
            dependencies=dependency_topics,
        )
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content=json.dumps(request, ensure_ascii=False, sort_keys=True)),
        ]
        contract = (
            _profile_contract(
                node,
                expected_count=count,
                expected_ids=ids,
                expected_names=expected_entity_names,
                dependencies=dependency_topics,
            )
            if _definitions(node)
            else _topic_contract(
                node.topic_id,
                expected_entity_count=count,
                expected_entity_ids=ids,
                expected_entity_names=expected_entity_names,
            )
        )
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
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                outcome.error,
                outcome.diagnostics.as_dict(),
                unit="topic",
            ) from outcome.error
        assert outcome.value is not None
        value = self._apply_registry_slots(outcome.value, assigned_entities)
        rendered = json.dumps(value.model_dump(mode="python"), ensure_ascii=False, sort_keys=True)
        return (
            value,
            outcome.diagnostics.as_dict(),
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
                        assigned_entity_ids=ids,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        gateway: StructuredOutputGateway[Any] = StructuredOutputGateway(self.provider)
        with self._limiter():
            outcome = gateway.try_generate(
                messages,
                contract=replace(
                    _entity_registry_contract(node.topic_id, expected_entity_ids=ids),
                    temperature=self.config.temperature,
                    max_tokens=min(self.config.max_tokens, 2048),
                ),
                model=self.config.model or None,
                retry_budget=_one_call_budget(self.config.timeout_seconds),
            )
        if outcome.error is not None:
            raise SinglePassWorldForgeProviderError(
                node.topic_id,
                outcome.error,
                outcome.diagnostics.as_dict(),
                unit="registry",
            ) from outcome.error
        assert outcome.value is not None
        rendered = json.dumps(
            outcome.value.model_dump(mode="python"),
            ensure_ascii=False,
            sort_keys=True,
        )
        return (
            outcome.value,
            outcome.diagnostics.as_dict(),
            sum(_token_estimate(message.content) for message in messages),
            _token_estimate(rendered),
        )


__all__ = [
    "SinglePassProviderWorldForgeTopicGenerator",
    "SinglePassWorldForgeProviderError",
]
