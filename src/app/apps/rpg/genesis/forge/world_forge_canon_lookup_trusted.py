"""Attach machine-readable canon lookups without promoting facts into lore prose."""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping

from .world_forge_generation import GeneratedTopic

_STRUCTURED_SOURCES = {
    "profile_structured_fact_compiler_v1",
    "profile_structured_fact_compiler_v2",
}
_LOOKUP_SCHEMA = "rpg_structured_canon_lookup_v2"


def _lookup(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "subject": str(row.get("subject") or ""),
        "predicate": str(row.get("predicate") or row.get("field_id") or ""),
        "object": row.get("object"),
        "value_type": str(row.get("value_type") or ""),
        "semantic_role": str(row.get("semantic_role") or ""),
        "topic_id": str(row.get("topic_id") or ""),
        "entity_refs": [str(value) for value in row.get("entity_refs") or ()],
    }


def attach_structured_canon_lookup(topic: GeneratedTopic) -> GeneratedTopic:
    facts: list[dict[str, Any]] = []
    lookup_count = 0
    index: dict[str, dict[str, str]] = {}
    for source in topic.facts:
        row = dict(source)
        if str(row.get("source") or "") not in _STRUCTURED_SOURCES:
            facts.append(row)
            continue
        lookup = _lookup(row)
        row["lookup_schema"] = _LOOKUP_SCHEMA
        row["lookup"] = lookup
        row["content"] = json.dumps(
            lookup,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # V1 compiler prose was code-authored. Never preserve it as display lore.
        if str(row.get("source") or "") == "profile_structured_fact_compiler_v1":
            row.pop("display_text", None)
            row.pop("expanded_description", None)
        row["source"] = "profile_structured_fact_compiler_v2"
        row["authorship_class"] = "machine_structured"
        facts.append(row)
        subject = lookup["subject"]
        predicate = lookup["predicate"]
        fact_id = str(row.get("id") or "")
        if subject and predicate and fact_id:
            index.setdefault(subject, {})[predicate] = fact_id
        lookup_count += 1
    if not lookup_count:
        return topic
    return replace(
        topic,
        facts=tuple(facts),
        provenance={
            **dict(topic.provenance),
            "structured_canon_lookup_schema": _LOOKUP_SCHEMA,
            "structured_canon_lookup_count": lookup_count,
            "structured_canon_fact_index": index,
            "presentation_derived_from_structured_facts": False,
        },
    )


__all__ = ["attach_structured_canon_lookup"]
