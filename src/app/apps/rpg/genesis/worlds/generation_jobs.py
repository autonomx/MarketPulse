"""Deterministic durable-job planning for reusable-world topic generation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.jobs.models import ResourceClass
from app.apps.rpg.genesis.forge.world_forge_contract import (
    CampaignTopicGraph,
    CampaignTopicNode,
)
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic

from .generation_contract_bundle import build_topic_contract_bundle
from .generation_entity_manifest import build_entity_manifest, topic_manifest_slots

WORLD_TOPIC_JOB_TYPE = "rpg.world.topic.generate"
WORLD_TOPIC_RESOURCE_CLASS = ResourceClass.RPG_WORLD_GENERATION.value
WORLD_TOPIC_JOB_CONTRACT = "rpg_world_topic_job_v2"
WORLD_TOPIC_OUTPUT_SCHEMA = "rpg_world_topic_output_v2"
_NON_GENERATION_CATEGORIES = {"compiler", "audit", "index", "bootstrap"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.:-]+")
_PROMPT_ONLY_DIRECTIVE_KEYS = {"manual_retry"}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def canonical_generation_directives(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove run-specific prompt evidence from reusable authoring fingerprints."""

    return {
        str(key): item
        for key, item in value.items()
        if str(key) not in _PROMPT_ONLY_DIRECTIVE_KEYS
    }


def world_generation_run_id(
    *,
    world_id: str,
    draft_revision: int,
    scope_hash: str = "",
    run_nonce: str = "",
) -> str:
    safe_world = _SAFE_ID.sub("-", world_id).strip("-")
    scope_digest = str(scope_hash or "full").removeprefix("sha256:")[:12]
    nonce = _SAFE_ID.sub("-", str(run_nonce or "run")).strip("-")[:16]
    return (
        f"world-generation:{safe_world}:draft:{int(draft_revision)}:"
        f"{scope_digest}:{nonce}"
    )


@dataclass(frozen=True)
class WorldTopicGenerationSettings:
    generator_version: str
    prompt_version: str
    provider_route: str
    model: str
    seed: int
    topic_contract_version: str = WORLD_TOPIC_JOB_CONTRACT
    output_schema_version: str = WORLD_TOPIC_OUTPUT_SCHEMA
    compiler_version: str = "world-compiler-v1"
    # Attempt one performs the provider call. Later queue leases are persistence or
    # phase replay only and are extended from durable spool evidence by the worker.
    max_attempts: int = 2
    priority: int = 10

    def as_dict(self) -> dict[str, Any]:
        return {
            "generator_version": self.generator_version,
            "prompt_version": self.prompt_version,
            "provider_route": self.provider_route,
            "model": self.model,
            "seed": int(self.seed),
            "topic_contract_version": self.topic_contract_version,
            "output_schema_version": self.output_schema_version,
            "compiler_version": self.compiler_version,
            "max_attempts": 2,
            "content_generation_attempts": 1,
            "priority": int(self.priority),
        }


@dataclass(frozen=True)
class WorldTopicJobPlan:
    run_id: str
    world_id: str
    draft_revision: int
    topic_id: str
    dependency_ids: tuple[str, ...]
    dependency_hashes: Mapping[str, str]
    directive_hash: str
    input_hash: str
    fingerprint: str
    job_id: str
    job_payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "world_id": self.world_id,
            "draft_revision": self.draft_revision,
            "topic_id": self.topic_id,
            "dependency_ids": list(self.dependency_ids),
            "dependency_hashes": dict(self.dependency_hashes),
            "directive_hash": self.directive_hash,
            "input_hash": self.input_hash,
            "fingerprint": self.fingerprint,
            "job_id": self.job_id,
            "job_payload": dict(self.job_payload),
        }


def topic_generation_fingerprint(
    node: CampaignTopicNode,
    *,
    normalized_topic_input: Mapping[str, Any],
    dependency_hashes: Mapping[str, str],
    directives: Mapping[str, Any],
    entity_manifest_hash: str,
    settings: WorldTopicGenerationSettings,
    contract_descriptor: Mapping[str, Any] | None = None,
) -> tuple[str, str, str]:
    topic_input = {
        "node": node.as_dict(),
        "input": dict(normalized_topic_input),
    }
    input_hash = canonical_hash(topic_input)
    directive_hash = canonical_hash(canonical_generation_directives(directives))
    fingerprint_payload = {
        "topic_id": node.topic_id,
        "input_hash": input_hash,
        "dependency_hashes": dict(sorted(dependency_hashes.items())),
        "directive_hash": directive_hash,
        "entity_manifest_hash": entity_manifest_hash,
        "contract_descriptor": dict(contract_descriptor or {}),
        **settings.as_dict(),
    }
    return canonical_hash(fingerprint_payload), input_hash, directive_hash


