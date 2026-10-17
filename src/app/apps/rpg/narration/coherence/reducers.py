from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.apps.rpg.foundation.core.event_bus import Event
from .models import CoherenceMutation


def normalize_event(event: Any) -> dict:
    if isinstance(event, Event):
        return {
            "type": event.type,
            "payload": dict(event.payload or {}),
            "event_id": event.event_id,
            "tick": event.tick,
            "source": event.source,
        }
    if isinstance(event, dict):
        return {
            "type": event.get("type"),
            "payload": dict(event.get("payload", {}) or {}),
            "event_id": event.get("event_id"),
            "tick": event.get("tick") if "tick" in event else event.get("payload", {}).get("tick"),
            "source": event.get("source"),
        }
    raise TypeError(f"Unsupported event type for coherence reducer: {type(event)!r}")


def _event_text(event: dict) -> str:
    payload = event.get("payload", {})
    return (
        payload.get("text")
        or payload.get("summary")
        or payload.get("description")
        or event.get("type", "event")
    )


def _fact_mutation(
    *,
    target: str,
    fact_id: str,
    category: str,
    subject: str,
    predicate: str,
    value: Any,
    authority: str,
    event: dict,
    status: str = "confirmed",
    confidence: float = 1.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> CoherenceMutation:
    return CoherenceMutation(
        action="upsert_fact",
        target=target,
        data={
            "fact_id": fact_id,
            "category": category,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "authority": authority,
            "status": status,
            "confidence": confidence,
            "source_event_id": event.get("event_id"),
            "tick_first_seen": event.get("tick"),
            "tick_last_updated": event.get("tick"),
            "metadata": metadata or {},
        },
    )


def reduce_scene_started(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    mutations: List[CoherenceMutation] = []
    location = payload.get("location")
    if location:
        mutations.append(
            _fact_mutation(
                target="scene",
                fact_id="scene:location",
                category="scene",
                subject="scene",
                predicate="location",
                value=location,
                authority="event_confirmed",
                event=event,
            )
        )
    summary = payload.get("summary") or payload.get("description") or "Scene started"
    mutations.append(
        CoherenceMutation(
            action="push_anchor",
            target="anchor",
            data={
                "anchor_id": f"anchor:{event.get('event_id') or event.get('tick')}",
                "tick": event.get("tick"),
                "location": location,
                "present_actors": payload.get("present_actors", []),
                "active_tensions": payload.get("active_tensions", []),
                "unresolved_thread_ids": list(state.unresolved_threads.keys()),
                "summary": summary,
                "scene_fact_ids": ["scene:location"] if location else [],
                "source_event_id": event.get("event_id"),
                "metadata": {},
            },
        )
    )
    return mutations


def reduce_scene_generated(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    scene = payload.get("scene", payload)
    location = scene.get("location") or payload.get("location")
    summary = (
        scene.get("summary")
        or scene.get("description")
        or scene.get("narrative", {}).get("description")
        if isinstance(scene.get("narrative"), dict)
        else None
    ) or payload.get("summary") or "Scene generated"

    present = scene.get("present_actors") or scene.get("participants") or payload.get("participants", [])
    tensions = scene.get("active_tensions") or payload.get("active_tensions", [])

    mutations: List[CoherenceMutation] = []
    if location:
        mutations.append(
            _fact_mutation(
                target="scene",
                fact_id="scene:location",
                category="scene",
                subject="scene",
                predicate="location",
                value=location,
                authority="event_confirmed",
                event=event,
            )
        )
    mutations.append(
        CoherenceMutation(
            action="push_anchor",
            target="anchor",
            data={
                "anchor_id": f"anchor:{event.get('event_id') or event.get('tick')}",
                "tick": event.get("tick"),
                "location": location,
                "present_actors": present,
                "active_tensions": tensions,
                "unresolved_thread_ids": list(state.unresolved_threads.keys()),
                "summary": summary,
                "scene_fact_ids": ["scene:location"] if location else [],
                "source_event_id": event.get("event_id"),
                "metadata": {},
            },
        )
    )
    mutations.append(
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:{event.get('event_id')}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": summary,
                "entity_ids": list(present),
                "consequence_type": "scene_generated",
                "metadata": {},
            },
        )
    )
    return mutations


def reduce_npc_moved(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    npc_id = payload.get("npc_id") or payload.get("actor_id") or payload.get("entity_id")
    location = payload.get("location") or payload.get("to")
    if not npc_id or location is None:
        return []
    return [
        _fact_mutation(
            target="world",
            fact_id=f"{npc_id}:location",
            category="world",
            subject=npc_id,
            predicate="location",
            value=location,
            authority="event_confirmed",
            event=event,
        )
    ]


def reduce_relationship_changed(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    npc_id = payload.get("npc_id") or payload.get("source_id")
    target_id = payload.get("target_id")
    value = payload.get("relationship") or payload.get("delta")
    if not npc_id or not target_id:
        return []
    return [
        _fact_mutation(
            target="world",
            fact_id=f"rel:{npc_id}:{target_id}",
            category="world",
            subject=npc_id,
            predicate=f"relationship:{target_id}",
            value=value,
            authority="event_confirmed",
            event=event,
        )
    ]


def reduce_item_acquired(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    actor_id = payload.get("actor_id") or payload.get("npc_id") or payload.get("owner_id")
    item_id = payload.get("item_id") or payload.get("item")
    if not actor_id or not item_id:
        return []
    return [
        _fact_mutation(
            target="world",
            fact_id=f"item:{item_id}:owner",
            category="world",
            subject=item_id,
            predicate="owner",
            value=actor_id,
            authority="event_confirmed",
            event=event,
        )
    ]


def reduce_item_lost(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    item_id = payload.get("item_id") or payload.get("item")
    if not item_id:
        return []
    return [
        _fact_mutation(
            target="assumption",
            fact_id=f"item:{item_id}:owner",
            category="assumption",
            subject=item_id,
            predicate="owner",
            value=None,
            authority="event_confirmed",
            event=event,
            status="uncertain",
        )
    ]


def reduce_thread_started(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    thread_id = payload.get("thread_id") or payload.get("quest_id") or f"thread:{event.get('event_id')}"
    return [
        CoherenceMutation(
            action="upsert_thread",
            target="thread",
            data={
                "thread_id": thread_id,
                "title": payload.get("title") or payload.get("name") or _event_text(event),
                "status": "unresolved",
                "priority": payload.get("priority", "normal"),
                "source_event_id": event.get("event_id"),
                "opened_tick": event.get("tick"),
                "updated_tick": event.get("tick"),
                "resolved_tick": None,
                "anchor_entity_ids": payload.get("entity_ids", []),
                "notes": [payload.get("summary") or _event_text(event)],
                "metadata": {},
            },
        )
    ]


def reduce_thread_resolved(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    thread_id = payload.get("thread_id") or payload.get("quest_id")
    if not thread_id:
        return []
    return [
        CoherenceMutation(
            action="resolve_thread",
            target="thread",
            data={
                "thread_id": thread_id,
                "resolution_event_id": event.get("event_id"),
                "tick": event.get("tick"),
            },
        )
    ]


def reduce_commitment_created(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    actor_id = payload.get("actor_id") or payload.get("npc_id") or payload.get("source_id") or "unknown"
    target_id = payload.get("target_id")
    kind = payload.get("kind") or event["type"]
    text = payload.get("text") or payload.get("summary") or _event_text(event)
    commitment_id = payload.get("commitment_id") or f"commitment:{event.get('event_id')}"
    return [
        CoherenceMutation(
            action="upsert_commitment",
            target="commitment",
            data={
                "commitment_id": commitment_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "kind": kind,
                "text": text,
                "status": "active",
                "source_event_id": event.get("event_id"),
                "created_tick": event.get("tick"),
                "updated_tick": event.get("tick"),
                "broken_tick": None,
                "metadata": {},
            },
        )
    ]


def reduce_commitment_broken(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    commitment_id = payload.get("commitment_id")
    if not commitment_id:
        return []
    return [
        CoherenceMutation(
            action="break_commitment",
            target="commitment",
            data={
                "commitment_id": commitment_id,
                "breaking_event_id": event.get("event_id"),
                "tick": event.get("tick"),
            },
        )
    ]


def reduce_character_death(state: Any, event: dict) -> List[CoherenceMutation]:
    payload = event["payload"]
    entity_id = payload.get("npc_id") or payload.get("actor_id") or payload.get("entity_id") or payload.get("character_id")
    if not entity_id:
        return []
    return [
        _fact_mutation(
            target="world",
            fact_id=f"{entity_id}:alive",
            category="world",
            subject=entity_id,
            predicate="alive",
            value=False,
            authority="event_confirmed",
            event=event,
        )
    ]


# ------------------------------------------------------------------
# Phase 7.3 — Action-generated event reducers
# ------------------------------------------------------------------

def reduce_thread_progressed(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle thread_progressed events from action resolution."""
    payload = event["payload"]
    thread_id = payload.get("thread_id")
    if not thread_id:
        return []
    thread = getattr(state, "unresolved_threads", {}).get(thread_id)
    if thread is None:
        return []
    notes = list(getattr(thread, "notes", []) or [])
    notes.append(f"Progressed via action at tick {event.get('tick')}")
    return [
        CoherenceMutation(
            action="upsert_thread",
            target="thread",
            data={
                "thread_id": thread_id,
                "title": getattr(thread, "title", thread_id),
                "status": "unresolved",
                "priority": getattr(thread, "priority", "normal"),
                "source_event_id": event.get("event_id"),
                "opened_tick": getattr(thread, "opened_tick", None),
                "updated_tick": event.get("tick"),
                "resolved_tick": None,
                "anchor_entity_ids": getattr(thread, "anchor_entity_ids", []),
                "notes": notes,
                "metadata": dict(getattr(thread, "metadata", {}) or {}),
            },
        )
    ]


def reduce_npc_interaction_started(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_interaction_started events from action resolution."""
    payload = event["payload"]
    npc_id = payload.get("npc_id")
    if not npc_id:
        return []
    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:{event.get('event_id') or 'interaction'}:{npc_id}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": f"Started interaction with {npc_id}",
                "entity_ids": [npc_id],
                "consequence_type": "npc_interaction_started",
                "metadata": {},
            },
        )
    ]


def reduce_scene_transition_requested(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle scene_transition_requested events from action resolution."""
    payload = event["payload"]
    location = payload.get("location") or payload.get("to_location")
    if not location:
        return []
    mutations: List[CoherenceMutation] = [
        _fact_mutation(
            target="scene",
            fact_id="scene:location",
            category="scene",
            subject="scene",
            predicate="location",
            value=location,
            authority="event_confirmed",
            event=event,
        ),
    ]
    summary = f"Scene transition to {location}"
    mutations.append(
        CoherenceMutation(
            action="push_anchor",
            target="anchor",
            data={
                "anchor_id": f"anchor:transition:{event.get('event_id') or event.get('tick')}",
                "tick": event.get("tick"),
                "location": location,
                "present_actors": [],
                "active_tensions": [],
                "unresolved_thread_ids": list(state.unresolved_threads.keys()),
                "summary": summary,
                "scene_fact_ids": ["scene:location"],
                "source_event_id": event.get("event_id"),
                "metadata": {},
            },
        )
    )
    return mutations


def reduce_recap_requested(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle recap_requested events from action resolution."""
    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:recap:{event.get('event_id') or 'recap'}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": "Player requested a recap",
                "entity_ids": [],
                "consequence_type": "recap_requested",
                "metadata": {},
            },
        )
    ]


def reduce_action_blocked(state: Any, event: dict) -> List[CoherenceMutation]:
    """Record that an action was blocked without mutating canonical truth directly."""
    payload = event["payload"]
    intent_type = payload.get("intent_type", "unknown")
    target_id = payload.get("target_id")
    reason = payload.get("reason", "unknown")
    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"blocked:{intent_type}:{target_id or 'none'}:{event.get('tick')}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": f"Action '{intent_type}' was blocked ({reason}).",
                "entity_ids": [target_id] if target_id else [],
                "consequence_type": "action_blocked",
                "metadata": {"reason": reason},
            },
        )
    ]


# ------------------------------------------------------------------
# Phase 7.4 — NPC agency response reducers
# ------------------------------------------------------------------

def _reduce_npc_response(
    state: Any, event: dict, consequence_type: str
) -> List[CoherenceMutation]:
    """Shared reducer logic for NPC response events. Records a consequence."""
    payload = event["payload"]
    npc_id = payload.get("npc_id", "unknown_npc")
    summary = payload.get("summary") or payload.get("decision_summary") or f"NPC {consequence_type}"
    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:{event.get('event_id') or consequence_type}:{npc_id}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": summary,
                "entity_ids": [npc_id],
                "consequence_type": consequence_type,
                "metadata": {
                    "outcome": payload.get("outcome"),
                    "modifiers": payload.get("modifiers", []),
                },
            },
        )
    ]


