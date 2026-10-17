"""Parallel, dependency-aware World Forge topic generation.

Generation is provider-pluggable. Hosted tests use the deterministic generator;
production may inject a model-backed generator that returns the same validated
topic contract.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from .world_forge_anchor_registry import anchor_slice_for_domain
from .world_forge_contract import CampaignTopicGraph, CampaignTopicNode
from .world_forge_planning import planning_slice_for_topic

_COLLECTION_NAMES = (
    "documents",
    "entities",
    "facts",
    "relationships",
    "knowledge_rules",
    "story_threads",
)


def _validated_rows(value: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"generated_topic.{name}_must_be_array")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ValueError(f"generated_topic.{name}[{index}]_must_be_object")
        rows.append(dict(row))
    return tuple(rows)


@dataclass(frozen=True)
class GeneratedTopic:
    """Domain-valid generated topic envelope."""

    topic_id: str
    documents: tuple[Mapping[str, Any], ...] = ()
    entities: tuple[Mapping[str, Any], ...] = ()
    facts: tuple[Mapping[str, Any], ...] = ()
    relationships: tuple[Mapping[str, Any], ...] = ()
    knowledge_rules: tuple[Mapping[str, Any], ...] = ()
    story_threads: tuple[Mapping[str, Any], ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        topic_id = str(self.topic_id or "").strip()
        if not topic_id:
            raise ValueError("generated_topic.topic_id_required")
        object.__setattr__(self, "topic_id", topic_id)
        for name in _COLLECTION_NAMES:
            object.__setattr__(self, name, _validated_rows(getattr(self, name), name))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("generated_topic.provenance_must_be_object")
        object.__setattr__(self, "provenance", dict(self.provenance))

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "documents": [dict(row) for row in self.documents],
            "entities": [dict(row) for row in self.entities],
            "facts": [dict(row) for row in self.facts],
            "relationships": [dict(row) for row in self.relationships],
            "knowledge_rules": [dict(row) for row in self.knowledge_rules],
            "story_threads": [dict(row) for row in self.story_threads],
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedTopic":
        if not isinstance(value, Mapping):
            raise ValueError("generated_topic.root_must_be_object")
        provenance = value.get("provenance")
        if provenance is None:
            provenance = {}
        if not isinstance(provenance, Mapping):
            raise ValueError("generated_topic.provenance_must_be_object")
        return cls(
            topic_id=str(value.get("topic_id") or ""),
            documents=_validated_rows(value.get("documents"), "documents"),
            entities=_validated_rows(value.get("entities"), "entities"),
            facts=_validated_rows(value.get("facts"), "facts"),
            relationships=_validated_rows(value.get("relationships"), "relationships"),
            knowledge_rules=_validated_rows(value.get("knowledge_rules"), "knowledge_rules"),
            story_threads=_validated_rows(value.get("story_threads"), "story_threads"),
            provenance=dict(provenance),
        )


@dataclass(frozen=True)
class GeneratedTopicValidationReceipt:
    schema_version: str
    topic_id: str
    source_contract: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "topic_id": self.topic_id,
            "source_contract": self.source_contract,
        }


@dataclass(frozen=True)
class ValidatedGeneratedTopic:
    topic: GeneratedTopic
    receipt: GeneratedTopicValidationReceipt


def validate_generated_topic_for_publication(
    topic: GeneratedTopic,
    *,
    expected_topic_id: str = "",
) -> ValidatedGeneratedTopic:
    """Revalidate a topic immediately before compilation or persistence."""

    if not isinstance(topic, GeneratedTopic):
        raise TypeError("generated_topic.publication_requires_domain_type")
    canonical = GeneratedTopic.from_dict(topic.as_dict())
    expected = str(expected_topic_id or "").strip()
    if expected and canonical.topic_id != expected:
        raise ValueError(
            f"generated_topic.topic_id_mismatch:{canonical.topic_id}:expected:{expected}"
        )
    contract = str(canonical.provenance.get("structured_contract") or "")
    return ValidatedGeneratedTopic(
        topic=canonical,
        receipt=GeneratedTopicValidationReceipt(
            schema_version="rpg_generated_topic_domain_v2",
            topic_id=canonical.topic_id,
            source_contract=contract,
        ),
    )


@dataclass(frozen=True)
class WorldForgeJobRecord:
    topic_id: str
    status: str
    dependency_ids: tuple[str, ...]
    generator_role: str
    output_counts: Mapping[str, int] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "status": self.status,
            "dependency_ids": list(self.dependency_ids),
            "generator_role": self.generator_role,
            "output_counts": dict(self.output_counts),
            "error": self.error,
        }


@dataclass(frozen=True)
class WorldForgeGenerationResult:
    topics: tuple[GeneratedTopic, ...]
    jobs: tuple[WorldForgeJobRecord, ...]
    failed_topic_ids: tuple[str, ...]
    generation_order: tuple[tuple[str, ...], ...]

    @property
    def passed(self) -> bool:
        return not self.failed_topic_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "topics": [topic.as_dict() for topic in self.topics],
            "jobs": [job.as_dict() for job in self.jobs],
            "failed_topic_ids": list(self.failed_topic_ids),
            "generation_order": [list(batch) for batch in self.generation_order],
        }


class WorldForgeTopicGenerator(Protocol):
    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic: ...


_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}


def _counts(topic: GeneratedTopic) -> dict[str, int]:
    return {
        "documents": len(topic.documents),
        "entities": len(topic.entities),
        "facts": len(topic.facts),
        "relationships": len(topic.relationships),
        "knowledge_rules": len(topic.knowledge_rules),
        "story_threads": len(topic.story_threads),
    }


def _topic_campaign_context(topic_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    scoped = dict(context)
    planning_topics = context.get("planning_topics")
    if not isinstance(planning_topics, Mapping):
        return scoped
    planning_slice = planning_slice_for_topic(topic_id, planning_topics)
    anchor_registry = planning_slice.get("anchor_registry")
    if isinstance(anchor_registry, Mapping):
        planning_slice["anchor_registry"] = anchor_slice_for_domain(topic_id, anchor_registry)
    scoped["planning_slice"] = planning_slice
    scoped.pop("planning_topics", None)
    return scoped


def _authoritative_generation_node(
    node: CampaignTopicNode,
    campaign_context: Mapping[str, Any],
) -> CampaignTopicNode:
    planning_slice = campaign_context.get("planning_slice")
    if not isinstance(planning_slice, Mapping):
        return node
    anchor_registry = planning_slice.get("anchor_registry")
    if not isinstance(anchor_registry, Mapping):
        return node
    anchor_ids = tuple(
        str(row.get("id") or "")
        for row in anchor_registry.get("anchors") or ()
        if isinstance(row, Mapping) and str(row.get("id") or "")
    )
    if not anchor_ids:
        return node
    if len(anchor_ids) != node.target_count:
        raise ValueError(
            f"authoritative_anchor_count_mismatch:{node.topic_id}:{len(anchor_ids)}:{node.target_count}"
        )
    return replace(
        node,
        metadata={
            **dict(node.metadata),
            "authoritative_entity_ids": list(anchor_ids),
            "anchor_registry_hash": str(anchor_registry.get("registry_hash") or ""),
        },
    )


def _default_generator() -> WorldForgeTopicGenerator:
    from .world_forge_deterministic import DeterministicWorldForgeGenerator

    return DeterministicWorldForgeGenerator()


def generate_campaign_topics(
    graph: CampaignTopicGraph,
    *,
    generator: WorldForgeTopicGenerator | None = None,
    seed: int = 0,
    campaign_context: Mapping[str, Any] | None = None,
    max_parallel_jobs: int = 4,
    existing_topics: Mapping[str, GeneratedTopic] | None = None,
) -> WorldForgeGenerationResult:
    """Generate independent ready topics in parallel while preserving dependencies."""

    selected_generator = generator or _default_generator()
    context = dict(campaign_context or {})
    node_map = graph.node_map()
    topics: dict[str, GeneratedTopic] = {
        topic_id: validate_generated_topic_for_publication(topic, expected_topic_id=topic_id).topic
        for topic_id, topic in dict(existing_topics or {}).items()
    }
    pending = {
        node.topic_id: set(node.dependencies)
        for node in graph.nodes
        if node.category not in _NON_GENERATION_CATEGORIES and node.topic_id not in topics
    }
    jobs: dict[str, WorldForgeJobRecord] = {
        topic_id: WorldForgeJobRecord(
            topic_id,
            "completed",
            node_map[topic_id].dependencies,
            node_map[topic_id].generator_role,
            _counts(topic),
        )
        for topic_id, topic in topics.items()
        if topic_id in node_map
    }
    batches: list[tuple[str, ...]] = []
    workers = max(1, min(int(max_parallel_jobs), 4))
    while pending:
        ready = tuple(
            sorted(
                topic_id
                for topic_id, dependencies in pending.items()
                if dependencies.intersection(pending) == set()
                and dependencies.issubset(set(topics).union(_NON_GENERATION_CATEGORIES))
            )
        )
        if not ready:
            ready = tuple(
                sorted(
                    topic_id
                    for topic_id, dependencies in pending.items()
                    if all(dep in topics or dep not in pending for dep in dependencies)
                )
            )
        if not ready:
            unresolved = ",".join(sorted(pending))
            raise ValueError(f"World Forge generation dependencies cannot be resolved: {unresolved}")
        batches.append(ready)
        with ThreadPoolExecutor(max_workers=min(workers, len(ready))) as executor:
            futures = {}
            for topic_id in ready:
                original_node = node_map[topic_id]
                dependencies = {
                    dep: topics[dep] for dep in original_node.dependencies if dep in topics
                }
                scoped_context = _topic_campaign_context(topic_id, context)
                generation_node = _authoritative_generation_node(original_node, scoped_context)
                future = executor.submit(
                    selected_generator.generate,
                    generation_node,
                    seed=seed,
                    campaign_context=scoped_context,
                    dependency_topics=dependencies,
                )
                futures[future] = original_node
            for future in as_completed(futures):
                node = futures[future]
                try:
                    topic = validate_generated_topic_for_publication(
                        future.result(), expected_topic_id=node.topic_id
                    ).topic
                    topics[node.topic_id] = topic
                    jobs[node.topic_id] = WorldForgeJobRecord(
                        node.topic_id,
                        "completed",
                        node.dependencies,
                        node.generator_role,
                        _counts(topic),
                    )
                except Exception as exc:
                    jobs[node.topic_id] = WorldForgeJobRecord(
                        node.topic_id,
                        "failed",
                        node.dependencies,
                        node.generator_role,
                        error=str(exc),
                    )
        for topic_id in ready:
            pending.pop(topic_id, None)
        failed = {topic_id for topic_id in ready if topic_id not in topics}
        if failed:
            for topic_id, waiting_on in list(pending.items()):
                if waiting_on.intersection(failed):
                    node = node_map[topic_id]
                    jobs[topic_id] = WorldForgeJobRecord(
                        topic_id,
                        "blocked",
                        node.dependencies,
                        node.generator_role,
                        error="dependency_failed",
                    )
                    pending.pop(topic_id)
            break
    ordered_topics = tuple(
        topics[node.topic_id]
        for node in graph.topological_order()
        if node.topic_id in topics
    )
    ordered_jobs = tuple(jobs[topic_id] for topic_id in sorted(jobs))
    failed_ids = tuple(
        sorted(job.topic_id for job in ordered_jobs if job.status != "completed")
    )
    return WorldForgeGenerationResult(
        ordered_topics,
        ordered_jobs,
        failed_ids,
        tuple(batches),
    )
