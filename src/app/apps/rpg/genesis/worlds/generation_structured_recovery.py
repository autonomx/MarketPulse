"""Bounded recovery helpers for malformed World Forge structured responses."""
from __future__ import annotations

import logging

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    create_model,
)

from app.providers.base import ChatMessage, ChatResponse
from app.providers.structured import StructuredContract
from app.providers.structured.parsing import canonical_structured_text, decode_json_object
from app.apps.rpg.genesis.forge.world_forge_dossiers import dossier_prompt_contract

logger = logging.getLogger(__name__)

_COLLECTIONS = (
    "documents",
    "entities",
    "facts",
    "relationships",
    "knowledge_rules",
    "story_threads",
)

_MINIMUM_VIABILITY_SEMANTIC_PREFIXES = (
    "authored_draft_repeated_long_prose:",
)


class CapturingStructuredProvider:
    """Proxy one provider while retaining its latest non-streaming response."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.last_response: ChatResponse | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        response = self._provider.chat_completion(*args, **kwargs)
        if isinstance(response, ChatResponse):
            self.last_response = response
        return response

    @property
    def raw_text(self) -> str:
        if self.last_response is None:
            return ""
        try:
            return canonical_structured_text(self.last_response)
        except Exception:
            return str(self.last_response.content or "")


@dataclass(frozen=True)
class DeterministicRepair:
    payload: Mapping[str, Any] | None
    codes: tuple[str, ...]


_IDENTITY_PATH_PARTS = frozenset({
    "topic_id", "id", "entity_id", "kind", "source_id", "target_id",
    "actor_ids", "location_ids", "faction_ids", "related_entity_ids",
})


def missing_field_paths(error: Exception) -> tuple[str, ...]:
    """Return safe, provider-authored missing paths from a validation error.

    This supports all ordinary authored values. IDs and graph references stay
    excluded because they are authoritative boundaries, not model-repair data.
    """

    if not isinstance(error, ValidationError):
        return ()
    paths: list[str] = []
    for row in error.errors(include_url=False):
        if str(row.get("type") or "") != "missing":
            continue
        location = tuple(row.get("loc") or ())
        if not location:
            continue
        if not all(isinstance(part, (str, int)) for part in location):
            continue
        if str(location[-1]) in _IDENTITY_PATH_PARTS:
            continue
        paths.append(".".join(str(part) for part in location))
    return tuple(dict.fromkeys(paths))


def missing_field_patch_contract(paths: Sequence[str]) -> StructuredContract[Any]:
    """Build a tiny schema that can repair only the requested missing fields."""

    normalized = tuple(dict.fromkeys(str(path) for path in paths if str(path)))
    if not normalized:
        raise ValueError("missing_field_patch_paths_required")
    path_type = Literal.__getitem__(normalized)
    patch_model = create_model(
        "WorldForgeMissingFieldPatch",
        __config__=ConfigDict(extra="forbid"),
        path=(path_type, ...),
        value=(StrictStr | StrictInt | StrictFloat | StrictBool | list[Any] | dict[str, Any], ...),
    )
    response_model = create_model(
        "WorldForgeMissingFieldPatchResponse",
        __config__=ConfigDict(extra="forbid"),
        patches=(list[patch_model], Field(min_length=len(normalized), max_length=len(normalized))),  # type: ignore[valid-type]
    )

    def validate(value: BaseModel) -> None:
        actual = tuple(str(patch.path) for patch in cast(Any, value).patches)
        if set(actual) != set(normalized) or len(actual) != len(set(actual)):
            raise ValueError("missing_field_patch_path_set_mismatch")

    return StructuredContract(
        contract_id="rpg.world_forge.missing_field_patch",
        version=1,
        output_model=response_model,
        semantic_validator=validate,
        schema_profile="canon_strict",
        schema_name="rpg_world_forge_missing_field_patch",
        regenerate_on_semantic_failure=False,
        exact_json_object=True,
        max_raw_bytes=16_384,
        max_json_depth=5,
        max_json_nodes=128,
        max_json_string_length=4_096,
        max_json_array_length=len(normalized),
    )


def missing_field_patch_messages(
    *,
    raw_text: str,
    payload: Mapping[str, Any] | None,
    paths: Sequence[str],
) -> list[ChatMessage]:
    """Ask for only missing fields instead of another complete world draft."""

    return [
        ChatMessage(
            role="system",
            content=(
                "You repair narrow omissions in an authored World Forge JSON draft. "
                "Return only the requested patch object. Preserve the draft's established "
                "names, facts, and relationships; do not add entities or alter any other field."
            ),
        ),
        ChatMessage(
            role="user",
            content="WORLD_FORGE_MISSING_FIELD_PATCH:\n" + json.dumps(
                {
                    "requested_paths": list(paths),
                    "rule": "Provide exactly one schema-valid value for each requested path.",
                    "candidate": dict(payload) if payload is not None else raw_text,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        ),
    ]


def apply_missing_field_patches(
    payload: Mapping[str, Any] | None,
    patches: Sequence[Any],
) -> Mapping[str, Any] | None:
    """Apply validated patches without permitting graph mutations."""

    if payload is None:
        return None
    result: dict[str, Any] = copy.deepcopy(dict(payload))
    for patch in patches:
        path = str(getattr(patch, "path", "") or "")
        value = getattr(patch, "value", None)
        current: Any = result
        parts = path.split(".")
        for part in parts[:-1]:
            if isinstance(current, list):
                if not part.isdigit() or int(part) >= len(current):
                    return None
                current = current[int(part)]
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        leaf = parts[-1]
        if not isinstance(current, dict) or leaf in current or value is None:
            return None
        current[leaf] = value
    return result


def _dossier_section_titles(topic_id: str) -> dict[str, str]:
    """Return canonical display titles for the topic's fixed dossier sections."""

    try:
        dossier = dict(dossier_prompt_contract(topic_id).get("entity_fields") or {}).get(
            "dossier"
        )
        sections = dict(dossier or {}).get("sections") or ()
    except Exception:
        return {}
    return {
        str(section.get("id") or ""): str(section.get("title") or "").strip()
        for section in sections
        if isinstance(section, Mapping)
        and str(section.get("id") or "")
        and str(section.get("title") or "").strip()
    }


