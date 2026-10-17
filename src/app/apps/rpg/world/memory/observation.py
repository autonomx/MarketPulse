from __future__ import annotations

from typing import Any, Dict, Iterable, List

from app.apps.rpg.world.memory.causal_memory import add_causal_memory, make_causal_memory
from app.apps.rpg.world.spatial.audibility import can_hear_area
from app.apps.rpg.world.spatial.graph import get_entity_area
from app.apps.rpg.world.spatial.serialization import normalize_spatial_graph
from app.apps.rpg.world.spatial.visibility import can_see_entity
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _event_facts(event: Dict[str, Any]) -> Dict[str, Any]:
    event = _safe_dict(event)
    facts = dict(_safe_dict(event.get("facts")))
    for key in (
        "actor_id",
        "target_id",
        "action",
        "location_id",
        "object_id",
        "quest_id",
    ):
        if key in event and key not in facts:
            facts[key] = event.get(key)
    return facts


def _all_entity_ids(graph: Dict[str, Any]) -> List[str]:
    return sorted(str(k) for k in _safe_dict(graph.get("entity_locations")).keys())


def _record(
    simulation_state: Dict[str, Any],
    *,
    subject_id: str,
    event: Dict[str, Any],
    kind: str,
    source: str,
    confidence: float,
    turn_index: int,
    tags: List[str],
    visibility: Dict[str, Any] | None = None,
    audibility: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    memory = make_causal_memory(
        subject_id=subject_id,
        event_id=_safe_str(event.get("event_id")) or "event:unknown",
        kind=kind,
        source=source,
        summary=_safe_str(event.get("summary")) or "An event occurred.",
        facts=_event_facts(event),
        confidence=confidence,
        turn_index=turn_index,
        tags=tags,
        visibility=visibility,
        audibility=audibility,
    )
    return add_causal_memory(simulation_state, memory)


def record_event_observations(
    simulation_state: Dict[str, Any],
    event: Dict[str, Any],
    *,
    observer_entity_ids: Iterable[str] | None = None,
    turn_index: int = 0,
) -> Dict[str, Any]:
    """Record deterministic memories for observers/hearers/affected entities."""
    simulation_state = _safe_dict(simulation_state)
    event = _safe_dict(event)
    graph = normalize_spatial_graph(simulation_state.get("spatial_graph"))
    has_graph = bool(graph.get("areas") and graph.get("entity_locations"))

    actor_id = _safe_str(event.get("actor_id"))
    target_id = _safe_str(event.get("target_id"))
    location_id = _safe_str(event.get("location_id"))
    sound_level = _safe_str(event.get("sound_level")) or "normal"
    tags = [str(tag) for tag in _safe_list(event.get("tags")) if str(tag)]

    if observer_entity_ids is None:
        observer_ids = _all_entity_ids(graph) if has_graph else []
    else:
        observer_ids = [str(item) for item in observer_entity_ids if str(item)]

    recorded: List[Dict[str, Any]] = []

    # Directly affected target always remembers.
    if target_id:
        recorded.append(
            _record(
                simulation_state,
                subject_id=target_id,
                event=event,
                kind="affected",
                source=_safe_str(event.get("source")) or "event",
                confidence=1.0,
                turn_index=turn_index,
                tags=sorted(set(tags + ["affected"])),
            )
        )

    # Actor remembers their own action if present.
    if actor_id and actor_id != target_id:
        recorded.append(
            _record(
                simulation_state,
                subject_id=actor_id,
                event=event,
                kind="observed",
                source=_safe_str(event.get("source")) or "event_actor",
                confidence=1.0,
                turn_index=turn_index,
                tags=sorted(set(tags + ["self"])),
            )
        )

    if not has_graph:
        # Conservative fallback: explicit observer list only.
        for observer_id in observer_ids:
            if observer_id in {actor_id, target_id}:
                continue
            recorded.append(
                _record(
                    simulation_state,
                    subject_id=observer_id,
                    event=event,
                    kind="observed",
                    source="explicit_observer",
                    confidence=1.0,
                    turn_index=turn_index,
                    tags=sorted(set(tags + ["explicit_observer"])),
                )
            )
        return {
            "ok": True,
            "has_spatial_graph": False,
            "recorded": recorded,
        }

    for observer_id in observer_ids:
        if observer_id in {actor_id, target_id}:
            continue

        # Compute visibility for actor and target separately
        actor_visibility = None
        if actor_id:
            actor_visibility = can_see_entity(graph, observer_id, actor_id)

        target_visibility = None
        if target_id:
            target_visibility = can_see_entity(graph, observer_id, target_id)

        # Determine if observer should get observed memory
        should_record_observed = False
        visibility_info = None

        if target_id:
            # Event has a target
            if target_visibility and target_visibility.get("ok"):
                # Target is visible - allow observed memory
                should_record_observed = True
                visibility_info = target_visibility
            elif target_visibility and target_visibility.get("reason") == "hidden":
                # Target is hidden - only allow if event explicitly permits partial observation
                allow_partial = event.get("allow_partial_actor_observation") or "public_actor_only" in tags
                if allow_partial and actor_visibility and actor_visibility.get("ok"):
                    should_record_observed = True
                    visibility_info = actor_visibility
                # Otherwise, don't record observed memory for hidden target
            elif actor_visibility and actor_visibility.get("ok"):
                # Target exists but visibility check didn't find it hidden, and actor is visible
                # Allow for public/action events (default behavior)
                should_record_observed = True
                visibility_info = actor_visibility
        else:
            # No target - actor visibility is sufficient
            if actor_visibility and actor_visibility.get("ok"):
                should_record_observed = True
                visibility_info = actor_visibility

        if should_record_observed:
            recorded.append(
                _record(
                    simulation_state,
                    subject_id=observer_id,
                    event=event,
                    kind="observed",
                    source="spatial_visibility",
                    confidence=1.0,
                    turn_index=turn_index,
                    tags=sorted(set(tags + ["observed"])),
                    visibility=visibility_info,
                )
            )
            continue

        observer_area = get_entity_area(graph, observer_id)
        heard = can_hear_area(
            graph,
            observer_area,
            location_id,
            sound_level=sound_level,
        )
        if heard.get("ok"):
            recorded.append(
                _record(
                    simulation_state,
                    subject_id=observer_id,
                    event=event,
                    kind="heard",
                    source="spatial_audibility",
                    confidence=0.75 if heard.get("audibility") == "muffled" else 0.9,
                    turn_index=turn_index,
                    tags=sorted(set(tags + ["heard"])),
                    audibility=heard,
                )
            )

    return {
        "ok": True,
        "has_spatial_graph": True,
        "recorded": recorded,
    }


def record_told_memory(
    simulation_state: Dict[str, Any],
    subject_id: str,
    *,
    speaker_id: str,
    event_id: str,
    summary: str,
    facts: Dict[str, Any],
    confidence: float = 0.7,
    turn_index: int = 0,
    tags: List[str] | None = None,
    verified: bool = False,
) -> Dict[str, Any]:
    tags = list(tags or [])
    if not verified:
        tags = sorted(set(tags + ["claim", "unverified"]))
    memory = make_causal_memory(
        subject_id=subject_id,
        event_id=event_id,
        kind="told",
        source="dialogue",
        summary=summary,
        facts=dict(facts or {}, speaker_id=speaker_id),
        confidence=confidence if not verified else max(confidence, 0.9),
        turn_index=turn_index,
        tags=tags,
    )
    return add_causal_memory(simulation_state, memory)