from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .consolidation import ConsolidationPlan
from .contracts import (
    GraphAssertion,
    GraphEntityRef,
    GraphValue,
    MemoryDomain,
    MemorySpaceKey,
    Observation,
)

TemporalOperation = Literal["assert", "retract"]


@dataclass(frozen=True, slots=True)
class TemporalClaim:
    subject: GraphEntityRef
    predicate: str
    domain: MemoryDomain
    effective_at: datetime
    operation: TemporalOperation = "assert"
    value: GraphValue | None = None
    confidence: float = 1.0
    single_valued: bool = True

    def __post_init__(self) -> None:
        if not self.predicate:
            raise ValueError("temporal claim predicate is required")
        if self.operation == "assert" and self.value is None:
            raise ValueError("assert temporal claim requires a value")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("temporal claim confidence must be between 0 and 1")


def _same_entity(left: GraphEntityRef, right: GraphEntityRef) -> bool:
    return left.entity_id == right.entity_id and left.entity_type == right.entity_type


def _same_value(left: GraphValue, right: GraphValue | None) -> bool:
    return right is not None and left.model_dump(mode="json") == right.model_dump(mode="json")


def _claim_value(claim: TemporalClaim) -> GraphValue:
    if claim.value is None:
        raise ValueError(f"temporal claim {claim.predicate!r} has no value to assert")
    return claim.value


def _assertion_id(space: MemorySpaceKey, observation: Observation, claim: TemporalClaim) -> str:
    material = {
        "space": space.model_dump(mode="json"),
        "observation_id": observation.observation_id,
        "subject": claim.subject.model_dump(mode="json"),
        "predicate": claim.predicate,
        "value": claim.value.model_dump(mode="json") if claim.value is not None else None,
        "effective_at": claim.effective_at.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"assertion:{digest}"


def apply_temporal_claim(
    space: MemorySpaceKey,
    observation: Observation,
    existing: tuple[GraphAssertion, ...],
    claim: TemporalClaim,
    *,
    derivation_version: str = "memory-v2-temporal@1",
    dispute_threshold: float = 0.6,
) -> ConsolidationPlan:
    """Return deterministic graph updates for one temporal/current-belief claim.

    Historical assertions are never deleted. Newer strong single-valued claims supersede
    current state, weak conflicting claims are disputed, and out-of-order claims are
    retained as historical state without replacing a newer current belief.
    """
    if observation.space != space:
        raise ValueError("temporal claim observation belongs to another memory space")

    related = tuple(
        item
        for item in existing
        if item.status == "active"
        and _same_entity(item.subject, claim.subject)
        and item.predicate == claim.predicate
    )

    if claim.operation == "retract":
        updates = []
        for item in related:
            if claim.value is not None and not _same_value(item.object, claim.value):
                continue
            updates.append(
                item.model_copy(
                    update={
                        "status": "retracted",
                        "valid_until": claim.effective_at,
                        "evidence_observation_ids": tuple(
                            sorted(set(item.evidence_observation_ids) | {observation.observation_id})
                        ),
                        "revision": item.revision + 1,
                        "derivation_version": derivation_version,
                    }
                )
            )
        return ConsolidationPlan(assertions=tuple(updates))

    matching = next((item for item in related if _same_value(item.object, claim.value)), None)
    if matching is not None:
        valid_from = matching.valid_from
        if valid_from is None or claim.effective_at < valid_from:
            valid_from = claim.effective_at
        reinforced = matching.model_copy(
            update={
                "confidence": max(matching.confidence, claim.confidence),
                "valid_from": valid_from,
                "evidence_observation_ids": tuple(
                    sorted(set(matching.evidence_observation_ids) | {observation.observation_id})
                ),
                "revision": matching.revision + 1,
                "derivation_version": derivation_version,
            }
        )
        return ConsolidationPlan(assertions=(reinforced,))

    newer_current = tuple(
        item
        for item in related
        if item.valid_from is not None and item.valid_from > claim.effective_at
    )
    if claim.single_valued and newer_current:
        next_boundary = min(item.valid_from for item in newer_current if item.valid_from is not None)
        historical = GraphAssertion(
            assertion_id=_assertion_id(space, observation, claim),
            space=space,
            visibility_scopes=(observation.visibility_scope,),
            subject=claim.subject,
            predicate=claim.predicate,
            object=_claim_value(claim),
            domain=claim.domain,
            confidence=claim.confidence,
            valid_from=claim.effective_at,
            valid_until=next_boundary,
            evidence_observation_ids=(observation.observation_id,),
            derivation_version=derivation_version,
            status="superseded",
        )
        return ConsolidationPlan(assertions=(historical,))

    if claim.single_valued and related and claim.confidence < dispute_threshold:
        disputed = GraphAssertion(
            assertion_id=_assertion_id(space, observation, claim),
            space=space,
            visibility_scopes=(observation.visibility_scope,),
            subject=claim.subject,
            predicate=claim.predicate,
            object=_claim_value(claim),
            domain=claim.domain,
            confidence=claim.confidence,
            valid_from=claim.effective_at,
            evidence_observation_ids=(observation.observation_id,),
            derivation_version=derivation_version,
            contradicted_by=tuple(sorted(item.assertion_id for item in related)),
            status="disputed",
        )
        return ConsolidationPlan(assertions=(disputed,))

    superseded: tuple[GraphAssertion, ...] = ()
    supersedes: tuple[str, ...] = ()
    if claim.single_valued and related:
        supersedes = tuple(sorted(item.assertion_id for item in related))
        superseded = tuple(
            item.model_copy(
                update={
                    "status": "superseded",
                    "valid_until": claim.effective_at,
                    "revision": item.revision + 1,
                    "derivation_version": derivation_version,
                }
            )
            for item in related
        )

    current = GraphAssertion(
        assertion_id=_assertion_id(space, observation, claim),
        space=space,
        visibility_scopes=(observation.visibility_scope,),
        subject=claim.subject,
        predicate=claim.predicate,
        object=_claim_value(claim),
        domain=claim.domain,
        confidence=claim.confidence,
        valid_from=claim.effective_at,
        evidence_observation_ids=(observation.observation_id,),
        derivation_version=derivation_version,
        supersedes=supersedes,
        status="active",
    )
    return ConsolidationPlan(assertions=(*superseded, current))
