from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _ensure_semantic_action_runtime_state as _ensure_semantic_action_runtime_state, _normalize_social_axes as _normalize_social_axes,
    _safe_bool as _safe_bool, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _MAX_INTERACTION_REACTION_STATE as _MAX_INTERACTION_REACTION_STATE, _MAX_NPC_REACTION_RECORDS as _MAX_NPC_REACTION_RECORDS, _MAX_RECENT_SCENE_BEATS as _MAX_RECENT_SCENE_BEATS,
    _MAX_RECENT_WORLD_EVENT_ROWS as _MAX_RECENT_WORLD_EVENT_ROWS, _MAX_RUNTIME_LLM_RECORDS as _MAX_RUNTIME_LLM_RECORDS,
    _MAX_SEMANTIC_ACTION_RECORDS as _MAX_SEMANTIC_ACTION_RECORDS, _MAX_WORLD_CONSEQUENCES as _MAX_WORLD_CONSEQUENCES, _MAX_WORLD_PRESSURE as _MAX_WORLD_PRESSURE,
    _MAX_WORLD_RUMORS as _MAX_WORLD_RUMORS,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _dialogue_semantic_action_from_player_input as _dialogue_semantic_action_from_player_input,
)
import json as json
import hashlib as hashlib
from typing import (
    Any as Any, Dict as Dict, List as List,
)


