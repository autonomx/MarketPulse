from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _normalize_active_interactions as _normalize_active_interactions, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
import hashlib as hashlib
import json as json
from app.apps.rpg.session.companion_turn_runtime import (
    _LOCATION_CONDITION_DECAY_TICKS as _LOCATION_CONDITION_DECAY_TICKS, _MAX_ACCEPTED_STATE_CHANGE_EVENTS as _MAX_ACCEPTED_STATE_CHANGE_EVENTS,
    _MAX_APPLIED_PROPOSAL_IDS as _MAX_APPLIED_PROPOSAL_IDS, _MAX_LOCATION_CONDITIONS as _MAX_LOCATION_CONDITIONS, _MAX_RECENT_WORLD_EVENT_ROWS as _MAX_RECENT_WORLD_EVENT_ROWS,
    _MAX_RECORDED_SEMANTIC_LLM_PROPOSALS as _MAX_RECORDED_SEMANTIC_LLM_PROPOSALS, _MAX_SEMANTIC_PROPOSALS as _MAX_SEMANTIC_PROPOSALS,
    _MAX_WORLD_CONSEQUENCES as _MAX_WORLD_CONSEQUENCES, _MAX_WORLD_PRESSURE as _MAX_WORLD_PRESSURE, _MAX_WORLD_RUMORS as _MAX_WORLD_RUMORS,
    _WORLD_CONSEQUENCE_DECAY_TICKS as _WORLD_CONSEQUENCE_DECAY_TICKS, _WORLD_PRESSURE_DECAY_TICKS as _WORLD_PRESSURE_DECAY_TICKS,
    _WORLD_RUMOR_DECAY_TICKS as _WORLD_RUMOR_DECAY_TICKS,
)
from app.apps.rpg.session.player_activity_runtime import (
    _ensure_recent_scene_beats as _ensure_recent_scene_beats, _location_condition_key as _location_condition_key, _normalize_activity_record as _normalize_activity_record,
    _normalize_location_condition as _normalize_location_condition, _normalize_pressure_record as _normalize_pressure_record, _normalize_scene_beat as _normalize_scene_beat,
    _normalize_world_consequence as _normalize_world_consequence, _normalize_world_rumor as _normalize_world_rumor, _stable_consequence_id as _stable_consequence_id,
    _world_consequence_key as _world_consequence_key, _world_pressure_key as _world_pressure_key, _world_rumor_key as _world_rumor_key,
    ensure_actor_activity_state as ensure_actor_activity_state, ensure_world_consequence_state as ensure_world_consequence_state,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)


def _append_world_rumor(runtime_state: dict[str, Any], rumor: dict[str, Any]) -> dict[str, Any]:
    runtime_state = ensure_world_consequence_state(runtime_state)
    rumor = _normalize_world_rumor(rumor)
    rumor_key = _world_rumor_key(rumor)

    rumors = _safe_list(runtime_state.get("world_rumors"))
    updated = False
    merged: list[dict[str, Any]] = []

    for existing in rumors:
        existing = _normalize_world_rumor(existing)
        if _world_rumor_key(existing) == rumor_key:
            existing["updated_tick"] = max(_safe_int(existing.get("updated_tick"), 0), _safe_int(rumor.get("updated_tick"), 0))
            existing["strength"] = min(10, _safe_int(existing.get("strength"), 1) + max(1, _safe_int(rumor.get("strength"), 1)))
            existing_tags = set(_safe_list(existing.get("tags")))
            for tag in _safe_list(rumor.get("tags")):
                existing_tags.add(_safe_str(tag))
            existing["tags"] = sorted([t for t in existing_tags if t])
            merged.append(existing)
            updated = True
        else:
            merged.append(existing)

    if not updated:
        merged.append(rumor)

    runtime_state["world_rumors"] = merged[-_MAX_WORLD_RUMORS:]
    return runtime_state


