"""Atomic local spools for World Forge provider and persistence phases."""
from __future__ import annotations
from app.config.env import env_str as _env_str

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _root() -> Path:
    configured = str(_env_str("OMNIX_RPG_WORLD_GENERATION_SPOOL_DIR") or "").strip()
    return Path(configured or "resources/data/world-generation-spool")


def _safe_job_id(job_id: str) -> str:
    safe = _SAFE.sub("-", str(job_id)).strip("-")[:140]
    digest = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:16]
    return f"{safe or 'job'}-{digest}.json"


def spool_path(job_id: str) -> Path:
    return _root() / _safe_job_id(job_id)


def raw_candidate_spool_path(job_id: str) -> Path:
    target = spool_path(job_id)
    return target.with_name(target.name.removesuffix(".json") + ".raw.json")


def provider_started_spool_path(job_id: str) -> Path:
    target = spool_path(job_id)
    return target.with_name(target.name.removesuffix(".json") + ".started.json")


def _write_json(target: Path, payload: Mapping[str, Any]) -> Path:
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=root,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            directory_fd = os.open(root, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _read_json(target: Path, *, error_code: str) -> dict[str, Any] | None:
    if not target.exists():
        return None
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(error_code)
    return dict(value)


def write_candidate_spool(job_id: str, payload: Mapping[str, Any]) -> Path:
    """Write one complete validated candidate artifact atomically and durably."""

    return _write_json(spool_path(job_id), payload)


def read_candidate_spool(job_id: str) -> dict[str, Any] | None:
    return _read_json(
        spool_path(job_id),
        error_code=f"world_generation_spool_invalid:{job_id}",
    )


def delete_candidate_spool(job_id: str) -> None:
    spool_path(job_id).unlink(missing_ok=True)


def write_provider_started_spool(job_id: str, payload: Mapping[str, Any]) -> Path:
    """Durably consume the single provider-attempt budget before invocation."""

    return _write_json(provider_started_spool_path(job_id), payload)


def read_provider_started_spool(job_id: str) -> dict[str, Any] | None:
    return _read_json(
        provider_started_spool_path(job_id),
        error_code=f"world_generation_provider_started_spool_invalid:{job_id}",
    )


def delete_provider_started_spool(job_id: str) -> None:
    provider_started_spool_path(job_id).unlink(missing_ok=True)


def write_raw_candidate_spool(job_id: str, payload: Mapping[str, Any]) -> Path:
    """Checkpoint provider output before outer validation and PostgreSQL work."""

    return _write_json(raw_candidate_spool_path(job_id), payload)


def read_raw_candidate_spool(job_id: str) -> dict[str, Any] | None:
    return _read_json(
        raw_candidate_spool_path(job_id),
        error_code=f"world_generation_raw_candidate_spool_invalid:{job_id}",
    )


def delete_raw_candidate_spool(job_id: str) -> None:
    raw_candidate_spool_path(job_id).unlink(missing_ok=True)


def _raw_candidate_payload(value: Any) -> tuple[str, Any]:
    if isinstance(value, GeneratedTopic):
        return "generated_topic", value.as_dict()
    if isinstance(value, Mapping):
        return "mapping", dict(value)
    if value is None or isinstance(value, (str, bool, int, float, list, tuple)):
        return "raw_json", value
    return "repr", repr(value)


class RawCandidateSpoolingWorldForgeGenerator:
    """Checkpoint the first provider candidate before outer validation work."""

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
    ) -> Any:
        generated = self.generator.generate(
            node,
            seed=seed,
            campaign_context=campaign_context,
            dependency_topics=dependency_topics,
        )
        candidate_type, candidate = _raw_candidate_payload(generated)
        write_raw_candidate_spool(
            self.job_id,
            {
                "schema_version": "rpg_world_generation_raw_candidate_spool_v2",
                "run_id": self.run_id,
                "world_id": self.world_id,
                "draft_revision": self.draft_revision,
                "topic_id": self.topic_id,
                "candidate_type": candidate_type,
                "candidate": candidate,
                "dependency_hashes": self.dependency_hashes,
                "dependency_trust": self.dependency_trust,
                "job_id": self.job_id,
            },
        )
        return generated


class ReplayedRawCandidateWorldForgeGenerator:
    """Return checkpointed output without invoking a provider or changing its type."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> Any:
        del seed, campaign_context, dependency_topics
        candidate_type = str(self.payload.get("candidate_type") or "generated_topic")
        candidate = self.payload.get("candidate")
        if candidate_type == "generated_topic":
            topic = GeneratedTopic.from_dict(dict(candidate or {}))
            expected = str(self.payload.get("topic_id") or node.topic_id)
            if topic.topic_id != expected or topic.topic_id != node.topic_id:
                raise RuntimeError(
                    f"world_generation_raw_spool_identity_mismatch:{topic.topic_id}:{expected}:{node.topic_id}"
                )
            return topic
        if candidate_type == "mapping":
            return dict(candidate or {})
        return candidate


__all__ = [
    "RawCandidateSpoolingWorldForgeGenerator",
    "ReplayedRawCandidateWorldForgeGenerator",
    "delete_candidate_spool",
    "delete_provider_started_spool",
    "delete_raw_candidate_spool",
    "provider_started_spool_path",
    "raw_candidate_spool_path",
    "read_candidate_spool",
    "read_provider_started_spool",
    "read_raw_candidate_spool",
    "spool_path",
    "write_candidate_spool",
    "write_provider_started_spool",
    "write_raw_candidate_spool",
]
