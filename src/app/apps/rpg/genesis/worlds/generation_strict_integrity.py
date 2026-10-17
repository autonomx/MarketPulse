"""Strict pre-compilation integrity checks for assembled World Forge candidates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

_COLLECTION_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "documents": ("document_id", "id"),
    "entities": ("id",),
    "facts": ("id", "evidence_id"),
    "relationships": ("id",),
    "knowledge_rules": ("id",),
    "story_threads": ("id",),
}


@dataclass(frozen=True)
class DuplicateCanonIdentifier:
    collection: str
    item_id: str
    source_topic_ids: tuple[str, ...]
    occurrences: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": "duplicate_canonical_identifier",
            "collection": self.collection,
            "item_id": self.item_id,
            "source_topic_ids": list(self.source_topic_ids),
            "occurrences": self.occurrences,
            "blocking": True,
        }


class DuplicateCanonCompilationError(ValueError):
    def __init__(self, duplicates: Sequence[DuplicateCanonIdentifier]) -> None:
        self.duplicates = tuple(duplicates)
        rendered = ";".join(
            f"{item.collection}:{item.item_id}:{','.join(item.source_topic_ids)}"
            for item in self.duplicates
        )
        super().__init__("duplicate_canonical_identifiers:" + rendered)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "code": "duplicate_canonical_identifiers",
            "duplicates": [item.as_dict() for item in self.duplicates],
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("candidate", "content"):
        value = row.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(row) if any(key in row for key in _COLLECTION_ID_FIELDS) else {}


def _rows(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _item_id(row: Mapping[str, Any], id_fields: Sequence[str]) -> str:
    return next(
        (
            str(row.get(field) or "").strip()
            for field in id_fields
            if str(row.get(field) or "").strip()
        ),
        "",
    )


def duplicate_canon_identifiers(
    topic_rows: Sequence[Mapping[str, Any]],
) -> tuple[DuplicateCanonIdentifier, ...]:
    """Find duplicate declared IDs before any compiler can collapse them."""

    owners: dict[tuple[str, str], list[str]] = {}
    for index, raw_topic in enumerate(topic_rows, start=1):
        topic = _mapping(raw_topic)
        candidate = _candidate(topic)
        topic_id = str(
            topic.get("topic_id")
            or candidate.get("topic_id")
            or f"topic:{index}"
        )
        for collection, id_fields in _COLLECTION_ID_FIELDS.items():
            for item in _rows(candidate.get(collection)):
                item_id = _item_id(item, id_fields)
                if item_id:
                    owners.setdefault((collection, item_id), []).append(topic_id)
    duplicates = [
        DuplicateCanonIdentifier(
            collection=collection,
            item_id=item_id,
            source_topic_ids=tuple(dict.fromkeys(source_topics)),
            occurrences=len(source_topics),
        )
        for (collection, item_id), source_topics in owners.items()
        if len(source_topics) > 1
    ]
    return tuple(
        sorted(
            duplicates,
            key=lambda item: (item.collection, item.item_id, item.source_topic_ids),
        )
    )


def strict_integrity_report(
    topic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    duplicates = duplicate_canon_identifiers(topic_rows)
    return {
        "schema_version": "rpg_world_generation_strict_integrity_v1",
        "passed": not duplicates,
        "duplicates": [item.as_dict() for item in duplicates],
        "checks": {
            "topic_rows": len(topic_rows),
            "duplicate_identifiers": len(duplicates),
        },
    }


def require_unique_canon_identifiers(
    topic_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    duplicates = duplicate_canon_identifiers(topic_rows)
    if duplicates:
        raise DuplicateCanonCompilationError(duplicates)
    return strict_integrity_report(topic_rows)


__all__ = [
    "DuplicateCanonCompilationError",
    "DuplicateCanonIdentifier",
    "duplicate_canon_identifiers",
    "require_unique_canon_identifiers",
    "strict_integrity_report",
]