def _append_world_pressure(runtime_state: dict[str, Any], pressure: dict[str, Any]) -> dict[str, Any]:
    runtime_state = ensure_world_consequence_state(runtime_state)
    pressure = _normalize_pressure_record(pressure)
    pressure_key = _world_pressure_key(pressure)

    items = _safe_list(runtime_state.get("world_pressure"))
    updated = False
    merged: list[dict[str, Any]] = []

    for existing in items:
        existing = _normalize_pressure_record(existing)
        if _world_pressure_key(existing) == pressure_key:
            existing["updated_tick"] = max(_safe_int(existing.get("updated_tick"), 0), _safe_int(pressure.get("updated_tick"), 0))
            existing["value"] = min(10, _safe_int(existing.get("value"), 0) + max(1, _safe_int(pressure.get("value"), 0)))
            existing["summary"] = _safe_str(pressure.get("summary")) or _safe_str(existing.get("summary"))
            existing_tags = set(_safe_list(existing.get("tags")))
            for tag in _safe_list(pressure.get("tags")):
                existing_tags.add(_safe_str(tag))
            existing["tags"] = sorted([t for t in existing_tags if t])
            merged.append(existing)
            updated = True
        else:
            merged.append(existing)

    if not updated:
        merged.append(pressure)

    runtime_state["world_pressure"] = merged[-_MAX_WORLD_PRESSURE:]
    return runtime_state


def _append_location_condition(runtime_state: dict[str, Any], condition: dict[str, Any]) -> dict[str, Any]:
    runtime_state = ensure_world_consequence_state(runtime_state)
    condition = _normalize_location_condition(condition)
    condition_key = _location_condition_key(condition)

    items = _safe_list(runtime_state.get("location_conditions"))
    updated = False
    merged: list[dict[str, Any]] = []

    for existing in items:
        existing = _normalize_location_condition(existing)
        if _location_condition_key(existing) == condition_key:
            existing["updated_tick"] = max(_safe_int(existing.get("updated_tick"), 0), _safe_int(condition.get("updated_tick"), 0))
            existing["severity"] = min(10, max(_safe_int(existing.get("severity"), 1), _safe_int(condition.get("severity"), 1)))
            existing["summary"] = _safe_str(condition.get("summary")) or _safe_str(existing.get("summary"))
            existing["status"] = _safe_str(condition.get("status")) or _safe_str(existing.get("status")) or "active"
            existing_tags = set(_safe_list(existing.get("tags")))
            for tag in _safe_list(condition.get("tags")):
                existing_tags.add(_safe_str(tag))
            existing["tags"] = sorted([t for t in existing_tags if t])
            merged.append(existing)
            updated = True
        else:
            merged.append(existing)

    if not updated:
        merged.append(condition)

    runtime_state["location_conditions"] = merged[-_MAX_LOCATION_CONDITIONS:]
    return runtime_state


def _append_world_consequence(runtime_state: dict[str, Any], consequence: dict[str, Any]) -> dict[str, Any]:
    runtime_state = ensure_world_consequence_state(runtime_state)
    consequence = _normalize_world_consequence(consequence)
    consequence_key = _world_consequence_key(consequence)

    items = _safe_list(runtime_state.get("world_consequences"))
    updated = False
    merged: list[dict[str, Any]] = []

    for existing in items:
        existing = _normalize_world_consequence(existing)
        if _world_consequence_key(existing) == consequence_key:
            existing["tick"] = max(_safe_int(existing.get("tick"), 0), _safe_int(consequence.get("tick"), 0))
            existing["priority"] = min(10, max(_safe_int(existing.get("priority"), 1), _safe_int(consequence.get("priority"), 1)))
            existing["summary"] = _safe_str(consequence.get("summary")) or _safe_str(existing.get("summary"))
            existing_tags = set(_safe_list(existing.get("tags")))
            for tag in _safe_list(consequence.get("tags")):
                existing_tags.add(_safe_str(tag))
            existing["tags"] = sorted([t for t in existing_tags if t])
            merged.append(existing)
            updated = True
        else:
            merged.append(existing)

    if not updated:
        merged.append(consequence)

    runtime_state["world_consequences"] = merged[-_MAX_WORLD_CONSEQUENCES:]
    return runtime_state


