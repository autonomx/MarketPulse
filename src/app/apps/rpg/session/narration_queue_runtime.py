from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _ensure_active_interactions as _ensure_active_interactions, _ensure_npc_reaction_runtime_state as _ensure_npc_reaction_runtime_state,
    _ensure_simulation_state as _ensure_simulation_state, _normalize_performance_settings as _normalize_performance_settings,
    _normalize_runtime_settings as _normalize_runtime_settings, _safe_bool as _safe_bool, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list,
    _safe_str as _safe_str,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _DEFAULT_INTERACTION_DURATION_TICKS as _DEFAULT_INTERACTION_DURATION_TICKS, _INTERACTION_STALE_GRACE_TICKS as _INTERACTION_STALE_GRACE_TICKS,
    _MAX_ACTIVE_INTERACTIONS as _MAX_ACTIVE_INTERACTIONS,
)
from app.apps.rpg.narration.ai.npc_reaction_layer import (
    apply_npc_reactions as apply_npc_reactions, build_interaction_reaction_context as build_interaction_reaction_context,
    build_npc_reaction_candidates as build_npc_reaction_candidates, select_npc_reactions as select_npc_reactions,
    update_interaction_reaction_state as update_interaction_reaction_state,
)
from app.apps.rpg.narration.ai.conversation_threads import (
    seed_or_update_thread as seed_or_update_thread,
)
import hashlib as hashlib
import logging
from typing import (
    Any as Any, Dict as Dict, List as List, Tuple as Tuple,
)

logger = logging.getLogger(__name__)


# Backward compatibility wrapper
# ── Fast-turn performance helpers ─────────────────────────────────────────

_FAST_TURN_DEFAULTS = {
    "enable_action_advisory": True,
    "enable_semantic_action_advisory": True,
    "enable_live_narration_llm": True,
    "enable_narration_retry": False,
    "enable_fast_live_narrator_mode": True,
    "enable_continuity_grounding": True,
    "compact_save": True,
}




def _runtime_fast_turn_enabled(runtime_state: dict[str, Any]) -> bool:
    return bool((_safe_dict(runtime_state).get("performance") or {}).get("fast_turn_mode", False))


def _runtime_action_advisory_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["enable_action_advisory"]


def _runtime_semantic_advisory_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["enable_semantic_action_advisory"]


def _runtime_narration_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["enable_live_narration_llm"]


def _runtime_narration_retry_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["enable_narration_retry"]


def _runtime_continuity_grounding_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["enable_continuity_grounding"]


def _runtime_compact_save_enabled(runtime_state: dict[str, Any]) -> bool:
    return _normalize_performance_settings(runtime_state)["compact_save"]


def _dialogue_semantic_action_from_player_input(player_input: str) -> dict[str, Any] | None:
    text = str(player_input or "").strip()
    lower = " ".join(text.lower().split())
    if not lower:
        return None

    target_name = ""
    target_id = ""
    if "bran" in lower or "innkeeper" in lower or "bartender" in lower:
        target_name = "Bran"
        target_id = "npc:bran"
    elif "mira" in lower:
        target_name = "Mira"
        target_id = "npc:mira"
    elif "cloaked traveler" in lower or "traveler" in lower:
        target_name = "Cloaked Traveler"
        target_id = "npc:cloaked_traveler"
    elif "patron" in lower:
        target_name = "Local Patron"
        target_id = "npc:local_patron"

    is_question = (
        lower.startswith("i ask ")
        or lower.startswith("ask ")
        or " ask " in lower
        or "where " in lower
        or "what " in lower
        or "who " in lower
        or "why " in lower
        or "how " in lower
    )
    is_report = lower.startswith("i report ") or lower.startswith("report ") or " report to " in lower
    is_tell = lower.startswith("i tell ") or lower.startswith("tell ")
    mentions_witness_thread = any(
        term in lower
        for term in (
            "cloaked traveler",
            "witness",
            "side door",
            "trail points toward the road",
            "road danger",
            "what danger this confirms",
            "leaving by the side door",
            "fresh tracks",
        )
    )

    if not target_name and not mentions_witness_thread:
        return None
    if not (is_question or is_report or is_tell or mentions_witness_thread):
        return None

    if is_report:
        activity_label = "report_witness_findings" if mentions_witness_thread else "report"
        semantic_family = "social"
        interaction_mode = "dialogue"
    elif is_question:
        activity_label = "ask_witness_lead" if mentions_witness_thread else "ask"
        semantic_family = "social"
        interaction_mode = "dialogue"
    else:
        activity_label = "discuss_witness_lead" if mentions_witness_thread else "talk"
        semantic_family = "social"
        interaction_mode = "dialogue"

    if not target_name:
        target_name = "Bran"
        target_id = "npc:bran"

    return {
        "action_type": "social",
        "activity_label": activity_label,
        "semantic_family": semantic_family,
        "interaction_mode": interaction_mode,
        "target_name": target_name,
        "target_id": target_id,
        "secondary_actor_ids": [target_id] if target_id else [],
        "summary": text,
        "reason": "dialogue_semantic_action_from_player_input",
        "tags": ["social", "dialogue", "npc", activity_label],
        "stakes": 2,
        "intensity": 1,
        "visibility": "local",
        "scene_impact": "dialogue",
        "player_input": text,
    }