def _clean_resolved_interaction_world_event_rows(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    """Remove recent_world_event_rows that reference resolved interactions."""
    resolved_labels: set[str] = set()
    resolved_ids: set[str] = set()
    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        if not _safe_bool(item.get("resolved"), False):
            continue
        state = _safe_dict(item.get("state"))
        label = _safe_str(state.get("activity_label") or item.get("subtype") or "").strip().lower()
        if label:
            resolved_labels.add(label)
        iid = _safe_str(item.get("id")).strip()
        if iid:
            resolved_ids.add(iid)
        sid = _safe_str(item.get("semantic_action_id")).strip()
        if sid:
            resolved_ids.add(sid)

    if not resolved_labels and not resolved_ids:
        return runtime_state

    def _row_references_resolved(row: dict[str, Any]) -> bool:
        row = _safe_dict(row)
        eid = _safe_str(row.get("event_id")).strip().lower()
        row_id = _safe_str(row.get("reaction_id") or row.get("id")).strip().lower()
        summary_lower = _safe_str(row.get("summary")).strip().lower()
        # Normalize separators for matching
        summary_normalized = summary_lower.replace("-", " ").replace("_", " ")
        for label in resolved_labels:
            label_normalized = label.replace("_", " ").replace("-", " ")
            if label_normalized and (label_normalized in summary_lower or label_normalized in summary_normalized):
                return True
        for resolved_id in resolved_ids:
            resolved_lower = resolved_id.lower()
            if resolved_lower and (resolved_lower in eid or resolved_lower in row_id):
                return True
        return False

    # Clean recent_world_event_rows
    rows = _safe_list(runtime_state.get("recent_world_event_rows"))
    kept: list[dict[str, Any]] = []
    for row in rows:
        row = _safe_dict(row)
        kind = _safe_str(row.get("kind")).strip().lower()
        # Skip non-interaction row kinds — always keep simulation/global rows
        if kind in ("world_event", "director_pressure"):
            kept.append(row)
            continue
        if _row_references_resolved(row):
            continue
        kept.append(row)
    runtime_state["recent_world_event_rows"] = kept[-_MAX_RECENT_WORLD_EVENT_ROWS:]

    # Clean recent_scene_beats
    beats = _safe_list(runtime_state.get("recent_scene_beats"))
    kept_beats: list[dict[str, Any]] = []
    for beat in beats:
        beat = _safe_dict(beat)
        if _row_references_resolved(beat):
            continue
        kept_beats.append(beat)
    runtime_state["recent_scene_beats"] = kept_beats[-_MAX_RECENT_SCENE_BEATS:]

    # Clean world_consequences
    consequences = _safe_list(runtime_state.get("world_consequences"))
    kept_consequences: list[dict[str, Any]] = []
    for c in consequences:
        if not _row_references_resolved(_safe_dict(c)):
            kept_consequences.append(c)
    runtime_state["world_consequences"] = kept_consequences[-_MAX_WORLD_CONSEQUENCES:]

    # Clean world_rumors
    rumors = _safe_list(runtime_state.get("world_rumors"))
    kept_rumors: list[dict[str, Any]] = []
    for r in rumors:
        if not _row_references_resolved(_safe_dict(r)):
            kept_rumors.append(r)
    runtime_state["world_rumors"] = kept_rumors[-_MAX_WORLD_RUMORS:]

    # Clean world_pressure
    pressure = _safe_list(runtime_state.get("world_pressure"))
    kept_pressure: list[dict[str, Any]] = []
    for p in pressure:
        if not _row_references_resolved(_safe_dict(p)):
            kept_pressure.append(p)
    runtime_state["world_pressure"] = kept_pressure[-_MAX_WORLD_PRESSURE:]

    # Clean npc_reaction_records tied to resolved interactions
    reaction_records = _safe_list(runtime_state.get("npc_reaction_records"))
    kept_reaction_records: list[dict[str, Any]] = []
    for record in reaction_records:
        record = _safe_dict(record)
        interaction_id = _safe_str(record.get("interaction_id")).strip()
        if interaction_id and interaction_id in resolved_ids:
            continue
        kept_reaction_records.append(record)
    runtime_state["npc_reaction_records"] = kept_reaction_records[-_MAX_NPC_REACTION_RECORDS:]

    # Clean escalation state tied to resolved interactions
    reaction_state_rows = _safe_list(runtime_state.get("interaction_reaction_state"))
    kept_reaction_state_rows: list[dict[str, Any]] = []
    for row in reaction_state_rows:
        row = _safe_dict(row)
        interaction_id = _safe_str(row.get("interaction_id")).strip()
        if interaction_id and interaction_id in resolved_ids:
            continue
        kept_reaction_state_rows.append(row)
    runtime_state["interaction_reaction_state"] = kept_reaction_state_rows[-_MAX_INTERACTION_REACTION_STATE:]

    return runtime_state


def _prune_llm_records_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _copy_dict(runtime_state)
    records = _safe_list(runtime_state.get("llm_records"))[-_MAX_RUNTIME_LLM_RECORDS:]
    new_index: dict[str, Any] = {}
    for item in records:
        item = _safe_dict(item)
        record_type = _safe_str(item.get("type")).strip()
        tick = _safe_int(item.get("tick"), -1)
        if tick < 0 or not record_type:
            continue
        new_index[f"{record_type}:{tick}"] = item
    runtime_state["llm_records"] = records
    runtime_state["llm_records_index"] = new_index
    return runtime_state


def _stable_semantic_action_id(tick: int, player_input: str, action_type: str, target_id: str, activity_label: str) -> str:
    material = json.dumps(
        {
            "tick": int(tick or 0),
            "player_input": _safe_str(player_input).strip(),
            "action_type": _safe_str(action_type).strip(),
            "target_id": _safe_str(target_id).strip(),
            "activity_label": _safe_str(activity_label).strip(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "semantic_action_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _find_npc_target_by_name(simulation_state: dict[str, Any], text: str) -> str:
    simulation_state = _safe_dict(simulation_state)
    npc_index = _safe_dict(simulation_state.get("npc_index"))
    text_lc = _safe_str(text).strip().lower()
    if not text_lc:
        return ""

    candidates: list[tuple[str, str]] = []
    for npc_id, raw in sorted(npc_index.items()):
        npc = _safe_dict(raw)
        name = _safe_str(npc.get("name")).strip().lower()
        role = _safe_str(npc.get("role")).strip().lower()
        title = _safe_str(npc.get("title")).strip().lower()
        stable_id = _safe_str(npc.get("id") or npc_id)
        if stable_id and name:
            candidates.append((stable_id, name))
        if stable_id and role:
            candidates.append((stable_id, role))
        if stable_id and title:
            candidates.append((stable_id, title))

    candidates.sort(key=lambda item: (-len(item[1]), item[1], item[0]))
    for npc_id, npc_name in candidates:
        if npc_name in text_lc:
            return npc_id
    return ""


def _coerce_action_target(simulation_state: dict[str, Any], action: dict[str, Any], player_input: str) -> dict[str, Any]:
    action = _safe_dict(action)
    target_id = _safe_str(action.get("target_id") or action.get("npc_id")).strip()
    if not target_id:
        target_id = _find_npc_target_by_name(simulation_state, player_input)
    if target_id and not _safe_str(action.get("target_id")).strip():
        action["target_id"] = target_id
    return action


def _coerce_action_target_to_active_combat_participant(
    runtime_state: dict[str, Any],
    action: dict[str, Any],
    player_input: str,
) -> dict[str, Any]:
    from app.apps.rpg.session.semantic_state_changes import (
        _get_combat_state as _get_combat_state,
    )

    action = dict(_safe_dict(action))
    combat_state = _safe_dict(_get_combat_state(runtime_state))
    if not combat_state.get("active"):
        return action

    participants = _safe_dict(combat_state.get("participants"))
    if not participants:
        return action

    current_target_id = _safe_str(action.get("target_id")).strip()
    if current_target_id in participants:
        return action

    text = _safe_str(player_input).strip().lower()
    target_hint = _safe_str(
        action.get("target_name")
        or action.get("target_ref")
        or current_target_id
    ).strip().lower()

    def norm(value: Any) -> str:
        return (
            _safe_str(value)
            .strip()
            .lower()
            .replace("enemy:", "")
            .replace("npc:", "")
            .replace(":", " ")
            .replace("_", " ")
            .replace("-", " ")
        )

    wanted = norm(target_hint or text)

    for actor_id, participant in participants.items():
        participant = _safe_dict(participant)
        if _safe_str(participant.get("side")).strip().lower() != "enemy":
            continue
        if _safe_int(participant.get("hp"), 0) <= 0:
            continue

        names = [
            actor_id,
            participant.get("actor_id"),
            participant.get("name"),
            participant.get("archetype_id"),
        ]

        for name in names:
            n = norm(name)
            if n and (n in wanted or wanted in n or n in norm(text)):
                action["target_id"] = str(actor_id)
                action["target_name"] = _safe_str(participant.get("name") or actor_id)
                return action

    # Safe active-combat fallback: first living enemy.
    if not current_target_id:
        for actor_id, participant in participants.items():
            participant = _safe_dict(participant)
            if _safe_str(participant.get("side")).strip().lower() == "enemy" and _safe_int(participant.get("hp"), 0) > 0:
                action["target_id"] = str(actor_id)
                action["target_name"] = _safe_str(participant.get("name") or actor_id)
                return action

    return action


def _compile_semantic_action_record(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    player_input: str,
    action: dict[str, Any],
    semantic_advisory: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    action = _safe_dict(action)
    semantic_advisory = _safe_dict(semantic_advisory)
    dialogue_semantic = _safe_dict(_dialogue_semantic_action_from_player_input(player_input))

    if dialogue_semantic:
        semantic_advisory = {
            **semantic_advisory,
            **dialogue_semantic,
        }
        action = {
            **action,
            "action_type": _safe_str(dialogue_semantic.get("action_type")) or _safe_str(action.get("action_type")),
            "target_id": _safe_str(dialogue_semantic.get("target_id")) or _safe_str(action.get("target_id")),
            "target_name": _safe_str(dialogue_semantic.get("target_name")) or _safe_str(action.get("target_name")),
        }

    tick = int(simulation_state.get("tick", runtime_state.get("tick", 0)) or 0) + 1
    player_state = _safe_dict(simulation_state.get("player_state"))
    current_scene = _safe_dict(runtime_state.get("current_scene"))

    target_id = _safe_str(semantic_advisory.get("target_id")).strip()
    if not target_id:
        target_id = _safe_str(action.get("target_id")).strip()
    if not target_id:
        target_id = _find_npc_target_by_name(simulation_state, player_input)

    npc_index = _safe_dict(simulation_state.get("npc_index"))
    if target_id and target_id not in npc_index:
        target_name_hint = _safe_str(
            semantic_advisory.get("target_name")
            or action.get("target_name")
            or dialogue_semantic.get("target_name")
        ).strip()
        normalized_target_id = _find_npc_target_by_name(
            simulation_state,
            target_name_hint or player_input,
        )
        if normalized_target_id:
            target_id = normalized_target_id
    target_npc = _safe_dict(npc_index.get(target_id))
    target_name = _safe_str(
        semantic_advisory.get("target_name")
        or target_npc.get("name")
        or action.get("target_name")
        or target_id
    ).strip()

    action_type = _safe_str(semantic_advisory.get("action_type") or action.get("action_type")).strip().lower() or "observe"
    semantic_family = _safe_str(semantic_advisory.get("semantic_family")).strip().lower() or "observation"
    interaction_mode = _safe_str(semantic_advisory.get("interaction_mode")).strip().lower() or ("direct" if target_id else "solo")
    activity_label = _safe_str(semantic_advisory.get("activity_label")).strip().lower().replace(" ", "_") or action_type
    visibility = _safe_str(semantic_advisory.get("visibility")).strip().lower() or "local"
    intensity = max(0, min(3, _safe_int(semantic_advisory.get("intensity"), 1)))
    stakes = max(0, min(3, _safe_int(semantic_advisory.get("stakes"), 1)))
    social_axes = _normalize_social_axes(_safe_list(semantic_advisory.get("social_axes")))
    observer_hooks = [str(x).strip().lower() for x in _safe_list(semantic_advisory.get("observer_hooks")) if str(x).strip()][:4]
    scene_impact = _safe_str(semantic_advisory.get("scene_impact")).strip().lower() or "none"

    location_id = _safe_str(
        current_scene.get("location_id")
        or player_state.get("location_id")
        or _safe_dict(target_npc).get("location_id")
    )

    semantic_action_id = _stable_semantic_action_id(
        tick=tick,
        player_input=player_input,
        action_type=action_type,
        target_id=target_id,
        activity_label=activity_label,
    )

    summary_parts = []
    if target_name:
        summary_parts.append(target_name)
    if activity_label:
        summary_parts.append(activity_label.replace("_", " "))
    summary = " / ".join(summary_parts).strip() or _safe_str(player_input).strip()[:120]

    return {
        "semantic_action_id": semantic_action_id,
        "tick": tick,
        "player_input": _safe_str(player_input).strip(),
        "semantic_action": activity_label or action_type,
        "action_type": action_type,
        "semantic_family": semantic_family,
        "interaction_mode": interaction_mode,
        "activity_label": activity_label,
        "target_id": target_id,
        "target_name": target_name,
        "secondary_actor_ids": [str(x).strip() for x in _safe_list(semantic_advisory.get("secondary_actor_ids")) if str(x).strip()][:4],
        "location_id": location_id,
        "visibility": visibility,
        "intensity": intensity,
        "stakes": stakes,
        "social_axes": social_axes,
        "observer_hooks": observer_hooks,
        "scene_impact": scene_impact,
        "reason": _safe_str(semantic_advisory.get("reason")).strip()[:200],
        "summary": summary[:160],
        "tags": sorted(list({
            "player_action",
            semantic_family or "semantic",
            action_type or "action",
            activity_label or "activity",
        })),
    }


def _append_simulation_semantic_event(simulation_state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    record = _safe_dict(record)
    if not record:
        return simulation_state

    event_history = _safe_list(simulation_state.get("event_history"))
    tick = _safe_int(record.get("tick"), 0)
    event_id = f"semantic_event:{_safe_str(record.get('semantic_action_id'))}"
    for existing in event_history:
        existing = _safe_dict(existing)
        if _safe_str(existing.get("id")) == event_id:
            return simulation_state
    event = {
        "id": event_id,
        "tick": tick,
        "type": "player_semantic_action",
        "category": "player_action",
        "source": "semantic_action_bridge",
        "location_id": _safe_str(record.get("location_id")),
        "actor_ids": ["player"] + ([_safe_str(record.get("target_id"))] if _safe_str(record.get("target_id")) else []),
        "payload": {
            "semantic_action_id": _safe_str(record.get("semantic_action_id")),
            "action_type": _safe_str(record.get("action_type")),
            "semantic_family": _safe_str(record.get("semantic_family")),
            "interaction_mode": _safe_str(record.get("interaction_mode")),
            "activity_label": _safe_str(record.get("activity_label")),
            "target_id": _safe_str(record.get("target_id")),
            "target_name": _safe_str(record.get("target_name")),
            "visibility": _safe_str(record.get("visibility")),
            "intensity": _safe_int(record.get("intensity"), 1),
            "stakes": _safe_int(record.get("stakes"), 1),
            "social_axes": _safe_list(record.get("social_axes")),
            "observer_hooks": _safe_list(record.get("observer_hooks")),
            "scene_impact": _safe_str(record.get("scene_impact")),
            "summary": _safe_str(record.get("summary")),
            "tags": _safe_list(record.get("tags")),
        },
    }
    event_history.append(event)
    simulation_state["event_history"] = event_history[-256:]
    return simulation_state


def _append_semantic_action_record(runtime_state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _ensure_semantic_action_runtime_state(runtime_state)
    record = _safe_dict(record)
    items = _safe_list(runtime_state.get("semantic_action_records"))
    index = _safe_dict(runtime_state.get("semantic_action_index"))
    record_id = _safe_str(record.get("semantic_action_id")).strip()
    if not record_id:
        return runtime_state
    if record_id in index:
        return runtime_state
    items.append(record)
    index[record_id] = record
    runtime_state["semantic_action_records"] = items[-_MAX_SEMANTIC_ACTION_RECORDS:]
    runtime_state["semantic_action_index"] = index
    return runtime_state


def _semantic_activity_kind(record: dict[str, Any]) -> str:
    record = _safe_dict(record)
    action_type = _safe_str(record.get("action_type"))
    semantic_family = _safe_str(record.get("semantic_family"))
    if action_type == "social_competition":
        return "player_social_competition"
    if action_type == "social_affection":
        return "player_social_affection"
    if action_type == "social_performance":
        return "player_social_performance"
    if action_type == "trade":
        return "player_trade"
    if action_type == "ritual":
        return "player_ritual"
    if semantic_family == "social":
        return "player_social_activity"
    return "player_engaged"


def _semantic_consequence_summary(record: dict[str, Any]) -> str:
    record = _safe_dict(record)
    target_name = _safe_str(record.get("target_name"))
    activity_label = _safe_str(record.get("activity_label")).replace("_", " ")
    action_type = _safe_str(record.get("action_type"))
    visibility = _safe_str(record.get("visibility"))

    if action_type == "social_competition":
        return f"A {activity_label or 'contest'} between the player and {target_name or 'someone'} draws a crowd."
    if action_type == "social_affection":
        return f"{target_name or 'Someone'} reacts warmly to the player."
    if action_type == "social_performance":
        return f"The player's {activity_label or 'performance'} shifts the local mood."
    if action_type == "trade":
        return f"The player's {activity_label or 'exchange'} changes the local social flow."
    if action_type == "ritual":
        return f"The player's {activity_label or 'ritual'} leaves a noticeable impression."
    if visibility == "public":
        return f"The player's {activity_label or 'action'} becomes the center of attention."
    return f"The player's {activity_label or 'action'} affects the immediate scene."


def _safe_relationship_state(simulation_state: dict[str, Any]) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    relationships = _safe_dict(simulation_state.get("relationship_state"))
    simulation_state["relationship_state"] = relationships
    return relationships


def _relationship_bucket_key(a: str, b: str) -> str:
    left = _safe_str(a).strip()
    right = _safe_str(b).strip()
    ordered = sorted([left, right])
    return f"{ordered[0]}::{ordered[1]}"


def _apply_semantic_social_axes_to_relationships(
    simulation_state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    record = _safe_dict(record)
    target_id = _safe_str(record.get("target_id")).strip()
    if not target_id:
        return simulation_state

    relationships = _safe_relationship_state(simulation_state)
    rel_key = _relationship_bucket_key("player", target_id)
    rel = _safe_dict(relationships.get(rel_key))
    axes = _safe_dict(rel.get("axes"))

    for item in _safe_list(record.get("social_axes")):
        item = _safe_dict(item)
        axis = _safe_str(item.get("axis")).strip().lower()
        delta = _safe_int(item.get("delta"), 0)
        if not axis or delta == 0:
            continue
        current = _safe_int(axes.get(axis), 0)
        next_value = current + delta
        if next_value > 10:
            next_value = 10
        if next_value < -10:
            next_value = -10
        axes[axis] = next_value

    rel["pair"] = ["player", target_id]
    rel["axes"] = axes
    rel["updated_tick"] = _safe_int(record.get("tick"), 0)
    relationships[rel_key] = rel
    simulation_state["relationship_state"] = relationships
    return simulation_state


def _derive_semantic_observer_ids(
    simulation_state: dict[str, Any],
    record: dict[str, Any],
) -> list[str]:
    simulation_state = _safe_dict(simulation_state)
    record = _safe_dict(record)
    target_id = _safe_str(record.get("target_id")).strip()
    location_id = _safe_str(record.get("location_id")).strip()
    npc_index = _safe_dict(simulation_state.get("npc_index"))

    observer_ids: list[str] = []
    for npc_id, raw in sorted(npc_index.items()):
        npc = _safe_dict(raw)
        stable_id = _safe_str(npc.get("id") or npc_id).strip()
        if not stable_id or stable_id == target_id:
            continue
        npc_location = _safe_str(npc.get("location_id")).strip()
        if location_id and npc_location and npc_location != location_id:
            continue
        observer_ids.append(stable_id)
    return observer_ids[:4]


def _build_observer_activity_summary(
    observer_name: str,
    record: dict[str, Any],
) -> str:
    record = _safe_dict(record)
    action_type = _safe_str(record.get("action_type"))
    activity_label = _safe_str(record.get("activity_label")).replace("_", " ")
    target_name = _safe_str(record.get("target_name")) or "someone"

    if action_type == "social_competition":
        return f"{observer_name} watches the {activity_label or 'contest'} with {target_name} closely."
    if action_type == "social_performance":
        return f"{observer_name} pays attention to the player's {activity_label or 'performance'}."
    if action_type == "social_affection":
        return f"{observer_name} notices the warm exchange between the player and {target_name}."
    return f"{observer_name} reacts to the player's action nearby."


def _apply_semantic_observer_reactions(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.session.player_activity_runtime import (
        _normalize_activity_record as _normalize_activity_record, _stable_activity_id as _stable_activity_id, ensure_actor_activity_state as ensure_actor_activity_state,
        set_actor_activity as set_actor_activity,
    )

    simulation_state = _safe_dict(simulation_state)
    runtime_state = ensure_actor_activity_state(runtime_state)
    record = _safe_dict(record)

    hooks = [str(x).strip().lower() for x in _safe_list(record.get("observer_hooks")) if str(x).strip()]
    if not any(h in {"spectacle", "crowd_attention", "authority_notice", "conversation_seed", "relationship_shift", "rumor_seed"} for h in hooks):
        return runtime_state

    tick = _safe_int(record.get("tick"), 0)
    location_id = _safe_str(record.get("location_id"))
    npc_index = _safe_dict(simulation_state.get("npc_index"))

    for observer_id in _derive_semantic_observer_ids(simulation_state, record):
        npc = _safe_dict(npc_index.get(observer_id))
        observer_name = _safe_str(npc.get("name") or observer_id)
        activity_kind = "observer_reaction"
        if "authority_notice" in hooks and ("guard" in observer_name.lower() or "captain" in observer_name.lower() or "watch" in observer_name.lower()):
            activity_kind = "authority_observation"

        runtime_state = set_actor_activity(
            runtime_state,
            observer_id,
            _normalize_activity_record(
                {
                    "activity_id": _stable_activity_id(observer_id, tick, activity_kind, location_id),
                    "kind": activity_kind,
                    "subtype": _safe_str(record.get("activity_label")),
                    "summary": _build_observer_activity_summary(observer_name, record),
                    "location_id": location_id,
                    "target_id": _safe_str(record.get("target_id")),
                    "target_label": _safe_str(record.get("target_name")),
                    "started_tick": tick,
                    "updated_tick": tick,
                    "expected_duration": 2,
                    "status": "active",
                    "intent": "React to a notable player-driven local event.",
                    "world_tags": _safe_list(record.get("tags")) + ["observer_reaction"],
                    "priority": 4,
                }
            ),
        )

    return runtime_state


def _append_semantic_world_pressure(runtime_state: dict[str, Any], pressure: dict[str, Any]) -> dict[str, Any]:
    items = _safe_list(runtime_state.get("world_pressure"))
    pressure = _safe_dict(pressure)
    pressure_id = _safe_str(pressure.get("pressure_id")).strip()
    if pressure_id and any(_safe_str(_safe_dict(existing).get("pressure_id")) == pressure_id for existing in items):
        return runtime_state
    items.append(pressure)
    runtime_state["world_pressure"] = items[-64:]
    return runtime_state


def _append_semantic_world_rumor(runtime_state: dict[str, Any], rumor: dict[str, Any]) -> dict[str, Any]:
    items = _safe_list(runtime_state.get("world_rumors"))
    rumor = _safe_dict(rumor)
    rumor_id = _safe_str(rumor.get("rumor_id")).strip()
    if rumor_id and any(_safe_str(_safe_dict(existing).get("rumor_id")) == rumor_id for existing in items):
        return runtime_state
    items.append(rumor)
    runtime_state["world_rumors"] = items[-64:]
    return runtime_state


def _apply_semantic_world_propagation(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.apps.rpg.session.player_activity_runtime import (
        _stable_consequence_id as _stable_consequence_id, ensure_world_consequence_state as ensure_world_consequence_state,
    )

    from app.apps.rpg.session.world_consequence_runtime import (
        _append_world_pressure as _append_world_pressure, _append_world_rumor as _append_world_rumor,
    )

    simulation_state = _safe_dict(simulation_state)
    runtime_state = ensure_world_consequence_state(runtime_state)
    record = _safe_dict(record)
    hooks = [str(x).strip().lower() for x in _safe_list(record.get("observer_hooks")) if str(x).strip()]
    tick = _safe_int(record.get("tick"), 0)
    location_id = _safe_str(record.get("location_id"))
    action_type = _safe_str(record.get("action_type"))
    activity_label = _safe_str(record.get("activity_label")).replace("_", " ")
    target_name = _safe_str(record.get("target_name"))
    intensity = max(0, min(3, _safe_int(record.get("intensity"), 1)))
    visibility = _safe_str(record.get("visibility"))

    simulation_state = _apply_semantic_social_axes_to_relationships(simulation_state, record)
    runtime_state = _apply_semantic_observer_reactions(simulation_state, runtime_state, record)

    if visibility == "public" or "crowd_attention" in hooks or "spectacle" in hooks:
        runtime_state = _append_world_pressure(
            runtime_state,
            {
                "pressure_id": f"semantic_pressure:{_safe_str(record.get('semantic_action_id'))}",
                "tick": tick,
                "kind": "local_attention",
                "location_id": location_id,
                "summary": f"Attention builds around the player's {activity_label or 'action'}.",
                "intensity": intensity,
                "tags": _safe_list(record.get("tags")) + ["crowd_attention"],
            },
        )

    if "rumor_seed" in hooks or (visibility == "public" and action_type in {"social_competition", "social_performance", "threat"}):
        runtime_state = _append_world_rumor(
            runtime_state,
            {
                "rumor_id": f"semantic_rumor:{_safe_str(record.get('semantic_action_id'))}",
                "tick": tick,
                "location_id": location_id,
                "summary": (
                    f"People start talking about the player's {activity_label or 'action'}"
                    + (f" with {target_name}" if target_name else "")
                    + "."
                ),
                "intensity": intensity,
                "tags": _safe_list(record.get("tags")) + ["rumor_seed"],
            },
        )

    if _safe_str(record.get("scene_impact")) in {"gathers_attention", "changes_mood", "disrupts_flow"}:
        consequence_summary = {
            "gathers_attention": f"The scene grows more focused on the player's {activity_label or 'action'}.",
            "changes_mood": f"The mood shifts after the player's {activity_label or 'action'}.",
            "disrupts_flow": f"The usual rhythm of the area is disrupted by the player's {activity_label or 'action'}.",
        }.get(_safe_str(record.get("scene_impact")), "")
        if consequence_summary:
            runtime_state = _append_semantic_world_consequence(
                runtime_state,
                {
                    "consequence_id": _stable_consequence_id(
                        "consequence",
                        tick,
                        "local" if location_id else "global",
                        location_id or "player",
                        consequence_summary,
                    ),
                    "kind": "semantic_scene_impact",
                    "scope": "local" if location_id else "global",
                    "location_id": location_id,
                    "summary": consequence_summary,
                    "source_actor_id": _safe_str(record.get("target_id")),
                    "tick": tick,
                    "priority": 0.7,
                    "tags": _safe_list(record.get("tags")) + ["scene_impact"],
                },
            )

    return simulation_state, runtime_state


def _append_world_event_row(runtime_state: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    rows = _safe_list(runtime_state.get("recent_world_event_rows"))
    row = _safe_dict(row)
    row_id = _safe_str(row.get("event_id")).strip()
    if row_id and any(_safe_str(_safe_dict(existing).get("event_id")) == row_id for existing in rows):
        return runtime_state
    rows.append(row)
    runtime_state["recent_world_event_rows"] = rows[-_MAX_RECENT_WORLD_EVENT_ROWS:]
    return runtime_state


def _append_semantic_world_consequence(runtime_state: dict[str, Any], consequence: dict[str, Any]) -> dict[str, Any]:
    items = _safe_list(runtime_state.get("world_consequences"))
    consequence = _safe_dict(consequence)
    consequence_id = _safe_str(consequence.get("consequence_id")).strip()
    if consequence_id and any(_safe_str(_safe_dict(existing).get("consequence_id")) == consequence_id for existing in items):
        return runtime_state
    items.append(consequence)
    runtime_state["world_consequences"] = items[-_MAX_WORLD_CONSEQUENCES:]
    return runtime_state


def _emit_scene_beat_from_semantic_action(runtime_state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    beats = _safe_list(runtime_state.get("recent_scene_beats"))
    record = _safe_dict(record)
    tick = _safe_int(record.get("tick"), 0)
    beat_id = f"semantic_beat:{_safe_str(record.get('semantic_action_id'))}"
    if any(_safe_str(_safe_dict(existing).get("beat_id")) == beat_id for existing in beats):
        return runtime_state
    beat = {
        "beat_id": beat_id,
        "tick": tick,
        "kind": "interaction_beat",
        "summary": _safe_str(record.get("summary")) or _safe_str(record.get("player_input")),
        "priority": 0.8 if _safe_str(record.get("action_type")) == "social_competition" else 0.68,
        "scene_id": _safe_str(_safe_dict(runtime_state.get("current_scene")).get("scene_id")),
        "interaction_id": f"semantic_interaction:{_safe_str(record.get('semantic_action_id'))}",
        "actors": ["player"] + ([_safe_str(record.get("target_id"))] if _safe_str(record.get("target_id")) else []),
        "location_id": _safe_str(record.get("location_id")),
        "recap_level": "major" if _safe_str(record.get("action_type")) in ("social_competition", "social_performance") else "notable",
        "tags": _safe_list(record.get("tags")),
    }
    beats.append(beat)
    runtime_state["recent_scene_beats"] = beats[-_MAX_RECENT_SCENE_BEATS:]
    return runtime_state

__all__ = [
    '_MAX_INTERACTION_REACTION_STATE',
    '_MAX_NPC_REACTION_RECORDS',
    '_MAX_RUNTIME_LLM_RECORDS',
    '_MAX_SEMANTIC_ACTION_RECORDS',
    '_append_semantic_action_record',
    '_append_semantic_world_consequence',
    '_append_semantic_world_pressure',
    '_append_semantic_world_rumor',
    '_append_simulation_semantic_event',
    '_append_world_event_row',
    '_apply_semantic_observer_reactions',
    '_apply_semantic_social_axes_to_relationships',
    '_apply_semantic_world_propagation',
    '_build_observer_activity_summary',
    '_clean_resolved_interaction_world_event_rows',
    '_coerce_action_target',
    '_coerce_action_target_to_active_combat_participant',
    '_derive_semantic_observer_ids',
    '_emit_scene_beat_from_semantic_action',
    '_find_npc_target_by_name',
    '_prune_llm_records_state',
    '_relationship_bucket_key',
    '_safe_relationship_state',
    '_semantic_activity_kind',
    '_semantic_consequence_summary',
    '_stable_semantic_action_id',
]
