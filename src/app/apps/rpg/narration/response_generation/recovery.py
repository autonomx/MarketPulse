from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .intent_affordance import IntentAnalysis, NarrativeAffordanceClassifier
from .retrieval import EvidenceRecord, LocalKnowledgeRetriever, RetrievalResult


@dataclass(frozen=True)
class LocalRecoveryAnalysis:
    intent: IntentAnalysis
    retrieval: RetrievalResult
    needs_hermes: bool
    reason: str
    state_mutation_allowed: bool = False

    @property
    def hermes_eligible(self) -> bool:
        """Compatibility name used by the production recovery orchestrator."""

        return self.needs_hermes

    def as_dict(self) -> dict[str, Any]:
        selected = self.intent.selected
        return {
            "selected_intent": selected.intent,
            "selected_affordance": selected.affordance,
            "confidence": selected.confidence,
            "underlying_goal": selected.underlying_goal,
            "ambiguity": selected.ambiguity,
            "unresolved_references": list(self.intent.unresolved_references),
            "knowledge_status": self.retrieval.knowledge_status,
            "evidence_ids": [row.evidence_id for row in self.retrieval.evidence],
            "hidden_evidence_ids": list(self.retrieval.hidden_evidence_ids),
            "conflicting_evidence_ids": list(self.retrieval.conflicting_evidence_ids),
            "needs_hermes": self.needs_hermes,
            "hermes_eligible": self.hermes_eligible,
            "reason": self.reason,
            "state_mutation_allowed": False,
        }


class LocalRecoveryCoordinator:
    def __init__(
        self,
        *,
        classifier: NarrativeAffordanceClassifier | None = None,
        retriever: LocalKnowledgeRetriever | None = None,
        hermes_confidence_threshold: float = 0.72,
    ) -> None:
        self.classifier = classifier or NarrativeAffordanceClassifier()
        self.retriever = retriever or LocalKnowledgeRetriever()
        self.hermes_confidence_threshold = hermes_confidence_threshold

    def analyze(
        self,
        player_input: str,
        *,
        known_entities: Mapping[str, Any] | None = None,
        known_locations: Mapping[str, Any] | None = None,
        supported_mechanics: tuple[str, ...] = (),
        retrieval_sources: Mapping[str, Iterable[EvidenceRecord | Mapping[str, Any]]] | None = None,
        speaker_id: str = "",
        narrator_mode: bool = False,
        hermes_allowed: bool = True,
    ) -> LocalRecoveryAnalysis:
        intent = self.classifier.classify(
            player_input,
            known_entities=known_entities,
            known_locations=known_locations,
            supported_mechanics=supported_mechanics,
        )
        retrieval = self.retriever.retrieve(
            player_input,
            retrieval_sources or {},
            speaker_id=speaker_id,
            narrator_mode=narrator_mode,
        )
        selected = intent.selected
        needs_hermes = bool(
            hermes_allowed
            and selected.confidence >= self.hermes_confidence_threshold
            and retrieval.knowledge_status in {"unknown", "conflicting"}
            and selected.affordance in {
                "lore_search",
                "entity_search",
                "ask_directions",
                "unverified_player_claim",
            }
        )
        if retrieval.local_hit and retrieval.knowledge_status == "known":
            reason = "local_evidence_sufficient"
        elif selected.ambiguity == "high":
            reason = "clarification_preferred"
            needs_hermes = False
        elif needs_hermes:
            reason = "local_evidence_insufficient"
        else:
            reason = "local_recovery_required"
        return LocalRecoveryAnalysis(
            intent=intent,
            retrieval=retrieval,
            needs_hermes=needs_hermes,
            reason=reason,
        )