def _build_dialogue_state_update_payload(
    *,
    simulation_state: dict[str, Any],
    speaker: str,
    player_action: str,
    npc_line: str,
) -> dict[str, Any]:
    if not speaker or not npc_line:
        return {}
    return _safe_dict(_safe_dict(simulation_state).get("dialogue_state"))


def _apply_dialogue_state_update_from_narration(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    narration_payload: dict[str, Any],
) -> None:
    update = narration_payload.get("dialogue_state_update") if isinstance(narration_payload, dict) else None
    if isinstance(update, dict) and update:
        simulation_state["dialogue_state"] = dict(update)
        player_state = _safe_dict(simulation_state.get("player_state"))
        player_state["dialogue_state"] = dict(update)
        simulation_state["player_state"] = player_state
        runtime_state["dialogue_state"] = update


def _build_fast_semantic_action_record(
    player_input: str,
    action: dict[str, Any],
    simulation_state: dict[str, Any],
) -> dict[str, Any]:
    """Build a deterministic semantic action record without LLM advisory.

    The semantic_action_id is derived from a content hash of the key
    identity fields so that it is stable across alternate branching or
    replay insertion scenarios.
    """
    action = _safe_dict(action)
    simulation_state = _safe_dict(simulation_state)
    dialogue_semantic = _dialogue_semantic_action_from_player_input(player_input)
    if dialogue_semantic:
        return dialogue_semantic
    action_type = _safe_str(action.get("action_type")).strip().lower() or "observe"
    target_id = _safe_str(action.get("target_id")).strip()
    location_id = _safe_str(
        _safe_dict(simulation_state.get("player_state")).get("location_id")
    )
    normalised_input = _safe_str(player_input).strip()

    # Content-based identity hash
    id_seed = f"{normalised_input}|{action_type}|{target_id}|{location_id}"
    id_hash = hashlib.sha256(id_seed.encode("utf-8")).hexdigest()[:16]

    return {
        "semantic_action_id": f"fast_semantic_action_{id_hash}",
        "player_input": normalised_input,
        "action_type": action_type,
        "semantic_family": "observation",
        "interaction_mode": "direct" if target_id else "solo",
        "activity_label": action_type,
        "target_id": target_id,
        "target_name": _safe_str(action.get("target_name")).strip() or target_id,
        "secondary_actor_ids": [],
        "location_id": location_id,
        "visibility": "local",
        "intensity": 1,
        "stakes": 1,
        "social_axes": [],
        "observer_hooks": [],
        "scene_impact": "none",
        "reason": "",
        "summary": normalised_input[:160] or action_type,
        "tags": sorted(list({"player_action", "observation", action_type})),
    }






def _build_last_player_action_record(
    *,
    tick: int,
    player_input: str,
    action: dict[str, Any],
    semantic_action_record: dict[str, Any],
) -> dict[str, Any]:
    action = _safe_dict(action)
    semantic_action_record = _safe_dict(semantic_action_record)
    return {
        "action_id": f"player_action:{int(tick or 0)}",
        "tick": int(tick or 0),
        "text": _safe_str(player_input).strip()[:200],
        "action_type": _safe_str(
            semantic_action_record.get("action_type")
            or action.get("action_type")
        ).strip(),
        "target_id": _safe_str(
            semantic_action_record.get("target_id")
            or action.get("target_id")
            or action.get("npc_id")
        ).strip(),
        "semantic_action_id": _safe_str(
            semantic_action_record.get("semantic_action_id")
        ).strip(),
    }


