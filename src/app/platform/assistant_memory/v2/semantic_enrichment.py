from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .consolidation import ConsolidationPlan
from .contracts import (
    GraphAssertion,
    GraphEntityRef,
    GraphValue,
    MemoryDomain,
    MemorySpaceKey,
    Observation,
)
from .convergence import DerivedPlanPayload
from .temporal import TemporalClaim, apply_temporal_claim

SemanticOperation = Literal["assert", "retract"]


@dataclass(frozen=True, slots=True)
class SemanticMemoryProposal:
    """Provider-neutral semantic proposal; never a direct graph write."""

    subject: GraphEntityRef
    predicate: str
    domain: MemoryDomain
    effective_at: datetime
    operation: SemanticOperation = "assert"
    value: GraphValue | None = None
    confidence: float = 1.0
    single_valued: bool = True

    def __post_init__(self) -> None:
        if not self.predicate:
            raise ValueError("semantic proposal predicate is required")
        if self.operation == "assert" and self.value is None:
            raise ValueError("assert proposal requires a value")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("semantic proposal confidence must be between 0 and 1")

    def normalized(self, observation_id: str) -> dict[str, Any]:
        return {
            "source_observation_id": observation_id,
            "subject": self.subject.model_dump(mode="json"),
            "predicate": self.predicate,
            "domain": self.domain,
            "effective_at": self.effective_at.isoformat(),
            "operation": self.operation,
            "value": self.value.model_dump(mode="json") if self.value is not None else None,
            "confidence": self.confidence,
            "single_valued": self.single_valued,
        }


SemanticExtractor = Callable[[Observation], Iterable[SemanticMemoryProposal]]

_AUTHORITATIVE_SEMANTIC_EVENTS = {
    "user_said",
    "assistant_experienced",
    "external_observed",
    "system_event",
    "imported_legacy_memory",
}


def _replace_assertion(
    working: list[GraphAssertion],
    replacement: GraphAssertion,
) -> None:
    for index, item in enumerate(working):
        if item.assertion_id == replacement.assertion_id:
            working[index] = replacement
            return
    working.append(replacement)


class VoiceMemDerivedSemanticEnricher:
    """VoiceMem-style extraction behind Omnix-native authority boundaries.

    `plan()` is intended for `PostgresMemoryV2DerivedCoordinator.prepare()`: extraction
    occurs after all source reads and outside a database transaction. Normalized proposals
    are retained in the decision set so exact replay never needs to call the provider again.
    `project()` remains a compatibility adapter for pre-convergence deterministic tests.
    """

    def __init__(
        self,
        extractor: SemanticExtractor,
        *,
        derivation_version: str = "voicemem-derived-semantic@1",
        dispute_threshold: float = 0.6,
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.extractor = extractor
        self.derivation_version = derivation_version
        self.dispute_threshold = dispute_threshold
        self.provider_id = provider_id
        self.model_id = model_id

    def plan(
        self,
        space: MemorySpaceKey,
        observations: tuple[Observation, ...],
        existing: tuple[GraphAssertion, ...],
    ) -> DerivedPlanPayload:
        if any(item.space != space for item in observations):
            raise ValueError("semantic enrichment window crosses memory spaces")
        if any(item.space != space for item in existing):
            raise ValueError("semantic enrichment graph crosses memory spaces")

        working = list(existing)
        changed: dict[str, GraphAssertion] = {}
        normalized_proposals: list[dict[str, Any]] = []
        for observation in sorted(observations, key=lambda item: item.authority_sequence):
            if observation.event_type not in _AUTHORITATIVE_SEMANTIC_EVENTS:
                continue
            proposals = tuple(self.extractor(observation))
            for proposal in proposals:
                normalized_proposals.append(proposal.normalized(observation.observation_id))
                claim = TemporalClaim(
                    subject=proposal.subject,
                    predicate=proposal.predicate,
                    domain=proposal.domain,
                    effective_at=proposal.effective_at,
                    operation=proposal.operation,
                    value=proposal.value,
                    confidence=proposal.confidence,
                    single_valued=proposal.single_valued,
                )
                temporal_plan = apply_temporal_claim(
                    space,
                    observation,
                    tuple(working),
                    claim,
                    derivation_version=self.derivation_version,
                    dispute_threshold=self.dispute_threshold,
                )
                for assertion in temporal_plan.assertions:
                    _replace_assertion(working, assertion)
                    changed[assertion.assertion_id] = assertion
        return DerivedPlanPayload(
            assertions=tuple(sorted(changed.values(), key=lambda item: item.assertion_id)),
            normalized_proposals=tuple(normalized_proposals),
            deterministic_decisions={"temporal_policy": self.derivation_version},
            consolidator_version=self.derivation_version,
            provider_id=self.provider_id,
            model_id=self.model_id,
        )

    def project(
        self,
        space: MemorySpaceKey,
        observations: tuple[Observation, ...],
        existing: tuple[GraphAssertion, ...],
    ) -> ConsolidationPlan:
        payload = self.plan(space, observations, existing)
        return ConsolidationPlan(assertions=payload.assertions)
