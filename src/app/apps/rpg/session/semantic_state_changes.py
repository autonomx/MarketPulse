from __future__ import annotations

import logging

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _normalize_active_interactions as _normalize_active_interactions, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.rules.combat.state import (
    normalize_combat_state as normalize_combat_state,
)
from app.apps.rpg.session.world_consequence_runtime import (
    _accepted_state_change_event_ids as _accepted_state_change_event_ids, _allowed_semantic_actions as _allowed_semantic_actions,
    _applied_semantic_proposal_ids as _applied_semantic_proposal_ids, _ensure_semantic_pipeline_state as _ensure_semantic_pipeline_state, _find_actor_state as _find_actor_state,
    _normalize_actor_state_for_delta as _normalize_actor_state_for_delta, _normalize_semantic_state_change_proposal as _normalize_semantic_state_change_proposal,
    _safe_actor_states as _safe_actor_states, _stable_semantic_state_change_proposal_id as _stable_semantic_state_change_proposal_id,
    _stable_state_change_event_id as _stable_state_change_event_id, _write_actor_states as _write_actor_states,
    clear_recorded_semantic_llm_capture as clear_recorded_semantic_llm_capture, emit_scene_beat as emit_scene_beat,
    enqueue_semantic_state_change_proposal as enqueue_semantic_state_change_proposal, validate_semantic_state_change_proposal as validate_semantic_state_change_proposal,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _MAX_ACCEPTED_STATE_CHANGE_EVENTS as _MAX_ACCEPTED_STATE_CHANGE_EVENTS, _MAX_APPLIED_PROPOSAL_IDS as _MAX_APPLIED_PROPOSAL_IDS,
    _MAX_LLM_PROPOSAL_CANDIDATES as _MAX_LLM_PROPOSAL_CANDIDATES, _MAX_PROMPT_SCENE_BEATS as _MAX_PROMPT_SCENE_BEATS,
    _SEMANTIC_LLM_PROPOSAL_COOLDOWN_TICKS as _SEMANTIC_LLM_PROPOSAL_COOLDOWN_TICKS,
)
import json as json
from app.apps.rpg.session.narration_queue_runtime import (
    _build_active_interaction_prompt_context as _build_active_interaction_prompt_context,
)
from app.apps.rpg.narration.ai.conversation_threads import (
    build_conversation_thread_prompt_context as build_conversation_thread_prompt_context,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)

import re as re
from app.prompts import prompt_template

_PROMPT_1 = prompt_template('rpg.session_semantic_state_changes.build_semantic_state_change_prompt_contract', "1", (
    "You are a deterministic state-change generator for an RPG simulation.\n\n"
        "OUTPUT FORMAT REQUIREMENTS (MANDATORY):\n"
        "- Output ONLY valid JSON\n"
        "- No explanations\n"
        "- No thinking\n"
        "- No commentary\n"
        "- No markdown\n"
        "- No text outside JSON\n"
        "- JSON MUST be inside <RESPONSE> ... </RESPONSE>\n\n"
        "REQUIRED JSON STRUCTURE:\n\n"
        "<RESPONSE>{\n"
        '  "actor_id": "<npc_id>",\n'
        '  "proposal_kind": "state_delta",\n'
        '  "semantic_action": "<action>",\n'
        '  "delta": {\n'
        '    "activity": "<non-empty>",\n'
        '    "engagement": "<non-empty>"\n'
        '  },\n'
        '  "beat_summary": "<short sentence>"\n'
        "}</RESPONSE>\n\n"
))
_PROMPT_2 = prompt_template('rpg.session_semantic_state_changes.build_semantic_state_change_prompt_contract_2', "1", (
    "RULES:\n"
        '- "delta" MUST NOT be empty\n'
        '- "activity" MUST be meaningful (not "active")\n'
        '- "engagement" MUST be meaningful (not "ongoing")\n'
        "- Choose actions based on scene context\n"
        "- Prefer interaction, movement, or reactions over idle\n"
        "- When a player action is happening, NPCs should react to it\n\n"
        "EXAMPLES OF GOOD ACTIONS:\n"
        "- argue\n"
        "- observe\n"
        "- investigate\n"
        "- negotiate\n"
        "- rest\n"
        "- trade\n"
        "- react_to_player\n\n"
        "INPUT:\n"
))
_PROMPT_3 = prompt_template('rpg.session_semantic_state_changes.player_context_instruction', "1", (
    "IMPORTANT — REACT TO PLAYER ACTION:\n"
            "The player recently performed an action (see recent_player_action in INPUT).\n"
            "NPCs MUST react to the player's action rather than continuing generic routines.\n"
            "- NPCs nearby should watch, react, comment, or be affected by what the player is doing.\n"
            "- Do NOT generate generic patrol/observe/tidy actions when a notable player action is happening.\n"
            "- beat_summary MUST reference the player's ongoing activity, not routine NPC behavior.\n\n"
))
_PROMPT_4 = prompt_template('rpg.session_semantic_state_changes.interaction_context_instruction', "1", (
    "IMPORTANT — ACTIVE INTERACTION IS STILL ONGOING:\n"
            "There is an unresolved active interaction in the scene (see active_interactions in INPUT).\n"
            "NPCs nearby MUST continue reacting to that interaction until it expires or resolves.\n"
            "- Do NOT revert to generic patrol, tidy, serve, or idle routines while the interaction is active.\n"
            "- beat_summary should reference the ongoing contest / performance / confrontation when appropriate.\n"
            "- Nearby authority figures should watch or react if the interaction is public.\n\n"
))

