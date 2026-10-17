"""Deterministic cross-topic duplicate semantic-field detection."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_MIN_TEXT_CHARS = 48
_MIN_TEXT_TOKENS = 8
_TOKEN = re.compile(r"[a-z0-9]+")
_IDENTITY_FIELDS = {
    "id",
    "entity_id",
    "topic_id",
    "manifest_slot_id",
    "slot_id",
    "kind",
    "type",
    "visibility",
    "status",
    "dossier_status",
    "schema_version",
    "canon_revision",
    "name",
    "title",
    "short_name",
    "slug",
}
_PRESENTATION_FIELDS = {
    "dossier",
    "short_summary",
    "summary",
    "summary_120",
    "summary_500",
    "full_text",
    "keywords",
}
_REFERENCE_VALUE_TYPES = {"entity_ref", "entity_ref_list"}
_LOW_INFORMATION_VALUE_TYPES = {"boolean", "integer", "number", "enum"}


@dataclass(frozen=True)
class DuplicateFieldOccurrence:
    topic_id: str
    entity_id: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "path": self.path,
        }


@dataclass(frozen=True)
class CrossTopicDuplicateFieldIssue:
    field_id: str
    fingerprint: str
    occurrences: tuple[DuplicateFieldOccurrence, ...]
    sample: str

    @property
    def code(self) -> str:
        return "cross_topic_duplicate_field"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field_id": self.field_id,
            "fingerprint": self.fingerprint,
            "occurrences": [row.as_dict() for row in self.occurrences],
            "sample": self.sample,
            "severity": "error",
            "blocking": True,
        }


class CrossTopicDuplicateFieldCompilationError(ValueError):
    def __init__(self, issues: Sequence[CrossTopicDuplicateFieldIssue]) -> None:
        self.issues = tuple(issues)
        rendered = ";".join(
            f"{issue.field_id}:{issue.fingerprint}:"
            + ",".join(
                f"{row.topic_id}/{row.entity_id}" for row in issue.occurrences
            )
            for issue in self.issues
        )
        super().__init__("cross_topic_duplicate_fields_failed:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "cross_topic_duplicate_fields_failed",
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row)


def _profile_domains(topic_graph: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    graph = _mapping(topic_graph)
    profile = _mapping(_mapping(graph.get("metadata")).get("resolved_profile"))
    return {
        str(domain.get("domain_id") or ""): domain
        for domain in _rows(profile.get("domains"))
        if str(domain.get("domain_id") or "")
    }


def _profile_semantic_fields(
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, dict[str, str]]:
    domains = _profile_domains(topic_graph)
    result: dict[str, dict[str, str]] = {}
    for domain_id, domain in domains.items():
        fields: dict[str, str] = {}
        for definition in _rows(domain.get("fields")):
            field_id = str(definition.get("field_id") or "")
            value_type = str(definition.get("value_type") or "string")
            if (
                not field_id
                or field_id in _IDENTITY_FIELDS
                or field_id in _PRESENTATION_FIELDS
                or value_type in _REFERENCE_VALUE_TYPES
                or value_type in _LOW_INFORMATION_VALUE_TYPES
            ):
                continue
            fields[field_id] = value_type
        result[domain_id] = fields
    return result


def _is_reference_field(field_id: str) -> bool:
    return (
        field_id.endswith("_id")
        or field_id.endswith("_ids")
        or field_id in {"entities", "entity_refs", "known_by"}
    )


def _fallback_field_ids(entity: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(field_id)
            for field_id in entity
            if str(field_id) not in _IDENTITY_FIELDS
            and str(field_id) not in _PRESENTATION_FIELDS
            and not _is_reference_field(str(field_id))
        )
    )


def _normalise_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _substantial_text(value: str) -> bool:
    normalised = _normalise_text(value)
    return (
        len(normalised) >= _MIN_TEXT_CHARS
        and len(_TOKEN.findall(normalised)) >= _MIN_TEXT_TOKENS
    )


def _semantic_value(value: Any) -> Any:
    if isinstance(value, str):
        normalised = _normalise_text(value)
        return normalised if _substantial_text(normalised) else None
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            field_id = str(key)
            if (
                field_id in _IDENTITY_FIELDS
                or field_id in _PRESENTATION_FIELDS
                or _is_reference_field(field_id)
            ):
                continue
            normalised = _semantic_value(item)
            if normalised not in (None, "", [], {}):
                result[field_id] = normalised
        return result or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [
            normalised
            for item in value
            if (normalised := _semantic_value(item)) not in (None, "", [], {})
        ]
        return items or None
    return None


def _signature(value: Any) -> tuple[str, str] | None:
    semantic = _semantic_value(value)
    if semantic is None:
        return None
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded) < _MIN_TEXT_CHARS:
        return None
    fingerprint = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    sample = encoded[:240]
    return fingerprint, sample


def cross_topic_duplicate_field_issues(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> tuple[CrossTopicDuplicateFieldIssue, ...]:
    """Find exact structured semantic-field signatures reused across topics."""

    profile_fields = _profile_semantic_fields(topic_graph)
    signatures: dict[
        tuple[str, str],
        tuple[str, list[DuplicateFieldOccurrence]],
    ] = {}
    for topic_index, raw_row in enumerate(topic_rows, start=1):
        row = _mapping(raw_row)
        candidate = _candidate(row)
        topic_id = str(
            row.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{topic_index}"
        )
        configured_fields = profile_fields.get(topic_id, {})
        for entity_index, entity in enumerate(_rows(candidate.get("entities")), start=1):
            entity_id = str(entity.get("id") or f"{topic_id}:entity:{entity_index}")
            field_ids = (
                tuple(sorted(configured_fields))
                if configured_fields
                else _fallback_field_ids(entity)
            )
            for field_id in field_ids:
                if field_id not in entity:
                    continue
                signed = _signature(entity.get(field_id))
                if signed is None:
                    continue
                fingerprint, sample = signed
                key = (field_id, fingerprint)
                occurrence = DuplicateFieldOccurrence(
                    topic_id=topic_id,
                    entity_id=entity_id,
                    path=f"/entities/{entity_index - 1}/{field_id}",
                )
                if key not in signatures:
                    signatures[key] = (sample, [occurrence])
                else:
                    signatures[key][1].append(occurrence)

    issues: list[CrossTopicDuplicateFieldIssue] = []
    for (field_id, fingerprint), (sample, occurrences) in signatures.items():
        unique_occurrences = {
            (row.topic_id, row.entity_id, row.path): row for row in occurrences
        }
        ordered = tuple(unique_occurrences[key] for key in sorted(unique_occurrences))
        if len({row.topic_id for row in ordered}) < 2:
            continue
        if len({row.entity_id for row in ordered}) < 2:
            continue
        issues.append(
            CrossTopicDuplicateFieldIssue(
                field_id=field_id,
                fingerprint=fingerprint,
                occurrences=ordered,
                sample=sample,
            )
        )
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.field_id,
                issue.fingerprint,
                tuple(
                    (row.topic_id, row.entity_id, row.path)
                    for row in issue.occurrences
                ),
            ),
        )
    )


def cross_topic_duplicate_field_report(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = cross_topic_duplicate_field_issues(topic_rows, topic_graph)
    return {
        "schema_version": "rpg_world_cross_topic_duplicate_fields_v1",
        "passed": not issues,
        "issues": [issue.as_dict() for issue in issues],
        "checks": {
            "duplicate_signature_count": len(issues),
            "minimum_text_characters": _MIN_TEXT_CHARS,
            "minimum_text_tokens": _MIN_TEXT_TOKENS,
            "profile_domain_count": len(_profile_domains(topic_graph)),
        },
    }


def require_no_cross_topic_duplicate_fields(
    topic_rows: Sequence[Mapping[str, Any]],
    topic_graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    issues = cross_topic_duplicate_field_issues(topic_rows, topic_graph)
    if issues:
        raise CrossTopicDuplicateFieldCompilationError(issues)
    return cross_topic_duplicate_field_report(topic_rows, topic_graph)


__all__ = [
    "CrossTopicDuplicateFieldCompilationError",
    "CrossTopicDuplicateFieldIssue",
    "DuplicateFieldOccurrence",
    "cross_topic_duplicate_field_issues",
    "cross_topic_duplicate_field_report",
    "require_no_cross_topic_duplicate_fields",
]
