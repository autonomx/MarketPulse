"""Trusted field-origin helpers for entity and dossier regeneration."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
)

from .generation_authorship_policy_signing import (
    bind_signed_authorship_policy,
    require_policy_bound_authorship,
    signed_authorship_policy,
)
from .generation_authorship_runtime import (
    build_generation_artifact,
    generation_artifact,
    generation_artifacts,
)
from .generation_authorship_signing import (
    attach_signed_partial_llm_authorship,
    strict_lore_string_leaves,
    verify_record_signature,
)
from .generation_authorship_targeted_signing import (
    harden_and_sign_targeted_artifact,
)
from .generation_validation import PublicationValidatedWorldForgeGenerator


class _TargetedGeneratorContext:
    def __init__(
        self,
        generator: WorldForgeTopicGenerator,
        *,
        run_id: str,
        job_id: str,
        topic_id: str,
    ) -> None:
        self.generator = generator
        self.run_id = run_id
        self.job_id = job_id
        self.topic_id = topic_id

    def generate(self, *args: Any, **kwargs: Any) -> GeneratedTopic:
        return self.generator.generate(*args, **kwargs)


def trusted_targeted_generator(
    generator: WorldForgeTopicGenerator,
    *,
    world_id: str,
    topic_id: str,
    entity_id: str,
    operation: str,
) -> WorldForgeTopicGenerator:
    stamp = datetime.now(timezone.utc).isoformat()
    run_id = f"targeted:{operation}:{world_id}:{topic_id}:{stamp}"
    job_id = f"targeted:{operation}:{entity_id}:{stamp}"
    return PublicationValidatedWorldForgeGenerator(
        _TargetedGeneratorContext(
            generator,
            run_id=run_id,
            job_id=job_id,
            topic_id=topic_id,
        )
    )


def changed_lore_paths(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    prior = {
        str(row["path"]): str(row["content_hash"])
        for row in strict_lore_string_leaves(before, policy=policy)
    }
    return tuple(
        sorted(
            str(row["path"])
            for row in strict_lore_string_leaves(after, policy=policy)
            if prior.get(str(row["path"])) != str(row["content_hash"])
        )
    )


def attach_targeted_regeneration_authorship(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    generated: GeneratedTopic,
    *,
    topic_id: str,
    operation: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Attest only changed lore paths to the new signed provider response."""

    policy = signed_authorship_policy(before)
    require_policy_bound_authorship(before, policy=policy)
    generated_payload = generated.as_dict()
    generated_policy = signed_authorship_policy(generated_payload)
    require_policy_bound_authorship(generated_payload, policy=generated_policy)
    provider_artifact = generation_artifact(generated_payload)
    if not provider_artifact or not verify_record_signature(provider_artifact):
        raise ValueError(
            f"targeted_generation_artifact_required:{topic_id}:{operation}"
        )
    changed = changed_lore_paths(before, after, policy=policy)
    if not changed:
        raise ValueError(f"targeted_generation_changed_lore_required:{topic_id}:{operation}")
    prior_artifacts = generation_artifacts(before)
    provider_artifact_id = str(
        provider_artifact.get("generation_artifact_id") or ""
    )
    parent_ids = tuple(
        dict.fromkeys((*prior_artifacts.keys(), provider_artifact_id))
    )
    provenance = {
        **dict(generated.provenance),
        "raw_response_hash": str(provider_artifact.get("raw_response_hash") or ""),
        "raw_response_hash_kind": str(
            provider_artifact.get("raw_response_hash_kind") or "provider_response"
        ),
        "transformations": ["targeted_regeneration_merge"],
    }
    unsigned = build_generation_artifact(
        after,
        run_id=str(provider_artifact.get("generation_run_id") or ""),
        job_id=str(provider_artifact.get("job_id") or ""),
        topic_id=topic_id,
        provider=provenance,
        settings={
            "generator_version": provider_artifact.get("generator_version") or "",
            "prompt_version": provider_artifact.get("prompt_version") or "",
        },
        attempt=int(provider_artifact.get("attempt") or 1),
        authored_paths=changed,
        parent_artifact_ids=parent_ids,
    )
    artifact = harden_and_sign_targeted_artifact(
        after,
        unsigned,
        authored_paths=changed,
        policy=policy,
    )
    payload = attach_signed_partial_llm_authorship(
        after,
        artifact,
        llm_paths=changed,
        prior_candidate=before,
        policy=policy,
    )
    payload = bind_signed_authorship_policy(payload, policy)
    require_policy_bound_authorship(payload, policy=policy)
    return payload, changed


__all__ = [
    "attach_targeted_regeneration_authorship",
    "changed_lore_paths",
    "trusted_targeted_generator",
]
