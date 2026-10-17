from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class ResponseMode(str, Enum):
    UTILITY = "utility"
    DIALOGUE = "dialogue"
    OBSERVATION = "observation"
    ACTION = "action"
    TRANSACTION = "transaction"
    TRAVEL = "travel"
    COMBAT = "combat"
    INVESTIGATION = "investigation"
    RECOVERY = "recovery"
    FAILURE = "failure"
    MAJOR_BEAT = "major_beat"


class SectionType(str, Enum):
    NARRATION = "narration"
    ACTION = "action"
    NPC_DIALOGUE = "npc_dialogue"
    RESULT = "result"
    CHOICE = "choice"
    CLARIFICATION = "clarification"
    STATE_CHANGE = "state_change"


class CandidateSource(str, Enum):
    PROVIDER = "provider"
    DETERMINISTIC = "deterministic"
    RECOVERY = "recovery"
    HERMES_ASSISTED = "hermes_assisted"
    LEGACY_RUNTIME = "legacy_runtime"
    LEGACY_WORLD_SCENE = "legacy_world_scene"


class AgencyEffect(str, Enum):
    NONE = "none"
    OFFER_ONLY = "offer_only"
    CLARIFICATION_ONLY = "clarification_only"
    RESOLVED_MECHANIC = "resolved_mechanic"
    PLAYER_CONFIRMED = "player_confirmed"


class Reversibility(str, Enum):
    FULLY_REVERSIBLE = "fully_reversible"
    SCENE_REVERSIBLE = "scene_reversible"
    PERSISTENT = "persistent"


RESPONSE_WORD_BUDGETS: dict[ResponseMode, tuple[int, int]] = {
    ResponseMode.UTILITY: (1, 80),
    ResponseMode.DIALOGUE: (35, 100),
    ResponseMode.OBSERVATION: (50, 130),
    ResponseMode.ACTION: (35, 100),
    ResponseMode.TRANSACTION: (35, 100),
    ResponseMode.TRAVEL: (50, 140),
    ResponseMode.COMBAT: (60, 160),
    ResponseMode.INVESTIGATION: (50, 150),
    ResponseMode.RECOVERY: (45, 140),
    ResponseMode.FAILURE: (30, 100),
    ResponseMode.MAJOR_BEAT: (100, 240),
}


@dataclass(frozen=True)
class ResponseRequest:
    turn_id: str
    player_input: str
    schema_version: str = "rpg_response_request_v1"
    authoritative_turn_result: Mapping[str, Any] = field(default_factory=dict)
    session_id: str = ""
    world_id: str = ""
    scene_id: str = ""
    player_id: str = ""
    party_ids: tuple[str, ...] = ()
    speaker_id: str = ""
    runtime_mode: str = "compatibility"
    delivery_capabilities: Mapping[str, Any] = field(default_factory=dict)
    provider_policy: Mapping[str, Any] = field(default_factory=dict)
    feature_flags: Mapping[str, bool] = field(default_factory=dict)
    legacy_payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticSection:
    section_id: str
    section_type: SectionType
    text: str
    speaker_id: str = ""
    claim_refs: tuple[str, ...] = ()
    soft_truth_refs: tuple[str, ...] = ()
    proposal_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalized_text(self) -> str:
        return " ".join(self.text.casefold().split())


@dataclass(frozen=True)
class SemanticResponsePlan:
    mode: ResponseMode
    sections: tuple[SemanticSection, ...]
    forward_strategy: str = "answer_directly"
    agency_effect: AgencyEffect = AgencyEffect.NONE
    reversibility: Reversibility = Reversibility.FULLY_REVERSIBLE
    proposal_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateDecision:
    gate: str
    passed: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponseCandidate:
    candidate_id: str
    plan: SemanticResponsePlan
    source: CandidateSource
    gate_decisions: tuple[GateDecision, ...] = ()
    current_turn_relevance: float = 0.0
    forward_motion: float = 0.0
    specificity: float = 0.0
    naturalness: float = 0.0
    repetition_issues: tuple[str, ...] = ()
    style_issues: tuple[str, ...] = ()
    latency_ms: float = 0.0
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    repair_history: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return all(decision.passed for decision in self.gate_decisions)


@dataclass(frozen=True)
class RenderedResponse:
    text: str
    mode: ResponseMode
    approved_section_ids: tuple[str, ...]
    resolved_claim_refs: tuple[str, ...] = ()
    truth_classes: tuple[str, ...] = ()
    lifetimes: tuple[str, ...] = ()
    word_budget: tuple[int, int] = (0, 0)
    repair_history: tuple[str, ...] = ()
    delivery_units: tuple[str, ...] = ()
    quality_report: Mapping[str, Any] = field(default_factory=dict)
    authoritative_deltas: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def coerce_response_mode(value: Any, default: ResponseMode = ResponseMode.ACTION) -> ResponseMode:
    if isinstance(value, ResponseMode):
        return value
    normalized = str(value or "").strip().casefold()
    aliases = {
        "service": ResponseMode.TRANSACTION,
        "shop": ResponseMode.TRANSACTION,
        "conversation": ResponseMode.DIALOGUE,
        "social": ResponseMode.DIALOGUE,
        "look": ResponseMode.OBSERVATION,
        "inspect": ResponseMode.OBSERVATION,
        "fight": ResponseMode.COMBAT,
        "move": ResponseMode.TRAVEL,
        "unknown": ResponseMode.RECOVERY,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return ResponseMode(normalized)
    except ValueError:
        return default


def collect_claim_refs(sections: Sequence[SemanticSection]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for section in sections:
        for claim_ref in section.claim_refs:
            if claim_ref and claim_ref not in seen:
                seen.add(claim_ref)
                ordered.append(claim_ref)
    return tuple(ordered)
