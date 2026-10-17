"""Construct non-stateful dialogue intent without accepting legacy visible prose."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list | tuple) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _diagnostics(*advisories: Mapping[str, Any]) -> dict[str, Any]:
    for advisory in advisories:
        value = _mapping(advisory).get("first_call_grounding_diagnostics")
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _canonical_npc_id(value: Any) -> str:
    raw_id = _text(value)
    if not raw_id or raw_id.casefold() in {"npc", "unknown", "npc:unknown", "npc:npc"}:
        return ""
    if not raw_id.startswith("npc:"):
        raw_id = f"npc:{raw_id.casefold().replace(' ', '_')}"
    return raw_id


def _speaker(
    action_advisory: Mapping[str, Any],
    semantic_advisory: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    diagnostics = _diagnostics(semantic_advisory, action_advisory)
    packet = _mapping(diagnostics.get("turn_grounding_packet"))
    priority = _mapping(packet.get("priority_context"))
    npc_context = _mapping(packet.get("npc_context"))
    resolution = _mapping(priority.get("dialogue_resolution"))
    addressed_ids = [
        _canonical_npc_id(value) for value in _list(priority.get("addressed_npc_ids"))
        if _canonical_npc_id(value)
    ]
    profiles = [
        _mapping(value) for value in _list(npc_context.get("addressed_npcs"))
        if isinstance(value, Mapping)
    ]
    selected = profiles[0] if profiles else {}
    locked_id = _canonical_npc_id(resolution.get("target_id")) if resolution.get("locked") else ""
    advisory_id = _canonical_npc_id(
        _mapping(semantic_advisory).get("target_id")
        or _mapping(action_advisory).get("target_id")
    )
    candidates = [
        _canonical_npc_id(value) for value in _list(resolution.get("candidate_target_ids"))
        if _canonical_npc_id(value)
    ]
    raw_id = locked_id or (addressed_ids[0] if addressed_ids else "")
    if not raw_id and advisory_id and candidates and advisory_id in candidates:
        raw_id = advisory_id
        resolution = {
            **resolution,
            "target_id": raw_id,
            "resolution_source": "llm_context_resolution",
            "locked": True,
            "ambiguous": False,
            "requires_clarification": False,
            "confidence": float(_mapping(semantic_advisory).get("confidence") or 0.75),
        }
    elif not raw_id and advisory_id and not resolution.get("requires_clarification"):
        raw_id = advisory_id
    name = _text(selected.get("name")) or _text(
        _mapping(semantic_advisory).get("target_name")
        or _mapping(action_advisory).get("target_name")
    )
    if not name and raw_id:
        name = raw_id.split(":", 1)[-1].replace("_", " ").title()
    if raw_id:
        resolution = {
            **resolution,
            "target_id": raw_id,
            "target_name": name,
            "candidate_target_ids": candidates or [raw_id],
            "locked": True,
            "ambiguous": False,
            "requires_clarification": False,
            "confidence": float(resolution.get("confidence") or 1.0),
            "source": "deterministic_dialogue_focus_v1",
        }
    return raw_id, name, resolution


def _clarification_result(
    *,
    session: Mapping[str, Any],
    simulation_state: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
    player_input: str,
    action_advisory: Mapping[str, Any],
    semantic_advisory: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    resolution: Mapping[str, Any],
) -> dict[str, Any]:
    message = "Who are you speaking to?"
    resolved = {
        "ok": False,
        "error": "dialogue_target_unresolved",
        "action_type": "dialogue_clarification",
        "semantic_action_type": "dialogue_clarification",
        "semantic_family": "social",
        "response_mode": "clarification",
        "stateful": False,
        "needs_runtime_resolution": False,
        "dialogue_resolution": deepcopy(dict(resolution)),
        "outcome": "clarification_required",
        "source": "canonical_direct_dialogue_intent_v2",
    }
    return {
        "consumed": True,
        "ok": False,
        "error": "dialogue_target_unresolved",
        "clarification_required": True,
        "clarification": message,
        "result": deepcopy(resolved),
        "resolved_result": deepcopy(resolved),
        "dialogue_resolution": deepcopy(dict(resolution)),
        "visible_response": {"narration": message, "npc": {}},
        "narration": message,
        "final_narration": message,
        "summary": message,
        "first_call_action_advisory": deepcopy(dict(action_advisory)),
        "first_call_semantic_advisory": deepcopy(dict(semantic_advisory)),
        "first_call_grounding_diagnostics": deepcopy(dict(diagnostics)),
        "stateful": False,
        "needs_runtime_resolution": False,
        "simulation_state": deepcopy(dict(simulation_state)),
        "runtime_state": deepcopy(dict(runtime_state)),
        "session": deepcopy(dict(session)),
        "player_input": _text(player_input),
        "source": "canonical_direct_dialogue_intent_v2",
        "legacy_visible_prose_consumed": False,
    }


def build_canonical_direct_dialogue_intent(
    *,
    session: Mapping[str, Any],
    simulation_state: Mapping[str, Any],
    runtime_state: Mapping[str, Any],
    player_input: str,
    action_advisory: Mapping[str, Any],
    semantic_advisory: Mapping[str, Any],
) -> dict[str, Any]:
    """Return target and grounding data only; the Narrative Engine owns all prose."""

    speaker_id, speaker, dialogue_resolution = _speaker(action_advisory, semantic_advisory)
    diagnostics = _diagnostics(semantic_advisory, action_advisory)
    if not speaker_id:
        return _clarification_result(
            session=session,
            simulation_state=simulation_state,
            runtime_state=runtime_state,
            player_input=player_input,
            action_advisory=action_advisory,
            semantic_advisory=semantic_advisory,
            diagnostics=diagnostics,
            resolution=dialogue_resolution,
        )
    resolved = {
        "ok": True,
        "action_type": "npc_interpretive_dialogue",
        "semantic_action_type": "npc_interpretive_dialogue",
        "semantic_family": "social",
        "response_mode": "dialogue",
        "stateful": False,
        "needs_runtime_resolution": False,
        "target_id": speaker_id,
        "target_name": speaker,
        "dialogue_resolution": deepcopy(dialogue_resolution),
        "reply_to_beat_id": _text(dialogue_resolution.get("reply_to_beat_id")),
        "resolution_source": _text(dialogue_resolution.get("resolution_source")),
        "candidate_target_ids": deepcopy(_list(dialogue_resolution.get("candidate_target_ids"))),
        "outcome": "canonical_narrative_required",
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "source": "canonical_direct_dialogue_intent_v2",
    }
    return {
        "consumed": True,
        "ok": True,
        "result": deepcopy(resolved),
        "resolved_result": deepcopy(resolved),
        "dialogue_resolution": deepcopy(dialogue_resolution),
        "npc": {
            "speaker": speaker,
            "speaker_id": speaker_id,
        },
        "visible_response": {},
        "narration": "",
        "final_narration": "",
        "summary": "",
        "first_call_action_advisory": deepcopy(dict(action_advisory)),
        "first_call_semantic_advisory": deepcopy(dict(semantic_advisory)),
        "first_call_grounding_diagnostics": deepcopy(diagnostics),
        "stateful": False,
        "needs_runtime_resolution": False,
        "simulation_state": deepcopy(dict(simulation_state)),
        "runtime_state": deepcopy(dict(runtime_state)),
        "session": deepcopy(dict(session)),
        "player_input": _text(player_input),
        "source": "canonical_direct_dialogue_intent_v2",
        "legacy_visible_prose_consumed": False,
    }
