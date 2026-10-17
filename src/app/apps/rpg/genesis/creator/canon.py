from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CreatorCanonFact:
    fact_id: str
    subject: str
    predicate: str
    value: Any
    source: str = "creator"
    authority: str = "creator_canon"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CreatorCanonFact":
        return cls(**data)


class CreatorCanonState:
    def __init__(self) -> None:
        self.facts: dict[str, CreatorCanonFact] = {}
        self.setup_id: str | None = None
        self.metadata: dict[str, Any] = {}

    def add_fact(self, fact: CreatorCanonFact) -> None:
        self.facts[fact.fact_id] = fact

    def remove_fact(self, fact_id: str) -> None:
        self.facts.pop(fact_id, None)

    def list_facts(self) -> list[CreatorCanonFact]:
        return list(self.facts.values())

    def apply_to_coherence(self, coherence_core: Any) -> None:
        from app.apps.rpg.narration.coherence.models import FactRecord

        for fact in self.facts.values():
            coherence_core.insert_fact(
                FactRecord(
                    fact_id=fact.fact_id,
                    category="world",
                    subject=fact.subject,
                    predicate=fact.predicate,
                    value=fact.value,
                    authority="creator_canon",
                    status="confirmed",
                    metadata=dict(fact.metadata),
                )
            )

    def serialize_state(self) -> dict:
        return {
            "facts": {k: v.to_dict() for k, v in self.facts.items()},
            "setup_id": self.setup_id,
            "metadata": dict(self.metadata),
        }

    def deserialize_state(self, data: dict) -> None:
        self.facts = {
            k: CreatorCanonFact.from_dict(v)
            for k, v in data.get("facts", {}).items()
        }
        self.setup_id = data.get("setup_id")
        self.metadata = dict(data.get("metadata", {}))