def world_topic_job_id(
    *,
    world_id: str,
    draft_revision: int,
    topic_id: str,
    fingerprint: str,
    run_id: str = "",
    recovery_pass: int = 0,
) -> str:
    safe_world = _SAFE_ID.sub("-", world_id).strip("-")
    safe_topic = _SAFE_ID.sub("-", topic_id).strip("-")
    digest = fingerprint.removeprefix("sha256:")[:16]
    run_digest = canonical_hash({"run_id": run_id}).removeprefix("sha256:")[:10]
    job_id = (
        f"world-topic:{safe_world}:draft:{int(draft_revision)}:"
        f"{safe_topic}:{digest}:{run_digest}"
    )
    return job_id if recovery_pass <= 0 else f"{job_id}:recovery:{int(recovery_pass)}"


def generation_topic_ids(
    graph: CampaignTopicGraph,
    target_topic_ids: Sequence[str] | None = None,
) -> tuple[str, ...]:
    allowed = set(target_topic_ids or ())
    return tuple(
        node.topic_id
        for node in graph.topological_order()
        if node.category not in _NON_GENERATION_CATEGORIES
        and (not allowed or node.topic_id in allowed)
    )


def plan_ready_topic_jobs(
    graph: CampaignTopicGraph,
    *,
    run_id: str,
    world_id: str,
    draft_revision: int,
    generation_context: Mapping[str, Any],
    topic_directives: Mapping[str, Mapping[str, Any]],
    completed_topics: Mapping[str, Mapping[str, Any]],
    existing_job_ids: Sequence[str],
    entity_manifest_hash: str,
    settings: WorldTopicGenerationSettings,
    target_topic_ids: Sequence[str] | None = None,
    recovery_pass: int = 0,
) -> tuple[WorldTopicJobPlan, ...]:
    existing = set(existing_job_ids)
    targets = set(generation_topic_ids(graph, target_topic_ids))
    completed_ids = {
        topic_id
        for topic_id, row in completed_topics.items()
        if str(row.get("status") or "") == "ready" and row.get("content_hash")
    }
    authoritative_manifest = build_entity_manifest(graph)
    authoritative_manifest_hash = str(authoritative_manifest["content_hash"])
    plans: list[WorldTopicJobPlan] = []
    for node in graph.topological_order():
        if node.category in _NON_GENERATION_CATEGORIES or node.topic_id not in targets:
            continue
        if node.topic_id in completed_ids:
            continue
        if not set(node.dependencies).issubset(completed_ids):
            continue
        dependency_hashes = {
            dependency_id: str(completed_topics[dependency_id]["content_hash"])
            for dependency_id in node.dependencies
        }
        dependency_trust = {
            dependency_id: str(
                completed_topics[dependency_id].get("dependency_trust") or "accepted"
            )
            for dependency_id in node.dependencies
        }
        topic_slots = list(topic_manifest_slots(authoritative_manifest, node.topic_id))
        dependencies: dict[str, GeneratedTopic] = {}
        for dependency_id in node.dependencies:
            completed = dict(completed_topics.get(dependency_id) or {})
            candidate = completed.get("content") or completed.get("candidate")
            if isinstance(candidate, Mapping):
                dependencies[dependency_id] = GeneratedTopic.from_dict(dict(candidate))
        bundle = build_topic_contract_bundle(
            node,
            allocated_entity_ids=tuple(
                str(slot["entity_id"]) for slot in topic_slots
            ),
            dependencies=dependencies,
        )
        contract_descriptor = bundle.descriptor()
        directives = dict(topic_directives.get(node.topic_id) or {})
        canonical_directives = canonical_generation_directives(directives)
        fingerprint, input_hash, directive_hash = topic_generation_fingerprint(
            node,
            normalized_topic_input={
                "generation_context": dict(generation_context),
                "target_count": node.target_count,
                "visibility": node.visibility,
                "dependency_trust": dependency_trust,
            },
            dependency_hashes=dependency_hashes,
            directives=directives,
            entity_manifest_hash=entity_manifest_hash,
            settings=settings,
            contract_descriptor=contract_descriptor,
        )
        job_id = world_topic_job_id(
            world_id=world_id,
            draft_revision=draft_revision,
            topic_id=node.topic_id,
            fingerprint=fingerprint,
            run_id=run_id,
            recovery_pass=recovery_pass,
        )
        if job_id in existing:
            continue
        topic_payload = node.as_dict()
        topic_payload["metadata"] = {
            **dict(node.metadata),
            "authoritative_entity_ids": [
                str(slot["entity_id"]) for slot in topic_slots
            ],
            "entity_manifest_slots": topic_slots,
            "contract_descriptor": contract_descriptor,
            "entity_manifest_hash": authoritative_manifest_hash,
        }
        input_payload = {
            "contract_version": WORLD_TOPIC_JOB_CONTRACT,
            "run_id": run_id,
            "world_id": world_id,
            "draft_revision": int(draft_revision),
            "topic": topic_payload,
            "generation_context": dict(generation_context),
            "directives": directives,
            "canonical_directives": canonical_directives,
            "dependency_hashes": dependency_hashes,
            "dependency_trust": dependency_trust,
            "fingerprint": fingerprint,
            "input_hash": input_hash,
            "directive_hash": directive_hash,
            "entity_manifest_hash": authoritative_manifest_hash,
            "requested_entity_manifest_hash": entity_manifest_hash,
            "entity_manifest": authoritative_manifest,
            "entity_manifest_slots": topic_slots,
            "contract_descriptor": contract_descriptor,
            "settings": settings.as_dict(),
            "recovery_pass": int(recovery_pass),
        }
        job_payload = {
            "id": job_id,
            "module": "rpg",
            "job_type": WORLD_TOPIC_JOB_TYPE,
            "resource_class": WORLD_TOPIC_RESOURCE_CLASS,
            "priority": settings.priority,
            "max_attempts": settings.max_attempts,
            "input_payload": input_payload,
            "metadata": {
                "contract_version": WORLD_TOPIC_JOB_CONTRACT,
                "run_id": run_id,
                "world_id": world_id,
                "draft_revision": int(draft_revision),
                "topic_id": node.topic_id,
                "fingerprint": fingerprint,
                "dependency_ids": list(node.dependencies),
                "dependency_trust": dependency_trust,
                "entity_manifest_hash": authoritative_manifest_hash,
                "entity_manifest_slot_count": len(topic_slots),
                "recovery_pass": int(recovery_pass),
            },
        }
        plans.append(
            WorldTopicJobPlan(
                run_id=run_id,
                world_id=world_id,
                draft_revision=int(draft_revision),
                topic_id=node.topic_id,
                dependency_ids=node.dependencies,
                dependency_hashes=dependency_hashes,
                directive_hash=directive_hash,
                input_hash=input_hash,
                fingerprint=fingerprint,
                job_id=job_id,
                job_payload=job_payload,
            )
        )
    return tuple(plans)


