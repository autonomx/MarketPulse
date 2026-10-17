from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FactRecord:
    fact_id: str
    category: str
    subject: str
    predicate: str
    value: Any
    confidence: float = 1.0
    source_event_id: Optional[str] = None
    tick_first_seen: Optional[int] = None
    tick_last_updated: Optional[int] = None
    status: str = "confirmed"
    authority: str = "runtime"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FactRecord":
        return cls(**data)


@dataclass
class ThreadRecord:
    thread_id: str
    title: str
    status: str = "unresolved"
    priority: str = "normal"
    source_event_id: Optional[str] = None
    opened_tick: Optional[int] = None
    updated_tick: Optional[int] = None
    resolved_tick: Optional[int] = None
    anchor_entity_ids: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ThreadRecord":
        return cls(**data)


@dataclass
class CommitmentRecord:
    commitment_id: str
    actor_id: str
    target_id: Optional[str]
    kind: str
    text: str
    status: str = "active"
    source_event_id: Optional[str] = None
    created_tick: Optional[int] = None
    updated_tick: Optional[int] = None
    broken_tick: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CommitmentRecord":
        return cls(**data)


@dataclass
class SceneAnchor:
    anchor_id: str
    tick: Optional[int]
    location: Optional[str]
    present_actors: List[str] = field(default_factory=list)
    active_tensions: List[str] = field(default_factory=list)
    unresolved_thread_ids: List[str] = field(default_factory=list)
    summary: str = ""
    scene_fact_ids: List[str] = field(default_factory=list)
    source_event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SceneAnchor":
        return cls(**data)


@dataclass
class ConsequenceRecord:
    consequence_id: str
    event_id: Optional[str]
    tick: Optional[int]
    summary: str
    entity_ids: List[str] = field(default_factory=list)
    consequence_type: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConsequenceRecord":
        return cls(**data)


@dataclass
class ContradictionRecord:
    contradiction_id: str
    contradiction_type: str
    severity: str
    message: str
    event_id: Optional[str] = None
    tick: Optional[int] = None
    entity_ids: List[str] = field(default_factory=list)
    related_fact_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ContradictionRecord":
        return cls(**data)


@dataclass
class EntityCoherenceView:
    entity_id: str
    facts: List[Dict[str, Any]] = field(default_factory=list)
    commitments: List[Dict[str, Any]] = field(default_factory=list)
    recent_consequences: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EntityCoherenceView":
        return cls(**data)


@dataclass
class CoherenceMutation:
    action: str
    target: str
    data: Dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CoherenceMutation":
        return cls(**data)


@dataclass
class CoherenceUpdateResult:
    events_applied: int = 0
    mutations: List[CoherenceMutation] = field(default_factory=list)
    contradictions: List[ContradictionRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "events_applied": self.events_applied,
            "mutations": [m.to_dict() for m in self.mutations],
            "contradictions": [c.to_dict() for c in self.contradictions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoherenceUpdateResult":
        return cls(
            events_applied=data.get("events_applied", 0),
            mutations=[CoherenceMutation.from_dict(m) for m in data.get("mutations", [])],
            contradictions=[
                ContradictionRecord.from_dict(c)
                for c in data.get("contradictions", [])
            ],
        )


@dataclass
class CoherenceState:
    stable_world_facts: Dict[str, FactRecord] = field(default_factory=dict)
    scene_facts: Dict[str, FactRecord] = field(default_factory=dict)
    temporary_assumptions: Dict[str, FactRecord] = field(default_factory=dict)
    player_commitments: Dict[str, CommitmentRecord] = field(default_factory=dict)
    npc_commitments: Dict[str, CommitmentRecord] = field(default_factory=dict)
    recent_changes: List[ConsequenceRecord] = field(default_factory=list)
    unresolved_threads: Dict[str, ThreadRecord] = field(default_factory=dict)
    continuity_anchors: List[SceneAnchor] = field(default_factory=list)
    contradictions: List[ContradictionRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stable_world_facts": {
                k: v.to_dict() for k, v in self.stable_world_facts.items()
            },
            "scene_facts": {k: v.to_dict() for k, v in self.scene_facts.items()},
            "temporary_assumptions": {
                k: v.to_dict() for k, v in self.temporary_assumptions.items()
            },
            "player_commitments": {
                k: v.to_dict() for k, v in self.player_commitments.items()
            },
            "npc_commitments": {
                k: v.to_dict() for k, v in self.npc_commitments.items()
            },
            "recent_changes": [c.to_dict() for c in self.recent_changes],
            "unresolved_threads": {
                k: v.to_dict() for k, v in self.unresolved_threads.items()
            },
            "continuity_anchors": [a.to_dict() for a in self.continuity_anchors],
            "contradictions": [c.to_dict() for c in self.contradictions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoherenceState":
        state = cls()
        state.stable_world_facts = {
            k: FactRecord.from_dict(v)
            for k, v in data.get("stable_world_facts", {}).items()
        }
        state.scene_facts = {
            k: FactRecord.from_dict(v)
            for k, v in data.get("scene_facts", {}).items()
        }
        state.temporary_assumptions = {
            k: FactRecord.from_dict(v)
            for k, v in data.get("temporary_assumptions", {}).items()
        }
        state.player_commitments = {
            k: CommitmentRecord.from_dict(v)
            for k, v in data.get("player_commitments", {}).items()
        }
        state.npc_commitments = {
            k: CommitmentRecord.from_dict(v)
            for k, v in data.get("npc_commitments", {}).items()
        }
        state.recent_changes = [
            ConsequenceRecord.from_dict(v)
            for v in data.get("recent_changes", [])
        ]
        state.unresolved_threads = {
            k: ThreadRecord.from_dict(v)
            for k, v in data.get("unresolved_threads", {}).items()
        }
        state.continuity_anchors = [
            SceneAnchor.from_dict(v)
            for v in data.get("continuity_anchors", [])
        ]
        state.contradictions = [
            ContradictionRecord.from_dict(v)
            for v in data.get("contradictions", [])
        ]
        return state