def reduce_npc_response_agreed(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_response_agreed events from NPC agency."""
    return _reduce_npc_response(state, event, "npc_response_agreed")


def reduce_npc_response_refused(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_response_refused events from NPC agency."""
    return _reduce_npc_response(state, event, "npc_response_refused")


def reduce_npc_response_delayed(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_response_delayed events from NPC agency."""
    return _reduce_npc_response(state, event, "npc_response_delayed")


def reduce_npc_response_threatened(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_response_threatened events from NPC agency."""
    return _reduce_npc_response(state, event, "npc_response_threatened")


def reduce_npc_response_redirected(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_response_redirected events from NPC agency."""
    return _reduce_npc_response(state, event, "npc_response_redirected")


# ------------------------------------------------------------------
# Phase 7.5 — Group dynamics reducers
# ------------------------------------------------------------------

def _reduce_group_secondary(
    state: Any, event: dict, consequence_type: str
) -> List[CoherenceMutation]:
    """Shared reducer logic for group secondary reaction events."""
    payload = event["payload"]
    npc_id = payload.get("npc_id", "unknown_npc")
    summary = payload.get("summary") or f"NPC {consequence_type}"
    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:{event.get('event_id') or consequence_type}:{npc_id}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": summary,
                "entity_ids": [npc_id],
                "consequence_type": consequence_type,
                "metadata": {
                    "reaction_type": payload.get("reaction_type"),
                    "modifiers": payload.get("modifiers", []),
                },
            },
        )
    ]


def reduce_npc_secondary_supported(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_secondary_supported events from group dynamics."""
    return _reduce_group_secondary(state, event, "npc_secondary_supported")


def reduce_npc_secondary_opposed(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_secondary_opposed events from group dynamics."""
    return _reduce_group_secondary(state, event, "npc_secondary_opposed")


def reduce_npc_secondary_observed(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle npc_secondary_observed events from group dynamics."""
    return _reduce_group_secondary(state, event, "npc_secondary_observed")


def reduce_rumor_seeded(state: Any, event: dict) -> List[CoherenceMutation]:
    """Handle rumor_seeded events from group dynamics.

    Records the rumor seed as a consequence with rumor metadata.
    Does not create a full rumor propagation system yet.
    """
    payload = event["payload"]
    rumor_id = payload.get("rumor_id", "unknown_rumor")
    source_npc_id = payload.get("source_npc_id")
    subject_id = payload.get("subject_id")
    summary = payload.get("summary") or "Rumor seeded"

    entity_ids = []
    if source_npc_id:
        entity_ids.append(source_npc_id)
    if subject_id and subject_id != source_npc_id:
        entity_ids.append(subject_id)

    return [
        CoherenceMutation(
            action="record_consequence",
            target="consequence",
            data={
                "consequence_id": f"cons:rumor:{rumor_id}",
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
                "summary": summary,
                "entity_ids": entity_ids,
                "consequence_type": "rumor_seeded",
                "metadata": {
                    "rumor_id": rumor_id,
                    "rumor_type": payload.get("rumor_type"),
                    "location": payload.get("location"),
                },
            },
        )
    ]


REDUCERS = {
    "scene_started": reduce_scene_started,
    "scene_generated": reduce_scene_generated,
    "npc_moved": reduce_npc_moved,
    "relationship_changed": reduce_relationship_changed,
    "item_acquired": reduce_item_acquired,
    "item_lost": reduce_item_lost,
    "quest_started": reduce_thread_started,
    "thread_started": reduce_thread_started,
    "quest_completed": reduce_thread_resolved,
    "thread_resolved": reduce_thread_resolved,
    "promise_made": reduce_commitment_created,
    "threat_made": reduce_commitment_created,
    "promise_broken": reduce_commitment_broken,
    "character_died": reduce_character_death,
    # Phase 7.3 — Action-generated event types
    "thread_progressed": reduce_thread_progressed,
    "npc_interaction_started": reduce_npc_interaction_started,
    "scene_transition_requested": reduce_scene_transition_requested,
    "recap_requested": reduce_recap_requested,
    "action_blocked": reduce_action_blocked,
    # Phase 7.4 — NPC agency response event types
    "npc_response_agreed": reduce_npc_response_agreed,
    "npc_response_refused": reduce_npc_response_refused,
    "npc_response_delayed": reduce_npc_response_delayed,
    "npc_response_threatened": reduce_npc_response_threatened,
    "npc_response_redirected": reduce_npc_response_redirected,
    # Phase 7.5 — Group dynamics event types
    "npc_secondary_supported": reduce_npc_secondary_supported,
    "npc_secondary_opposed": reduce_npc_secondary_opposed,
    "npc_secondary_observed": reduce_npc_secondary_observed,
    "rumor_seeded": reduce_rumor_seeded,
}


def reduce_event(state: Any, event: dict) -> List[CoherenceMutation]:
    normalized = normalize_event(event)
    reducer = REDUCERS.get(normalized["type"])
    if reducer is None:
        return []
    return reducer(state, normalized)
