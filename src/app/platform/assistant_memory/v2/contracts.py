"""Contract-first types for the VoiceMem-derived Omnix Memory v2 architecture.

This module intentionally contains no persistence, retrieval, consolidation, or VoiceMem
implementation. These contracts freeze authority, ownership, evidence, sharing, replay,
and cutover semantics before implementation begins.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.conversation.memory_policy import Sensitivity, TrustLevel

SYSTEM_MEMORY_OWNER_ID = "system-assistant"

MemoryOwnerType = Literal["system", "character"]
VisibilityScopeKind = Literal["global", "workspace", "project", "session"]
MemoryDomain = Literal[
    "fact",
    "preference",
    "temporal",
    "episode",
    "goal",
    "open_loop",
    "relationship",
    "trait",
    "affect",
    "routine",
    "instruction",
]
ObservationEventType = Literal[
    "user_said",
    "assistant_generated",
    "assistant_delivered",
    "assistant_experienced",
    "external_observed",
    "system_event",
    "imported_legacy_memory",
    "acoustic_observation",
    # One revision of a curated memory record (saved, approved, edited, moved,
    # pinned or archived) written while Memory v2 is authoritative.
    "curated_memory",
]
ObservationSourceType = Literal[
    "user",
    "assistant",
    "system",
    "external",
    "import",
    "acoustic",
    "migration",
]
ObservationGovernanceState = Literal["active", "revoked", "purged"]
AssertionStatus = Literal["active", "superseded", "retracted", "disputed"]
AssertionType = Literal["derived", "seeded"]
RelationshipStatus = Literal["active", "superseded", "archived"]
AffectSource = Literal["explicit", "semantic", "acoustic", "fused"]
RetrievalAuthority = Literal["partial", "final", "system"]
RetrievalItemType = Literal["assertion", "episode", "relationship"]
DerivedItemType = Literal["assertion", "episode", "relationship", "affect"]
MemoryAuthority = Literal["v1", "v2"]


class FrozenContract(BaseModel):
    """Immutable value object used at Memory v2 authority boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySpaceKey(FrozenContract):
    """Physical brain/ownership boundary; visibility scope is intentionally separate."""

    principal_id: str = Field(min_length=1, max_length=160)
    owner_type: MemoryOwnerType
    owner_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_owner_identity(self) -> MemorySpaceKey:
        if self.owner_type == "system" and self.owner_id != SYSTEM_MEMORY_OWNER_ID:
            raise ValueError("system memory space must use the System Assistant owner")
        if self.owner_type == "character" and self.owner_id == SYSTEM_MEMORY_OWNER_ID:
            raise ValueError("character memory space requires a character owner")
        return self


class VisibilityScope(FrozenContract):
    """Interaction visibility constraint inside one MemorySpaceKey."""

    kind: VisibilityScopeKind
    scope_id: str = Field(min_length=1, max_length=200)


class ObservationProvenance(FrozenContract):
    """Where an observation came from; never inferred from payload text."""

    source_type: ObservationSourceType
    source_id: str = Field(min_length=1, max_length=240)
    trust_level: TrustLevel
    session_id: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=200)
    message_id: str | None = Field(default=None, max_length=200)
    provider_id: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, max_length=200)


class Observation(FrozenContract):
    """Append-only evidence event from which all derived memory can be rebuilt."""

    observation_id: str = Field(min_length=1, max_length=200)
    authority_sequence: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=240)
    space: MemorySpaceKey
    visibility_scope: VisibilityScope
    event_type: ObservationEventType
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: ObservationProvenance
    sensitivity: Sensitivity = "normal"
    correlation_id: str | None = Field(default=None, max_length=240)
    schema_version: str = Field(default="memory-v2-observation@1", min_length=1, max_length=80)
    content_digest: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def validate_event_provenance(self) -> Observation:
        expected_sources: dict[str, set[str]] = {
            "user_said": {"user"},
            "assistant_generated": {"assistant"},
            "assistant_delivered": {"assistant", "system"},
            "assistant_experienced": {"assistant", "system"},
            "external_observed": {"external"},
            "system_event": {"system"},
            "imported_legacy_memory": {"import", "migration"},
            "acoustic_observation": {"acoustic"},
            "curated_memory": {"user", "assistant", "system", "external", "import"},
        }
        if self.provenance.source_type not in expected_sources[self.event_type]:
            raise ValueError("observation event_type and provenance source_type disagree")
        if self.event_type.startswith("assistant_") and not self.correlation_id:
            raise ValueError("assistant output observations require a correlation_id")
        return self


