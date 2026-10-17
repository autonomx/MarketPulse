"""Recursive manifest-ownership and canonical entity-reference closure checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_IDENTITY_FIELDS = {
    "id",
    "topic_id",
    "document_id",
    "fact_id",
    "evidence_id",
    "relationship_id",
    "thread_id",
    "card_id",
    "campaign_id",
    "world_id",
    "run_id",
    "job_id",
    "map_id",
    "slot_id",
    "manifest_slot_id",
}
_NON_ENTITY_REFERENCE_FIELDS = {
    "canonical_source_fact_ids",
    "source_fact_ids",
    "fact_ids",
    "known_facts",
    "document_ids",
    "card_ids",
    "retrieval_card_ids",
    "topic_ids",
    "job_ids",
    "run_ids",
    "map_ids",
    "keywords",
}
_KNOWN_REFERENCE_FIELDS = {
    "entities",
    "entity_refs",
    "known_by",
    "subject",
}


@dataclass(frozen=True)
class ManifestReferenceIssue:
    code: str
    topic_id: str
    path: str
    target_id: str = ""
    item_id: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "topic_id": self.topic_id,
            "path": self.path,
            "target_id": self.target_id,
            "item_id": self.item_id,
            "message": self.message,
            "severity": "fatal",
            "blocking": True,
        }


class ManifestReferenceCompilationError(ValueError):
    def __init__(self, issues: Sequence[ManifestReferenceIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.code}:{issue.topic_id}:{issue.path}:{issue.target_id}"
            for issue in self.issues
        )
        super().__init__("manifest_reference_closure_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "manifest_reference_closure_failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row)


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(row) for row in value if isinstance(row, Mapping))


def _profile_reference_fields(topic_graph: Mapping[str, Any] | None) -> set[str]:
    graph = dict(topic_graph or {})
    metadata = graph.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    profile = metadata.get("resolved_profile")
    profile = dict(profile) if isinstance(profile, Mapping) else {}
    fields: set[str] = set()
    for domain in _rows(profile.get("domains")):
        for definition in _rows(domain.get("fields")):
            if str(definition.get("value_type") or "") not in {
                "entity_ref",
                "entity_ref_list",
            }:
                continue
            field_id = str(definition.get("field_id") or "")
            if field_id:
                fields.add(field_id)
    return fields


def _is_reference_field(field: str, declared: set[str]) -> bool:
    if field in _IDENTITY_FIELDS or field in _NON_ENTITY_REFERENCE_FIELDS:
        return False
    return (
        field in declared
        or field in _KNOWN_REFERENCE_FIELDS
        or field.endswith("_id")
        or field.endswith("_ids")
    )


def _binding(candidate: Mapping[str, Any]) -> dict[str, Any]:
    provenance = candidate.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, Mapping) else {}
    value = provenance.get("entity_manifest_binding")
    return dict(value) if isinstance(value, Mapping) else {}


def _ownership(
    topic_rows: Sequence[Mapping[str, Any]],
    *,
    require_binding: bool,
) -> tuple[
    set[str],
    set[str],
    set[str],
    list[ManifestReferenceIssue],
    int,
]:
    all_entity_ids: set[str] = set()
    owned_entity_ids: set[str] = set()
    legacy_aliases: set[str] = set()
    issues: list[ManifestReferenceIssue] = []
    binding_topic_count = 0
    for index, row in enumerate(topic_rows, start=1):
        candidate = _candidate(row)
        topic_id = str(
            row.get("topic_id") or candidate.get("topic_id") or f"topic:{index}"
        )
        binding = _binding(candidate)
        if binding:
            binding_topic_count += 1
        slot_ids = [str(value) for value in binding.get("slot_ids") or () if str(value)]
        entity_ids = [
            str(value) for value in binding.get("entity_ids") or () if str(value)
        ]
        expected_by_slot = dict(zip(slot_ids, entity_ids, strict=False))
        legacy_aliases.update(
            str(key)
            for key in dict(binding.get("rewritten_provider_ids") or {})
            if str(key)
        )
        entities = _rows(candidate.get("entities"))
        provisional_topic = bool(entities) and not binding and not require_binding
        for entity_index, entity in enumerate(entities, start=1):
            entity_id = str(entity.get("id") or "")
            item_id = entity_id or f"{topic_id}:entity:{entity_index}"
            path = f"/entities/{entity_index - 1}"
            if not entity_id:
                issues.append(
                    ManifestReferenceIssue(
                        code="manifest_entity_id_missing",
                        topic_id=topic_id,
                        path=f"{path}/id",
                        item_id=item_id,
                        message="Generated entity has no canonical ID.",
                    )
                )
                continue
            all_entity_ids.add(entity_id)
            if provisional_topic:
                owned_entity_ids.add(entity_id)
                continue
            manifest_slot_id = str(entity.get("manifest_slot_id") or "")
            if not manifest_slot_id:
                issues.append(
                    ManifestReferenceIssue(
                        code="entity_not_manifest_owned",
                        topic_id=topic_id,
                        path=f"{path}/manifest_slot_id",
                        target_id=entity_id,
                        item_id=item_id,
                        message="Entity does not identify its planner-owned manifest slot.",
                    )
                )
                continue
            expected_id = expected_by_slot.get(manifest_slot_id, "")
            if not expected_id:
                issues.append(
                    ManifestReferenceIssue(
                        code="unknown_manifest_slot",
                        topic_id=topic_id,
                        path=f"{path}/manifest_slot_id",
                        target_id=manifest_slot_id,
                        item_id=item_id,
                        message="Entity names a slot absent from topic binding evidence.",
                    )
                )
                continue
            if expected_id != entity_id:
                issues.append(
                    ManifestReferenceIssue(
                        code="manifest_slot_entity_mismatch",
                        topic_id=topic_id,
                        path=f"{path}/id",
                        target_id=entity_id,
                        item_id=item_id,
                        message=f"Manifest slot requires {expected_id}.",
                    )
                )
                continue
            owned_entity_ids.add(entity_id)
    return (
        all_entity_ids,
        owned_entity_ids,
        legacy_aliases,
        issues,
        binding_topic_count,
    )


def _walk_references(
    value: Any,
    *,
    topic_id: str,
    path: str,
    field: str,
    item_id: str,
    declared_fields: set[str],
    all_entity_ids: set[str],
    owned_entity_ids: set[str],
    legacy_aliases: set[str],
    issues: list[ManifestReferenceIssue],
) -> None:
    if isinstance(value, Mapping):
        current_item_id = str(
            value.get("id") or value.get("document_id") or item_id
        )
        for key, child in value.items():
            rendered_key = str(key)
            _walk_references(
                child,
                topic_id=topic_id,
                path=f"{path}/{rendered_key}",
                field=rendered_key,
                item_id=current_item_id,
                declared_fields=declared_fields,
                all_entity_ids=all_entity_ids,
                owned_entity_ids=owned_entity_ids,
                legacy_aliases=legacy_aliases,
                issues=issues,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            _walk_references(
                child,
                topic_id=topic_id,
                path=f"{path}/{index}",
                field=field,
                item_id=item_id,
                declared_fields=declared_fields,
                all_entity_ids=all_entity_ids,
                owned_entity_ids=owned_entity_ids,
                legacy_aliases=legacy_aliases,
                issues=issues,
            )
        return
    if not isinstance(value, str) or not value or not _is_reference_field(
        field,
        declared_fields,
    ):
        return
    if value in owned_entity_ids:
        return
    if value in legacy_aliases:
        code = "legacy_entity_alias_retained"
        message = "Structured reference retains a pre-canonical provider or name alias."
    elif value in all_entity_ids:
        code = "reference_to_unowned_entity"
        message = "Structured reference targets an entity without valid manifest ownership."
    else:
        code = "unresolved_canonical_entity_reference"
        message = "Structured entity reference does not resolve to assembled canonical canon."
    issues.append(
        ManifestReferenceIssue(
            code=code,
            topic_id=topic_id,
            path=path,
            target_id=value,
            item_id=item_id,
            message=message,
        )
    )


def _manifest_reference_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
    *,
    require_binding: bool,
) -> tuple[tuple[ManifestReferenceIssue, ...], int]:
    all_ids, owned_ids, aliases, issues, binding_count = _ownership(
        topic_rows,
        require_binding=require_binding,
    )
    declared_fields = _profile_reference_fields(topic_graph)
    for index, row in enumerate(topic_rows, start=1):
        candidate = _candidate(row)
        topic_id = str(
            row.get("topic_id") or candidate.get("topic_id") or f"topic:{index}"
        )
        for collection in (
            "entities",
            "documents",
            "facts",
            "relationships",
            "knowledge_rules",
            "story_threads",
        ):
            value = candidate.get(collection)
            if value is None:
                continue
            _walk_references(
                value,
                topic_id=topic_id,
                path=f"/{collection}",
                field=collection,
                item_id="",
                declared_fields=declared_fields,
                all_entity_ids=all_ids,
                owned_entity_ids=owned_ids,
                legacy_aliases=aliases,
                issues=issues,
            )
    unique = {
        (issue.code, issue.topic_id, issue.path, issue.target_id, issue.item_id): issue
        for issue in issues
    }
    return tuple(unique[key] for key in sorted(unique)), binding_count


def manifest_reference_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[ManifestReferenceIssue, ...]:
    issues, _ = _manifest_reference_issues(
        topic_rows,
        topic_graph,
        require_binding=True,
    )
    return issues


def manifest_reference_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
    *,
    require_binding: bool = False,
) -> dict[str, Any]:
    issues, binding_count = _manifest_reference_issues(
        topic_rows,
        topic_graph,
        require_binding=require_binding,
    )
    entity_count = sum(len(_rows(_candidate(row).get("entities"))) for row in topic_rows)
    return {
        "schema_version": "rpg_world_manifest_reference_closure_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "entity_count": entity_count,
            "reference_issue_count": len(issues),
            "profile_reference_field_count": len(
                _profile_reference_fields(topic_graph)
            ),
            "binding_evidence_topic_count": binding_count,
            "ownership_mode": "certified" if require_binding else "diagnostic",
        },
    }


def require_manifest_reference_closure(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues, _ = _manifest_reference_issues(
        topic_rows,
        topic_graph,
        require_binding=True,
    )
    if issues:
        raise ManifestReferenceCompilationError(issues)
    return manifest_reference_report(
        topic_rows,
        topic_graph,
        require_binding=True,
    )


__all__ = [
    "ManifestReferenceCompilationError",
    "ManifestReferenceIssue",
    "manifest_reference_issues",
    "manifest_reference_report",
    "require_manifest_reference_closure",
]
