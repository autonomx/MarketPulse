"""Validate profile-defined entity records and compile field-level canon proposals."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic


@dataclass(frozen=True)
class StructuredFactIssue:
    code: str
    topic_id: str
    entity_id: str
    field_id: str
    message: str
    supplied_value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "field_id": self.field_id,
            "message": self.message,
            "supplied_value": self.supplied_value,
        }


class StructuredFactValidationError(ValueError):
    def __init__(self, issues: Iterable[StructuredFactIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "structured_fact_validation_failed:"
            + ";".join(
                f"{issue.code}:{issue.topic_id}:{issue.entity_id}:{issue.field_id}"
                for issue in self.issues
            )
        )


def _field_definitions(node: CampaignTopicNode) -> tuple[dict[str, Any], ...]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _entity_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("entity_id") or "").strip()


def _known_by_domain(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> dict[str, dict[str, Mapping[str, Any]]]:
    known: dict[str, dict[str, Mapping[str, Any]]] = {}
    for domain_id, dependency in dependencies.items():
        for entity in dependency.entities:
            entity_id = _entity_id(entity)
            if entity_id:
                known.setdefault(str(domain_id), {})[entity_id] = entity
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        if entity_id:
            known.setdefault(node.topic_id, {})[entity_id] = entity
    return known


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _value_type_valid(value: Any, value_type: str, enum_values: tuple[str, ...]) -> bool:
    if value_type == "string":
        return isinstance(value, str) and bool(value.strip())
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "enum":
        return isinstance(value, str) and value in enum_values
    if value_type == "entity_ref":
        return isinstance(value, str) and bool(value.strip())
    if value_type == "entity_ref_list":
        return _is_sequence(value) and all(
            isinstance(item, str) and bool(item.strip()) for item in value
        )
    if value_type == "structured_object":
        return isinstance(value, Mapping) or _is_sequence(value)
    return False


def _reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        return (str(value).strip(),) if str(value or "").strip() else ()
    if value_type == "entity_ref_list" and _is_sequence(value):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _validate_reference(
    *,
    topic_id: str,
    entity_id: str,
    field_id: str,
    reference: str,
    allowed_domains: tuple[str, ...],
    known: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> StructuredFactIssue | None:
    if any(reference in known.get(domain_id, {}) for domain_id in allowed_domains):
        return None
    candidates = sorted(
        entity_id
        for domain_id in allowed_domains
        for entity_id in known.get(domain_id, {})
    )
    return StructuredFactIssue(
        "unresolved_typed_reference",
        topic_id,
        entity_id,
        field_id,
        "Reference does not resolve in an allowed target domain. "
        f"Allowed domains: {','.join(allowed_domains)}; candidates: {','.join(candidates)}",
        reference,
    )


def validate_structured_entity_records(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> None:
    """Validate profile-compiled fields before prose or canon compilation."""

    definitions = _field_definitions(node)
    if not definitions:
        return
    issues: list[StructuredFactIssue] = []
    known = _known_by_domain(node, topic, dependencies)
    seen_ids: set[str] = set()
    expected_kind = str(node.metadata.get("entity_kind") or "").strip()
    for index, entity in enumerate(topic.entities, start=1):
        entity_id = _entity_id(entity)
        display_id = entity_id or f"{node.topic_id}:{index}"
        if not entity_id:
            issues.append(
                StructuredFactIssue(
                    "missing_entity_id",
                    node.topic_id,
                    display_id,
                    "id",
                    "Profile-generated entities require stable IDs.",
                )
            )
        elif entity_id in seen_ids:
            issues.append(
                StructuredFactIssue(
                    "duplicate_entity_id",
                    node.topic_id,
                    display_id,
                    "id",
                    "Entity ID is duplicated in the topic.",
                    entity_id,
                )
            )
        seen_ids.add(entity_id)
        actual_kind = str(entity.get("kind") or "").strip()
        if expected_kind and actual_kind != expected_kind:
            issues.append(
                StructuredFactIssue(
                    "profile_entity_kind_mismatch",
                    node.topic_id,
                    display_id,
                    "kind",
                    f"Expected {expected_kind}; received {actual_kind or '<missing>'}.",
                    actual_kind,
                )
            )

        for definition in definitions:
            field_id = str(definition.get("field_id") or "")
            value_type = str(definition.get("value_type") or "")
            required = bool(definition.get("required", False))
            value = entity.get(field_id)
            missing = value is None or value == "" or (
                required and _is_sequence(value) and not value
            )
            if missing:
                if required:
                    issues.append(
                        StructuredFactIssue(
                            "missing_required_structured_field",
                            node.topic_id,
                            display_id,
                            field_id,
                            "Required profile field is missing.",
                        )
                    )
                continue
            enum_values = tuple(
                str(item) for item in definition.get("enum_values") or ()
            )
            if not _value_type_valid(value, value_type, enum_values):
                issues.append(
                    StructuredFactIssue(
                        "invalid_structured_field_type",
                        node.topic_id,
                        display_id,
                        field_id,
                        f"Expected {value_type}.",
                        value,
                    )
                )
                continue
            allowed_domains = tuple(
                str(item)
                for item in definition.get("allowed_target_domains") or ()
            )
            for reference in _reference_values(value, value_type):
                reference_issue = _validate_reference(
                    topic_id=node.topic_id,
                    entity_id=display_id,
                    field_id=field_id,
                    reference=reference,
                    allowed_domains=allowed_domains,
                    known=known,
                )
                if reference_issue is not None:
                    issues.append(reference_issue)
    if issues:
        raise StructuredFactValidationError(issues)


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _fact_id(entity_id: str, field_id: str) -> str:
    safe_entity = entity_id.replace(":", "_").replace("/", "_")
    safe_field = field_id.replace(":", "_").replace("/", "_")
    return f"fact:{safe_entity}:{safe_field}"


def _canonical_fact_signature(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable canon payload, excluding lookup/presentation enrichments."""

    return {
        "id": str(row.get("id") or ""),
        "subject": str(row.get("subject") or ""),
        "predicate": str(row.get("predicate") or row.get("field_id") or ""),
        "object": row.get("object"),
        "authority": str(row.get("authority") or ""),
        "approved_authority": str(row.get("approved_authority") or ""),
        "visibility": str(row.get("visibility") or ""),
        "entity_refs": list(row.get("entity_refs") or ()),
        "topic_id": str(row.get("topic_id") or ""),
        "field_id": str(row.get("field_id") or ""),
        "value_type": str(row.get("value_type") or ""),
        "semantic_role": str(row.get("semantic_role") or ""),
        "source": str(row.get("source") or ""),
    }