class ObservationDisposition(FrozenContract):
    """Governance overlay for immutable observations."""

    observation_id: str = Field(min_length=1, max_length=200)
    state: ObservationGovernanceState = "active"
    authority_sequence: int = Field(ge=1)
    changed_at: datetime
    reason: str | None = Field(default=None, max_length=500)
    actor_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(default=1, ge=1)


class DerivedPolicyEnvelope(FrozenContract):
    """Deterministic monotonic policy inherited from authoritative evidence."""

    sensitivity: Sensitivity = "normal"
    effective_visibility: tuple[VisibilityScope, ...] = Field(min_length=1)
    trust_class: TrustLevel = "assistant_inference"
    source_observation_ids: tuple[str, ...] = ()
    source_assertion_ids: tuple[str, ...] = ()
    source_governance_revision: int = Field(ge=0)
    policy_version: str = Field(default="memory-v2-derived-policy@1", min_length=1, max_length=160)
    policy_digest: str = Field(min_length=8, max_length=160)


class GraphEntityRef(FrozenContract):
    entity_id: str = Field(min_length=1, max_length=240)
    entity_type: str = Field(min_length=1, max_length=100)


class GraphValue(FrozenContract):
    """Structured graph object; exactly one entity or literal value is present."""

    kind: Literal["entity", "literal"]
    entity: GraphEntityRef | None = None
    literal: Any | None = None

    @model_validator(mode="after")
    def validate_value(self) -> GraphValue:
        if self.kind == "entity":
            if self.entity is None or self.literal is not None:
                raise ValueError("entity graph value requires entity and no literal")
        elif self.literal is None or self.entity is not None:
            raise ValueError("literal graph value requires literal and no entity")
        return self


class GraphAssertion(FrozenContract):
    """Evidence-addressable current or historical belief in the derived memory graph."""

    assertion_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    visibility_scopes: tuple[VisibilityScope, ...] = Field(min_length=1)
    subject: GraphEntityRef
    predicate: str = Field(min_length=1, max_length=160)
    object: GraphValue
    domain: MemoryDomain
    assertion_type: AssertionType = "derived"
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    evidence_observation_ids: tuple[str, ...] = ()
    evidence_assertion_ids: tuple[str, ...] = ()
    derivation_version: str = Field(min_length=1, max_length=160)
    supersedes: tuple[str, ...] = ()
    contradicted_by: tuple[str, ...] = ()
    status: AssertionStatus = "active"
    revision: int = Field(default=1, ge=1)
    policy: DerivedPolicyEnvelope | None = None

    @model_validator(mode="after")
    def validate_assertion(self) -> GraphAssertion:
        if not self.evidence_observation_ids and not self.evidence_assertion_ids:
            raise ValueError("every graph assertion must be evidence-addressable")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self


class Episode(FrozenContract):
    """Derived event/experience grouping backed by authoritative observations."""

    episode_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    visibility_scopes: tuple[VisibilityScope, ...] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=4000)
    started_at: datetime
    ended_at: datetime | None = None
    participant_entity_ids: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = Field(min_length=1)
    generated_assertion_ids: tuple[str, ...] = ()
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    derivation_version: str = Field(min_length=1, max_length=160)
    revision: int = Field(default=1, ge=1)
    policy: DerivedPolicyEnvelope | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Episode:
        if self.ended_at and self.ended_at < self.started_at:
            raise ValueError("episode ended_at cannot precede started_at")
        return self


class RelationshipMetric(FrozenContract):
    """Internal latent state; prompt-facing output should use interpretation, not raw precision."""

    name: str = Field(min_length=1, max_length=100)
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_observation_ids: tuple[str, ...] = Field(min_length=1)


class RelationshipState(FrozenContract):
    relationship_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    subject: GraphEntityRef
    counterpart: GraphEntityRef
    metrics: tuple[RelationshipMetric, ...] = ()
    prompt_interpretation: str = Field(min_length=1, max_length=2000)
    evidence_observation_ids: tuple[str, ...] = Field(min_length=1)
    derivation_version: str = Field(min_length=1, max_length=160)
    status: RelationshipStatus = "active"
    revision: int = Field(default=1, ge=1)
    policy: DerivedPolicyEnvelope | None = None


