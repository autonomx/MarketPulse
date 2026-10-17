"""Canonical versioned contracts for the unified RPG Narrative Engine."""
from __future__ import annotations

import hashlib
import json
from functools import cached_property
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Mapping, cast

from .authority import (
    AuthorityClass,
    BeatKind,
    BeatPurpose,
    DeliveryMode,
    EvidenceLifetime,
    NarrativeSignificance,
    PresentationProfile,
    VisibilityClass,
)

TURN_PRESENTATION_REQUEST_VERSION = "rpg_turn_presentation_request_v1"
EVIDENCE_RECORD_VERSION = "rpg_narrative_evidence_v1"
NARRATIVE_BEAT_VERSION = "rpg_narrative_beat_v1"
NARRATIVE_RESPONSE_VERSION = "rpg_narrative_response_v1"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(cast(Any, value)))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SceneChange:
    kind: str
    importance: str = "minor"
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnPresentationRequest:
    request_id: str
    turn_id: str
    campaign_id: str
    player_input: str
    authoritative_outcome: Mapping[str, Any] = field(default_factory=dict)
    scene_snapshot: Mapping[str, Any] = field(default_factory=dict)
    actor_ids: tuple[str, ...] = ()
    target_actor_id: str | None = None
    scene_changes: tuple[SceneChange, ...] = ()
    significance: NarrativeSignificance = NarrativeSignificance.ROUTINE
    presentation_profile: PresentationProfile = PresentationProfile.IMMERSIVE
    delivery_mode: DeliveryMode = DeliveryMode.BLOCKING
    schema_version: str = TURN_PRESENTATION_REQUEST_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    @property
    def request_hash(self) -> str:
        return stable_hash(self.as_dict())


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    content: str
    authority: AuthorityClass
    visibility: VisibilityClass
    known_by: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    source_revision: int = 0
    confidence: float = 1.0
    lifetime: EvidenceLifetime = EvidenceLifetime.CAMPAIGN
    claim_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = EVIDENCE_RECORD_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class NarrativeBeat:
    beat_id: str
    sequence: int
    kind: BeatKind
    purpose: BeatPurpose
    speaker_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    required_claim_refs: tuple[str, ...] = ()
    instructions: str = ""
    required: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = NARRATIVE_BEAT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class ClaimAssertion:
    """One normalized factual assertion made by a canonical block."""

    claim_id: str
    text: str
    authority: AuthorityClass
    evidence_refs: tuple[str, ...]
    scope: str = "player"
    subject_id: str | None = None
    predicate: str = ""
    value: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class NarrativeBlock:
    block_id: str
    beat_id: str
    sequence: int
    kind: BeatKind
    purpose: BeatPurpose
    text: str
    speaker_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    claims: tuple[ClaimAssertion, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    block_id: str | None = None
    severity: str = "error"


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    issues: tuple[ValidationIssue, ...] = ()
    repair_history: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class GenerationMetadata:
    source: str
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    attempt_count: int = 1
    evidence_count: int = 0
    beat_count: int = 0
    hermes_used: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryMetadata:
    mode: DeliveryMode
    status: str = "complete"
    delivered_block_ids: tuple[str, ...] = ()
    interruption_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalNarrativeResponse:
    response_id: str
    request_id: str
    turn_id: str
    campaign_id: str
    revision: int
    blocks: tuple[NarrativeBlock, ...]
    evidence_used: tuple[str, ...]
    validation: ValidationReport
    generation: GenerationMetadata
    delivery: DeliveryMetadata
    content_hash: str = ""
    schema_version: str = NARRATIVE_RESPONSE_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def semantic_content_payload(self) -> dict[str, Any]:
        """Return meaning-bearing response content used for stable identity."""

        return {
            "schema_version": self.schema_version,
            "response_id": self.response_id,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "campaign_id": self.campaign_id,
            "revision": self.revision,
            "blocks": [block.as_dict() for block in ordered_blocks(self.blocks)],
            "evidence_used": list(self.evidence_used),
        }

    def content_payload(self) -> dict[str, Any]:
        return self.semantic_content_payload()

    @cached_property
    def semantic_hash(self) -> str:
        """Computed once per response: the response and its blocks are frozen (WP-5.6)."""
        return stable_hash(self.semantic_content_payload())

    def as_dict(self) -> dict[str, Any]:
        payload = self.semantic_content_payload()
        payload["validation"] = self.validation.as_dict()
        payload["generation"] = _jsonable(self.generation)
        payload["delivery"] = _jsonable(self.delivery)
        payload["content_hash"] = self.semantic_hash
        payload["metadata"] = _jsonable(self.metadata)
        return payload

    def with_content_hash(self) -> "CanonicalNarrativeResponse":
        semantic_hash = self.semantic_hash
        if self.content_hash == semantic_hash:
            return self
        return CanonicalNarrativeResponse(
            response_id=self.response_id,
            request_id=self.request_id,
            turn_id=self.turn_id,
            campaign_id=self.campaign_id,
            revision=self.revision,
            blocks=ordered_blocks(self.blocks),
            evidence_used=self.evidence_used,
            validation=self.validation,
            generation=self.generation,
            delivery=self.delivery,
            content_hash=semantic_hash,
            schema_version=self.schema_version,
            metadata=self.metadata,
        )


def ordered_blocks(blocks: tuple[NarrativeBlock, ...] | list[NarrativeBlock]) -> tuple[NarrativeBlock, ...]:
    return tuple(sorted(blocks, key=lambda block: (block.sequence, block.block_id)))
