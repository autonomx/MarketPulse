"""Typed evidence contracts for the continuous Companion Activity Runtime.

Perception and integrations produce propositions, never authoritative activity truth.
Trust, confidence, authority and sensitivity remain separate dimensions. Only explicit
trust-bearing provenance links participate in monotonic trust/sensitivity inheritance.
"""
from __future__ import annotations

from importlib import import_module

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation.memory_policy import Sensitivity, TrustLevel, strongest_sensitivity, weakest_trust

EvidenceRelation = Literal[
    "supports",
    "derived_from",
    "corroborates",
    "contextualizes",
    "contradicts",
    "corrects",
    "supersedes",
]
EvidenceSourceKind = Literal[
    "user",
    "system",
    "runtime",
    "telemetry",
    "external",
    "assistant",
    "import",
]

TRUST_BEARING_RELATIONS: frozenset[str] = frozenset({"supports", "derived_from"})


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceLink(FrozenContract):
    """Directed provenance edge from one proposition to another evidence source."""

    ref: str = Field(min_length=1, max_length=240)
    relation: EvidenceRelation

    @property
    def trust_bearing(self) -> bool:
        return self.relation in TRUST_BEARING_RELATIONS


class EvidenceProposition(FrozenContract):
    """One uncertain, source-addressable proposition about companion activity."""

    proposition_id: str = Field(min_length=1, max_length=240)
    subject: str = Field(min_length=1, max_length=240)
    predicate: str = Field(min_length=1, max_length=160)
    value: Any

    source_kind: EvidenceSourceKind
    trust_level: TrustLevel
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity = "normal"

    links: tuple[EvidenceLink, ...] = ()
    observed_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    generation: str | None = Field(default=None, max_length=160)
    schema_version: str = Field(
        default="companion-evidence@1",
        min_length=1,
        max_length=80,
    )

    @model_validator(mode="after")
    def validate_interval(self) -> EvidenceProposition:
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self

    @property
    def trust_bearing_refs(self) -> tuple[str, ...]:
        return tuple(link.ref for link in self.links if link.trust_bearing)


class EvidenceInheritedPolicy(FrozenContract):
    """Monotonic trust/sensitivity inherited through trust-bearing edges only."""

    trust_level: TrustLevel
    sensitivity: Sensitivity
    trust_bearing_refs: tuple[str, ...] = ()


def _resolve_trust_bearing_evidence(
    links: tuple[EvidenceLink, ...],
    propositions_by_id: dict[str, EvidenceProposition],
    *,
    require_any: bool,
) -> tuple[EvidenceProposition, ...]:
    trust_links = tuple(link for link in links if link.trust_bearing)
    if require_any and not trust_links:
        raise ValueError("semantic derivation requires trust-bearing evidence")
    missing = tuple(link.ref for link in trust_links if link.ref not in propositions_by_id)
    if missing:
        raise ValueError(
            "trust-bearing evidence reference is missing: " + ", ".join(sorted(set(missing)))
        )
    return tuple(propositions_by_id[link.ref] for link in trust_links)


def inherit_evidence_policy(
    proposition: EvidenceProposition,
    propositions_by_id: dict[str, EvidenceProposition],
    *,
    semantic_derivation: bool = True,
) -> EvidenceInheritedPolicy:
    """Derive policy from the proposition plus only trust-bearing linked evidence.

    Corroboration/context/contradiction/correction/supersession remain auditable graph
    relationships but cannot silently lower or raise the proposition's provenance class.
    Missing trust-bearing evidence fails closed instead of being silently ignored.
    Semantic derivations are additionally capped at ``assistant_inference`` trust, matching
    Memory v2's derived-policy semantics.
    """

    linked = _resolve_trust_bearing_evidence(
        proposition.links,
        propositions_by_id,
        require_any=False,
    )
    trust_candidates = [proposition.trust_level]
    trust_candidates.extend(item.trust_level for item in linked)
    sensitivity_candidates = [proposition.sensitivity]
    sensitivity_candidates.extend(item.sensitivity for item in linked)
    return EvidenceInheritedPolicy(
        trust_level=weakest_trust(
            trust_candidates,
            cap_derived_at_assistant_inference=semantic_derivation,
        ),
        sensitivity=strongest_sensitivity(sensitivity_candidates),
        trust_bearing_refs=tuple(item.proposition_id for item in linked),
    )


def derive_proposition(
    *,
    proposition_id: str,
    subject: str,
    predicate: str,
    value: Any,
    confidence: float,
    source_kind: EvidenceSourceKind,
    observed_at: datetime,
    links: tuple[EvidenceLink, ...],
    propositions_by_id: dict[str, EvidenceProposition],
    generation: str | None = None,
    schema_version: str = "companion-evidence@1",
) -> EvidenceProposition:
    """Create a semantic derivation without permitting trust/sensitivity promotion."""

    linked = _resolve_trust_bearing_evidence(
        links,
        propositions_by_id,
        require_any=True,
    )
    inherited_trust = weakest_trust(
        (item.trust_level for item in linked),
        cap_derived_at_assistant_inference=True,
    )
    inherited_sensitivity = strongest_sensitivity(item.sensitivity for item in linked)
    return EvidenceProposition(
        proposition_id=proposition_id,
        subject=subject,
        predicate=predicate,
        value=value,
        source_kind=source_kind,
        trust_level=inherited_trust,
        confidence=confidence,
        sensitivity=inherited_sensitivity,
        links=links,
        observed_at=observed_at,
        valid_from=observed_at,
        generation=generation,
        schema_version=schema_version,
    )


__all__ = [
    "TRUST_BEARING_RELATIONS",
    "EvidenceInheritedPolicy",
    "EvidenceLink",
    "EvidenceProposition",
    "EvidenceRelation",
    "EvidenceSourceKind",
    "derive_proposition",
    "inherit_evidence_policy",
]


# Services apps import through this contract, loaded on first use (ADR-0016).
_LAZY_EXPORTS = {
    "CompanionInitiativeAuthorityStore": "initiative",
    "InitiativeAcquireRequest": "initiative",
    "default_companion_initiative_authority": "initiative",
    "CognitionResult": "cognition",
    "CompanionCognition": "cognition",
    "ActivityCheckpointReason": "persistence",
    "CompanionActivityCheckpointStore": "persistence",
    "CompanionCheckpointPolicy": "persistence",
    "PostgresCompanionActivityCheckpointStore": "persistence",
    "build_activity_checkpoint": "persistence",
    "ActivityRuntimeResult": "runtime",
    "CompanionActivityRuntime": "runtime",
    "CompanionActivityState": "state",
    "empty_activity_state": "state",
    "user_activity_propositions": "user_evidence",
}


def __getattr__(name: str) -> Any:
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"app.platform.companion_activity.{module}"), name)