class AffectObservation(FrozenContract):
    """Evidence-bearing affect signal; never silently promoted to timeless current state."""

    affect_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    source_observation_id: str = Field(min_length=1, max_length=200)
    source: AffectSource
    observed_at: datetime
    valence: float | None = Field(default=None, ge=-1.0, le=1.0)
    arousal: float | None = Field(default=None, ge=-1.0, le=1.0)
    emotion_distribution: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str = Field(min_length=1, max_length=160)
    policy: DerivedPolicyEnvelope | None = None

    @field_validator("emotion_distribution")
    @classmethod
    def validate_distribution(cls, value: dict[str, float]) -> dict[str, float]:
        if any(probability < 0.0 or probability > 1.0 for probability in value.values()):
            raise ValueError("emotion probabilities must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_signal(self) -> AffectObservation:
        if self.valence is None and self.arousal is None and not self.emotion_distribution:
            raise ValueError("affect observation must contain at least one affect signal")
        return self


class MemoryGrant(FrozenContract):
    """Authorization policy for federated cross-space reads; never copied into memory state."""

    grant_id: str = Field(min_length=1, max_length=200)
    source_space: MemorySpaceKey
    target_space: MemorySpaceKey
    access: Literal["read"] = "read"
    allowed_domains: tuple[MemoryDomain, ...] = Field(min_length=1)
    max_sensitivity: Sensitivity = "normal"
    scope_constraints: tuple[VisibilityScope, ...] = ()
    created_by: str = Field(min_length=1, max_length=200)
    created_at: datetime
    revoked_at: datetime | None = None
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_spaces(self) -> MemoryGrant:
        if self.source_space == self.target_space:
            raise ValueError("memory grants must cross distinct memory spaces")
        if self.source_space.principal_id != self.target_space.principal_id:
            raise ValueError("memory grants cannot cross principals")
        return self


class RetrievalQuery(FrozenContract):
    """Read-only retrieval request. Partial STT has no mutation capability by construction."""

    query_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    visible_scopes: tuple[VisibilityScope, ...] = Field(min_length=1)
    text: str = Field(min_length=1, max_length=16000)
    authority: RetrievalAuthority
    as_of: datetime
    top_k: int = Field(default=5, ge=1, le=100)
    token_budget: int = Field(default=600, ge=0, le=100_000)
    deadline_ms: float = Field(default=50.0, ge=0.0, le=10_000.0)
    domains: tuple[MemoryDomain, ...] = ()
    grant_ids: tuple[str, ...] = ()


class RetrievalScore(FrozenContract):
    semantic: float = 0.0
    graph: float = 0.0
    temporal: float = 0.0
    relationship: float = 0.0
    episodic: float = 0.0
    importance: float = 0.0
    confidence: float = 0.0
    recency: float = 0.0
    pinning: float = 0.0
    stale_penalty: float = 0.0
    composite: float = 0.0


class RetrievalCandidate(FrozenContract):
    ref_id: str = Field(min_length=1, max_length=200)
    item_type: RetrievalItemType
    domain: MemoryDomain
    content: str = Field(min_length=1, max_length=8000)
    scores: RetrievalScore
    reasons: tuple[str, ...] = ()
    evidence_observation_ids: tuple[str, ...] = ()
    prompt_eligible: bool = True
    policy: DerivedPolicyEnvelope | None = None
    source_space: MemorySpaceKey | None = None


class RetrievalSourceRevision(FrozenContract):
    source_space: MemorySpaceKey
    observation_watermark: int = Field(ge=0)
    governance_revision: int = Field(ge=0)
    derived_revision: int = Field(ge=0)
    index_revision: int = Field(ge=0)
    grant_revision: int = Field(default=0, ge=0)


class RetrievalResult(FrozenContract):
    query_id: str = Field(min_length=1, max_length=200)
    candidates: tuple[RetrievalCandidate, ...] = ()
    dynamic_context: tuple[str, ...] = ()
    token_estimate: int = Field(default=0, ge=0)
    observation_watermark: int = Field(ge=0)
    graph_revision: int = Field(ge=0)
    index_graph_revision: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0.0)
    deadline_ms: float = Field(ge=0.0)
    source_revisions: tuple[RetrievalSourceRevision, ...] = ()
    federation_revision_digest: str | None = Field(default=None, max_length=160)


class ConsolidationDecisionSet(FrozenContract):
    """Content-addressed semantic decisions used for exact replay."""

    decision_set_id: str = Field(min_length=1, max_length=240)
    space: MemorySpaceKey
    input_observation_from: int = Field(ge=1)
    input_observation_through: int = Field(ge=1)
    previous_derived_revision: int = Field(ge=0)
    source_governance_revision: int = Field(ge=0)
    normalized_proposals: tuple[dict[str, Any], ...] = ()
    deterministic_decisions: dict[str, Any] = Field(default_factory=dict)
    consolidator_version: str = Field(min_length=1, max_length=160)
    schema_version: str = Field(min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, max_length=200)
    decision_digest: str = Field(min_length=8, max_length=160)
    invalidated_at: datetime | None = None
    redacted_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> ConsolidationDecisionSet:
        if self.input_observation_through < self.input_observation_from:
            raise ValueError("decision-set observation range is reversed")
        return self