def compile_structured_entity_facts(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    """Emit one canon proposal per validated profile-defined field.

    Existing provider facts remain proposals. Generated dossier prose therefore
    has a complete structured source of truth to consume in later phases. Lookup
    and display enrichments may be added after compilation; publication revalidation
    compares only the immutable canon signature and preserves those enrichments.
    """

    definitions = _field_definitions(node)
    if not definitions:
        return topic
    validate_structured_entity_records(node, topic, dependencies)
    existing = {str(row.get("id") or ""): dict(row) for row in topic.facts}
    fact_ids: list[str] = []
    for entity in topic.entities:
        entity_id = _entity_id(entity)
        entity_name = str(entity.get("name") or entity_id).strip()
        visibility = str(entity.get("visibility") or node.visibility)
        for definition in definitions:
            field_id = str(definition.get("field_id") or "")
            if field_id not in entity or entity.get(field_id) in (None, ""):
                continue
            value = entity[field_id]
            fact_id = _fact_id(entity_id, field_id)
            references = [
                entity_id,
                *_reference_values(
                    value,
                    str(definition.get("value_type") or ""),
                ),
            ]
            row = {
                "id": fact_id,
                "subject": entity_id,
                "predicate": field_id,
                "object": value,
                "content": f"{entity_name}: {field_id.replace('_', ' ')} is {_render_value(value)}.",
                "expanded_description": (
                    f"This structured value was validated against the {node.topic_id} "
                    "profile schema before presentation prose was generated."
                ),
                "authority": "generated_proposal",
                "approved_authority": "objective_canon",
                "visibility": visibility,
                "entity_refs": list(dict.fromkeys(references)),
                "topic_id": node.topic_id,
                "field_id": field_id,
                "value_type": str(definition.get("value_type") or ""),
                "semantic_role": str(definition.get("semantic_role") or ""),
                "source": "profile_structured_fact_compiler_v1",
            }
            current = existing.get(fact_id)
            if current is not None:
                if _canonical_fact_signature(current) != _canonical_fact_signature(row):
                    raise StructuredFactValidationError(
                        (
                            StructuredFactIssue(
                                "conflicting_structured_fact",
                                node.topic_id,
                                entity_id,
                                field_id,
                                "Existing fact conflicts with the validated structured field.",
                                current,
                            ),
                        )
                    )
                # Preserve post-compilation fields such as lookup, lookup_schema,
                # display_text, and compact JSON content after the immutable canon
                # signature has been proven identical.
                existing[fact_id] = {**row, **current}
            else:
                existing[fact_id] = row
            fact_ids.append(fact_id)
    return replace(
        topic,
        facts=tuple(existing[key] for key in sorted(existing)),
        provenance={
            **dict(topic.provenance),
            "structured_fact_schema": "rpg_profile_structured_facts_v1",
            "structured_facts_validated": True,
            "structured_fact_ids": sorted(fact_ids),
        },
    )
