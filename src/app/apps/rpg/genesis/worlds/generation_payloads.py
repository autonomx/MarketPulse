"""World Forge generation payloads read back from durable rows (split from generation_coordinator)."""
from __future__ import annotations

from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_contract import (
    CampaignTopicGraph,
    CampaignTopicNode,
)

from .generation_jobs import WorldTopicGenerationSettings


def graph_from_payload(value: Mapping[str, Any]) -> CampaignTopicGraph:
    nodes = tuple(
        CampaignTopicNode(
            topic_id=str(row.get("topic_id") or ""),
            title=str(row.get("title") or ""),
            category=str(row.get("category") or "lore"),
            dependencies=tuple(str(item) for item in row.get("dependencies") or ()),
            generator_role=str(row.get("generator_role") or "world_forge"),
            required_before_launch=bool(row.get("required_before_launch", True)),
            visibility=str(row.get("visibility") or "game_master_canon"),
            target_count=int(row.get("target_count") or 1),
            metadata=dict(row.get("metadata") or {}),
        )
        for row in value.get("nodes") or ()
        if isinstance(row, Mapping)
    )
    graph = CampaignTopicGraph(
        graph_version=str(value.get("graph_version") or "rpg_world_topic_graph_v1"),
        campaign_template=str(value.get("campaign_template") or "classic_fantasy"),
        depth=str(value.get("depth") or "standard"),  # type: ignore[arg-type]
        nodes=nodes,
        metadata=dict(value.get("metadata") or {}),
    )
    issues = graph.validate()
    if issues:
        raise ValueError("invalid_world_generation_graph:" + ",".join(issues))
    return graph


def settings_from_payload(value: Mapping[str, Any]) -> WorldTopicGenerationSettings:
    return WorldTopicGenerationSettings(
        generator_version=str(value.get("generator_version") or "world-generator-v1"),
        prompt_version=str(value.get("prompt_version") or "world-prompt-v1"),
        provider_route=str(value.get("provider_route") or "configured"),
        model=str(value.get("model") or "configured"),
        seed=int(value.get("seed") or 0),
        topic_contract_version=str(
            value.get("topic_contract_version") or "rpg_world_topic_job_v2"
        ),
        output_schema_version=str(
            value.get("output_schema_version") or "rpg_world_topic_output_v2"
        ),
        compiler_version=str(value.get("compiler_version") or "world-compiler-v1"),
        max_attempts=2,
        priority=int(value.get("priority") or 10),
    )


def authoring(row: Mapping[str, Any]) -> dict[str, Any]:
    provenance = row.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    authoring = provenance.get("authoring")
    return dict(authoring) if isinstance(authoring, Mapping) else {}


def protected(row: Mapping[str, Any]) -> bool:
    return str(row.get("source") or "") == "manual" or bool(
        authoring(row).get("generation_lock")
    )


def available_result_row(result: Mapping[str, Any]) -> dict[str, Any] | None:
    status = str(result.get("status") or "")
    candidate = result.get("candidate")
    if status not in {"accepted", "needs_review"} or not isinstance(candidate, Mapping):
        return None
    return {
        "topic_id": str(result.get("topic_id") or ""),
        "status": "ready",
        "source": "ai",
        "content": dict(candidate),
        "content_hash": str(result.get("candidate_hash") or ""),
        "input_hash": "",
        "dependency_hashes": dict(result.get("dependency_hashes") or {}),
        "dependency_trust": "quarantined" if status == "needs_review" else "accepted",
        "provenance": {
            **dict(dict(candidate).get("provenance") or {}),
            "run_id": str(result.get("run_id") or ""),
            "generation_result_status": status,
        },
    }
