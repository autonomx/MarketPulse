"""Authoritative per-topic contracts for provider-authored World Forge content.

The provider authors prose and meaningful references.  Omnix owns canonical
identifiers, dossier structure, authority, visibility, provenance, and the
validation receipt that permits a candidate to enter editorial review.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence, cast

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

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_dossiers import dossier_prompt_contract
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.providers.world_forge import WorldForgeTopicResponse
from app.apps.rpg.genesis.worlds.generation_contract_receipt import (
    RECEIPT_SCHEMA_VERSION,
    canonical_candidate_content_hash,
)

CONTRACT_VERSION = "world-topic-authored-draft-v2"
AUTHORING_PROMPT_VERSION = "world-topic-authoring-prompt-v2"
MATERIALIZER_VERSION = "world-topic-materializer-v2"
SEMANTIC_POLICY_VERSION = "world-topic-semantics-v3"
COLLECTION_POLICY_VERSION = "world-topic-collections-v2"
PAYLOAD_LIMITS_VERSION = "world-topic-limits-v1"
SCHEMA_PROJECTION_VERSION = "structured-schema-projection-v2"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _literal(values: Sequence[str]) -> Any:
    unique = tuple(dict.fromkeys(str(value) for value in values if str(value)))
    return Literal.__getitem__(unique) if unique else StrictStr


class ProviderSectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraphs: list[StrictStr] = Field(min_length=1, max_length=3)


class ProviderQuoteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr
    attribution: StrictStr = ""


class ProviderQuickFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: StrictStr
    value: StrictStr | StrictInt | StrictFloat | StrictBool


class ProviderDocumentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    full_text: StrictStr
    summary_500: StrictStr
    summary_120: StrictStr
    entities: list[StrictStr] = Field(default_factory=list, max_length=32)


class ProviderRelationshipDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: StrictStr
    target_id: StrictStr
    relationship_type: StrictStr
    description: StrictStr


class ProviderKnowledgeRuleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: StrictStr
    description: StrictStr
    evidence_index: StrictInt | None = None


class ProviderStoryThreadDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    summary: StrictStr
    status: StrictStr
    actor_ids: list[StrictStr] = Field(default_factory=list, max_length=32)
    location_ids: list[StrictStr] = Field(default_factory=list, max_length=32)
    faction_ids: list[StrictStr] = Field(default_factory=list, max_length=32)


@dataclass(frozen=True)
class PayloadLimits:
    max_raw_bytes: int = 262_144
    max_depth: int = 24
    max_nodes: int = 20_000
    max_string_length: int = 16_384
    max_collection_rows: int = 32
    max_total_references: int = 512

    def as_dict(self) -> dict[str, int | str]:
        return {
            "version": PAYLOAD_LIMITS_VERSION,
            "max_raw_bytes": self.max_raw_bytes,
            "max_depth": self.max_depth,
            "max_nodes": self.max_nodes,
            "max_string_length": self.max_string_length,
            "max_collection_rows": self.max_collection_rows,
            "max_total_references": self.max_total_references,
        }


@dataclass(frozen=True)
class CollectionPolicy:
    documents: str = "optional"
    entities: str = "required"
    facts: str = "server_compiled"
    relationships: str = "optional"
    knowledge_rules: str = "optional"
    story_threads: str = "optional"
    max_documents: int = 4
    max_facts: int = 8
    max_relationships: int = 12
    max_knowledge_rules: int = 8
    max_story_threads: int = 6

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": COLLECTION_POLICY_VERSION,
            "documents": self.documents,
            "entities": self.entities,
            "facts": self.facts,
            "relationships": self.relationships,
            "knowledge_rules": self.knowledge_rules,
            "story_threads": self.story_threads,
            "max_documents": self.max_documents,
            "max_facts": self.max_facts,
            "max_relationships": self.max_relationships,
            "max_knowledge_rules": self.max_knowledge_rules,
            "max_story_threads": self.max_story_threads,
        }


@dataclass(frozen=True)
class TopicContractBundle:
    contract_id: str
    contract_version: str
    authored_draft_model: type[BaseModel]
    canonical_model: type[BaseModel]
    prompt_contract: Mapping[str, Any]
    dossier_template: tuple[tuple[str, str], ...]
    collection_policy: CollectionPolicy
    allowed_reference_ids: frozenset[str]
    limits: PayloadLimits
    provider_schema_hash: str
    authored_schema_hash: str
    prompt_contract_hash: str
    canonical_contract_hash: str
    dossier_template_hash: str
    collection_policy_hash: str
    payload_limits_hash: str
    semantic_validator: Callable[[BaseModel], None]
    materializer: Callable[[BaseModel], WorldForgeTopicResponse]

    def descriptor(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "provider_schema_hash": self.provider_schema_hash,
            "authored_schema_hash": self.authored_schema_hash,
            "prompt_contract_hash": self.prompt_contract_hash,
            "canonical_contract_hash": self.canonical_contract_hash,
            "dossier_template_hash": self.dossier_template_hash,
            "collection_policy_hash": self.collection_policy_hash,
            "payload_limits_hash": self.payload_limits_hash,
            "materializer_version": MATERIALIZER_VERSION,
            "semantic_policy_version": SEMANTIC_POLICY_VERSION,
            "schema_projection_version": SCHEMA_PROJECTION_VERSION,
        }


def _definitions(node: CampaignTopicNode) -> tuple[dict[str, Any], ...]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _dependency_ids(
    node: CampaignTopicNode,
    dependencies: Mapping[str, GeneratedTopic],
    own_ids: tuple[str, ...],
) -> tuple[frozenset[str], dict[str, tuple[str, ...]]]:
    by_domain = {
        domain_id: tuple(
            str(row.get("id") or row.get("entity_id") or "")
            for row in topic.entities
            if str(row.get("id") or row.get("entity_id") or "")
        )
        for domain_id, topic in dependencies.items()
    }
    by_domain[node.topic_id] = own_ids
    all_ids = frozenset(entity_id for values in by_domain.values() for entity_id in values)
    return all_ids, by_domain


def _field_type(
    definition: Mapping[str, Any],
    *,
    references: tuple[str, ...],
) -> Any:
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


def _dossier_template(topic_id: str) -> tuple[tuple[str, str], ...]:
    contract = dossier_prompt_contract(topic_id)
    dossier = dict(dict(contract.get("entity_fields") or {}).get("dossier") or {})
    return tuple(
        (str(section.get("id") or ""), str(section.get("title") or ""))
        for section in dossier.get("sections") or ()
        if isinstance(section, Mapping)
        and str(section.get("id") or "")
        and str(section.get("title") or "")
    )


def _sections_model(topic_id: str, template: tuple[tuple[str, str], ...]) -> type[BaseModel]:
    fields = {
        section_id: (ProviderSectionDraft, ...)
        for section_id, _title in template
    }
    return cast(Any, create_model)(
        f"WorldForgeAuthoredSections_{topic_id}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _dossier_model(
    topic_id: str,
    template: tuple[tuple[str, str], ...],
    reference_type: Any,
) -> type[BaseModel]:
    sections_model = _sections_model(topic_id, template)
    return cast(Any, create_model)(
        f"WorldForgeAuthoredDossier_{topic_id}",
        __config__=ConfigDict(extra="forbid"),
        subtitle=(StrictStr, ""),
        quote=(ProviderQuoteDraft | None, None),
        quick_facts=(list[ProviderQuickFactDraft], Field(default_factory=list, max_length=12)),
        sections=(sections_model, ...),
        related_entity_ids=(list[reference_type], Field(default_factory=list, max_length=32)),
    )


def _entity_model(
    node: CampaignTopicNode,
    *,
    allocated_ids: tuple[str, ...],
    dependencies: Mapping[str, GeneratedTopic],
    dossier_model: type[BaseModel],
    by_domain: Mapping[str, tuple[str, ...]],
) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {
        "id": (_literal(allocated_ids), ...),
        "kind": (_literal((str(node.metadata.get("entity_kind") or node.topic_id),)), ...),
    }
    definitions = _definitions(node)
    for definition in definitions:
        field_id = str(definition.get("field_id") or "").strip()
        if not field_id or field_id in fields:
            continue
        references = tuple(
            entity_id
            for domain_id in definition.get("allowed_target_domains") or ()
            for entity_id in by_domain.get(str(domain_id), ())
        )
        annotation = _field_type(definition, references=references)
        required = bool(definition.get("required", False))
        fields[field_id] = (
            annotation if required else annotation | None,
            Field(default=... if required else None),
        )
    fields.setdefault("name", (StrictStr, ...))
    fields["short_summary"] = (StrictStr, ...)
    fields["dossier"] = (dossier_model, ...)
    if not definitions:
        fields["attributes"] = (dict[str, Any], Field(default_factory=dict))
    return cast(Any, create_model)(
        f"WorldForgeAuthoredEntity_{node.topic_id}",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _draft_model(
    node: CampaignTopicNode,
    *,
    entity_model: type[BaseModel],
    expected_count: int,
    policy: CollectionPolicy,
    relationships_allowed: bool,
) -> type[BaseModel]:
    return cast(Any, create_model)(
        f"WorldForgeAuthoredTopicDraft_{node.topic_id}",
        __config__=ConfigDict(extra="forbid"),
        topic_id=(_literal((node.topic_id,)), ...),
        documents=(
            list[ProviderDocumentDraft],
            Field(default_factory=list, max_length=policy.max_documents),
        ),
        entities=(
            list[entity_model],  # type: ignore[valid-type]
            Field(min_length=expected_count, max_length=expected_count),
        ),
        relationships=(
            list[ProviderRelationshipDraft],
            Field(
                default_factory=list,
                max_length=policy.max_relationships if relationships_allowed else 0,
            ),
        ),
        knowledge_rules=(
            list[ProviderKnowledgeRuleDraft],
            Field(default_factory=list, max_length=policy.max_knowledge_rules),
        ),
        story_threads=(
            list[ProviderStoryThreadDraft],
            Field(default_factory=list, max_length=policy.max_story_threads),
        ),
    )


def _validate_references(
    payload: Mapping[str, Any],
    *,
    allowed_ids: frozenset[str],
) -> None:
    for index, fact in enumerate(payload.get("facts") or ()):
        for reference in dict(fact).get("entity_refs") or ():
            if str(reference) not in allowed_ids:
                raise ValueError(
                    f"authored_draft_unknown_reference:/facts/{index}/entity_refs:{reference}"
                )
    for index, relationship in enumerate(payload.get("relationships") or ()):
        row = dict(relationship)
        source_id = str(row.get("source_id") or "")
        target_id = str(row.get("target_id") or "")
        if source_id not in allowed_ids or target_id not in allowed_ids:
            raise ValueError(
                f"authored_draft_unknown_relationship_endpoint:/relationships/{index}"
            )
        if source_id == target_id:
            raise ValueError(
                f"authored_draft_self_relationship:/relationships/{index}"
            )
    for index, document in enumerate(payload.get("documents") or ()):
        for reference in dict(document).get("entities") or ():
            if str(reference) not in allowed_ids:
                raise ValueError(
                    f"authored_draft_unknown_reference:/documents/{index}/entities:{reference}"
                )
    for index, entity in enumerate(payload.get("entities") or ()):
        dossier = dict(dict(entity).get("dossier") or {})
        for reference in dossier.get("related_entity_ids") or ():
            if str(reference) not in allowed_ids:
                raise ValueError(
                    f"authored_draft_unknown_reference:/entities/{index}/"
                    f"dossier/related_entity_ids:{reference}"
                )
    for index, thread in enumerate(payload.get("story_threads") or ()):
        row = dict(thread)
        for field_id in ("actor_ids", "location_ids", "faction_ids"):
            for reference in row.get(field_id) or ():
                if str(reference) not in allowed_ids:
                    raise ValueError(
                        f"authored_draft_unknown_reference:/story_threads/{index}/"
                        f"{field_id}:{reference}"
                    )


def _semantic_validator(
    *,
    allowed_ids: frozenset[str],
    limits: PayloadLimits,
) -> Callable[[BaseModel], None]:
    mojibake_markers = ("\u00c3", "\u00c2", "\u00e2\u20ac")

    def validate(value: BaseModel) -> None:
        payload = value.model_dump(mode="python")
        _validate_references(payload, allowed_ids=allowed_ids)
        entity_ids = [str(row.get("id") or "") for row in payload["entities"]]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("authored_draft_duplicate_entity_id")

        references = 0
        repeated: set[str] = set()
        seen_long_strings: set[str] = set()
        stack: list[tuple[Any, str]] = [(payload, "")]
        while stack:
            current, path = stack.pop()
            if isinstance(current, Mapping):
                for key, item in current.items():
                    stack.append((item, f"{path}/{key}"))
                continue
            if isinstance(current, list):
                if current and all(isinstance(item, str) for item in current) and path.endswith(
                    (
                        "/entity_refs",
                        "/related_entity_ids",
                        "/actor_ids",
                        "/location_ids",
                        "/faction_ids",
                        "/entities",
                    )
                ):
                    references += len(current)
                for index, item in enumerate(current):
                    stack.append((item, f"{path}/{index}"))
                continue
            if not isinstance(current, str):
                continue
            if any(marker in current for marker in mojibake_markers):
                raise ValueError(f"authored_draft_mojibake:{path or '/'}")
            normalized = " ".join(current.casefold().split())
            if len(normalized) >= 160:
                if normalized in seen_long_strings:
                    repeated.add(path or "/")
                seen_long_strings.add(normalized)
        if references > limits.max_total_references:
            raise ValueError("authored_draft_reference_limit_exceeded")
        if repeated:
            raise ValueError(
                "authored_draft_repeated_long_prose:" + ",".join(sorted(repeated))
            )

        fact_count = len(payload.get("facts") or ())
        for index, rule in enumerate(payload.get("knowledge_rules") or ()):
            evidence_index = dict(rule).get("evidence_index")
            if evidence_index is not None and not 1 <= int(evidence_index) <= fact_count:
                raise ValueError(
                    f"authored_draft_unknown_evidence_index:/knowledge_rules/{index}"
                )

    return validate


def _canonical_visibility(node: CampaignTopicNode) -> str:
    visibility = str(node.visibility or "")
    return visibility if visibility else "game_master_canon"


def _materializer(
    node: CampaignTopicNode,
    *,
    template: tuple[tuple[str, str], ...],
    allowed_ids: frozenset[str],
    descriptor: Mapping[str, Any],
) -> Callable[[BaseModel], WorldForgeTopicResponse]:
    def materialize(value: BaseModel) -> WorldForgeTopicResponse:
        authored = value.model_dump(mode="python")
        _validate_references(authored, allowed_ids=allowed_ids)
        visibility = _canonical_visibility(node)
        entities: list[dict[str, Any]] = []
        for entity in authored["entities"]:
            row = dict(entity)
            dossier = dict(row.pop("dossier"))
            section_payload = dict(dossier.pop("sections"))
            dossier["schema_version"] = "rpg_world_entity_dossier_v1"
            dossier["sections"] = [
                {
                    "id": section_id,
                    "title": title,
                    "paragraphs": list(
                        dict(section_payload[section_id]).get("paragraphs") or ()
                    ),
                }
                for section_id, title in template
            ]
            attributes = row.pop("attributes", None)
            if isinstance(attributes, Mapping):
                row.update(dict(attributes))
            row["dossier"] = dossier
            entities.append(row)

        facts = [
            {
                "id": f"fact:{node.topic_id}:{index:03d}",
                **dict(fact),
                "authority": "generated_proposal",
                "approved_authority": "objective_canon",
                "visibility": visibility,
            }
            for index, fact in enumerate(authored.get("facts") or (), start=1)
        ]
        relationships = [
            {
                "id": f"rel:{node.topic_id}:{index:03d}",
                **dict(relationship),
                "visibility": visibility,
            }
            for index, relationship in enumerate(authored["relationships"], start=1)
        ]
        documents = [
            {
                "document_id": f"doc:{node.topic_id}:{index:03d}",
                **dict(document),
                "visibility": visibility,
            }
            for index, document in enumerate(authored["documents"], start=1)
        ]
        knowledge_rules = []
        for index, rule in enumerate(authored["knowledge_rules"], start=1):
            row = dict(rule)
            evidence_index = row.pop("evidence_index", None)
            evidence_id = (
                f"fact:{node.topic_id}:{int(evidence_index):03d}"
                if isinstance(evidence_index, int)
                and 1 <= evidence_index <= len(facts)
                else ""
            )
            knowledge_rules.append(
                {
                    "id": f"kr:{node.topic_id}:{index:03d}",
                    **row,
                    "evidence_id": evidence_id,
                    "visibility": visibility,
                }
            )
        story_threads = [
            {
                "id": f"thread:{node.topic_id}:{index:03d}",
                **dict(thread),
                "visibility": visibility,
            }
            for index, thread in enumerate(authored["story_threads"], start=1)
        ]
        canonical_payload = {
                "topic_id": node.topic_id,
                "documents": documents,
                "entities": entities,
                "facts": facts,
                "relationships": relationships,
                "knowledge_rules": knowledge_rules,
                "story_threads": story_threads,
                "provenance": {},
            }
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "topic_id": node.topic_id,
            **dict(descriptor),
            "authored_draft_hash": _canonical_hash(authored),
            "canonical_content_hash": canonical_candidate_content_hash(
                canonical_payload
            ),
            "materialized": True,
        }
        canonical_payload["provenance"] = {
            "authoritative_contract_receipt": receipt
        }
        candidate = WorldForgeTopicResponse.model_validate(canonical_payload)
        return candidate

    return materialize


def build_topic_contract_bundle(
    node: CampaignTopicNode,
    *,
    allocated_entity_ids: tuple[str, ...],
    dependencies: Mapping[str, GeneratedTopic],
    expected_entity_count: int | None = None,
    collection_policy: CollectionPolicy | None = None,
    limits: PayloadLimits | None = None,
) -> TopicContractBundle:
    """Build the single authoritative authored/canonical contract for one topic."""

    expected_count = int(expected_entity_count or node.target_count)
    policy = collection_policy or CollectionPolicy()
    selected_limits = limits or PayloadLimits()
    template = _dossier_template(node.topic_id)
    allowed_ids, by_domain = _dependency_ids(node, dependencies, allocated_entity_ids)
    reference_type = _literal(tuple(sorted(allowed_ids)))
    dossier_model = _dossier_model(node.topic_id, template, reference_type)
    entity_model = _entity_model(
        node,
        allocated_ids=allocated_entity_ids,
        dependencies=dependencies,
        dossier_model=dossier_model,
        by_domain=by_domain,
    )
    relationships_allowed = len(allowed_ids) > 1
    authored_model = _draft_model(
        node,
        entity_model=entity_model,
        expected_count=expected_count,
        policy=policy,
        relationships_allowed=relationships_allowed,
    )
    provider_schema = authored_model.model_json_schema()
    provider_schema_hash = _canonical_hash(provider_schema)
    dossier_template_hash = _canonical_hash(template)
    collection_policy_hash = _canonical_hash(policy.as_dict())
    payload_limits_hash = _canonical_hash(selected_limits.as_dict())
    prompt_contract = {
        "authoring_prompt_version": AUTHORING_PROMPT_VERSION,
        "authored_draft_schema": provider_schema,
        "dossier_sections": {
            section_id: {"title": title, "paragraphs": ["1-3 paragraphs"]}
            for section_id, title in template
        },
        "collection_policy": policy.as_dict(),
        "provenance": "omitted; server-authored",
    }
    prompt_contract_hash = _canonical_hash(prompt_contract)
    canonical_components = {
        "contract_version": CONTRACT_VERSION,
        "authored_draft_schema_hash": provider_schema_hash,
        "canonical_model": "WorldForgeTopicResponse",
        "dossier_template_hash": dossier_template_hash,
        "collection_policy_hash": collection_policy_hash,
        "payload_limits_hash": payload_limits_hash,
        "prompt_contract_hash": prompt_contract_hash,
        "materializer_version": MATERIALIZER_VERSION,
        "semantic_policy_version": SEMANTIC_POLICY_VERSION,
    }
    canonical_contract_hash = _canonical_hash(canonical_components)
    descriptor = {
        "contract_id": f"rpg.world_forge.{node.topic_id}",
        "contract_version": CONTRACT_VERSION,
        "provider_schema_hash": provider_schema_hash,
        "authored_schema_hash": provider_schema_hash,
        "prompt_contract_hash": prompt_contract_hash,
        "canonical_contract_hash": canonical_contract_hash,
        "dossier_template_hash": dossier_template_hash,
        "collection_policy_hash": collection_policy_hash,
        "payload_limits_hash": payload_limits_hash,
        "materializer_version": MATERIALIZER_VERSION,
        "semantic_policy_version": SEMANTIC_POLICY_VERSION,
        "schema_projection_version": SCHEMA_PROJECTION_VERSION,
    }
    semantic_validator = _semantic_validator(
        allowed_ids=allowed_ids,
        limits=selected_limits,
    )
    return TopicContractBundle(
        contract_id=str(descriptor["contract_id"]),
        contract_version=CONTRACT_VERSION,
        authored_draft_model=authored_model,
        canonical_model=WorldForgeTopicResponse,
        prompt_contract=prompt_contract,
        dossier_template=template,
        collection_policy=policy,
        allowed_reference_ids=allowed_ids,
        limits=selected_limits,
        provider_schema_hash=provider_schema_hash,
        authored_schema_hash=provider_schema_hash,
        prompt_contract_hash=prompt_contract_hash,
        canonical_contract_hash=canonical_contract_hash,
        dossier_template_hash=dossier_template_hash,
        collection_policy_hash=collection_policy_hash,
        payload_limits_hash=payload_limits_hash,
        semantic_validator=semantic_validator,
        materializer=_materializer(
            node,
            template=template,
            allowed_ids=allowed_ids,
            descriptor=descriptor,
        ),
    )


__all__ = [
    "AUTHORING_PROMPT_VERSION",
    "COLLECTION_POLICY_VERSION",
    "CONTRACT_VERSION",
    "CollectionPolicy",
    "MATERIALIZER_VERSION",
    "PAYLOAD_LIMITS_VERSION",
    "PayloadLimits",
    "SCHEMA_PROJECTION_VERSION",
    "SEMANTIC_POLICY_VERSION",
    "TopicContractBundle",
    "build_topic_contract_bundle",
]
