"""Local terminal-failure spool preventing provider regeneration after DB outages."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)
from app.apps.rpg.genesis.forge.world_forge_review import failure_report

from .generation_candidate_spool import (
    spool_path,
    write_provider_started_spool,
)


def failure_spool_path(job_id: str) -> Path:
    candidate_path = spool_path(job_id)
    return candidate_path.with_name(candidate_path.name.removesuffix(".json") + ".failure.json")


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_failure_spool(
    job_id: str,
    *,
    run_id: str,
    world_id: str,
    draft_revision: int,
    topic_id: str,
    error: Exception,
    dependency_hashes: Mapping[str, Any],
    dependency_trust: Mapping[str, Any],
) -> Path:
    target = failure_spool_path(job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    diagnostics = dict(getattr(error, "diagnostics", {}) or {})
    payload = {
        "schema_version": "rpg_world_generation_failure_spool_v1",
        "run_id": run_id,
        "world_id": world_id,
        "draft_revision": int(draft_revision),
        "topic_id": topic_id,
        "validation": failure_report(topic_id, error),
        "provider": diagnostics,
        "dependency_hashes": dict(dependency_hashes),
        "dependency_trust": dict(dependency_trust),
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_failure_spool(job_id: str) -> dict[str, Any] | None:
    target = failure_spool_path(job_id)
    if not target.exists():
        return None
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"world_generation_failure_spool_invalid:{job_id}")
    return dict(value)


def delete_failure_spool(job_id: str) -> None:
    failure_spool_path(job_id).unlink(missing_ok=True)


class ReplayedGenerationFailure(RuntimeError):
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        self.diagnostics = dict(payload.get("provider") or {})
        super().__init__(str(payload.get("error_message") or "world_generation_failed"))


class FailureSpoolingWorldForgeGenerator:
    """Record provider-started and terminal provider-error phases durably."""

    def __init__(
        self,
        generator: WorldForgeTopicGenerator,
        *,
        job_id: str,
        run_id: str,
        world_id: str,
        draft_revision: int,
        topic_id: str,
        dependency_hashes: Mapping[str, Any],
        dependency_trust: Mapping[str, Any],
    ) -> None:
        self.generator = generator
        self.job_id = job_id
        self.run_id = run_id
        self.world_id = world_id
        self.draft_revision = draft_revision
        self.topic_id = topic_id
        self.dependency_hashes = dict(dependency_hashes)
        self.dependency_trust = dict(dependency_trust)

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        write_provider_started_spool(
            self.job_id,
            {
                "schema_version": "rpg_world_generation_provider_started_v1",
                "run_id": self.run_id,
                "world_id": self.world_id,
                "draft_revision": self.draft_revision,
                "topic_id": self.topic_id,
                "job_id": self.job_id,
            },
        )
        try:
            return self.generator.generate(
                node,
                seed=seed,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )
        except Exception as error:
            write_failure_spool(
                self.job_id,
                run_id=self.run_id,
                world_id=self.world_id,
                draft_revision=self.draft_revision,
                topic_id=self.topic_id,
                error=error,
                dependency_hashes=self.dependency_hashes,
                dependency_trust=self.dependency_trust,
            )
            raise


class ReplayedFailureWorldForgeGenerator:
    """Raise the original spooled failure without invoking a provider."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        del node, seed, campaign_context, dependency_topics
        raise ReplayedGenerationFailure(self.payload)


__all__ = [
    "FailureSpoolingWorldForgeGenerator",
    "ReplayedFailureWorldForgeGenerator",
    "delete_failure_spool",
    "failure_spool_path",
    "read_failure_spool",
    "write_failure_spool",
]
