"""Compatibility adapter for presentation built from trusted machine facts.

The presentation compiler historically identified structured facts by the v1 compiler
source. Production generation now emits machine-only v2 facts with the same semantic
contract. This adapter aliases only the machine metadata during rendering and restores
the original source afterwards; it never creates or alters lore prose.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .world_forge_causal_presentation import project_causal_link_presentations
from .world_forge_contract import CampaignTopicNode
from .world_forge_generation import GeneratedTopic
from .world_forge_presentation import (
    render_fact_derived_presentations as _render,
)

_STRUCTURED_FACT_SOURCES = {
    "profile_structured_fact_compiler_v1",
    "profile_structured_fact_compiler_v2",
}


def _structured(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("source") or "") in _STRUCTURED_FACT_SOURCES
        or str(row.get("authorship_class") or "") == "machine_structured"
    ) and str(row.get("approved_authority") or "") == "objective_canon"


def render_fact_derived_presentations(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
) -> GeneratedTopic:
    sources = {
        str(row.get("id") or row.get("fact_id") or ""): str(row.get("source") or "")
        for row in topic.facts
    }
    aliased = replace(
        topic,
        facts=tuple(
            {
                **dict(row),
                "source": "profile_structured_fact_compiler_v1",
            }
            if _structured(row)
            else dict(row)
            for row in topic.facts
        ),
    )
    rendered = project_causal_link_presentations(_render(node, aliased))
    restored = tuple(
        {
            **dict(row),
            "source": sources.get(
                str(row.get("id") or row.get("fact_id") or ""),
                str(row.get("source") or ""),
            ),
        }
        for row in rendered.facts
    )
    return replace(
        rendered,
        facts=restored,
        provenance={
            **dict(rendered.provenance),
            "presentation_structured_fact_contract": "machine_fact_v2_compatible",
        },
    )


__all__ = ["render_fact_derived_presentations"]
