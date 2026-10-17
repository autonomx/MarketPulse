"""Explicit test-fixture structured facts with deterministic presentation prose.

Production World Forge generation uses ``world_forge_fact_pipeline_trusted`` and
never synthesises lore sentences. The deterministic generator remains useful for
architecture and playtest fixtures; those fixtures need the historical fact prose
only so downstream presentation compilers can exercise their contracts.

This module must never be used by a live provider route. Its output carries blocked
fixture provenance and cannot pass trusted-authorship publication validation.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from .world_forge_contract import CampaignTopicNode
from .world_forge_fact_pipeline import (
    compile_structured_entity_facts as _compile_fixture_facts,
)
from .world_forge_generation import GeneratedTopic


def compile_deterministic_fixture_facts(
    node: CampaignTopicNode,
    topic: GeneratedTopic,
    dependencies: Mapping[str, GeneratedTopic],
) -> GeneratedTopic:
    compiled = _compile_fixture_facts(node, topic, dependencies)
    return replace(
        compiled,
        provenance={
            **dict(compiled.provenance),
            "deterministic_fixture_fact_presentation": True,
            "deterministic_fixture_only": True,
        },
    )


__all__ = ["compile_deterministic_fixture_facts"]
