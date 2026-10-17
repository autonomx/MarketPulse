"""Evidence-backed recovery for production World Forge provider responses.

The existing recovery adapter is intentionally conservative and review-gated. This
wrapper adds the server evidence required by trusted authorship: hashes for every
accepted provider response, a path-aware proof that deterministic normalisation did
not move or alter lore strings, and one combined response fingerprint for batched
provider calls.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping, Sequence

from app.apps.rpg.genesis.forge.world_forge_contract import CampaignTopicNode
from app.apps.rpg.genesis.forge.world_forge_generation import GeneratedTopic
from app.apps.rpg.genesis.worlds.providers.world_forge import WorldForgeTopicResponse

from .generation_authorship_signing import prove_path_aware_structural_repair
from .generation_recovering_provider import (
    RecoveringFirstPassWorldForgeTopicGenerator,
)
from .generation_structured_recovery import decode_candidate, deterministic_repair


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


class _DiagnosticsWithRawEvidence:
    def __init__(self, diagnostics: Any, raw_text: str) -> None:
        self._diagnostics = diagnostics
        self._raw_text = raw_text

    def __getattr__(self, name: str) -> Any:
        return getattr(self._diagnostics, name)

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self._diagnostics.as_dict())
        raw_hash = _text_hash(self._raw_text)
        if raw_hash:
            payload["raw_response_hash"] = raw_hash
            payload["raw_response_length"] = len(self._raw_text)
            payload["raw_response_hash_kind"] = "provider_response"
        return payload


class _OutcomeWithRawEvidence:
    def __init__(self, outcome: Any, raw_text: str) -> None:
        self._outcome = outcome
        self.diagnostics = _DiagnosticsWithRawEvidence(
            outcome.diagnostics,
            raw_text,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._outcome, name)


class EvidenceBackedRecoveringWorldForgeTopicGenerator(
    RecoveringFirstPassWorldForgeTopicGenerator
):
    """Add immutable provider-response evidence without changing recovery policy."""

    def _provider_call(
        self,
        messages: list[Any],
        *,
        contract: Any,
        max_tokens: int,
        temperature: float,
    ):
        outcome, raw_text = super()._provider_call(
            messages,
            contract=contract,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return _OutcomeWithRawEvidence(outcome, raw_text), raw_text

    def _recover(
        self,
        *,
        contract: Any,
        outcome: Any,
        raw_text: str,
        original_messages: list[Any],
        expected_topic_id: str,
        allocated_entity_ids: tuple[str, ...],
        allowed_reference_ids: frozenset[str],
        expected_entity_kind: str,
        field_definitions: Sequence[Mapping[str, Any]],
        max_tokens: int,
        retain_invalid_kind: str,
    ):
        decoded = decode_candidate(raw_text)
        include_provenance = "provenance" in contract.output_model.model_fields
        repaired = deterministic_repair(
            decoded,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            expected_entity_kind=expected_entity_kind,
            include_provenance=include_provenance,
            allowed_root_fields=frozenset(contract.output_model.model_fields),
            allowed_reference_ids=allowed_reference_ids,
        )
        proof = None
        if decoded is not None and repaired.payload is not None:
            proof = prove_path_aware_structural_repair(decoded, repaired.payload)

        recovered = super()._recover(
            contract=contract,
            outcome=outcome,
            raw_text=raw_text,
            original_messages=original_messages,
            expected_topic_id=expected_topic_id,
            allocated_entity_ids=allocated_entity_ids,
            allowed_reference_ids=allowed_reference_ids,
            expected_entity_kind=expected_entity_kind,
            field_definitions=field_definitions,
            max_tokens=max_tokens,
            retain_invalid_kind=retain_invalid_kind,
        )
        diagnostics = dict(recovered.diagnostics)
        recovery = diagnostics.get("structured_recovery")
        recovery = dict(recovery) if isinstance(recovery, Mapping) else {}
        method = str(recovery.get("method") or "")
        if proof is not None:
            recovery["deterministic_non_authoring_proof"] = proof
        if method in {
            "deterministic_normalisation",
        }:
            original_hash = _text_hash(raw_text)
            if original_hash:
                diagnostics["raw_response_hash"] = original_hash
                diagnostics["raw_response_hash_kind"] = (
                    "provider_response"
                )
                recovery["accepted_response_hash"] = original_hash
        elif diagnostics.get("raw_response_hash"):
            recovery["accepted_response_hash"] = str(
                diagnostics["raw_response_hash"]
            )
        diagnostics["structured_recovery"] = recovery
        return replace(recovered, diagnostics=diagnostics)

    def _to_generated_topic(
        self,
        node: CampaignTopicNode,
        *,
        values: tuple[WorldForgeTopicResponse, ...],
        diagnostics: tuple[Mapping[str, Any], ...],
        prompt_tokens: int,
        completion_tokens: int,
        batch_size: int | None = None,
        entity_registry: tuple[Mapping[str, Any], ...] = (),
    ) -> GeneratedTopic:
        topic = super()._to_generated_topic(
            node,
            values=values,
            diagnostics=diagnostics,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            batch_size=batch_size,
            entity_registry=entity_registry,
        )
        response_hashes = tuple(
            str(row.get("raw_response_hash") or "")
            for row in diagnostics
            if str(row.get("raw_response_hash") or "")
        )
        if not response_hashes:
            return topic
        combined = hashlib.sha256(
            json.dumps(
                list(response_hashes),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        proofs = [
            dict(proof)
            for row in diagnostics
            for recovery in (row.get("structured_recovery"),)
            if isinstance(recovery, Mapping)
            for proof in (recovery.get("deterministic_non_authoring_proof"),)
            if isinstance(proof, Mapping)
        ]
        return replace(
            topic,
            provenance={
                **dict(topic.provenance),
                "raw_response_hash": combined,
                "raw_response_hash_kind": "provider_response_set",
                "raw_response_hashes": list(response_hashes),
                "structural_repair_proofs": proofs,
            },
        )


__all__ = ["EvidenceBackedRecoveringWorldForgeTopicGenerator"]