def _clear_stale_last_player_action(
    runtime_state: dict[str, Any],
    current_tick: int,
    max_age_ticks: int = 2,
) -> dict[str, Any]:
    runtime_state = _copy_dict(runtime_state)
    last_player_action = _safe_dict(runtime_state.get("last_player_action"))
    if not last_player_action:
        return runtime_state
    action_tick = _safe_int(last_player_action.get("tick"), -999999)
    if action_tick < 0:
        runtime_state["last_player_action"] = {}
        return runtime_state
    if _safe_int(current_tick, 0) - action_tick > max_age_ticks:
        runtime_state["last_player_action"] = {}
    return runtime_state




def _semantic_action_starts_persistent_interaction(record: dict[str, Any]) -> bool:
    record = _safe_dict(record)
    action_type = _safe_str(record.get("action_type")).strip().lower()
    interaction_mode = _safe_str(record.get("interaction_mode")).strip().lower()
    visibility = _safe_str(record.get("visibility")).strip().lower()
    if action_type in {"social_competition", "social_affection", "social_performance", "threat"}:
        return True
    if interaction_mode in {"direct", "group", "public"} and visibility in {"local", "public"}:
        return True
    return False


def _interaction_duration_for_record(record: dict[str, Any]) -> int:
    record = _safe_dict(record)
    action_type = _safe_str(record.get("action_type")).strip().lower()
    intensity = max(0, min(3, _safe_int(record.get("intensity"), 1)))
    if action_type == "social_competition":
        return _DEFAULT_INTERACTION_DURATION_TICKS + max(1, intensity)
    if action_type in {"social_performance", "threat"}:
        return _DEFAULT_INTERACTION_DURATION_TICKS + intensity
    return _DEFAULT_INTERACTION_DURATION_TICKS


def _get_interaction_duration_mode(runtime_state: dict[str, Any]) -> str:
    runtime_state = _safe_dict(runtime_state)
    settings = _normalize_runtime_settings(_safe_dict(runtime_state.get("runtime_settings")))
    return _safe_str(settings.get("interaction_duration_mode") or "until_next_command").strip().lower() or "until_next_command"


def _get_interaction_duration_ticks(runtime_state: dict[str, Any], record: dict[str, Any]) -> int:
    runtime_state = _safe_dict(runtime_state)
    settings = _normalize_runtime_settings(_safe_dict(runtime_state.get("runtime_settings")))
    configured = _safe_int(settings.get("interaction_duration_ticks"), 5)
    if configured < 1:
        configured = 1
    if configured > 20:
        configured = 20
    return configured


def _compute_interaction_expires_tick(
    runtime_state: dict[str, Any],
    record: dict[str, Any],
    updated_tick: int,
) -> int:
    mode = _get_interaction_duration_mode(runtime_state)
    if mode == "until_next_command":
        # Large sentinel; explicit command transition / resolution will end it.
        return 10**9
    return _safe_int(updated_tick, 0) + _get_interaction_duration_ticks(runtime_state, record)