def generation_progress(
    graph: CampaignTopicGraph,
    *,
    completed_topic_ids: Sequence[str] = (),
    active_topic_ids: Sequence[str],
    failed_topic_ids: Sequence[str] = (),
    flagged_topic_ids: Sequence[str] = (),
    blocked_topic_ids: Sequence[str] = (),
    accepted_topic_ids: Sequence[str] = (),
    issue_counts: Mapping[str, Any] | None = None,
    target_topic_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    generation_ids = list(generation_topic_ids(graph, target_topic_ids))
    targets = set(generation_ids)
    accepted = set(accepted_topic_ids or completed_topic_ids).intersection(targets)
    flagged = set(flagged_topic_ids).intersection(targets)
    failed = set(failed_topic_ids).intersection(targets)
    blocked = set(blocked_topic_ids).intersection(targets)
    active = set(active_topic_ids).intersection(targets)
    terminal = accepted | flagged | failed | blocked
    total = len(generation_ids)
    terminal_count = len(terminal)
    return {
        "total_topics": total,
        "completed_topics": terminal_count,
        "accepted_topics": len(accepted),
        "flagged_topics": len(flagged),
        "failed_topics": len(failed),
        "blocked_topics": len(blocked),
        "target_topic_ids": generation_ids,
        "accepted_topic_ids": sorted(accepted),
        "flagged_topic_ids": sorted(flagged),
        "failed_topic_ids": sorted(failed),
        "blocked_topic_ids": sorted(blocked),
        "active_topic_ids": sorted(active),
        "issue_counts": dict(issue_counts or {}),
        "percent": 100 if total == 0 else round(terminal_count / total * 100),
        "generation_complete": terminal_count == total and not active,
        "publication_blocked": bool(flagged or failed or blocked),
    }