def _emit_consequence_world_rows(runtime_state: dict[str, Any], consequence: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    rows = _safe_list(runtime_state.get("recent_world_event_rows"))
    consequence = _normalize_world_consequence(consequence)

    event_id = _safe_str(consequence.get("consequence_id"))
    replaced = False
    merged_rows: list[dict[str, Any]] = []

    for row in rows:
        row = _safe_dict(row)
        if _safe_str(row.get("event_id")) == event_id:
            merged_rows.append({
                "event_id": event_id,
                "scope": _safe_str(consequence.get("scope")) or "local",
                "kind": _safe_str(consequence.get("kind")) or "world_consequence",
                "title": "World Consequence",
                "summary": _safe_str(consequence.get("summary")),
                "tick": _safe_int(consequence.get("tick"), 0),
                "actors": [_safe_str(consequence.get("source_actor_id"))] if _safe_str(consequence.get("source_actor_id")) else [],
                "actor_id": _safe_str(consequence.get("source_actor_id")),
                "location_id": _safe_str(consequence.get("location_id")),
                "priority": min(1.0, 0.4 + (0.1 * _safe_int(consequence.get("priority"), 1))),
                "status": "active",
                "source": "consequence_runtime",
            })
            replaced = True
        else:
            merged_rows.append(row)

    if not replaced:
        merged_rows.append({
            "event_id": event_id,
            "scope": _safe_str(consequence.get("scope")) or "local",
            "kind": _safe_str(consequence.get("kind")) or "world_consequence",
            "title": "World Consequence",
            "summary": _safe_str(consequence.get("summary")),
            "tick": _safe_int(consequence.get("tick"), 0),
            "actors": [_safe_str(consequence.get("source_actor_id"))] if _safe_str(consequence.get("source_actor_id")) else [],
            "actor_id": _safe_str(consequence.get("source_actor_id")),
            "location_id": _safe_str(consequence.get("location_id")),
            "priority": min(1.0, 0.4 + (0.1 * _safe_int(consequence.get("priority"), 1))),
            "status": "active",
            "source": "consequence_runtime",
        })

    runtime_state["recent_world_event_rows"] = merged_rows[-_MAX_RECENT_WORLD_EVENT_ROWS:]
    return runtime_state


def propagate_activity_consequences_for_tick(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = ensure_world_consequence_state(runtime_state)
    runtime_state = ensure_actor_activity_state(runtime_state)

    tick = _safe_int(simulation_state.get("tick"), 0)
    actor_activities = _safe_dict(runtime_state.get("actor_activities"))

    for actor_id, activity in sorted(actor_activities.items()):
        activity = _normalize_activity_record(activity)
        if _safe_str(activity.get("status")) != "active":
            continue
        if _safe_int(activity.get("updated_tick"), 0) != tick:
            continue

        kind = _safe_str(activity.get("kind"))
        location_id = _safe_str(activity.get("location_id"))
        summary = _safe_str(activity.get("summary"))
        activity_id = _safe_str(activity.get("activity_id"))

        # Gossip creates rumors
        if kind == "gossip":
            rumor_summary = f"Rumors spread that {summary[:1].lower() + summary[1:]}" if summary else "Rumors spread among the locals."
            rumor = {
                "rumor_id": _stable_consequence_id("rumor", tick, "local", location_id, rumor_summary),
                "summary": rumor_summary,
                "scope": "local",
                "location_id": location_id,
                "source_actor_id": actor_id,
                "source_kind": kind,
                "started_tick": tick,
                "updated_tick": tick,
                "strength": 1,
                "tags": ["rumor", "social"],
            }
            consequence = {
                "consequence_id": _stable_consequence_id("consequence", tick, "local", location_id, rumor_summary),
                "kind": "rumor",
                "scope": "local",
                "location_id": location_id,
                "summary": rumor_summary,
                "source_actor_id": actor_id,
                "source_activity_id": activity_id,
                "tick": tick,
                "priority": 2,
                "tags": ["rumor", "social"],
            }
            runtime_state = _append_world_rumor(runtime_state, rumor)
            runtime_state = _append_world_consequence(runtime_state, consequence)
            runtime_state = _emit_consequence_world_rows(runtime_state, consequence)

        # Patrol / questioning increases security pressure
        elif kind in ("patrol", "watch_crowd", "question_patron"):
            pressure_summary = "The local watch grows more visible and alert."
            pressure = {
                "pressure_id": _stable_consequence_id("pressure", tick, "local", location_id, pressure_summary),
                "kind": "security_presence",
                "scope": "local",
                "location_id": location_id,
                "value": 1,
                "started_tick": tick,
                "updated_tick": tick,
                "summary": pressure_summary,
                "tags": ["security", "watch"],
            }
            consequence = {
                "consequence_id": _stable_consequence_id("consequence", tick, "local", location_id, pressure_summary),
                "kind": "security_pressure",
                "scope": "local",
                "location_id": location_id,
                "summary": pressure_summary,
                "source_actor_id": actor_id,
                "source_activity_id": activity_id,
                "tick": tick,
                "priority": 2,
                "tags": ["security", "watch"],
            }
            runtime_state = _append_world_pressure(runtime_state, pressure)
            runtime_state = _append_world_consequence(runtime_state, consequence)
            runtime_state = _emit_consequence_world_rows(runtime_state, consequence)

        # Trade creates a global market consequence
        elif kind == "trade":
            consequence_summary = "Trade shifts local prices and availability."
            consequence = {
                "consequence_id": _stable_consequence_id("consequence", tick, "global", "trade", consequence_summary),
                "kind": "market_shift",
                "scope": "global",
                "location_id": "",
                "summary": consequence_summary,
                "source_actor_id": actor_id,
                "source_activity_id": activity_id,
                "tick": tick,
                "priority": 2,
                "tags": ["commerce", "market"],
            }
            runtime_state = _append_world_consequence(runtime_state, consequence)
            runtime_state = _emit_consequence_world_rows(runtime_state, consequence)

        # Cleaning / service can improve local condition
        elif kind in ("clean", "serve"):
            cond_summary = "The area feels more orderly and well-kept."
            condition = {
                "condition_id": _stable_consequence_id("condition", tick, "local", location_id, cond_summary),
                "location_id": location_id,
                "kind": "orderly",
                "summary": cond_summary,
                "severity": 1,
                "started_tick": tick,
                "updated_tick": tick,
                "status": "active",
                "tags": ["order", "service"],
            }
            consequence = {
                "consequence_id": _stable_consequence_id("consequence", tick, "local", location_id, cond_summary),
                "kind": "location_condition",
                "scope": "local",
                "location_id": location_id,
                "summary": cond_summary,
                "source_actor_id": actor_id,
                "source_activity_id": activity_id,
                "tick": tick,
                "priority": 1,
                "tags": ["order", "service"],
            }
            runtime_state = _append_location_condition(runtime_state, condition)
            runtime_state = _append_world_consequence(runtime_state, consequence)
            runtime_state = _emit_consequence_world_rows(runtime_state, consequence)

    return runtime_state


def decay_world_consequences_for_tick(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = ensure_world_consequence_state(runtime_state)
    tick = _safe_int(simulation_state.get("tick"), 0)

    # Rumors decay by strength, then disappear
    rumors_out: list[dict[str, Any]] = []
    for rumor in _safe_list(runtime_state.get("world_rumors")):
        rumor = _normalize_world_rumor(rumor)
        age = tick - _safe_int(rumor.get("updated_tick"), 0)
        strength = _safe_int(rumor.get("strength"), 1)
        if age >= _WORLD_RUMOR_DECAY_TICKS:
            strength -= 1
        if strength > 0:
            rumor["strength"] = strength
            rumors_out.append(rumor)
    runtime_state["world_rumors"] = rumors_out[-_MAX_WORLD_RUMORS:]

    # Pressure decays by value, then disappears
    pressure_out: list[dict[str, Any]] = []
    for pressure in _safe_list(runtime_state.get("world_pressure")):
        pressure = _normalize_pressure_record(pressure)
        age = tick - _safe_int(pressure.get("updated_tick"), 0)
        value = _safe_int(pressure.get("value"), 0)
        if age >= _WORLD_PRESSURE_DECAY_TICKS:
            value -= 1
        if value > 0:
            pressure["value"] = value
            pressure_out.append(pressure)
    runtime_state["world_pressure"] = pressure_out[-_MAX_WORLD_PRESSURE:]

    # Location conditions cool and eventually resolve
    condition_out: list[dict[str, Any]] = []
    for condition in _safe_list(runtime_state.get("location_conditions")):
        condition = _normalize_location_condition(condition)
        age = tick - _safe_int(condition.get("updated_tick"), 0)
        severity = _safe_int(condition.get("severity"), 1)
        if age >= _LOCATION_CONDITION_DECAY_TICKS:
            severity -= 1
        if severity > 0:
            condition["severity"] = severity
            condition_out.append(condition)
    runtime_state["location_conditions"] = condition_out[-_MAX_LOCATION_CONDITIONS:]

    # Consequences fade out of active memory if stale
    consequence_out: list[dict[str, Any]] = []
    for consequence in _safe_list(runtime_state.get("world_consequences")):
        consequence = _normalize_world_consequence(consequence)
        age = tick - _safe_int(consequence.get("tick"), 0)
        if age < _WORLD_CONSEQUENCE_DECAY_TICKS:
            consequence_out.append(consequence)
    runtime_state["world_consequences"] = consequence_out[-_MAX_WORLD_CONSEQUENCES:]

    return runtime_state


def emit_scene_beat(
    runtime_state: dict[str, Any],
    *,
    tick: int,
    summary: str,
    kind: str = "scene_beat",
    priority: int = 50,
    scene_id: str = "",
    interaction_id: str = "",
    actors: list[str] | None = None,
    location_id: str = "",
    recap_level: str = "notable",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    runtime_state = _ensure_recent_scene_beats(runtime_state)
    beat = _normalize_scene_beat(
        {
            "tick": tick,
            "kind": kind,
            "summary": summary,
            "priority": priority,
            "scene_id": scene_id,
            "interaction_id": interaction_id,
            "actors": actors or [],
            "location_id": location_id,
            "recap_level": recap_level,
            "tags": tags or [],
        }
    )
    if not beat["summary"]:
        return runtime_state
    if beat["recap_level"] not in ("notable", "major"):
        return runtime_state
    beats = _safe_list(runtime_state.get("recent_scene_beats"))
    beats.append(beat)
    runtime_state["recent_scene_beats"] = beats
    return _ensure_recent_scene_beats(runtime_state)


def _stable_state_change_event_id(event: dict[str, Any]) -> str:
    payload = {
        "tick": int(_safe_dict(event).get("tick", 0) or 0),
        "actor_id": _safe_str(_safe_dict(event).get("actor_id")),
        "semantic_action": _safe_str(_safe_dict(event).get("semantic_action")),
        "summary": _safe_str(_safe_dict(event).get("summary")),
        "location_id": _safe_str(_safe_dict(event).get("location_id")),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "state_change_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _normalize_semantic_state_change_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    proposal = _safe_dict(proposal)
    delta = _safe_dict(proposal.get("delta"))
    out = {
        "proposal_id": _safe_str(proposal.get("proposal_id") or proposal.get("id")),
        "actor_id": _safe_str(proposal.get("actor_id")),
        "proposal_kind": _safe_str(proposal.get("proposal_kind")) or "state_delta",
        "semantic_action": _safe_str(proposal.get("semantic_action")),
        "target_id": _safe_str(proposal.get("target_id")),
        "target_location_id": _safe_str(proposal.get("target_location_id")),
        "summary": _safe_str(proposal.get("summary")),
        "beat_summary": _safe_str(proposal.get("beat_summary")),
        "priority": int(proposal.get("priority", 50) or 50),
        "delta": {
            "activity": _safe_str(delta.get("activity")),
            "availability": _safe_str(delta.get("availability")),
            "location_id": _safe_str(delta.get("location_id")),
            "mood": _safe_str(delta.get("mood")),
            "intent": _safe_str(delta.get("intent")),
            "engagement": _safe_str(delta.get("engagement")),
        },
        "tags": [_safe_str(x) for x in _safe_list(proposal.get("tags")) if _safe_str(x)],
        "source": _safe_str(proposal.get("source")) or "llm",
    }
    if not out["proposal_id"]:
        raw = json.dumps(
            {
                "actor_id": out["actor_id"],
                "semantic_action": out["semantic_action"],
                "delta": out["delta"],
                "summary": out["summary"],
                "target_location_id": out["target_location_id"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        out["proposal_id"] = "proposal_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return out


def _stable_semantic_state_change_proposal_id(
    proposal: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> str:
    """
    Build a deterministic per-tick proposal identity.

    IMPORTANT:
    - proposal_id must NOT be a constant like "llm_<actor_id>"
    - otherwise applied_proposal_ids suppress all future proposals for that actor
    - identity must vary across ticks / payload changes but remain deterministic
    """
    proposal = _normalize_semantic_state_change_proposal(proposal)
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    tick = int(
        simulation_state.get("current_tick", 0)
        or simulation_state.get("tick", 0)
        or runtime_state.get("tick", 0)
        or 0
    )
    payload = {
        "tick": tick,
        "actor_id": _safe_str(proposal.get("actor_id")),
        "semantic_action": _safe_str(proposal.get("semantic_action")),
        "summary": _safe_str(proposal.get("summary")),
        "beat_summary": _safe_str(proposal.get("beat_summary")),
        "target_id": _safe_str(proposal.get("target_id")),
        "target_location_id": _safe_str(proposal.get("target_location_id")),
        "delta": _safe_dict(proposal.get("delta")),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "semantic_proposal_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _ensure_semantic_pipeline_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    from app.apps.rpg.session.combat_intent import (
        ensure_ambient_runtime_state as ensure_ambient_runtime_state,
    )

    runtime_state = ensure_ambient_runtime_state(_safe_dict(runtime_state))
    runtime_state = ensure_actor_activity_state(runtime_state)
    runtime_state = ensure_world_consequence_state(runtime_state)
    runtime_state.setdefault("semantic_state_change_proposals", [])
    runtime_state.setdefault("accepted_state_change_events", [])
    runtime_state.setdefault("rejected_state_change_events", [])
    runtime_state.setdefault("applied_semantic_proposal_ids", [])
    runtime_state.setdefault("last_semantic_llm_tick", -999999)
    runtime_state.setdefault("recorded_semantic_llm_proposals", [])
    runtime_state.setdefault("recorded_semantic_llm_prompt", "")
    runtime_state.setdefault("recorded_semantic_llm_raw_output", "")
    runtime_state.setdefault("recorded_semantic_llm_capture_tick", -999999)
    runtime_state["semantic_state_change_proposals"] = _safe_list(
        runtime_state.get("semantic_state_change_proposals")
    )[-_MAX_SEMANTIC_PROPOSALS:]
    runtime_state["accepted_state_change_events"] = _safe_list(
        runtime_state.get("accepted_state_change_events")
    )[-_MAX_ACCEPTED_STATE_CHANGE_EVENTS:]
    runtime_state["rejected_state_change_events"] = _safe_list(
        runtime_state.get("rejected_state_change_events")
    )[-_MAX_ACCEPTED_STATE_CHANGE_EVENTS:]
    runtime_state["applied_semantic_proposal_ids"] = [
        _safe_str(x) for x in _safe_list(runtime_state.get("applied_semantic_proposal_ids")) if _safe_str(x)
    ][-_MAX_APPLIED_PROPOSAL_IDS:]
    runtime_state["recorded_semantic_llm_proposals"] = [
        _normalize_semantic_state_change_proposal(x)
        for x in _safe_list(runtime_state.get("recorded_semantic_llm_proposals"))
        if _safe_dict(x)
    ][-_MAX_RECORDED_SEMANTIC_LLM_PROPOSALS:]
    runtime_state["recorded_semantic_llm_prompt"] = _safe_str(runtime_state.get("recorded_semantic_llm_prompt"))
    runtime_state["recorded_semantic_llm_raw_output"] = _safe_str(runtime_state.get("recorded_semantic_llm_raw_output"))
    runtime_state["recorded_semantic_llm_capture_tick"] = _safe_int(runtime_state.get("recorded_semantic_llm_capture_tick", -999999), -999999)
    runtime_state["last_semantic_llm_tick"] = _safe_int(runtime_state.get("last_semantic_llm_tick", -999999), -999999)
    return runtime_state


def _accepted_state_change_event_ids(runtime_state: dict[str, Any]) -> set[str]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    ids = set()
    for item in _safe_list(runtime_state.get("accepted_state_change_events")):
        event_id = _safe_str(_safe_dict(item).get("event_id"))
        if event_id:
            ids.add(event_id)
    return ids


def _applied_semantic_proposal_ids(runtime_state: dict[str, Any]) -> set[str]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    ids = set()
    for item in _safe_list(runtime_state.get("applied_semantic_proposal_ids")):
        proposal_id = _safe_str(item)
        if proposal_id:
            ids.add(proposal_id)
    return ids


def _safe_actor_states(simulation_state: dict[str, Any]) -> list[dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    if npc_index:
        derived: list[dict[str, Any]] = []
        for npc_id, npc in npc_index.items():
            npc = _safe_dict(npc)
            actor_id = _safe_str(npc_id)
            if not actor_id:
                continue
            derived.append(
                {
                    "id": actor_id,
                    "name": _safe_str(npc.get("name")) or actor_id,
                    "location_id": _safe_str(npc.get("location_id")),
                    "activity": _safe_str(npc.get("activity")),
                    "availability": _safe_str(npc.get("availability")),
                    "mood": _safe_str(npc.get("mood")),
                    "intent": _safe_str(npc.get("intent")),
                    "engagement": _safe_str(npc.get("engagement")),
                }
            )
        return derived
    actor_states = _safe_list(simulation_state.get("actor_states"))
    if actor_states:
        return [_safe_dict(x) for x in actor_states if _safe_dict(x)]
    npc_states = _safe_list(simulation_state.get("npc_states"))
    return [_safe_dict(x) for x in npc_states if _safe_dict(x)]


def _write_actor_states(simulation_state: dict[str, Any], actor_states: list[dict[str, Any]]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    actor_states = [ _safe_dict(x) for x in _safe_list(actor_states) ]
    simulation_state["actor_states"] = actor_states

    # Preserve backward compatibility for npc_states only when that projection
    # already exists. Do not blindly force every actor state into npc_states.
    if "npc_states" in simulation_state:
        npc_ids = {
            _safe_str(_safe_dict(x).get("id"))
            for x in _safe_list(simulation_state.get("npc_states"))
            if _safe_str(_safe_dict(x).get("id"))
        }
        simulation_state["npc_states"] = [
            _safe_dict(x) for x in actor_states if _safe_str(_safe_dict(x).get("id")) in npc_ids
        ]
    return simulation_state


def _find_actor_state(actor_states: list[dict[str, Any]], actor_id: str) -> dict[str, Any]:
    actor_id = _safe_str(actor_id)
    for actor in _safe_list(actor_states):
        actor = _safe_dict(actor)
        if _safe_str(actor.get("id")) == actor_id:
            return actor
    return {}


def _normalize_actor_state_for_delta(actor: dict[str, Any]) -> dict[str, Any]:
    actor = _safe_dict(actor)
    return {
        "id": _safe_str(actor.get("id")),
        "name": _safe_str(actor.get("name")),
        "activity": _safe_str(actor.get("activity")),
        "availability": _safe_str(actor.get("availability")),
        "location_id": _safe_str(actor.get("location_id")),
        "mood": _safe_str(actor.get("mood")),
        "intent": _safe_str(actor.get("intent")),
        "engagement": _safe_str(actor.get("engagement")),
    }


def _allowed_semantic_actions() -> dict[str, dict[str, str]]:
    return {
        "take_break": {"activity": "on_break", "availability": "temporarily_unavailable"},
        "wash_up": {"activity": "washing_up", "availability": "occupied"},
        "rest": {"activity": "resting", "availability": "temporarily_unavailable"},
        "investigate": {"activity": "investigating"},
        "argue": {"activity": "arguing", "engagement": "active"},
        "leave_scene": {"activity": "departing", "availability": "unavailable"},
        "return_to_scene": {"activity": "present", "availability": "available"},
    }


def record_semantic_llm_capture(
    runtime_state: dict[str, Any],
    simulation_state: dict[str, Any],
    *,
    prompt: str,
    raw_output: Any,
    proposals: list[dict[str, Any]],
    tick: int,
) -> dict[str, Any]:

    from app.apps.rpg.session.semantic_state_changes import (
        _normalize_llm_text_output as _normalize_llm_text_output,
    )

    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    normalized = [
        _normalize_semantic_state_change_proposal(x)
        for x in _safe_list(proposals)
        if _safe_dict(x)
    ][-_MAX_RECORDED_SEMANTIC_LLM_PROPOSALS:]

    # Hard filter: only allow actors in active interactions if any exist
    interactions = _normalize_active_interactions(simulation_state, runtime_state)
    if interactions:
        allowed_actor_ids = set()
        for i in interactions:
            for p in i.get("participants") or []:
                allowed_actor_ids.add(_safe_str(p))
        if allowed_actor_ids:
            normalized = [p for p in normalized if p.get("actor_id") in allowed_actor_ids]

    # Fallback: if no proposals, keep one actor active to prevent dead world
    if not normalized:
        actor_states = _safe_actor_states(simulation_state)
        if actor_states:
            actor = actor_states[0]
            normalized = [{
                "actor_id": actor.get("id"),
                "proposal_kind": "state_delta",
                "semantic_action": "continue_activity",
                "delta": {
                    "activity": _safe_str(actor.get("activity")) or "active",
                    "engagement": "ongoing"
                },
                "beat_summary": f"{_safe_str(actor.get('name'))} continues their current activity."
            }]

    runtime_state["recorded_semantic_llm_prompt"] = _safe_str(prompt)
    runtime_state["recorded_semantic_llm_raw_output"] = _normalize_llm_text_output(raw_output)
    runtime_state["recorded_semantic_llm_proposals"] = normalized[:_MAX_RECORDED_SEMANTIC_LLM_PROPOSALS]
    runtime_state["recorded_semantic_llm_capture_tick"] = int(tick or 0)
    return runtime_state


def clear_recorded_semantic_llm_capture(runtime_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    runtime_state["recorded_semantic_llm_proposals"] = []
    return runtime_state


def _build_location_id_index(simulation_state: dict[str, Any]) -> set[str]:
    simulation_state = _safe_dict(simulation_state)
    ids = {
        _safe_str(x.get("id"))
        for x in _safe_list(simulation_state.get("locations"))
        if isinstance(x, dict) and _safe_str(x.get("id"))
    }
    scene_location = _safe_str(simulation_state.get("location_id"))
    if scene_location:
        ids.add(scene_location)
    return ids


def _canonical_delta_has_values(delta: dict[str, Any]) -> bool:
    delta = _safe_dict(delta)
    return any(
        _safe_str(delta.get(key))
        for key in ("activity", "availability", "location_id", "mood", "intent", "engagement")
    )


def enqueue_semantic_state_change_proposal(runtime_state: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    proposal = _normalize_semantic_state_change_proposal(proposal)
    items = _safe_list(runtime_state.get("semantic_state_change_proposals"))
    # Do not allow empty / constant IDs to poison proposal replay suppression.
    # Proposal IDs should already be stamped upstream with a deterministic
    # per-tick identity, but preserve any explicit ID here.
    proposal_id = _safe_str(proposal.get("proposal_id"))
    if not proposal_id:
        proposal = dict(proposal)
        proposal["proposal_id"] = "semantic_proposal_" + hashlib.sha1(
            json.dumps(proposal, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]
    items.append(proposal)
    runtime_state["semantic_state_change_proposals"] = items[-_MAX_SEMANTIC_PROPOSALS:]
    return runtime_state


def validate_semantic_state_change_proposal(
    proposal: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    proposal = _normalize_semantic_state_change_proposal(proposal)
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)

    errors: list[str] = []
    if proposal["proposal_kind"] != "state_delta":
        errors.append("unsupported_proposal_kind")
    if not proposal["actor_id"]:
        errors.append("missing_actor_id")

    actor_states = _safe_actor_states(simulation_state)
    actor = _find_actor_state(actor_states, proposal["actor_id"])
    if not actor:
        known_ids = [str(_safe_dict(x).get("id") or "").strip() for x in actor_states if _safe_dict(x)]

        errors.append("unknown_actor")

    delta = _safe_dict(proposal.get("delta"))
    semantic_action = _safe_str(proposal.get("semantic_action"))
    known_actions = _allowed_semantic_actions()
    if semantic_action and semantic_action not in known_actions:
        # Open-ended actions are allowed only if they already compile to a canonical bounded delta.
        if not any(_safe_str(delta.get(k)) for k in ("activity", "availability", "location_id", "mood", "intent", "engagement")):
            errors.append("uncompilable_semantic_action")

    if proposal["target_location_id"]:
        valid_location_ids = _build_location_id_index(simulation_state)
        if not valid_location_ids:
            actor_location = _safe_str(actor.get("location_id"))
            if not actor_location:
                errors.append("unvalidated_target_location")
        elif proposal["target_location_id"] not in valid_location_ids:
            errors.append("invalid_target_location")

    if not semantic_action and not _canonical_delta_has_values(delta):
        errors.append("empty_state_delta")

    active_interactions = _normalize_active_interactions(simulation_state, runtime_state)
    for interaction in active_interactions:
        participants = [_safe_str(x) for x in _safe_list(interaction.get("participants")) if _safe_str(x)]
        if proposal["actor_id"] in participants and not bool(interaction.get("resolved")):
            if semantic_action in ("take_break", "wash_up", "leave_scene"):
                errors.append("actor_locked_in_active_interaction")
                break

    return {
        "ok": not errors,
        "errors": errors,
        "proposal": proposal,
        "actor_before": _normalize_actor_state_for_delta(actor),
    }

__all__ = ['_LOCATION_CONDITION_DECAY_TICKS', '_MAX_LOCATION_CONDITIONS', '_MAX_RECORDED_SEMANTIC_LLM_PROPOSALS', '_MAX_SEMANTIC_PROPOSALS', '_MAX_WORLD_CONSEQUENCES', '_MAX_WORLD_PRESSURE', '_MAX_WORLD_RUMORS', '_WORLD_CONSEQUENCE_DECAY_TICKS', '_WORLD_PRESSURE_DECAY_TICKS', '_WORLD_RUMOR_DECAY_TICKS', '_accepted_state_change_event_ids', '_allowed_semantic_actions', '_append_location_condition', '_append_world_consequence', '_append_world_pressure', '_append_world_rumor', '_applied_semantic_proposal_ids', '_build_location_id_index', '_canonical_delta_has_values', '_emit_consequence_world_rows', '_ensure_semantic_pipeline_state', '_find_actor_state', '_normalize_actor_state_for_delta', '_normalize_semantic_state_change_proposal', '_safe_actor_states', '_stable_semantic_state_change_proposal_id', '_stable_state_change_event_id', '_write_actor_states', 'clear_recorded_semantic_llm_capture', 'decay_world_consequences_for_tick', 'emit_scene_beat', 'enqueue_semantic_state_change_proposal', 'propagate_activity_consequences_for_tick', 'record_semantic_llm_capture', 'validate_semantic_state_change_proposal']