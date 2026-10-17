from __future__ import annotations

from typing import Any, List, Optional, cast

from .models import CoherenceMutation, CoherenceState, ContradictionRecord


class ContradictionDetector:
    """Lightweight deterministic contradiction checks."""

    def detect(
        self,
        state: CoherenceState,
        mutations: List[CoherenceMutation],
        event: Optional[dict] = None,
    ) -> List[ContradictionRecord]:
        contradictions: List[ContradictionRecord] = []
        contradictions.extend(self._check_dead_actor_conflicts(state, mutations, event))
        contradictions.extend(self._check_location_conflicts(state, mutations, event))
        contradictions.extend(self._check_inventory_conflicts(state, mutations, event))
        contradictions.extend(self._check_thread_conflicts(state, mutations, event))
        return contradictions

    def _check_dead_actor_conflicts(
        self,
        state: CoherenceState,
        mutations: List[CoherenceMutation],
        event: Optional[dict],
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        for mutation in mutations:
            if mutation.action != "upsert_fact":
                continue
            data = mutation.data
            subject = data.get("subject")
            predicate = data.get("predicate")
            if predicate not in {"location", "relationship", "owner"}:
                continue
            alive_fact = state.stable_world_facts.get(f"{subject}:alive")
            if alive_fact and alive_fact.value is False:
                out.append(
                    ContradictionRecord(
                        contradiction_id=f"contradiction:{event.get('event_id')}:dead_actor"
                        if event else "contradiction:dead_actor",
                        contradiction_type="dead_actor_conflict",
                        severity="high",
                        message=f"Entity '{subject}' received update '{predicate}' after confirmed death.",
                        event_id=event.get("event_id") if event else None,
                        tick=event.get("tick") if event else None,
                        entity_ids=[cast(Any, subject)],
                        related_fact_ids=[alive_fact.fact_id],
                    )
                )
        return out

    def _check_location_conflicts(
        self,
        state: CoherenceState,
        mutations: List[CoherenceMutation],
        event: Optional[dict],
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        for mutation in mutations:
            if mutation.action != "upsert_fact":
                continue
            data = mutation.data
            if data.get("predicate") != "location":
                continue
            fact_id = data.get("fact_id")
            existing = state.stable_world_facts.get(cast(Any, fact_id)) or state.scene_facts.get(cast(Any, fact_id))
            if existing and existing.value != data.get("value"):
                out.append(
                    ContradictionRecord(
                        contradiction_id=f"contradiction:{event.get('event_id')}:location"
                        if event else "contradiction:location",
                        contradiction_type="location_conflict",
                        severity="warning",
                        message=(
                            f"Entity '{data.get('subject')}' moved from '{existing.value}' "
                            f"to '{data.get('value')}' without explicit transition event."
                        ),
                        event_id=event.get("event_id") if event else None,
                        tick=event.get("tick") if event else None,
                        entity_ids=[cast(Any, data.get("subject"))],
                        related_fact_ids=[existing.fact_id],
                    )
                )
        return out

    def _check_inventory_conflicts(
        self,
        state: CoherenceState,
        mutations: List[CoherenceMutation],
        event: Optional[dict],
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        for mutation in mutations:
            if mutation.action != "upsert_fact":
                continue
            data = mutation.data
            if data.get("predicate") != "owner":
                continue
            existing = state.stable_world_facts.get(cast(Any, data.get("fact_id")))
            if existing and existing.value not in (None, data.get("value")):
                out.append(
                    ContradictionRecord(
                        contradiction_id=f"contradiction:{event.get('event_id')}:inventory"
                        if event else "contradiction:inventory",
                        contradiction_type="inventory_conflict",
                        severity="warning",
                        message=(
                            f"Item '{data.get('subject')}' already owned by '{existing.value}' "
                            f"and now also assigned to '{data.get('value')}'."
                        ),
                        event_id=event.get("event_id") if event else None,
                        tick=event.get("tick") if event else None,
                        entity_ids=[cast(Any, data.get("subject")), str(existing.value), str(data.get("value"))],
                        related_fact_ids=[existing.fact_id],
                    )
                )
        return out

    def _check_thread_conflicts(
        self,
        state: CoherenceState,
        mutations: List[CoherenceMutation],
        event: Optional[dict],
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        for mutation in mutations:
            if mutation.action != "resolve_thread":
                continue
            thread_id = mutation.data.get("thread_id")
            if thread_id and thread_id not in state.unresolved_threads:
                out.append(
                    ContradictionRecord(
                        contradiction_id=f"contradiction:{event.get('event_id')}:thread"
                        if event else "contradiction:thread",
                        contradiction_type="thread_resolution_conflict",
                        severity="info",
                        message=f"Attempted to resolve unknown or already resolved thread '{thread_id}'.",
                        event_id=event.get("event_id") if event else None,
                        tick=event.get("tick") if event else None,
                        entity_ids=[],
                        related_fact_ids=[],
                    )
                )
        return out