def decode_candidate(raw_text: str) -> Mapping[str, Any] | None:
    if not raw_text.strip():
        return None
    try:
        value = decode_json_object(raw_text)
    except Exception:
        logger.debug("suppressed error in %s", "decode_candidate", exc_info=True)
        return None
    return dict(value) if isinstance(value, Mapping) else None


def deterministic_repair(
    payload: Mapping[str, Any] | None,
    *,
    expected_topic_id: str,
    allocated_entity_ids: tuple[str, ...],
    expected_entity_kind: str,
    include_provenance: bool = True,
    allowed_root_fields: frozenset[str] | None = None,
    allowed_reference_ids: frozenset[str] | None = None,
) -> DeterministicRepair:
    """Repair only transformations whose intended value is authoritative."""

    if payload is None:
        return DeterministicRepair(None, ())
    value: dict[str, Any] = copy.deepcopy(dict(payload))
    codes: list[str] = []

    for wrapper in ("response", "result", "output"):
        wrapped = value.get(wrapper)
        if len(value) == 1 and isinstance(wrapped, Mapping):
            value = copy.deepcopy(dict(wrapped))
            codes.append(f"unwrap_{wrapper}")
            break

    topic_id = str(value.get("topic_id") or "")
    if topic_id in set(allocated_entity_ids):
        value["topic_id"] = expected_topic_id
        codes.append("root_topic_id_from_entity_id")
    elif not topic_id:
        for alias in ("domain_id", "topic"):
            if value.get(alias) == expected_topic_id:
                value["topic_id"] = expected_topic_id
                value.pop(alias, None)
                codes.append(f"root_topic_id_from_{alias}")
                break

    for collection in _COLLECTIONS:
        if allowed_root_fields is not None and collection not in allowed_root_fields:
            value.pop(collection, None)
            continue
        current = value.get(collection)
        if isinstance(current, Mapping):
            value[collection] = [copy.deepcopy(dict(current))]
            codes.append(f"wrap_single_{collection}")
        elif current is None:
            value[collection] = []
            codes.append(f"add_empty_{collection}")
    if include_provenance and not isinstance(value.get("provenance"), Mapping):
        value["provenance"] = {}
        codes.append("add_empty_provenance")
    if allowed_root_fields is not None:
        disallowed = tuple(
            field
            for field in ("facts", "provenance")
            if field in value and field not in allowed_root_fields
        )
        for field in disallowed:
            value.pop(field, None)
        if disallowed:
            codes.append("remove_server_owned_root_fields")

    entities = value.get("entities")
    if isinstance(entities, list):
        repaired_entities: list[Any] = []
        allowed = set(allocated_entity_ids)
        section_titles = _dossier_section_titles(expected_topic_id)
        for item in entities:
            if not isinstance(item, Mapping):
                repaired_entities.append(item)
                continue
            entity = copy.deepcopy(dict(item))
            entity_id = str(entity.get("id") or "")
            alias_id = str(entity.get("entity_id") or "")
            if not entity_id and alias_id in allowed:
                entity["id"] = alias_id
                codes.append("entity_id_from_entity_id_alias")
            if not entity.get("kind") and entity.get("type") == expected_entity_kind:
                entity["kind"] = expected_entity_kind
                entity.pop("type", None)
                codes.append("entity_kind_from_type_alias")
            dossier = entity.get("dossier")
            if isinstance(dossier, Mapping):
                repaired_dossier = copy.deepcopy(dict(dossier))
                related_ids = repaired_dossier.get("related_entity_ids")
                if allowed_reference_ids is not None and isinstance(related_ids, list):
                    retained_ids = [
                        reference
                        for reference in related_ids
                        if str(reference) in allowed_reference_ids
                    ]
                    if retained_ids != related_ids:
                        repaired_dossier["related_entity_ids"] = retained_ids
                        codes.append("remove_unknown_related_entity_ids")
                sections = repaired_dossier.get("sections")
                if sections is None and section_titles:
                    direct_sections = {
                        section_id: repaired_dossier.pop(section_id)
                        for section_id in section_titles
                        if section_id in repaired_dossier
                    }
                    if direct_sections:
                        repaired_dossier["sections"] = direct_sections
                        sections = direct_sections
                        codes.append("nest_dossier_sections_under_sections")
                if isinstance(sections, list):
                    repaired_sections: list[Any] = []
                    for section in sections:
                        if not isinstance(section, Mapping):
                            repaired_sections.append(section)
                            continue
                        repaired_section = copy.deepcopy(dict(section))
                        section_id = str(repaired_section.get("id") or "")
                        title = section_titles.get(section_id)
                        if title and not str(repaired_section.get("title") or "").strip():
                            repaired_section["title"] = title
                            codes.append("dossier_section_title_from_template")
                        repaired_sections.append(repaired_section)
                    repaired_dossier["sections"] = repaired_sections
                entity["dossier"] = repaired_dossier
            repaired_entities.append(entity)
        value["entities"] = repaired_entities

    if allowed_reference_ids is not None:
        documents = value.get("documents")
        if isinstance(documents, list):
            for document in documents:
                if not isinstance(document, dict):
                    continue
                references = document.get("entities")
                if not isinstance(references, list):
                    continue
                retained = [
                    reference
                    for reference in references
                    if str(reference) in allowed_reference_ids
                ]
                if retained != references:
                    document["entities"] = retained
                    codes.append("remove_unknown_document_entity_ids")
        story_threads = value.get("story_threads")
        if isinstance(story_threads, list):
            for thread in story_threads:
                if not isinstance(thread, dict):
                    continue
                for field_id in ("actor_ids", "location_ids", "faction_ids"):
                    references = thread.get(field_id)
                    if not isinstance(references, list):
                        continue
                    retained = [
                        reference
                        for reference in references
                        if str(reference) in allowed_reference_ids
                    ]
                    if retained != references:
                        thread[field_id] = retained
                        codes.append(f"remove_unknown_story_thread_{field_id}")

    return DeterministicRepair(value, tuple(dict.fromkeys(codes)))


