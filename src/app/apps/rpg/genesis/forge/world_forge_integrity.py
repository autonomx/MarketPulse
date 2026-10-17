"""Fail-closed semantic integrity for generated World Forge proposals.

This module deliberately permits representation normalization while refusing to
choose or invent semantic meaning. Provider output may name an existing entity,
use an exact canonical ID, or use an explicitly declared alias. Anything else is
reported as unresolved or ambiguous and must be regenerated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_domains import DOMAIN_SPECS
from .world_forge_generation import GeneratedTopic

ReferenceStatus = Literal["resolved", "alias_resolved", "unresolved", "ambiguous"]


@dataclass(frozen=True)
class ReferenceResolution:
    status: ReferenceStatus
    requested_value: str
    resolved_id: str | None = None
    candidates: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_value": self.requested_value,
            "resolved_id": self.resolved_id,
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class WorldForgeIntegrityIssue:
    code: str
    topic_id: str
    item_id: str
    field: str
    supplied_value: str
    candidates: tuple[str, ...] = ()
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "item_id": self.item_id,
            "field": self.field,
            "supplied_value": self.supplied_value,
            "candidates": list(self.candidates),
            "message": self.message,
        }


class WorldForgeIntegrityError(ValueError):
    def __init__(self, issues: Iterable[WorldForgeIntegrityIssue]) -> None:
        self.issues = tuple(issues)
        summary = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.item_id}:{issue.field}:"
            f"{issue.supplied_value or '<missing>'}"
            for issue in self.issues
        )
        super().__init__(f"world_forge_integrity_failed:{summary}")


def _normalized_name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _entity_kind(entity_id: str, entity: Mapping[str, Any]) -> str:
    kind = str(entity.get("kind") or "").strip().casefold().rstrip("s")
    if kind:
        return kind
    if entity_id.startswith("ent:"):
        parts = entity_id.split(":", 2)
        return parts[1].casefold().rstrip("s") if len(parts) > 1 else ""
    return entity_id.split(":", 1)[0].casefold().rstrip("s")


def _known_entities(
    dependencies: Mapping[str, GeneratedTopic],
    own_entities: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Mapping[str, Any]]:
    known: dict[str, Mapping[str, Any]] = {}
    for topic in dependencies.values():
        for entity in topic.entities:
            entity_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
            if entity_id:
                known[entity_id] = dict(entity)
    for entity in own_entities:
        entity_id = str(entity.get("id") or entity.get("entity_id") or "").strip()
        if entity_id:
            known[entity_id] = dict(entity)
    return known


def resolve_reference(
    requested_value: Any,
    *,
    known: Mapping[str, Mapping[str, Any]],
    allowed_kinds: Sequence[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> ReferenceResolution:
    requested = str(requested_value or "").strip()
    allowed = {str(kind).casefold().rstrip("s") for kind in allowed_kinds}
    candidates = tuple(
        sorted(
            entity_id
            for entity_id, entity in known.items()
            if not allowed or _entity_kind(entity_id, entity) in allowed
        )
    )
    if requested in candidates:
        return ReferenceResolution("resolved", requested, requested, (requested,))

    alias_target = str(dict(aliases or {}).get(requested) or "").strip()
    if alias_target and alias_target in candidates:
        return ReferenceResolution(
            "alias_resolved", requested, alias_target, (alias_target,)
        )

    requested_name = _normalized_name(requested)
    name_matches = tuple(
        entity_id
        for entity_id in candidates
        if _normalized_name(
            known[entity_id].get("name") or known[entity_id].get("title")
        )
        == requested_name
        and requested_name
    )
    if len(name_matches) == 1:
        return ReferenceResolution("resolved", requested, name_matches[0], name_matches)
    if len(name_matches) > 1:
        return ReferenceResolution("ambiguous", requested, None, name_matches)
    return ReferenceResolution("unresolved", requested, None, candidates)


def _reference_values(value: Any, *, multiple: bool) -> list[Any]:
    if multiple:
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value] if value not in (None, "") else []
    return [value] if value not in (None, "") else []


def validate_and_normalize_provider_topic(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
    *,
    aliases: Mapping[str, str] | None = None,
) -> GeneratedTopic:
    """Resolve only exact IDs, explicit aliases, or unique exact names.

    The returned topic differs only where a supplied reference was safely mapped
    to an existing canonical ID. No entity, fact, relationship, or location is
    synthesized, reassigned, or removed.
    """

    issues: list[WorldForgeIntegrityIssue] = []
    seen_ids: set[str] = set()
    entities: list[dict[str, Any]] = []
    for index, source in enumerate(topic.entities, start=1):
        row = dict(source)
        entity_id = str(row.get("id") or row.get("entity_id") or "").strip()
        item_id = entity_id or f"{node.topic_id}:{index}"
        if not entity_id:
            issues.append(
                WorldForgeIntegrityIssue(
                    "missing_entity_id", node.topic_id, item_id, "id", ""
                )
            )
        elif entity_id in seen_ids:
            issues.append(
                WorldForgeIntegrityIssue(
                    "duplicate_entity_id", node.topic_id, item_id, "id", entity_id
                )
            )
        seen_ids.add(entity_id)
        entities.append(row)

    known = _known_entities(dependencies, entities)
    spec = DOMAIN_SPECS.get(node.topic_id)
    resolution_rows: list[dict[str, Any]] = []
    if spec is not None:
        for index, row in enumerate(entities, start=1):
            item_id = str(row.get("id") or row.get("entity_id") or f"{node.topic_id}:{index}")
            for field, kinds in spec.reference_fields.items():
                multiple = field in spec.required_lists
                raw_values = _reference_values(row.get(field), multiple=multiple)
                if not raw_values:
                    issues.append(
                        WorldForgeIntegrityIssue(
                            "missing_required_reference",
                            node.topic_id,
                            item_id,
                            field,
                            "",
                        )
                    )
                    continue
                resolved_values: list[str] = []
                for supplied in raw_values:
                    resolution = resolve_reference(
                        supplied,
                        known=known,
                        allowed_kinds=kinds,
                        aliases=aliases,
                    )
                    resolution_rows.append(
                        {
                            "topic_id": node.topic_id,
                            "item_id": item_id,
                            "field": field,
                            **resolution.as_dict(),
                        }
                    )
                    if resolution.resolved_id is None:
                        issues.append(
                            WorldForgeIntegrityIssue(
                                f"{resolution.status}_reference",
                                node.topic_id,
                                item_id,
                                field,
                                resolution.requested_value,
                                resolution.candidates,
                            )
                        )
                    else:
                        resolved_values.append(resolution.resolved_id)
                if resolved_values:
                    row[field] = resolved_values if multiple else resolved_values[0]

    all_ids = set(known)
    relationships: list[dict[str, Any]] = []
    for index, source in enumerate(topic.relationships, start=1):
        row = dict(source)
        item_id = str(row.get("id") or f"relationship:{index}")
        for field in ("source_id", "target_id"):
            supplied = str(row.get(field) or "").strip()
            resolution = resolve_reference(supplied, known=known)
            if resolution.resolved_id is None:
                issues.append(
                    WorldForgeIntegrityIssue(
                        f"{resolution.status}_reference",
                        node.topic_id,
                        item_id,
                        field,
                        supplied,
                        resolution.candidates,
                    )
                )
            else:
                row[field] = resolution.resolved_id
        relationships.append(row)

    for collection_name, rows, id_field in (
        ("facts", topic.facts, "entity_refs"),
        ("documents", topic.documents, "entities"),
    ):
        for index, source in enumerate(rows, start=1):
            row = dict(source)
            item_id = str(
                row.get("id")
                or row.get("document_id")
                or f"{collection_name}:{index}"
            )
            refs = row.get(id_field)
            if not isinstance(refs, (list, tuple, set)):
                continue
            for supplied in refs:
                if str(supplied) not in all_ids:
                    issues.append(
                        WorldForgeIntegrityIssue(
                            "unresolved_reference",
                            node.topic_id,
                            item_id,
                            id_field,
                            str(supplied),
                            tuple(sorted(all_ids)),
                        )
                    )

    if issues:
        raise WorldForgeIntegrityError(issues)
    return replace(
        topic,
        entities=tuple(entities),
        relationships=tuple(relationships),
        provenance={
            **dict(topic.provenance),
            "semantic_integrity": "fail_closed_v1",
            "reference_resolutions": resolution_rows,
        },
    )
