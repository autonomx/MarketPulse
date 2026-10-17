"""Authority-preserving TaskGraph optimization planning."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import EvidenceCoverage, ModelRef
from .evidence import capability_for_requirement
from .task_graph import TaskGraph, TaskNode, task_node_fingerprint


class EvidenceAcquisitionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    capability_id: str
    source_class: str
    trust_floor: str
    freshness: str
    fallback_policy: str
    node_ids: list[str]
    requirement_ids: list[str]
    coverage: list[EvidenceCoverage] = Field(default_factory=list)


class TaskGraphOptimizationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_batches: list[EvidenceAcquisitionBatch] = Field(default_factory=list)
    parallel_groups: list[list[str]] = Field(default_factory=list)
    speculative_read_nodes: list[str] = Field(default_factory=list)
    cache_keys: dict[str, str] = Field(default_factory=dict)
    cost_priority: list[str] = Field(default_factory=list)
    model_selections: dict[str, ModelRef] = Field(default_factory=dict)


def _batch_key(
    node: TaskNode,
    requirement,
) -> tuple[str, str, str, str, str, str]:
    capability, _trust = capability_for_requirement(requirement)
    as_of = (
        requirement.as_of_date.isoformat()
        if requirement.freshness == "as_of_date"
        and requirement.as_of_date is not None
        else ""
    )
    return (
        capability,
        requirement.source_class,
        requirement.trust_floor,
        requirement.freshness,
        as_of,
        requirement.fallback_policy,
    )


def plan_evidence_batches(graph: TaskGraph) -> list[EvidenceAcquisitionBatch]:
    grouped: dict[
        tuple[str, str, str, str, str, str],
        dict[str, Any],
    ] = {}
    for node in graph.nodes:
        if node.evidence_policy.requirement != "required":
            continue
        for requirement in node.evidence_policy.requirements:
            key = _batch_key(node, requirement)
            row = grouped.setdefault(
                key,
                {
                    "nodes": [],
                    "requirements": [],
                    "coverage": [],
                },
            )
            if node.id not in row["nodes"]:
                row["nodes"].append(node.id)
            row["requirements"].append(requirement.id)
            if requirement.coverage is not None:
                coverage_key = requirement.coverage.model_dump_json()
                if all(item.model_dump_json() != coverage_key for item in row["coverage"]):
                    row["coverage"].append(requirement.coverage)

    batches: list[EvidenceAcquisitionBatch] = []
    for index, (key, row) in enumerate(sorted(grouped.items()), start=1):
        capability, source_class, trust, freshness, _as_of, fallback = key
        batches.append(
            EvidenceAcquisitionBatch(
                batch_id=f"evidence-batch-{index}",
                capability_id=capability,
                source_class=source_class,
                trust_floor=trust,
                freshness=freshness,
                fallback_policy=fallback,
                node_ids=sorted(row["nodes"]),
                requirement_ids=sorted(row["requirements"]),
                coverage=list(row["coverage"]),
            )
        )
    return batches


def _topological_levels(graph: TaskGraph) -> list[list[str]]:
    incoming: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    outgoing: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        incoming[edge.target].add(edge.source)
        outgoing[edge.source].add(edge.target)

    remaining = set(incoming)
    levels: list[list[str]] = []
    while remaining:
        level = sorted(
            node_id
            for node_id in remaining
            if not (incoming[node_id] & remaining)
        )
        if not level:
            raise ValueError("task graph contains a cycle")
        levels.append(level)
        remaining.difference_update(level)
    return levels


def _critical_path_costs(graph: TaskGraph) -> dict[str, float]:
    nodes = {node.id: node for node in graph.nodes}
    outgoing: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        outgoing[edge.source].append(edge.target)

    memo: dict[str, float] = {}

    def visit(node_id: str) -> float:
        if node_id in memo:
            return memo[node_id]
        downstream = max((visit(item) for item in outgoing[node_id]), default=0.0)
        memo[node_id] = float(nodes[node_id].estimated_cost) + downstream
        return memo[node_id]

    for node_id in nodes:
        visit(node_id)
    return memo


def task_node_cache_key(graph: TaskGraph, node: TaskNode) -> str | None:
    if not node.cacheable:
        return None
    if any(
        token in {
            "workspace_mutate",
            "workspace_execute",
            "ops_execute",
            "home_mutate",
            "email_send",
            "email_draft",
            "calendar_create",
        }
        for token in node.semantic_action_intents
    ):
        return None
    if any(
        requirement.freshness == "current"
        for requirement in node.evidence_policy.requirements
    ):
        # Current evidence needs a cache with explicit observed-at/max-age
        # validation. Phase 19's graph-local cache deliberately excludes it.
        return None
    incoming = sorted(
        (edge.model_dump(mode="json") for edge in graph.edges if edge.target == node.id),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    payload = {
        "node": task_node_fingerprint(node),
        "incoming": incoming,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def optimize_task_graph(
    graph: TaskGraph,
    *,
    model_overrides: dict[str, ModelRef] | None = None,
) -> TaskGraphOptimizationPlan:
    levels = _topological_levels(graph)
    node_map = {node.id: node for node in graph.nodes}
    cache_keys = {
        node.id: key
        for node in graph.nodes
        if (key := task_node_cache_key(graph, node)) is not None
    }
    roots = levels[0] if levels else []
    speculative = [
        node_id
        for node_id in roots
        if node_map[node_id].cacheable
        and node_map[node_id].kind in {"evidence_read", "agent"}
    ]
    costs = _critical_path_costs(graph)
    priority = sorted(costs, key=lambda node_id: (-costs[node_id], node_id))

    selections: dict[str, ModelRef] = {}
    overrides = model_overrides or {}
    for node in graph.nodes:
        override = overrides.get(node.profile_id or "")
        if override is not None and node.kind in {"agent", "evidence_read"}:
            selections[node.id] = override
        elif node.model is not None:
            selections[node.id] = node.model

    return TaskGraphOptimizationPlan(
        evidence_batches=plan_evidence_batches(graph),
        parallel_groups=levels,
        speculative_read_nodes=speculative,
        cache_keys=cache_keys,
        cost_priority=priority,
        model_selections=selections,
    )