def validate_payload(
    contract: StructuredContract[Any],
    payload: Mapping[str, Any] | None,
) -> tuple[BaseModel | None, Exception | None]:
    if payload is None:
        return None, ValueError("structured_candidate_not_decodable")
    try:
        candidate = contract.output_model.model_validate(payload)
    except ValidationError as exc:
        return None, exc
    if contract.semantic_validator is not None:
        try:
            contract.semantic_validator(candidate)
        except Exception as exc:
            return None, exc
    return candidate, None


def minimum_viability_candidate(
    contract: StructuredContract[Any],
    payload: Mapping[str, Any] | None,
    error: Exception | None,
) -> tuple[BaseModel | None, dict[str, Any] | None]:
    """Retain schema-valid drafts whose remaining errors are editorial only."""

    if payload is None or error is None or isinstance(error, ValidationError):
        return None, None
    message = str(error)
    code = next(
        (
            prefix.removesuffix(":")
            for prefix in _MINIMUM_VIABILITY_SEMANTIC_PREFIXES
            if message.startswith(prefix)
        ),
        "",
    )
    if not code:
        return None, None
    try:
        candidate = contract.output_model.model_validate(payload)
    except ValidationError:
        return None, None
    return candidate, {
        "status": "needs_review",
        "blocking_publication": True,
        "allows_dependency_generation": True,
        "error_type": type(error).__name__,
        "reason_code": code,
        "message": message,
    }


