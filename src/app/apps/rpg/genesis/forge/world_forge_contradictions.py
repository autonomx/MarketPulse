"""Detect explicit presentation claims that conflict with structured canon refs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic

_ENTITY_ID_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]*:[a-zA-Z0-9_:-]+\b")
_PRESENTATION_FIELDS = (
    "description",
    "summary",
    "short_summary",
    "quote",
    "subtitle",
    "dossier",
)


@dataclass(frozen=True)
class PresentationContradiction:
    topic_id: str
    entity_id: str
    source: str
    field_id: str
    canonical_reference_ids: tuple[str, ...]
    conflicting_reference_ids: tuple[str, ...]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "entity_id": self.entity_id,
            "source": self.source,
            "field_id": self.field_id,
            "canonical_reference_ids": list(self.canonical_reference_ids),
            "conflicting_reference_ids": list(self.conflicting_reference_ids),
            "message": self.message,
        }


@dataclass(frozen=True)
class PresentationContradictionReport:
    passed: bool
    contradictions: tuple[PresentationContradiction, ...]
    checked_sources: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "contradictions": [item.as_dict() for item in self.contradictions],
            "checked_sources": self.checked_sources,
        }


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _definitions(node: CampaignTopicNode) -> dict[str, dict[str, Any]]:
    value = node.metadata.get("field_definitions")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return {}
    return {
        str(item.get("field_id") or ""): dict(item)
        for item in value
        if isinstance(item, Mapping) and str(item.get("field_id") or "")
    }


def _reference_values(value: Any, value_type: str) -> tuple[str, ...]:
    if value_type == "entity_ref":
        rendered = str(value or "").strip()
        return (rendered,) if rendered else ()
    if value_type == "entity_ref_list" and isinstance(
        value,
        Sequence,
    ) and not isinstance(value, (str, bytes)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _canonical_references(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> dict[str, dict[str, tuple[str, ...]]]:
    definitions = _definitions(node)
    values: dict[str, dict[str, tuple[str, ...]]] = {}
    for fact in topic.facts:
        if str(fact.get("source") or "") != "profile_structured_fact_compiler_v1":
            continue
        entity_id = str(fact.get("subject") or "")
        field_id = str(fact.get("field_id") or "")
        definition = definitions.get(field_id, {})
        refs = _reference_values(
            fact.get("object"),
            str(definition.get("value_type") or fact.get("value_type") or ""),
        )
        if refs:
            values.setdefault(entity_id, {})[field_id] = refs
    return values


def _explicit_ids(value: Any) -> set[str]:
    return set(_ENTITY_ID_PATTERN.findall(_display(value)))


def _same_namespace(left: str, right: str) -> bool:
    return left.split(":", 1)[0].casefold() == right.split(":", 1)[0].casefold()


def audit_presentation_contradictions(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> PresentationContradictionReport:
    """Report explicit IDs in prose that contradict typed canonical references."""

    canonical = _canonical_references(node, topic)
    contradictions: list[PresentationContradiction] = []
    checked_sources = 0

    def inspect(
        *,
        entity_id: str,
        source: str,
        value: Any,
    ) -> None:
        nonlocal checked_sources
        text_ids = _explicit_ids(value)
        if not text_ids:
            return
        checked_sources += 1
        for field_id, canonical_ids in canonical.get(entity_id, {}).items():
            conflicting = tuple(
                sorted(
                    candidate
                    for candidate in text_ids
                    if candidate not in canonical_ids
                    and any(_same_namespace(candidate, expected) for expected in canonical_ids)
                )
            )
            if not conflicting:
                continue
            contradictions.append(
                PresentationContradiction(
                    topic_id=node.topic_id,
                    entity_id=entity_id,
                    source=source,
                    field_id=field_id,
                    canonical_reference_ids=tuple(sorted(canonical_ids)),
                    conflicting_reference_ids=conflicting,
                    message=(
                        "Presentation contains an explicit same-namespace reference that "
                        "does not match the approved structured field."
                    ),
                )
            )

    for entity in topic.entities:
        entity_id = str(entity.get("id") or entity.get("entity_id") or "")
        for field_id in _PRESENTATION_FIELDS:
            if entity.get(field_id) not in (None, "", [], (), {}):
                inspect(
                    entity_id=entity_id,
                    source=f"entity.{field_id}",
                    value=entity.get(field_id),
                )
    for document in topic.documents:
        entity_ids = tuple(str(item) for item in document.get("entities") or () if str(item))
        for entity_id in entity_ids:
            inspect(
                entity_id=entity_id,
                source=f"document:{document.get('document_id') or document.get('id') or ''}",
                value={
                    "full_text": document.get("full_text"),
                    "summary_500": document.get("summary_500"),
                    "summary_120": document.get("summary_120"),
                },
            )
    return PresentationContradictionReport(
        passed=not contradictions,
        contradictions=tuple(contradictions),
        checked_sources=checked_sources,
    )