logger = logging.getLogger(__name__)


def compile_semantic_state_change_to_canonical_delta(
    proposal: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    proposal = _normalize_semantic_state_change_proposal(proposal)
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)

    actor_states = _safe_actor_states(simulation_state)
    actor = _find_actor_state(actor_states, proposal["actor_id"])
    if not actor:
        raise ValueError(f"compile_missing_actor:{_safe_str(proposal.get('actor_id'))}")
    actor_before = _normalize_actor_state_for_delta(actor)
    delta = dict(_safe_dict(proposal.get("delta")))

    semantic_action = _safe_str(proposal.get("semantic_action"))
    implied = dict(_allowed_semantic_actions().get(semantic_action, {}))
    for key, value in implied.items():
        if not _safe_str(delta.get(key)):
            delta[key] = value

    if proposal["target_location_id"] and not _safe_str(delta.get("location_id")):
        delta["location_id"] = proposal["target_location_id"]

    actor_after = dict(actor_before)
    for key in ("activity", "availability", "location_id", "mood", "intent", "engagement"):
        value = _safe_str(delta.get(key))
        if value:
            actor_after[key] = value
    if actor_after == actor_before:
        raise ValueError(f"empty_compiled_delta:{_safe_str(proposal.get('proposal_id'))}")

    beat_summary = _safe_str(proposal.get("beat_summary"))
    if not beat_summary:
        actor_name = _safe_str(actor_after.get("name")) or _safe_str(actor_before.get("name")) or "Someone"
        if semantic_action == "take_break":
            beat_summary = f"{actor_name} steps away for a short break."
        elif semantic_action == "wash_up":
            beat_summary = f"{actor_name} slips away to wash up."
        elif semantic_action == "rest":
            beat_summary = f"{actor_name} settles in to rest."
        elif semantic_action == "investigate":
            beat_summary = f"{actor_name} begins investigating the situation."
        elif semantic_action == "leave_scene":
            beat_summary = f"{actor_name} leaves the area."
        elif semantic_action == "return_to_scene":
            beat_summary = f"{actor_name} returns to the scene."
        elif semantic_action == "argue":
            beat_summary = f"{actor_name} becomes engaged in a heated exchange."
        elif _safe_str(actor_after.get("activity")) and _safe_str(actor_after.get("activity")) != _safe_str(actor_before.get("activity")):
            beat_summary = f"{actor_name} shifts into {_safe_str(actor_after.get('activity')).replace('_', ' ')}."
        else:
            beat_summary = _safe_str(proposal.get("summary"))

    current_tick = _safe_int(
        simulation_state.get("current_tick")
        or simulation_state.get("tick")
        or runtime_state.get("tick"),
        0,
    )

    canonical_event = {
        "event_id": "",
        "tick": current_tick,
        "proposal_id": _safe_str(proposal.get("proposal_id")),
        "actor_id": _safe_str(proposal.get("actor_id")),
        "semantic_action": semantic_action,
        "location_id": _safe_str(actor_after.get("location_id")),
        "summary": _safe_str(proposal.get("summary")) or beat_summary,
        "before": actor_before,
        "after": actor_after,
        "beat": {
            "summary": beat_summary,
            "priority": int(proposal.get("priority", 50) or 50),
            "recap_level": "notable",
            "tags": ["state_change", semantic_action or "semantic_action"],
        },
    }
    canonical_event["event_id"] = _stable_state_change_event_id(canonical_event)
    assert canonical_event["tick"] == _safe_int(simulation_state.get("tick"), 0)

    logger.debug(
        "DEBUG SEMANTIC EVENT CREATED = %s",
        {
            "event_id": canonical_event.get("event_id"),
            "tick": canonical_event.get("tick"),
            "proposal_id": canonical_event.get("proposal_id"),
            "actor_id": canonical_event.get("actor_id"),
            "summary": canonical_event.get("summary"),
            "location_id": canonical_event.get("location_id"),
        },
    )

    return canonical_event