class DerivedMemoryRevision(FrozenContract):
    """One atomic revision spanning every canonical derived-memory domain."""

    revision_id: str = Field(min_length=1, max_length=240)
    space: MemorySpaceKey
    derived_revision: int = Field(ge=1)
    source_observation_watermark: int = Field(ge=0)
    source_governance_revision: int = Field(ge=0)
    decision_set_id: str = Field(min_length=1, max_length=240)
    policy_digest: str = Field(min_length=8, max_length=160)
    created_at: datetime


class ConsolidationReceipt(FrozenContract):
    """Replay/audit receipt for one consolidation window."""

    receipt_id: str = Field(min_length=1, max_length=200)
    space: MemorySpaceKey
    input_observation_from: int = Field(ge=1)
    input_observation_through: int = Field(ge=1)
    consolidator_version: str = Field(min_length=1, max_length=160)
    schema_version: str = Field(min_length=1, max_length=160)
    provider_id: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, max_length=200)
    created_assertion_ids: tuple[str, ...] = ()
    reinforced_assertion_ids: tuple[str, ...] = ()
    superseded_assertion_ids: tuple[str, ...] = ()
    retracted_assertion_ids: tuple[str, ...] = ()
    conflicted_assertion_ids: tuple[str, ...] = ()
    created_episode_ids: tuple[str, ...] = ()
    relationship_update_ids: tuple[str, ...] = ()
    affect_update_ids: tuple[str, ...] = ()
    resulting_graph_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=240)
    started_at: datetime
    completed_at: datetime
    decision_set_id: str | None = Field(default=None, max_length=240)
    resulting_derived_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_receipt(self) -> ConsolidationReceipt:
        if self.input_observation_through < self.input_observation_from:
            raise ValueError("consolidation watermark range is reversed")
        if self.completed_at < self.started_at:
            raise ValueError("consolidation completed_at cannot precede started_at")
        return self


class MemoryWatermarks(FrozenContract):
    authoritative_event: int = Field(ge=0)
    observation: int = Field(ge=0)
    consolidation: int = Field(ge=0)
    graph_revision: int = Field(ge=0)
    index_graph_revision: int = Field(ge=0)
    governance_revision: int = Field(default=0, ge=0)
    derived_revision: int = Field(default=0, ge=0)
    derived_observation_watermark: int | None = Field(default=None, ge=0)
    index_derived_revision: int | None = Field(default=None, ge=0)


class CutoverReadiness(FrozenContract):
    watermarks: MemoryWatermarks
    graph_validation_passed: bool = False
    shadow_quality_passed: bool = False
    indexes_caught_up: bool = False
    ready: bool = False

    @model_validator(mode="after")
    def validate_ready_state(self) -> CutoverReadiness:
        if not self.ready:
            return self
        marks = self.watermarks
        if marks.observation != marks.authoritative_event:
            raise ValueError("observation log is not caught up to authority watermark")
        if marks.consolidation != marks.observation:
            raise ValueError("consolidator is not caught up to observation watermark")
        if marks.index_graph_revision != marks.graph_revision:
            raise ValueError("retrieval indexes are not caught up to graph revision")
        if (
            marks.derived_observation_watermark is not None
            and marks.derived_observation_watermark != marks.observation
        ):
            raise ValueError("derived memory is not caught up to observation watermark")
        if (
            marks.index_derived_revision is not None
            and marks.index_derived_revision != marks.derived_revision
        ):
            raise ValueError("search projection is not caught up to derived revision")
        if not self.graph_validation_passed:
            raise ValueError("graph validation must pass before cutover")
        if not self.shadow_quality_passed:
            raise ValueError("shadow retrieval quality must pass before cutover")
        if not self.indexes_caught_up:
            raise ValueError("indexes_caught_up must be true before cutover")
        return self


class MemoryAuthorityEpoch(FrozenContract):
    """Transactional memory authority selection; not a casual runtime feature flag."""

    epoch: int = Field(ge=1)
    authority: MemoryAuthority
    activated_at: datetime
    previous_epoch: int | None = Field(default=None, ge=1)
    readiness: CutoverReadiness | None = None
    activated_by: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_v2_cutover(self) -> MemoryAuthorityEpoch:
        if self.authority == "v2" and (self.readiness is None or not self.readiness.ready):
            raise ValueError("v2 authority requires a successful cutover readiness receipt")
        return self
