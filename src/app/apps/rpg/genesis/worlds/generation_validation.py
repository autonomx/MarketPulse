"""Fail-closed validation and trusted-authorship boundary for World Forge jobs."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import (
    GeneratedTopic,
    WorldForgeTopicGenerator,
    validate_generated_topic_for_publication,
)

from .generation_authorship import AuthorshipValidationError
from .generation_authorship_policy_signing import bind_signed_authorship_policy
from .generation_authorship_runtime import build_generation_artifact
from .generation_authorship_signing import (
    AuthorshipSigningKeyUnavailable,
    attach_signed_llm_authorship,
    harden_and_sign_generation_artifact,
    sanitize_untrusted_candidate,
    sign_record,
)
from .generation_manifest_binding import (
    bind_generated_topic_to_manifest,
    dependency_manifest_aliases,
    manifest_slots_from_node,
)
from .generation_contract_receipt import (
    canonical_candidate_content_hash,
    require_authoritative_contract_receipt,
)
from .generation_test_mode import deterministic_world_forge_test_mode
from .generation_failure_artifact import FailureStage, build_failure_artifact


class WorldForgePublicationBoundaryError(RuntimeError):
    def __init__(
        self,
        node: CampaignTopicNode,
        error: Exception,
        *,
        stage: FailureStage,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        rows = dict(diagnostics or {})
        artifact = build_failure_artifact(
            topic_id=node.topic_id,
            stage=stage,
            error=error,
            raw_text="",
            diagnostics=rows,
        )
        rows["failure_artifact"] = artifact.model_dump(mode="json")
        self.diagnostics = rows
        self.error = error
        super().__init__(
            f"world_forge_publication_boundary_failed:{node.topic_id}:"
            f"{type(error).__name__}:{error}"
        )


def _generator_context(generator: Any) -> dict[str, Any]:
    """Read immutable job identity through spool and provider wrappers."""

    visited: set[int] = set()
    current = generator
    context: dict[str, Any] = {}
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        payload = getattr(current, "payload", None)
        if isinstance(payload, Mapping):
            for key in ("run_id", "job_id", "topic_id", "world_id", "draft_revision"):
                if payload.get(key) not in (None, ""):
                    context.setdefault(key, payload.get(key))
            descriptor = payload.get("contract_descriptor")
            if isinstance(descriptor, Mapping):
                context.setdefault("contract_descriptor", dict(descriptor))
        for key in ("run_id", "job_id", "topic_id", "world_id", "draft_revision"):
            value = getattr(current, key, None)
            if value not in (None, ""):
                context.setdefault(key, value)
        config = getattr(current, "config", None)
        if config is not None:
            context.setdefault("provider", getattr(config, "provider", ""))
            context.setdefault("model", getattr(config, "model", ""))
        current = getattr(current, "generator", None)
    return context


def _reference_field_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    definitions = metadata.get("field_definitions")
    if not isinstance(definitions, Sequence) or isinstance(definitions, (str, bytes)):
        return ()
    return tuple(
        str(row.get("field_id") or "")
        for row in definitions
        if isinstance(row, Mapping)
        and str(row.get("value_type") or "") in {"entity_ref", "entity_ref_list"}
        and str(row.get("field_id") or "")
    )


class PublicationValidatedWorldForgeGenerator:
    """Validate provider output and replace all incoming authorship with signed evidence."""

    def __init__(self, generator: WorldForgeTopicGenerator) -> None:
        self.generator = generator

    def generate(
        self,
        node: CampaignTopicNode,
        *,
        seed: int,
        campaign_context: Mapping[str, Any],
        dependency_topics: Mapping[str, GeneratedTopic],
    ) -> GeneratedTopic:
        try:
            generated = self.generator.generate(
                node,
                seed=seed,
                campaign_context=campaign_context,
                dependency_topics=dependency_topics,
            )
        except Exception as exc:
            if getattr(exc, "diagnostics", None):
                raise
            raise WorldForgePublicationBoundaryError(
                node,
                exc,
                stage="topic_audit",
            ) from exc

        # Reject raw provider payloads before any receipt/authorship code can
        # dereference GeneratedTopic-only fields such as ``provenance``.
        if not isinstance(generated, GeneratedTopic):
            try:
                validate_generated_topic_for_publication(
                    generated,
                    expected_topic_id=node.topic_id,
                )
            except Exception as exc:
                raise WorldForgePublicationBoundaryError(
                    node,
                    exc,
                    stage="canonical_validation",
                ) from exc

        context = _generator_context(self.generator)
        authoritative_receipt: dict[str, Any] = {}
        try:
            authoritative_receipt = require_authoritative_contract_receipt(
                generated,
                expected_topic_id=node.topic_id,
                verify_content_hash=False,
            )
        except ValueError:
            if not deterministic_world_forge_test_mode():
                error = ValueError(
                    "world_forge_authoritative_contract_receipt_required"
                )
                raise WorldForgePublicationBoundaryError(
                    node,
                    error,
                    stage="contract_mismatch",
                    diagnostics=dict(generated.provenance),
                ) from error
        try:
            validated = validate_generated_topic_for_publication(
                generated,
                expected_topic_id=node.topic_id,
            )
        except Exception as exc:
            raise WorldForgePublicationBoundaryError(
                node,
                exc,
                stage="canonical_validation",
                diagnostics=dict(generated.provenance),
            ) from exc
        metadata = dict(node.metadata) if isinstance(node.metadata, Mapping) else {}
        try:
            bound = bind_generated_topic_to_manifest(
                validated.topic,
                manifest_slots_from_node(metadata),
                manifest_hash=str(metadata.get("entity_manifest_hash") or ""),
                reference_aliases=dependency_manifest_aliases(dependency_topics),
                reference_field_ids=_reference_field_ids(metadata),
            )
        except Exception as exc:
            raise WorldForgePublicationBoundaryError(
                node,
                exc,
                stage="canonical_validation",
                diagnostics=dict(generated.provenance),
            ) from exc
        if authoritative_receipt:
            planned_descriptor = context.get("contract_descriptor")
            if isinstance(planned_descriptor, Mapping):
                authoritative_receipt.update(dict(planned_descriptor))
            authoritative_receipt = sign_record(
                {
                    **authoritative_receipt,
                    "canonical_content_hash": canonical_candidate_content_hash(bound),
                    "publication_boundary": "publication_validated_world_forge_v2",
                }
            )
        receipt = validated.receipt.as_dict()
        sanitized = sanitize_untrusted_candidate(bound.as_dict())
        sanitized_provenance = dict(sanitized.get("provenance") or {})
        if authoritative_receipt:
            sanitized_provenance["authoritative_contract_receipt"] = (
                authoritative_receipt
            )
        sanitized_provenance["validation_receipt"] = receipt
        sanitized["provenance"] = sanitized_provenance
        topic = GeneratedTopic.from_dict(sanitized)

        provenance = dict(topic.provenance)
        provider = str(provenance.get("provider") or context.get("provider") or "")
        model = str(provenance.get("model") or context.get("model") or "")
        generator_name = str(provenance.get("generator") or "")
        provider_generated = generator_name.startswith("structured_world_forge_provider_")

        if not provider_generated or not provider or not model:
            if deterministic_world_forge_test_mode():
                return replace(
                    topic,
                    provenance={
                        **provenance,
                        "used_llm": False,
                        "deterministic_fixture_only": True,
                        "generation_status": "accepted",
                    },
                )
            error = ValueError("production_generation_artifact_untrusted")
            raise WorldForgePublicationBoundaryError(
                node,
                error,
                stage="canonical_validation",
                diagnostics=provenance,
            ) from error

        provider_metadata = {**provenance, "provider": provider, "model": model}
        policy = (
            dict(node.metadata.get("authorship_policy") or {})
            if isinstance(node.metadata, Mapping)
            else {}
        )
        try:
            unsigned = build_generation_artifact(
                topic.as_dict(),
                run_id=str(context.get("run_id") or ""),
                job_id=str(context.get("job_id") or ""),
                topic_id=node.topic_id,
                provider=provider_metadata,
                settings={
                    "generator_version": provenance.get("generator_version") or "",
                    "prompt_version": provenance.get("prompt_version") or "",
                },
                attempt=int(provenance.get("attempt_count") or 1),
            )
            artifact = harden_and_sign_generation_artifact(
                topic.as_dict(),
                unsigned,
                policy=policy,
            )
            authored = attach_signed_llm_authorship(
                topic.as_dict(),
                artifact,
                policy=policy,
            )
            authored = bind_signed_authorship_policy(authored, policy)
            authored_provenance = dict(authored.get("provenance") or {})
            authored_provenance["authoritative_contract_receipt"] = (
                authoritative_receipt
            )
            authored["provenance"] = authored_provenance
        except (AuthorshipValidationError, AuthorshipSigningKeyUnavailable) as exc:
            raise WorldForgePublicationBoundaryError(
                node,
                exc,
                stage="canonical_validation",
                diagnostics=provenance,
            ) from exc
        return GeneratedTopic.from_dict(authored)


__all__ = [
    "PublicationValidatedWorldForgeGenerator",
    "WorldForgePublicationBoundaryError",
]