def _apply_canonical_state_change_event(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    event: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    event = _safe_dict(event)

    actor_states = _safe_actor_states(simulation_state)
    actor_id = _safe_str(event.get("actor_id"))
    after = _safe_dict(event.get("after"))

    updated = []
    found = False
    for actor in actor_states:
        actor = _safe_dict(actor)
        if _safe_str(actor.get("id")) == actor_id:
            merged = dict(actor)
            for key in ("activity", "availability", "location_id", "mood", "intent", "engagement"):
                value = _safe_str(after.get(key))
                if value:
                    merged[key] = value
            updated.append(merged)
            found = True
        else:
            updated.append(actor)
    if not found:
        raise ValueError(f"state_change_target_actor_missing:{actor_id}")

    simulation_state = _write_actor_states(simulation_state, updated)
    return simulation_state, runtime_state


def _record_accepted_state_change_event(runtime_state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    event = _safe_dict(event)
    accepted = _safe_list(runtime_state.get("accepted_state_change_events"))
    accepted.append(event)
    accepted.sort(
        key=lambda x: (
            int(_safe_dict(x).get("tick", 0) or 0),
            _safe_str(_safe_dict(x).get("event_id")),
        )
    )
    runtime_state["accepted_state_change_events"] = accepted[-_MAX_ACCEPTED_STATE_CHANGE_EVENTS:]
    return runtime_state


def _record_applied_semantic_proposal_id(runtime_state: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    proposal_id = _safe_str(proposal_id)
    if not proposal_id:
        return runtime_state
    items = [x for x in _safe_list(runtime_state.get("applied_semantic_proposal_ids")) if _safe_str(x)]
    items.append(proposal_id)
    deduped = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    runtime_state["applied_semantic_proposal_ids"] = deduped[-_MAX_APPLIED_PROPOSAL_IDS:]
    return runtime_state


def _emit_scene_beat_from_accepted_state_change(runtime_state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    event = _safe_dict(event)
    beat = _safe_dict(event.get("beat"))
    after = _safe_dict(event.get("after"))
    return emit_scene_beat(
        runtime_state,
        tick=int(event.get("tick", 0) or 0),
        summary=_safe_str(beat.get("summary")) or _safe_str(event.get("summary")),
        kind="state_change_beat",
        priority=int(beat.get("priority", 50) or 50),
        scene_id="",
        interaction_id=_safe_str(event.get("proposal_id")),
        actors=[_safe_str(event.get("actor_id"))] if _safe_str(event.get("actor_id")) else [],
        location_id=_safe_str(after.get("location_id")),
        recap_level=_safe_str(beat.get("recap_level")) or "notable",
        tags=[_safe_str(x) for x in _safe_list(beat.get("tags")) if _safe_str(x)],
    )


def process_semantic_state_change_proposals(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:

    from app.apps.rpg.session.combat_intent import (
        _log_interaction_trace as _log_interaction_trace,
    )

    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)

    raw_proposals = [
        _normalize_semantic_state_change_proposal(x)
        for x in _safe_list(runtime_state.get("semantic_state_change_proposals"))
    ]
    proposals = []
    seen_proposal_ids = set()
    for proposal in raw_proposals:
        proposal_id = _safe_str(proposal.get("proposal_id"))
        if proposal_id and proposal_id in seen_proposal_ids:
            continue
        if proposal_id:
            seen_proposal_ids.add(proposal_id)
        proposals.append(proposal)

    # Current policy: proposals are processed once per tick and never retried.
    # Invalid proposals are recorded in rejected_state_change_events.
    remaining: list[Any] = []
    accepted_ids = _accepted_state_change_event_ids(runtime_state)
    applied_proposal_ids = _applied_semantic_proposal_ids(runtime_state)

    for proposal in proposals:
        proposal_id = _safe_str(proposal.get("proposal_id"))
        if proposal_id and proposal_id in applied_proposal_ids:
            continue

        validation = validate_semantic_state_change_proposal(proposal, simulation_state, runtime_state)
        if not validation.get("ok"):
            rejected = _safe_list(runtime_state.get("rejected_state_change_events"))
            rejected.append(
                {
                    "proposal_id": _safe_str(proposal.get("proposal_id")),
                    "actor_id": _safe_str(proposal.get("actor_id")),
                    "semantic_action": _safe_str(proposal.get("semantic_action")),
                    "errors": _safe_list(validation.get("errors")),
                    "tick": int(runtime_state.get("tick", 0) or 0),
                }
            )
            runtime_state["rejected_state_change_events"] = rejected[-_MAX_ACCEPTED_STATE_CHANGE_EVENTS:]
            continue

        try:
            event = compile_semantic_state_change_to_canonical_delta(
                proposal,
                simulation_state,
                runtime_state,
            )
        except ValueError as exc:
            rejected = _safe_list(runtime_state.get("rejected_state_change_events"))
            rejected.append(
                {
                    "proposal_id": _safe_str(proposal.get("proposal_id")),
                    "actor_id": _safe_str(proposal.get("actor_id")),
                    "semantic_action": _safe_str(proposal.get("semantic_action")),
                    "errors": [str(exc)],
                    "tick": int(runtime_state.get("tick", 0) or 0),
                }
            )
            runtime_state["rejected_state_change_events"] = rejected[-_MAX_ACCEPTED_STATE_CHANGE_EVENTS:]
            continue
        event_id = _safe_str(event.get("event_id"))
        if event_id and event_id in accepted_ids:
            continue

        simulation_state, runtime_state = _apply_canonical_state_change_event(
            simulation_state,
            runtime_state,
            event,
        )

        logger.debug(
            "DEBUG SEMANTIC EVENT APPEND accepted_state_change_events = %s",
            {
                "existing_count": len(_safe_list(runtime_state.get("accepted_state_change_events"))),
                "event_id": event.get("event_id"),
                "tick": event.get("tick"),
            },
        )

        runtime_state = _record_accepted_state_change_event(runtime_state, event)
        _log_interaction_trace(
            "semantic_accept",
            {
                "tick": _safe_int(event.get("tick"), 0),
                "actor_id": _safe_str(event.get("actor_id")),
                "semantic_action": _safe_str(event.get("semantic_action")),
                "summary": _safe_str(event.get("summary"))[:160],
                "interaction_count": len(_safe_list(simulation_state.get("active_interactions"))),
            },
            runtime_state,
        )
        if event_id:
            accepted_ids.add(event_id)
        runtime_state = _record_applied_semantic_proposal_id(runtime_state, proposal_id)
        applied_proposal_ids = _applied_semantic_proposal_ids(runtime_state)
        runtime_state = _emit_scene_beat_from_accepted_state_change(runtime_state, event)

    runtime_state["semantic_state_change_proposals"] = remaining
    return simulation_state, runtime_state


def _build_semantic_state_change_prompt_contract(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> str:
    from app.apps.rpg.session.combat_intent import (
        _log_interaction_trace as _log_interaction_trace,
    )

    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)

    actor_states = _safe_actor_states(simulation_state)
    location_rows = [
        {
            "id": _safe_str(x.get("id")),
            "name": _safe_str(x.get("name")),
        }
        for x in _safe_list(simulation_state.get("locations"))
        if isinstance(x, dict)
    ]
    interaction_rows: list[dict[str, Any]] = [
        {
            "id": _safe_str(x.get("id")),
            "type": _safe_str(x.get("type")),
            "subtype": _safe_str(x.get("subtype")),
            "participants": [_safe_str(p) for p in _safe_list(x.get("participants")) if _safe_str(p)],
            "resolved": bool(x.get("resolved")),
        }
        for x in _normalize_active_interactions(simulation_state, runtime_state)
    ]

    interacting_actor_ids = []
    seen_actor_ids = set()
    for row in interaction_rows:
        for actor_id in row.get("participants") or []:
            actor_id = _safe_str(actor_id)
            if actor_id and actor_id not in seen_actor_ids:
                seen_actor_ids.add(actor_id)
                interacting_actor_ids.append(actor_id)

    interacting_actor_rows = [
        {
            "id": _safe_str(a.get("id")),
            "name": _safe_str(a.get("name")),
            "activity": _safe_str(a.get("activity")),
            "availability": _safe_str(a.get("availability")),
            "location_id": _safe_str(a.get("location_id")),
            "mood": _safe_str(a.get("mood")),
            "intent": _safe_str(a.get("intent")),
            "engagement": _safe_str(a.get("engagement")),
        }
        for a in actor_states
        if _safe_str(a.get("id")) in set(interacting_actor_ids)
    ][: _MAX_LLM_PROPOSAL_CANDIDATES]

    # ── Recent player action context ────────────────────────────────────────
    last_player_action = _safe_dict(runtime_state.get("last_player_action"))
    player_action_context: dict[str, Any] = {}
    if _safe_str(last_player_action.get("text")):
        player_action_context = {
            "action_type": _safe_str(last_player_action.get("action_type")),
            "text": _safe_str(last_player_action.get("text"))[:200],
            "target_id": _safe_str(last_player_action.get("target_id")),
        }

    # ── Recent scene context (player-driven beats) ───────────────────────
    recent_beats_context: list[dict[str, str]] = []
    for beat in _safe_list(runtime_state.get("recent_scene_beats"))[-_MAX_PROMPT_SCENE_BEATS:]:
        beat = _safe_dict(beat)
        summary = _safe_str(beat.get("summary")).strip()
        if not summary:
            continue
        recent_beats_context.append({
            "kind": _safe_str(beat.get("kind")),
            "summary": summary[:200],
        })

    active_interactions_context = _build_active_interaction_prompt_context(
        simulation_state,
        _safe_int(simulation_state.get("tick"), 0),
    )
    conversation_threads_context = build_conversation_thread_prompt_context(
        runtime_state,
        current_tick=_safe_int(simulation_state.get("tick"), 0),
        limit=4,
    )

    _log_interaction_trace(
        "semantic_prompt_context",
        {
            "tick": _safe_int(simulation_state.get("tick"), 0),
            "interaction_count": len(active_interactions_context),
            "interaction_ids": [_safe_str(x.get("id")) for x in active_interactions_context],
        },
        runtime_state,
    )

    prompt_payload: dict[str, Any] = {
        "scene_title": _safe_str(simulation_state.get("scene_title")),
        "location_name": _safe_str(simulation_state.get("location_name")),
        "allowed_semantic_actions": sorted(list(_allowed_semantic_actions().keys())),
        "allowed_delta_fields": ["activity", "availability", "location_id", "mood", "intent", "engagement"],
        "actors": [
            {
                "id": _safe_str(a.get("id")),
                "name": _safe_str(a.get("name")),
                "activity": _safe_str(a.get("activity")),
                "availability": _safe_str(a.get("availability")),
                "location_id": _safe_str(a.get("location_id")),
                "mood": _safe_str(a.get("mood")),
                "intent": _safe_str(a.get("intent")),
                "engagement": _safe_str(a.get("engagement")),
            }
            for a in actor_states[:_MAX_LLM_PROPOSAL_CANDIDATES]
        ],
        "locations": location_rows[:12],
        "active_interactions": interaction_rows[:8],
        "interacting_actor_ids": interacting_actor_ids[:_MAX_LLM_PROPOSAL_CANDIDATES],
        "interacting_actors": interacting_actor_rows,
        "conversation_threads": conversation_threads_context,
    }
    if player_action_context:
        prompt_payload["recent_player_action"] = player_action_context
    if recent_beats_context:
        prompt_payload["recent_scene_beats"] = recent_beats_context[-_MAX_PROMPT_SCENE_BEATS:]
    if active_interactions_context:
        prompt_payload["active_interactions"] = active_interactions_context

    player_context_instruction = ""
    if player_action_context:
        player_context_instruction = (
            _PROMPT_3.text
        )

    interaction_context_instruction = ""
    if active_interactions_context:
        interaction_context_instruction = (
            _PROMPT_4.text
        )

    _log_interaction_trace(
        "semantic_prompt_context",
        {
            "tick": _safe_int(simulation_state.get("tick"), 0),
            "interaction_count": len(active_interactions_context),
            "active_interactions": active_interactions_context,
            "recent_player_action": player_action_context,
            "recent_scene_beats_count": len(recent_beats_context),
            "actor_count": len(_safe_list(simulation_state.get("actor_states"))),
        },
        runtime_state,
    )

    return (
        _PROMPT_1.text
        + player_context_instruction
        + interaction_context_instruction
        + _PROMPT_2.text
        + json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
    )


def _extract_json_array(text: str) -> list[Any]:
    text = _safe_str(text)
    if not text:
        return []
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _normalize_llm_text_output(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("text", "output_text", "content", "response"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(raw, ensure_ascii=False)
    return _safe_str(raw)


def llm_semantic_proposal_gateway(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Replay-safe gateway stub.

    Source of truth:
    - live mode: consume proposals already captured into runtime_state["recorded_semantic_llm_proposals"]
    - replay mode: consume the same recorded proposals

    This function MUST NOT call a live provider directly. Any future live LLM
    integration should happen upstream through a recorded nondeterministic
    boundary that persists prompt, raw output, and normalized proposals.
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    recorded = _safe_list(runtime_state.get("recorded_semantic_llm_proposals"))
    out: list[dict[str, Any]] = []
    seen = set()
    for item in recorded[:3]:
        proposal = _normalize_semantic_state_change_proposal(_safe_dict(item))
        proposal_id = _safe_str(proposal.get("proposal_id"))
        if proposal_id and proposal_id in seen:
            continue
        if proposal_id:
            seen.add(proposal_id)
        if not proposal.get("actor_id"):
            continue
        out.append(proposal)
    return out


def preview_semantic_state_change_prompt(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> str:
    return _build_semantic_state_change_prompt_contract(simulation_state, runtime_state)


def normalize_semantic_state_change_llm_output(raw_output: Any, simulation_state: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Helper for an upstream recorded LLM boundary.

    Intended usage:
    - upstream layer calls live LLM
    - upstream layer records prompt + raw output
    - upstream layer passes raw output here to normalize proposals
    - normalized proposals are written to runtime_state["recorded_semantic_llm_proposals"]
    """
    raw_text = _normalize_llm_text_output(raw_output)
    if not raw_text:
        return []

    import json

    text = str(raw_text)

    # 1. Extract <RESPONSE> block if present

    match = re.search(r"<RESPONSE>(.*?)</RESPONSE>", text, re.DOTALL)

    if match:
        text = match.group(1)
    else:
        # 🔥 HARD FAIL instead of parsing garbage

        return []

    text = text.strip()

    try:
        data = json.loads(text)
    except Exception:
        return []

    # 🔥 HANDLE ALL VALID SHAPES
    proposals: list[Any] = []

    # Case 1: wrapped
    if isinstance(data, dict) and "state_changes" in data:
        proposals = data.get("state_changes") or []

    # Case 2: single proposal object
    elif isinstance(data, dict):
        proposals = [data]

    # Case 3: already a list
    elif isinstance(data, list):
        proposals = data

    else:
        return []

    normalized = []

    for p in proposals:
        p = _safe_dict(p)

        actor_id = _safe_str(p.get("actor_id"))
        if not actor_id:
            # fallback: assign first actor in scene
            actor_id = next(iter(simulation_state.get("npc_index", {}).keys()), "")

        if not actor_id:
            continue

        normalized_proposal = {
            "proposal_id": _safe_str(p.get("proposal_id")),
            "actor_id": actor_id,
            "proposal_kind": _safe_str(p.get("proposal_kind")) or "state_delta",
            "semantic_action": _safe_str(p.get("semantic_action")),
            "target_id": _safe_str(p.get("target_id")),
            "target_location_id": _safe_str(p.get("target_location_id")),
            "summary": _safe_str(p.get("summary")),
            "beat_summary": _safe_str(p.get("beat_summary")),
            "priority": int(p.get("priority") or 50),
            "delta": _safe_dict(p.get("delta")),
            "tags": _safe_list(p.get("tags")),
        }
        normalized_proposal["proposal_id"] = (
            _safe_str(normalized_proposal.get("proposal_id"))
            or _stable_semantic_state_change_proposal_id(
                normalized_proposal,
                simulation_state,
                {},
            )
        )
        normalized.append(normalized_proposal)

    return normalized


def _should_generate_llm_semantic_proposals(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> bool:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    # Keep activity generation alive at a low rate even if a small number of
    # queued proposals already exist.
    if len(_safe_list(runtime_state.get("semantic_state_change_proposals"))) > 2:
        return False
    if _safe_list(runtime_state.get("recorded_semantic_llm_proposals")):
        return True

    # Active interactions are explicitly allowed. NPCs should remain visibly
    # active during them, and the LLM proposal layer should be able to describe
    # that bounded behavior.
    actor_states = _safe_actor_states(simulation_state)
    if not actor_states:
        return False
    tick = _safe_int(runtime_state.get("tick", 0), 0)
    last_tick = _safe_int(runtime_state.get("last_semantic_llm_tick", -999999), -999999)
    return (tick - last_tick) >= _SEMANTIC_LLM_PROPOSAL_COOLDOWN_TICKS


def maybe_enqueue_llm_semantic_state_change_proposals(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:

    simulation_state = _safe_dict(simulation_state)
    runtime_state = _ensure_semantic_pipeline_state(runtime_state)
    if not _should_generate_llm_semantic_proposals(simulation_state, runtime_state):
        return runtime_state
    proposals = llm_semantic_proposal_gateway(simulation_state, runtime_state)
    consumed_any = False
    for proposal in proposals:
        runtime_state = enqueue_semantic_state_change_proposal(runtime_state, proposal)
        consumed_any = True
    # Consume recorded proposals exactly once.
    if _safe_list(runtime_state.get("recorded_semantic_llm_proposals")):
        runtime_state = clear_recorded_semantic_llm_capture(runtime_state)
        consumed_any = True

    if consumed_any:
        runtime_state["last_semantic_llm_tick"] = _safe_int(runtime_state.get("tick", 0), 0)
    return runtime_state




def _build_recent_narration_continuity(runtime_state: dict[str, Any], current_turn_id: str, limit: int = 3) -> list[dict[str, Any]]:
    runtime_state = _safe_dict(runtime_state)
    artifacts = _safe_list(runtime_state.get("narration_artifacts"))
    rows: list[dict[str, Any]] = []

    for artifact in reversed(artifacts):
        artifact = _safe_dict(artifact)
        turn_id = _safe_str(artifact.get("turn_id")).strip()
        if not turn_id or turn_id == current_turn_id:
            continue
        narration_json = _safe_dict(artifact.get("narration_json"))
        if not narration_json:
            narration_json = {
                "action": _safe_str(artifact.get("authoritative_action")).strip(),
                "reward": _safe_str(artifact.get("authoritative_reward")).strip(),
                "npc": _safe_dict(artifact.get("authoritative_npc")),
            }
        rows.append({
            "turn_id": turn_id,
            "tick": int(artifact.get("tick", 0) or 0),
            "narration": _safe_str(narration_json.get("narration")).strip(),
            "action": _safe_str(narration_json.get("action")).strip(),
            "reward": _safe_str(narration_json.get("reward")).strip(),
            "npc": _safe_dict(narration_json.get("npc")),
        })
        if len(rows) >= max(0, int(limit or 0)):
            break

    rows.reverse()
    return rows


def _build_recent_authoritative_turn_facts(runtime_state: dict[str, Any], current_turn_id: str, limit: int = 3) -> list[str]:
    rows = _build_recent_narration_continuity(runtime_state, current_turn_id, limit=limit)
    facts: list[str] = []
    for row in rows:
        tick = int(row.get("tick", 0) or 0)
        action = _safe_str(row.get("action")).strip()
        reward = _safe_str(row.get("reward")).strip()
        npc = _safe_dict(row.get("npc"))
        speaker = _safe_str(npc.get("speaker")).strip()
        line = _safe_str(npc.get("line")).strip()
        parts: list[str] = []
        if action:
            parts.append(action)
        if speaker and line:
            parts.append(f'{speaker} said: "{line}"')
        if reward:
            parts.append(f"Reward: {reward}")
        if parts:
            facts.append(f"Tick {tick}: " + " | ".join(parts))
    return facts




def _get_combat_state(runtime_state: dict[str, Any]) -> dict[str, Any]:
    return normalize_combat_state(_safe_dict(runtime_state).get("combat_state"))


def _set_combat_state(runtime_state: dict[str, Any], combat_state: dict[str, Any]) -> dict[str, Any]:
    runtime_state = _safe_dict(runtime_state)
    runtime_state["combat_state"] = normalize_combat_state(combat_state)
    return runtime_state


def _active_combat_state_from_runtime_or_simulation(
    runtime_state: dict[str, Any],
    simulation_state: dict[str, Any],
) -> dict[str, Any]:
    combat_state = _safe_dict(_get_combat_state(runtime_state))
    if combat_state.get("active"):
        return combat_state
    combat_state = _safe_dict(_safe_dict(simulation_state).get("combat_state"))
    if combat_state.get("active"):
        return combat_state
    return {}


def _active_combat_utility_kind(
    runtime_state: dict[str, Any],
    semantic_action_record: dict[str, Any],
    player_input: str,
) -> str:
    from app.apps.rpg.session.combat_intent import (
        _extract_active_combat_state_for_turn as _extract_active_combat_state_for_turn,
    )

    from app.apps.rpg.session.combat_turn_actions import (
        _player_input_requests_combat_ability as _player_input_requests_combat_ability,
    )

    combat_state = _safe_dict(_extract_active_combat_state_for_turn(runtime_state))
    if not combat_state.get("active"):
        return ""

    if _player_input_requests_combat_ability(player_input):
        return ""

    text = _safe_str(player_input).strip().lower()
    semantic_kind = _safe_str(
        _safe_dict(semantic_action_record).get("kind")
        or _safe_dict(semantic_action_record).get("action_type")
    ).strip().lower()

    if semantic_kind == "defend" or any(
        term in text for term in ("defend", "guard", "block", "brace", "take cover")
    ):
        return "defend"

    if semantic_kind == "flee" or any(
        term in text for term in ("flee", "run away", "retreat", "escape", "withdraw")
    ):
        return "flee"

    if semantic_kind == "use_item" or any(
        term in text for term in ("use ", "drink ", "quaff ", "consume ", "eat ")
    ):
        return "use_item"

    return ""

__all__ = ['_MAX_ACCEPTED_STATE_CHANGE_EVENTS', '_MAX_APPLIED_PROPOSAL_IDS', '_MAX_LLM_PROPOSAL_CANDIDATES', '_MAX_PROMPT_SCENE_BEATS', '_SEMANTIC_LLM_PROPOSAL_COOLDOWN_TICKS', '_active_combat_state_from_runtime_or_simulation', '_active_combat_utility_kind', '_apply_canonical_state_change_event', '_build_recent_authoritative_turn_facts', '_build_recent_narration_continuity', '_build_semantic_state_change_prompt_contract', '_emit_scene_beat_from_accepted_state_change', '_extract_json_array', '_get_combat_state', '_normalize_llm_text_output', '_record_accepted_state_change_event', '_record_applied_semantic_proposal_id', '_set_combat_state', '_should_generate_llm_semantic_proposals', 'compile_semantic_state_change_to_canonical_delta', 'llm_semantic_proposal_gateway', 'maybe_enqueue_llm_semantic_state_change_proposals', 'normalize_semantic_state_change_llm_output', 'preview_semantic_state_change_prompt', 'process_semantic_state_change_proposals']
