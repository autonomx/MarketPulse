"""Bind World Forge certification to the exact topics sent to publication."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canon_repair import repair_generation_contracts
from .contracts import WorldRevisionDocument, canonical_content_hash
from .generation_publication import (
    WorldGenerationPublication,
    _generation_result,
    _graph_from_payload,
)


@dataclass(frozen=True)
class PreparedWorldGenerationAudit:
    """Canonical topic rows shared by post-normalisation audits and publication."""

    graph: Mapping[str, Any]
    topic_rows: tuple[Mapping[str, Any], ...]
    topic_hashes: Mapping[str, str]
    content_hash: str


class ExactArtifactBindingError(ValueError):
    """Raised when a compiled revision is not bound to its audited topics."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        self.report = dict(report)
        super().__init__("world_generation_exact_artifact_binding_failed")


def _starting_location(
    run: Mapping[str, Any],
    world: Mapping[str, Any],
    override: str,
) -> str:
    context = dict(run.get("context") or {})
    generation_context = dict(context.get("generation_context") or {})
    metadata = world.get("metadata")
    world_metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    return str(
        override
        or generation_context.get("starting_location")
        or world_metadata.get("starting_location")
        or ""
    )


def _publication_rows(
    topic_rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for raw in topic_rows:
        row = dict(raw)
        content = row.get("content")
        if not isinstance(content, Mapping):
            content = row.get("candidate")
        if not isinstance(content, Mapping):
            rows.append(row)
            continue
        rows.append(
            {
                **row,
                "status": str(row.get("status") or "ready"),
                "content": dict(content),
            }
        )
    return rows


def prepare_world_generation_audit_rows(
    *,
    run: Mapping[str, Any],
    world: Mapping[str, Any],
    topic_rows: Sequence[Mapping[str, Any]],
    starting_location_override: str = "",
) -> PreparedWorldGenerationAudit:
    """Normalise and revalidate the exact rows that publication will consume."""

    graph = _graph_from_payload(dict(run.get("graph") or {}))
    generation = _generation_result(graph, _publication_rows(topic_rows))
    context = dict(run.get("context") or {})
    generation_context = dict(context.get("generation_context") or {})
    generation = repair_generation_contracts(
        generation,
        starting_location=_starting_location(
            run,
            world,
            starting_location_override,
        ),
        topic_graph=graph,
        generation_context=generation_context,
    )
    prepared_rows: list[Mapping[str, Any]] = []
    topic_hashes: dict[str, str] = {}
    for topic in generation.topics:
        content = topic.as_dict()
        content_hash = canonical_content_hash(content)
        topic_hashes[topic.topic_id] = content_hash
        prepared_rows.append(
            {
                "topic_id": topic.topic_id,
                "status": "ready",
                "content": content,
                "content_hash": content_hash,
            }
        )
    binding_payload = {
        "schema_version": "rpg_world_audited_topic_set_v1",
        "topic_hashes": {
            key: topic_hashes[key]
            for key in sorted(topic_hashes)
        },
    }
    return PreparedWorldGenerationAudit(
        graph=graph.as_dict(),
        topic_rows=tuple(prepared_rows),
        topic_hashes=topic_hashes,
        content_hash=canonical_content_hash(binding_payload),
    )


def _binding_report(
    *,
    audited_topic_hash: str,
    expected_hashes: Mapping[str, str],
    revision: Any,
) -> dict[str, Any]:
    published_hashes = {
        str(key): str(value)
        for key, value in dict(revision.provenance.get("topic_hashes") or {}).items()
    }
    expected = {
        str(key): str(value)
        for key, value in expected_hashes.items()
    }
    revision_hash = str(revision.content_hash or "")
    checks = {
        "typed_revision_available": True,
        "audited_topic_set_hashed": str(audited_topic_hash).startswith("sha256:"),
        "published_topic_hashes_exact": published_hashes == expected,
        "published_revision_hashed": revision_hash.startswith("sha256:"),
    }
    issues = []
    if not checks["published_topic_hashes_exact"]:
        issues.append(
            {
                "code": "published_topic_hashes_mismatch",
                "severity": "error",
                "blocking": True,
                "expected_topic_hashes": expected,
                "published_topic_hashes": published_hashes,
            }
        )
    if not checks["published_revision_hashed"]:
        issues.append(
            {
                "code": "published_world_revision_hash_missing",
                "severity": "error",
                "blocking": True,
            }
        )
    return {
        "schema_version": "rpg_world_exact_artifact_binding_v1",
        "passed": all(checks.values()),
        "skipped": False,
        "issues": issues,
        "checks": checks,
        "audited_topic_hash": str(audited_topic_hash),
        "audited_topic_hashes": expected,
        "published_world_revision_hash": revision_hash,
    }


def exact_artifact_binding_report(
    prepared: PreparedWorldGenerationAudit,
    publication: WorldGenerationPublication,
) -> dict[str, Any]:
    revision = publication.world_revision
    if not hasattr(revision, "provenance") or not hasattr(
        revision,
        "content_hash",
    ):
        return {
            "schema_version": "rpg_world_exact_artifact_binding_v1",
            "passed": True,
            "skipped": True,
            "issues": [],
            "checks": {"typed_revision_available": False},
            "audited_topic_hash": prepared.content_hash,
            "audited_topic_hashes": dict(prepared.topic_hashes),
            "published_world_revision_hash": "",
        }
    return _binding_report(
        audited_topic_hash=prepared.content_hash,
        expected_hashes=prepared.topic_hashes,
        revision=revision,
    )


def rebind_exact_artifact_report(
    prior_report: Mapping[str, Any],
    revision: WorldRevisionDocument,
) -> dict[str, Any]:
    """Refresh the binding after blueprint and starter-map assembly changes the hash."""

    if bool(prior_report.get("skipped")):
        return dict(prior_report)
    return _binding_report(
        audited_topic_hash=str(prior_report.get("audited_topic_hash") or ""),
        expected_hashes={
            str(key): str(value)
            for key, value in dict(prior_report.get("audited_topic_hashes") or {}).items()
        },
        revision=revision,
    )


def require_exact_artifact_binding(report: Mapping[str, Any]) -> None:
    if not bool(report.get("passed")):
        raise ExactArtifactBindingError(report)


__all__ = [
    "ExactArtifactBindingError",
    "PreparedWorldGenerationAudit",
    "exact_artifact_binding_report",
    "prepare_world_generation_audit_rows",
    "rebind_exact_artifact_report",
    "require_exact_artifact_binding",
]
