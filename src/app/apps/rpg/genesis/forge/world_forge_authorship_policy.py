"""Field-level authorship policy for profile-driven World Forge schemas."""
from __future__ import annotations

from typing import Any, Mapping

from .world_forge_profiles import FieldDefinition

LLM_REQUIRED = "llm_required"
AUTHORED_REQUIRED = "authored_required"
MACHINE_ALLOWED = "machine_allowed"
STRUCTURAL_ONLY = "structural_only"

_STRUCTURAL_FIELD_IDS = {
    "id",
    "entity_id",
    "topic_id",
    "fact_id",
    "field_id",
    "source_id",
    "target_id",
    "document_id",
    "relationship_id",
    "schema_version",
    "visibility",
    "status",
    "kind",
    "type",
}
_MACHINE_VALUE_TYPES = {
    "integer",
    "number",
    "boolean",
    "enum",
}
_REFERENCE_VALUE_TYPES = {"entity_ref", "entity_ref_list"}
_PRESENTATION_FIELDS = {
    "name": LLM_REQUIRED,
    "short_summary": LLM_REQUIRED,
    "summary": LLM_REQUIRED,
    "description": LLM_REQUIRED,
    "dossier": LLM_REQUIRED,
    "documents": LLM_REQUIRED,
    "story_threads": LLM_REQUIRED,
}


def field_authorship_policy(field: FieldDefinition) -> str:
    field_id = str(field.field_id or "")
    if (
        field_id in _STRUCTURAL_FIELD_IDS
        or field_id.endswith("_id")
        or field_id.endswith("_ids")
        or field.value_type in _REFERENCE_VALUE_TYPES
    ):
        return STRUCTURAL_ONLY
    if field.value_type in _MACHINE_VALUE_TYPES:
        return MACHINE_ALLOWED
    return AUTHORED_REQUIRED


def field_policy_row(field: FieldDefinition) -> dict[str, Any]:
    return {**field.as_dict(), "authorship_policy": field_authorship_policy(field)}


def topic_authorship_policy(
    field_definitions: tuple[FieldDefinition, ...],
) -> dict[str, Any]:
    return {
        "schema_version": "rpg_world_field_authorship_policy_v1",
        "default_policy": AUTHORED_REQUIRED,
        "production_generated_default_policy": LLM_REQUIRED,
        "entity_fields": {
            field.field_id: field_authorship_policy(field)
            for field in field_definitions
        },
        "presentation_fields": dict(_PRESENTATION_FIELDS),
        "machine_containers": [
            "provenance",
            "authorship",
            "origin_ledger",
            "generation_artifact",
            "lookup",
            "validation",
            "dependency_hashes",
            "dependency_trust",
            "quick_facts",
            "presentation",
        ],
        "structured_fact_fields": {
            "subject": STRUCTURAL_ONLY,
            "predicate": STRUCTURAL_ONLY,
            "object": MACHINE_ALLOWED,
            "value_type": STRUCTURAL_ONLY,
            "semantic_role": STRUCTURAL_ONLY,
            "entity_refs": STRUCTURAL_ONLY,
            "content": AUTHORED_REQUIRED,
            "expanded_description": AUTHORED_REQUIRED,
        },
    }


def policy_for_path(
    policy: Mapping[str, Any] | None,
    path: str,
) -> str:
    """Resolve the declared policy for a serialized topic path."""

    row = dict(policy or {})
    segments = [segment for segment in str(path).split("/") if segment]
    if not segments:
        return str(row.get("default_policy") or AUTHORED_REQUIRED)
    if segments[0] == "entities" and len(segments) >= 3:
        field_id = segments[2].replace("~1", "/").replace("~0", "~")
        entity_fields = dict(row.get("entity_fields") or {})
        presentation_fields = dict(row.get("presentation_fields") or {})
        return str(
            presentation_fields.get(field_id)
            or entity_fields.get(field_id)
            or row.get("default_policy")
            or AUTHORED_REQUIRED
        )
    if segments[0] == "facts" and len(segments) >= 3:
        field_id = segments[2].replace("~1", "/").replace("~0", "~")
        return str(
            dict(row.get("structured_fact_fields") or {}).get(field_id)
            or row.get("default_policy")
            or AUTHORED_REQUIRED
        )
    if segments[0] in {"documents", "story_threads"}:
        return LLM_REQUIRED
    return str(row.get("default_policy") or AUTHORED_REQUIRED)


__all__ = [
    "AUTHORED_REQUIRED",
    "LLM_REQUIRED",
    "MACHINE_ALLOWED",
    "STRUCTURAL_ONLY",
    "field_authorship_policy",
    "field_policy_row",
    "policy_for_path",
    "topic_authorship_policy",
]
