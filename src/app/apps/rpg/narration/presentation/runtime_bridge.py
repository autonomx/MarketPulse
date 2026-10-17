"""Phase 10.5 — Runtime-to-presentation bridge.

Pure read-only bridge from normalized runtime dialogue state to presentation
payload shape.
"""
from __future__ import annotations

from typing import Any, Dict

from app.apps.rpg.narration.runtime.dialogue_runtime import (
    build_runtime_style_tags,
    get_runtime_dialogue_state,
)
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _build_turn_payload(turn: Dict[str, Any]) -> Dict[str, Any]:
    turn = _safe_dict(turn)
    chunks = [
        {
            "turn_id": _safe_str(chunk.get("turn_id")),
            "chunk_index": _safe_int(chunk.get("chunk_index"), 0),
            "actor_id": _safe_str(chunk.get("actor_id")),
            "speaker_id": _safe_str(chunk.get("speaker_id")),
            "text": _safe_str(chunk.get("text")),
            "final": bool(chunk.get("final")),
        }
        for chunk in _safe_list(turn.get("chunks"))
        if isinstance(chunk, dict)
    ]
    return {
        "turn_id": _safe_str(turn.get("turn_id")),
        "sequence_id": _safe_str(turn.get("sequence_id")),
        "tick": _safe_int(turn.get("tick"), 0),
        "sequence_index": _safe_int(turn.get("sequence_index"), 0),
        "actor_id": _safe_str(turn.get("actor_id")),
        "speaker_id": _safe_str(turn.get("speaker_id")),
        "speaker_name": _safe_str(turn.get("speaker_name")),
        "role": _safe_str(turn.get("role")),
        "text": _safe_str(turn.get("text")),
        "status": _safe_str(turn.get("status")),
        "emotion": _safe_str(turn.get("emotion")),
        "style_tags": [],
        "interruption": bool(turn.get("interruption")),
        "interrupt_target_id": _safe_str(turn.get("interrupt_target_id")),
        "chunks": chunks,
    }


def build_runtime_presentation_payload(simulation_state: Dict[str, Any], runtime_state: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build a pure presentation payload from runtime dialogue state."""
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    runtime_state = _safe_dict(runtime_state)

    turns = [
        _build_turn_payload(v)
        for v in _safe_list(dialogue_state.get("turns"))
        if isinstance(v, dict)
    ]
    for idx, turn in enumerate(turns):
        turns[idx]["style_tags"] = build_runtime_style_tags(
            simulation_state,
            actor_id=_safe_str(turn.get("actor_id")),
            base_tags=[],
        )

    stream_state = _safe_dict(dialogue_state.get("stream"))
    stream_chunks = [
        {
            "turn_id": _safe_str(chunk.get("turn_id")),
            "chunk_index": _safe_int(chunk.get("chunk_index"), 0),
            "actor_id": _safe_str(chunk.get("actor_id")),
            "speaker_id": _safe_str(chunk.get("speaker_id")),
            "text": _safe_str(chunk.get("text")),
            "final": bool(chunk.get("final")),
        }
        for chunk in _safe_list(stream_state.get("chunks"))
        if isinstance(chunk, dict)
    ]
    stream_chunks = sorted(
        stream_chunks,
        key=lambda chunk: (
            _safe_str(chunk.get("turn_id")),
            _safe_int(chunk.get("chunk_index"), 0),
            _safe_str(chunk.get("actor_id")),
        ),
    )

    emotions = []
    for actor_id in sorted(_safe_dict(dialogue_state.get("emotions")).keys()):
        item = _safe_dict(_safe_dict(dialogue_state.get("emotions")).get(actor_id))
        emotions.append({
            "actor_id": _safe_str(actor_id),
            "emotion": _safe_str(item.get("emotion") or "neutral"),
            "intensity": _safe_float(item.get("intensity"), 0.0),
            "updated_tick": _safe_int(item.get("updated_tick"), 0),
        })

    sequence_participants = []
    for item in _safe_list(dialogue_state.get("sequence_participants")):
        item = _safe_dict(item)
        if not item:
            continue
        sequence_participants.append({
            "actor_id": _safe_str(item.get("actor_id")),
            "speaker_id": _safe_str(item.get("speaker_id")),
            "speaker_name": _safe_str(item.get("speaker_name")),
            "role": _safe_str(item.get("role")),
            "sequence_index": _safe_int(item.get("sequence_index"), 0),
        })
    sequence_participants = sorted(
        sequence_participants,
        key=lambda item: (
            _safe_int(item.get("sequence_index"), 0),
            _safe_str(item.get("role")),
            _safe_str(item.get("actor_id")),
        ),
    )

    pending_interruptions = []
    for item in _safe_list(dialogue_state.get("pending_interruptions")):
        item = _safe_dict(item)
        if not item:
            continue
        pending_interruptions.append({
            "actor_id": _safe_str(item.get("actor_id")),
            "target_id": _safe_str(item.get("target_id")),
            "reason": _safe_str(item.get("reason")),
            "priority": _safe_int(item.get("priority"), 0),
        })
    pending_interruptions = sorted(
        pending_interruptions,
        key=lambda item: (
            _safe_int(item.get("priority"), 0),
            _safe_str(item.get("actor_id")),
            _safe_str(item.get("target_id")),
        ),
    )

    interruption_log = []
    for item in _safe_list(dialogue_state.get("interruption_log")):
        item = _safe_dict(item)
        if not item:
            continue
        interruption_log.append({
            "tick": _safe_int(item.get("tick"), 0),
            "actor_id": _safe_str(item.get("actor_id")),
            "target_id": _safe_str(item.get("target_id")),
            "reason": _safe_str(item.get("reason")),
            "turn_id": _safe_str(item.get("turn_id")),
        })
    interruption_log = sorted(
        interruption_log,
        key=lambda item: (
            _safe_int(item.get("tick"), 0),
            _safe_str(item.get("actor_id")),
            _safe_str(item.get("target_id")),
        ),
    )

    return {
        "runtime_dialogue": {
            "active_sequence_id": _safe_str(dialogue_state.get("active_sequence_id")),
            "active_turn_id": _safe_str(dialogue_state.get("active_turn_id")),
            "sequence_tick": _safe_int(dialogue_state.get("sequence_tick"), 0),
            "turn_cursor": _safe_int(dialogue_state.get("turn_cursor"), 0),
            "sequence_participants": sequence_participants,
            "turns": turns,
            "pending_interruptions": pending_interruptions,
            "interruption_log": interruption_log,
            "stream": {
                "active": bool(stream_state.get("active")),
                "active_turn_id": _safe_str(stream_state.get("active_turn_id")),
                "chunks": stream_chunks,
            },
            "emotions": emotions,
        },
        "combat_state": _safe_dict(runtime_state.get("combat_state")),
    }