def _error_rows(error: Exception) -> list[dict[str, Any]]:
    if isinstance(error, ValidationError):
        return [dict(row) for row in error.errors(include_url=False)]
    rows = []
    for issue in getattr(error, "issues", ()):
        method = getattr(issue, "as_dict", None)
        rows.append(method() if callable(method) else {"message": str(issue)})
    return rows or [{"type": type(error).__name__, "message": str(error)}]


def recovery_messages(
    *,
    contract: StructuredContract[Any],
    raw_text: str,
    decoded_payload: Mapping[str, Any] | None,
    error: Exception,
    expected_topic_id: str,
    allocated_entity_ids: tuple[str, ...],
    expected_entity_kind: str,
) -> list[ChatMessage]:
    payload: dict[str, Any] = {
        "task": "extract_existing_information_into_required_schema",
        "rules": [
            "Do not invent, enrich, summarise, or regenerate lore.",
            "Preserve all usable prose, names, dates, identifiers, and relationships.",
            "Only rename fields, move values, wrap or unwrap containers, and correct fixed identities.",
            "The root topic_id and allocated entity IDs are authoritative.",
            "Return exactly one JSON object and no explanation.",
        ],
        "identity": {
            "root_topic_id": expected_topic_id,
            "allocated_entity_ids": list(allocated_entity_ids),
            "entity_kind": expected_entity_kind,
        },
        "validation_errors": _error_rows(error),
        "required_schema": contract.output_model.model_json_schema(),
        "invalid_candidate": (
            dict(decoded_payload) if decoded_payload is not None else raw_text
        ),
    }
    return [
        ChatMessage(
            role="system",
            content=(
                "You are a loss-minimising JSON recovery transformer. The candidate already "
                "contains the author's lore. Extract that information into the supplied schema "
                "without writing replacement lore. Correct structure, field placement, aliases, "
                "and authoritative IDs only."
            ),
        ),
        ChatMessage(
            role="user",
            content="WORLD_FORGE_STRUCTURED_RECOVERY:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        ),
    ]


def semantic_correction_messages(
    *,
    contract: StructuredContract[Any],
    invalid_candidate: Mapping[str, Any],
    error: Exception,
) -> list[ChatMessage]:
    """Request one minimal, schema-preserving repair of a semantic violation."""

    return [
        ChatMessage(
            role="system",
            content=(
                "You perform one minimal semantic correction on an authored World Forge "
                "JSON draft. Return exactly one bare JSON object and no explanation. "
                "Preserve the topic ID, entity IDs, relationships, and every unaffected "
                "field. Correct only the supplied validation violation. Do not invent new "
                "lore: when duplicate prose is reported, move, remove, or use different "
                "already-authored prose so each retained long passage occurs only once."
            ),
        ),
        ChatMessage(
            role="user",
            content="WORLD_FORGE_SEMANTIC_CORRECTION:\n"
            + json.dumps(
                {
                    "validation_errors": _error_rows(error),
                    "required_schema": contract.output_model.model_json_schema(),
                    "invalid_candidate": dict(invalid_candidate),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
        ),
    ]


def merge_diagnostics(
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any] | None,
    *,
    method: str,
    repair_codes: Sequence[str],
    raw_text: str,
    error: Exception,
) -> dict[str, Any]:
    first = dict(primary)
    second = dict(secondary or {})
    merged = {**first, **second}
    for key in (
        "provider_calls",
        "transport_retries",
        "format_downgrades",
        "validation_regenerations",
        "latency_ms",
        "raw_response_length",
    ):
        merged[key] = float(first.get(key) or 0) + float(second.get(key) or 0)
        if key != "latency_ms":
            merged[key] = int(merged[key])
    usage: dict[str, Any] = {}
    for source in (first.get("usage"), second.get("usage")):
        if not isinstance(source, Mapping):
            continue
        for key, item in source.items():
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                usage[key] = usage.get(key, 0) + item
    merged["usage"] = usage
    merged["structured_recovery"] = {
        "method": method,
        "repair_codes": list(dict.fromkeys(str(code) for code in repair_codes)),
        "original_error_type": type(error).__name__,
        "original_error": str(error),
        "original_candidate_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        if raw_text
        else "",
        "requires_manual_review": True,
    }
    return merged


def recovery_review(topic_id: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods = tuple(str(row.get("method") or "unknown") for row in records)
    issues: list[dict[str, Any]] = [
        {
            "code": f"structured_recovery_{method}",
            "topic_id": topic_id,
            "entity_id": "",
            "field_id": "",
            "message": (
                "The original provider response required automatic structural recovery; "
                "review the recovered candidate before promotion."
            ),
            "expected": "manual review",
            "allowed_domains": [],
            "candidates": [],
            "supplied_value": None,
        }
        for method in methods
    ]
    for record in records:
        viability = record.get("minimum_viability")
        if not isinstance(viability, Mapping):
            continue
        issues.append(
            {
                "code": str(viability.get("reason_code") or "minimum_viability_review"),
                "topic_id": topic_id,
                "entity_id": "",
                "field_id": "",
                "message": str(viability.get("message") or "Editorial review required."),
                "expected": "editorial correction or explicit approval",
                "allowed_domains": [],
                "candidates": [],
                "supplied_value": None,
            }
        )
    return {
        "schema_version": "rpg_world_generation_review_v1",
        "status": "needs_review",
        "blocking": True,
        "error_type": "StructuredRecoveryApplied",
        "reason_codes": sorted({issue["code"] for issue in issues}),
        "issues": issues,
        "summary": "Automatic structural recovery produced a reviewable lore candidate.",
    }


__all__ = [
    "CapturingStructuredProvider",
    "apply_missing_field_patches",
    "decode_candidate",
    "deterministic_repair",
    "merge_diagnostics",
    "missing_field_patch_contract",
    "missing_field_patch_messages",
    "minimum_viability_candidate",
    "missing_field_paths",
    "recovery_messages",
    "semantic_correction_messages",
    "recovery_review",
    "validate_payload",
]
