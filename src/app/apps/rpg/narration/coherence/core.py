from __future__ import annotations

from typing import Any, Dict, List, Optional

from .detector import ContradictionDetector
from .models import (
    CoherenceMutation,
    CoherenceState,
    CoherenceUpdateResult,
    CommitmentRecord,
    ConsequenceRecord,
    ContradictionRecord,
    FactRecord,
    SceneAnchor,
    ThreadRecord,
)
from .query import CoherenceQueryAPI
from .reducers import normalize_event, reduce_event

AUTHORITY_RANK = {
    "creator_canon": 100,
    "engine_confirmed": 90,
    "event_confirmed": 80,
    "player_commitment": 70,
    "npc_commitment": 60,
    "runtime": 50,
    "inferred": 40,
    "assumption": 30,
}


class CoherenceCore:
    """Authoritative coherence state store for narrative/world truth."""

    def __init__(self, contradiction_detector: Optional[ContradictionDetector] = None) -> None:
        self.state = CoherenceState()
        self.mode: str = "live"
        self.contradiction_detector = contradiction_detector or ContradictionDetector()

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def get_state(self) -> CoherenceState:
        return self.state

    def get_query_api(self) -> CoherenceQueryAPI:
        return CoherenceQueryAPI(self.state)

    def apply_event(self, event: Any) -> CoherenceUpdateResult:
        normalized = normalize_event(event)
        mutations = reduce_event(self.state, normalized)
        contradictions = self.contradiction_detector.detect(self.state, mutations, normalized)

        for mutation in mutations:
            self._apply_mutation(mutation)
        if contradictions:
            self.record_contradictions(contradictions)

        return CoherenceUpdateResult(
            events_applied=1,
            mutations=mutations,
            contradictions=contradictions,
        )

    def apply_events(self, events: List[Any]) -> CoherenceUpdateResult:
        aggregate = CoherenceUpdateResult()
        for event in events:
            result = self.apply_event(event)
            aggregate.events_applied += result.events_applied
            aggregate.mutations.extend(result.mutations)
            aggregate.contradictions.extend(result.contradictions)
        return aggregate

    def get_scene_summary(self) -> dict:
        return self.get_query_api().get_scene_summary()

    def get_active_tensions(self) -> list:
        return self.get_query_api().get_active_tensions()

    def get_unresolved_threads(self) -> list:
        return self.get_query_api().get_unresolved_threads()

    def get_actor_commitments(self, actor_id: str) -> list:
        return self.get_query_api().get_actor_commitments(actor_id)

    def get_known_facts(self, entity_id: str) -> dict:
        return self.get_query_api().get_known_facts(entity_id)

    def get_recent_consequences(self, limit: int = 10) -> list:
        return self.get_query_api().get_recent_consequences(limit=limit)

    def get_last_good_anchor(self) -> dict | None:
        return self.get_query_api().get_last_good_anchor()

    def insert_fact(self, fact: FactRecord) -> None:
        self.upsert_fact(fact)

    def upsert_fact(self, fact: FactRecord) -> None:
        bucket = self._bucket_for_fact(fact)
        existing = bucket.get(fact.fact_id)
        if existing is not None:
            current_rank = AUTHORITY_RANK.get(existing.authority, 0)
            incoming_rank = AUTHORITY_RANK.get(fact.authority, 0)
            if incoming_rank < current_rank:
                return
            fact.tick_first_seen = existing.tick_first_seen
        bucket[fact.fact_id] = fact

    def remove_fact(self, fact_id: str) -> None:
        for bucket in (
            self.state.stable_world_facts,
            self.state.scene_facts,
            self.state.temporary_assumptions,
        ):
            bucket.pop(fact_id, None)

    def insert_thread(self, thread: ThreadRecord) -> None:
        self.state.unresolved_threads[thread.thread_id] = thread

    def resolve_thread(self, thread_id: str, resolution_event_id: Optional[str] = None, tick: Optional[int] = None) -> None:
        thread = self.state.unresolved_threads.get(thread_id)
        if thread is None:
            return
        thread.status = "resolved"
        thread.updated_tick = tick
        thread.resolved_tick = tick
        if resolution_event_id:
            thread.metadata["resolution_event_id"] = resolution_event_id

    def insert_commitment(self, commitment: CommitmentRecord) -> None:
        bucket = (
            self.state.player_commitments
            if commitment.actor_id == "player"
            else self.state.npc_commitments
        )
        bucket[commitment.commitment_id] = commitment

    def break_commitment(self, commitment_id: str, breaking_event_id: Optional[str] = None, tick: Optional[int] = None) -> None:
        for bucket in (self.state.player_commitments, self.state.npc_commitments):
            commitment = bucket.get(commitment_id)
            if commitment is not None:
                commitment.status = "broken"
                commitment.updated_tick = tick
                commitment.broken_tick = tick
                if breaking_event_id:
                    commitment.metadata["breaking_event_id"] = breaking_event_id
                return

    def push_anchor(self, anchor: SceneAnchor) -> None:
        self.state.continuity_anchors.append(anchor)
        if len(self.state.continuity_anchors) > 50:
            self.state.continuity_anchors = self.state.continuity_anchors[-50:]

    def record_contradictions(self, contradictions: List[ContradictionRecord]) -> None:
        self.state.contradictions.extend(contradictions)
        if len(self.state.contradictions) > 200:
            self.state.contradictions = self.state.contradictions[-200:]

    def _bucket_for_fact(self, fact: FactRecord) -> Dict[str, FactRecord]:
        if fact.category in {"world", "stable_world"} or fact.authority in {
            "creator_canon",
            "engine_confirmed",
            "event_confirmed",
        }:
            return self.state.stable_world_facts
        if fact.category in {"scene"}:
            return self.state.scene_facts
        return self.state.temporary_assumptions

    def _apply_mutation(self, mutation: CoherenceMutation) -> None:
        if mutation.action == "upsert_fact":
            self.upsert_fact(FactRecord.from_dict(mutation.data))
            return
        if mutation.action == "remove_fact":
            self.remove_fact(mutation.data["fact_id"])
            return
        if mutation.action == "upsert_thread":
            self.insert_thread(ThreadRecord.from_dict(mutation.data))
            return
        if mutation.action == "resolve_thread":
            self.resolve_thread(
                mutation.data["thread_id"],
                resolution_event_id=mutation.data.get("resolution_event_id"),
                tick=mutation.data.get("tick"),
            )
            return
        if mutation.action == "upsert_commitment":
            self.insert_commitment(CommitmentRecord.from_dict(mutation.data))
            return
        if mutation.action == "break_commitment":
            self.break_commitment(
                mutation.data["commitment_id"],
                breaking_event_id=mutation.data.get("breaking_event_id"),
                tick=mutation.data.get("tick"),
            )
            return
        if mutation.action == "push_anchor":
            self.push_anchor(SceneAnchor.from_dict(mutation.data))
            return
        if mutation.action == "record_consequence":
            self.state.recent_changes.append(ConsequenceRecord.from_dict(mutation.data))
            if len(self.state.recent_changes) > 100:
                self.state.recent_changes = self.state.recent_changes[-100:]
            return

    def serialize_state(self) -> dict:
        return {
            "mode": self.mode,
            "state": self.state.to_dict(),
        }

    def deserialize_state(self, data: dict) -> None:
        self.mode = data.get("mode", "live")
        self.state = CoherenceState.from_dict(data.get("state", {}))

    # SnapshotManager extension hooks
    def serialize(self) -> dict:
        return self.serialize_state()

    def deserialize(self, data: dict) -> None:
        self.deserialize_state(data)