def _build_active_interaction_from_semantic_action(
    runtime_state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    record = _safe_dict(record)
    tick = _safe_int(record.get("tick"), 0)
    target_id = _safe_str(record.get("target_id")).strip()
    location_id = _safe_str(record.get("location_id")).strip()
    action_type = _safe_str(record.get("action_type")).strip().lower()
    activity_label = _safe_str(record.get("activity_label")).strip().lower() or action_type or "interaction"
    scene_id = _safe_str(_safe_dict(runtime_state.get("current_scene")).get("scene_id"))
    interaction_id = f"semantic_interaction:{_safe_str(record.get('semantic_action_id'))}"
    return {
        "id": interaction_id,
        "type": "player_semantic_interaction",
        "subtype": activity_label,
        "semantic_action_id": _safe_str(record.get("semantic_action_id")),
        "action_type": action_type,
        "display_name": _safe_str(record.get("target_name") or activity_label.replace("_", " ")),
        "participants": ["player"] + ([target_id] if target_id else []),
        "location_id": location_id,
        "scene_id": scene_id,
        "phase": "active",
        "resolved": False,
        "started_tick": tick,
        "updated_tick": tick,
        "expires_tick": _compute_interaction_expires_tick(runtime_state, record, tick),
        "state": {
            "activity_label": activity_label,
            "visibility": _safe_str(record.get("visibility")),
            "intensity": _safe_int(record.get("intensity"), 1),
            "stakes": _safe_int(record.get("stakes"), 1),
            "summary": _safe_str(record.get("summary")),
            "duration_mode": _get_interaction_duration_mode(runtime_state),
            "duration_ticks": _get_interaction_duration_ticks(runtime_state, record),
        },
    }


def _upsert_active_interaction_from_semantic_action(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    from app.apps.rpg.session.combat_intent import (
        _compact_active_interactions as _compact_active_interactions, _log_interaction_trace as _log_interaction_trace,
    )

    simulation_state = _ensure_active_interactions(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    record = _safe_dict(record)
    if not _semantic_action_starts_persistent_interaction(record):
        return simulation_state

    new_interaction = _build_active_interaction_from_semantic_action(runtime_state, record)
    semantic_action_id = _safe_str(record.get("semantic_action_id")).strip()
    target_id = _safe_str(record.get("target_id")).strip()
    action_type = _safe_str(record.get("action_type")).strip().lower()
    location_id = _safe_str(record.get("location_id")).strip()
    updated_tick = _safe_int(record.get("tick"), 0)

    next_items = []
    matched = False
    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        same_semantic = _safe_str(item.get("semantic_action_id")).strip() == semantic_action_id and semantic_action_id
        same_shape = (
            _safe_str(item.get("action_type")).strip().lower() == action_type
            and _safe_str(item.get("location_id")).strip() == location_id
            and target_id in _safe_list(item.get("participants"))
        )
        if same_semantic or same_shape:
            item["updated_tick"] = updated_tick
            item["expires_tick"] = _compute_interaction_expires_tick(runtime_state, record, updated_tick)
            item["resolved"] = False
            item["phase"] = "active"
            state = _safe_dict(item.get("state"))
            state["summary"] = _safe_str(record.get("summary")) or _safe_str(state.get("summary"))
            state["activity_label"] = _safe_str(record.get("activity_label")) or _safe_str(state.get("activity_label"))
            state["duration_mode"] = _get_interaction_duration_mode(runtime_state)
            state["duration_ticks"] = _get_interaction_duration_ticks(runtime_state, record)
            item["state"] = state
            if semantic_action_id:
                item["semantic_action_id"] = semantic_action_id
            next_items.append(item)
            matched = True
        else:
            next_items.append(item)

    if not matched:
        next_items.append(new_interaction)

    next_items.sort(
        key=lambda x: (
            -_safe_int(_safe_dict(x).get("updated_tick"), 0),
            _safe_str(_safe_dict(x).get("id")),
        )
    )
    simulation_state["active_interactions"] = next_items[:_MAX_ACTIVE_INTERACTIONS]
    _log_interaction_trace(
        "upsert_active_interaction",
        {
            "tick": updated_tick,
            "semantic_action_id": semantic_action_id,
            "action_type": action_type,
            "target_id": target_id,
            "count": len(_safe_list(simulation_state.get("active_interactions"))),
            "items": _compact_active_interactions(_safe_list(simulation_state.get("active_interactions"))),
        },
        runtime_state,
    )
    return simulation_state


def _persist_player_interaction_state_after_turn(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    player_input: str,
    semantic_action_record: dict[str, Any],
    current_tick: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    simulation_state = _ensure_simulation_state(_safe_dict(simulation_state))
    runtime_state = _copy_dict(runtime_state)
    semantic_action_record = _safe_dict(semantic_action_record)

    runtime_state["last_player_action"] = _build_last_player_action_record(
        tick=current_tick,
        player_input=player_input,
        action={"action_type": _safe_str(semantic_action_record.get("action_type")), "target_id": _safe_str(semantic_action_record.get("target_id"))},
        semantic_action_record=semantic_action_record,
    )

    simulation_state = _upsert_active_interaction_from_semantic_action(
        simulation_state,
        runtime_state,
        semantic_action_record,
    )

    return simulation_state, runtime_state


def _expire_stale_active_interactions(
    simulation_state: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    from app.apps.rpg.session.combat_intent import (
        _compact_active_interactions as _compact_active_interactions, _log_interaction_trace as _log_interaction_trace,
    )

    simulation_state = _ensure_active_interactions(simulation_state)
    kept = []
    expired = []
    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        expires_tick = _safe_int(item.get("expires_tick"), -999999)
        if expires_tick >= _safe_int(current_tick, 0) - _INTERACTION_STALE_GRACE_TICKS:
            kept.append(item)
        else:
            expired.append(
                {
                    "id": _safe_str(item.get("id")),
                    "expires_tick": expires_tick,
                    "current_tick": _safe_int(current_tick, 0),
                    "reason": "stale",
                }
            )
    simulation_state["active_interactions"] = kept[:_MAX_ACTIVE_INTERACTIONS]
    if expired:
        _log_interaction_trace(
            "expire_active_interactions",
            {
                "tick": _safe_int(current_tick, 0),
                "expired": expired,
                "remaining_count": len(_safe_list(simulation_state.get("active_interactions"))),
                "remaining": _compact_active_interactions(_safe_list(simulation_state.get("active_interactions"))),
            },
        )
    return simulation_state


def _refresh_active_interactions_for_tick(
    simulation_state: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    """Keep unresolved interactions visually current across idle ticks.

    This does not change lifecycle semantics. It only updates the interaction's
    display freshness so world-events sorting does not bury an ongoing player
    interaction under ambient activity rows.
    """
    simulation_state = _ensure_active_interactions(simulation_state)
    refreshed: list[dict[str, Any]] = []

    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        if not _safe_bool(item.get("resolved"), False):
            item["updated_tick"] = _safe_int(current_tick, 0)
        refreshed.append(item)

    simulation_state["active_interactions"] = refreshed[:_MAX_ACTIVE_INTERACTIONS]
    return simulation_state


def _build_active_interaction_prompt_context(
    simulation_state: dict[str, Any],
    current_tick: int,
) -> list[dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    rows = []
    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        if _safe_bool(item.get("resolved"), False):
            continue
        expires_tick = _safe_int(item.get("expires_tick"), -999999)
        if expires_tick < _safe_int(current_tick, 0) - _INTERACTION_STALE_GRACE_TICKS:
            continue
        rows.append(
            {
                "id": _safe_str(item.get("id")),
                "type": _safe_str(item.get("type")),
                "subtype": _safe_str(item.get("subtype")),
                "action_type": _safe_str(item.get("action_type")),
                "participants": _safe_list(item.get("participants"))[:4],
                "location_id": _safe_str(item.get("location_id")),
                "phase": _safe_str(item.get("phase")),
                "summary": _safe_str(_safe_dict(item.get("state")).get("summary"))[:200],
                "expires_tick": expires_tick,
                "duration_mode": _safe_str(_safe_dict(item.get("state")).get("duration_mode")),
                "duration_ticks": _safe_int(_safe_dict(item.get("state")).get("duration_ticks"), 0),
            }
        )
    rows.sort(key=lambda x: (-_safe_int(x.get("expires_tick"), 0), _safe_str(x.get("id"))))
    return rows[:4]


def _seed_conversation_thread_from_active_interaction(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    interaction: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    interaction = _safe_dict(interaction)
    participants = [
        _safe_str(p).strip()
        for p in _safe_list(interaction.get("participants"))
        if _safe_str(p).strip()
    ]
    npc_participants = [p for p in participants if p != "player"]
    if not npc_participants:
        return runtime_state
    state = _safe_dict(interaction.get("state"))
    activity_label = _safe_str(
        state.get("activity_label")
        or interaction.get("subtype")
        or interaction.get("action_type")
        or "interaction"
    )
    topic_summary = _safe_str(state.get("summary")).strip()
    if not topic_summary:
        display_name = _safe_str(interaction.get("display_name")).strip()
        topic_summary = f"Player interaction with {display_name or npc_participants[0]} about {activity_label}."
    runtime_state = seed_or_update_thread(
        runtime_state,
        kind="player_interaction",
        participants=participants,
        topic={
            "key": f"interaction:{_safe_str(interaction.get('id'))}",
            "type": "player_interaction",
            "summary": topic_summary,
            "activity_label": activity_label,
            "allowed_world_signal_types": ["rumor", "tension", "quest_lead", "relationship_shift"],
        },
        current_tick=current_tick,
        location_id=_safe_str(interaction.get("location_id")),
        scene_id=_safe_str(interaction.get("scene_id")),
    )
    return runtime_state


def _run_npc_reaction_pass(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    current_tick: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    simulation_state = _ensure_simulation_state(_safe_dict(simulation_state))
    runtime_state = _ensure_npc_reaction_runtime_state(_safe_dict(runtime_state))

    interactions = _safe_list(simulation_state.get("active_interactions"))
    for raw in interactions:
        interaction = _safe_dict(raw)
        if _safe_bool(interaction.get("resolved"), False):
            continue
        runtime_state = _seed_conversation_thread_from_active_interaction(
            simulation_state,
            runtime_state,
            interaction,
            current_tick,
        )
        context = build_interaction_reaction_context(simulation_state, runtime_state, interaction)
        context["tick"] = _safe_int(current_tick, 0)
        runtime_state = update_interaction_reaction_state(simulation_state, runtime_state, context)
        candidates = build_npc_reaction_candidates(simulation_state, runtime_state, context)
        reactions = select_npc_reactions(simulation_state, runtime_state, candidates)
        simulation_state, runtime_state = apply_npc_reactions(simulation_state, runtime_state, reactions)

    return simulation_state, runtime_state


def _semantic_action_matches_active_interaction(
    interaction: dict[str, Any],
    semantic_action_record: dict[str, Any],
) -> bool:
    interaction = _safe_dict(interaction)
    semantic_action_record = _safe_dict(semantic_action_record)
    interaction_action_type = _safe_str(interaction.get("action_type")).strip().lower()
    interaction_subtype = _safe_str(interaction.get("subtype")).strip().lower()
    interaction_participants = set(str(x).strip() for x in _safe_list(interaction.get("participants")) if str(x).strip())

    record_action_type = _safe_str(semantic_action_record.get("action_type")).strip().lower()
    record_activity_label = _safe_str(semantic_action_record.get("activity_label")).strip().lower()
    record_target_id = _safe_str(semantic_action_record.get("target_id")).strip()

    if interaction_action_type and interaction_action_type == record_action_type:
        if interaction_subtype and record_activity_label and interaction_subtype == record_activity_label:
            if not record_target_id or record_target_id in interaction_participants:
                return True

    if record_target_id and record_target_id in interaction_participants and record_action_type == interaction_action_type:
        return True

    return False


def _resolve_until_next_command_interactions(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    semantic_action_record: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    from app.apps.rpg.session.combat_intent import (
        _compact_active_interactions as _compact_active_interactions, _log_interaction_trace as _log_interaction_trace,
    )

    simulation_state = _ensure_active_interactions(simulation_state)
    mode = _get_interaction_duration_mode(runtime_state)
    if mode != "until_next_command":
        return simulation_state

    semantic_action_record = _safe_dict(semantic_action_record)
    next_items = []
    resolved_ids = []
    for raw in _safe_list(simulation_state.get("active_interactions")):
        item = _safe_dict(raw)
        if _safe_bool(item.get("resolved"), False):
            next_items.append(item)
            continue
        if _semantic_action_matches_active_interaction(item, semantic_action_record):
            next_items.append(item)
            continue
        item["resolved"] = True
        item["phase"] = "resolved"
        item["updated_tick"] = _safe_int(current_tick, 0)
        item["expires_tick"] = _safe_int(current_tick, 0)
        resolved_ids.append(_safe_str(item.get("id")))
        next_items.append(item)
    simulation_state["active_interactions"] = next_items[:_MAX_ACTIVE_INTERACTIONS]
    if resolved_ids:
        _log_interaction_trace(
            "resolve_until_next_command",
            {
                "tick": _safe_int(current_tick, 0),
                "new_action_type": _safe_str(semantic_action_record.get("action_type")),
                "new_activity_label": _safe_str(semantic_action_record.get("activity_label")),
                "resolved_ids": resolved_ids,
                "remaining": _compact_active_interactions(_safe_list(simulation_state.get("active_interactions"))),
            },
            runtime_state,
        )
    return simulation_state

__all__ = ['_DEFAULT_INTERACTION_DURATION_TICKS', '_FAST_TURN_DEFAULTS', '_INTERACTION_STALE_GRACE_TICKS', '_MAX_ACTIVE_INTERACTIONS', '_apply_dialogue_state_update_from_narration', '_build_active_interaction_from_semantic_action', '_build_active_interaction_prompt_context', '_build_dialogue_state_update_payload', '_build_fast_semantic_action_record', '_build_last_player_action_record', '_clear_stale_last_player_action', '_compute_interaction_expires_tick', '_dialogue_semantic_action_from_player_input', '_expire_stale_active_interactions', '_get_interaction_duration_mode', '_get_interaction_duration_ticks', '_interaction_duration_for_record', '_persist_player_interaction_state_after_turn', '_refresh_active_interactions_for_tick', '_resolve_until_next_command_interactions', '_run_npc_reaction_pass', '_runtime_action_advisory_enabled', '_runtime_compact_save_enabled', '_runtime_continuity_grounding_enabled', '_runtime_fast_turn_enabled', '_runtime_narration_enabled', '_runtime_narration_retry_enabled', '_runtime_semantic_advisory_enabled', '_seed_conversation_thread_from_active_interaction', '_semantic_action_matches_active_interaction', '_semantic_action_starts_persistent_interaction', '_upsert_active_interaction_from_semantic_action']
