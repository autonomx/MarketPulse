"""Normalization helpers for runtime dialogue state."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, cast
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str


_MAX_RUNTIME_TURNS = 20
_MAX_TURN_CHUNKS = 12
_MAX_STREAM_CHUNKS = 40
_MAX_PENDING_INTERRUPTION = 4
_MAX_INTERRUPTION_LOG = 12
_MAX_EMOTION_ACTORS = 16
_MAX_INTERRUPTS_PER_TICK = 2
_MAX_SEQUENCE_ACTORS = 8

_VALID_TURN_STATUS = {
    "pending",
    "streaming",
    "complete",
    "interrupted",
}

_VALID_ROLES = {
    "player",
    "companion",
    "npc",
    "system",
}

_VALID_EMOTIONS = {
    "neutral",
    "warm",
    "supportive",
    "tense",
    "wary",
    "stern",
    "shaken",
}

_EMOTION_ORDER = {
    "neutral": 0,
    "warm": 1,
    "supportive": 2,
    "tense": 3,
    "wary": 4,
    "stern": 5,
    "shaken": 6,
}


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


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _normalize_status(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_TURN_STATUS else "complete"


def _normalize_role(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_ROLES else "npc"


def _normalize_emotion_name(v: Any) -> str:
    value = _safe_str(v).strip().lower()
    return value if value in _VALID_EMOTIONS else "neutral"


def build_runtime_sequence_id(tick: int, sequence_index: int) -> str:
    """Build a deterministic runtime sequence id."""
    return f"seq:{_safe_int(tick)}:{_safe_int(sequence_index)}"


def build_runtime_turn_id(tick: int, sequence_index: int, actor_id: str) -> str:
    """Build a deterministic runtime turn id."""
    return f"turn:{_safe_int(tick)}:{_safe_int(sequence_index)}:{_safe_str(actor_id)}"


def _sort_key_turn(turn: Dict[str, Any]) -> Tuple[int, int, str]:
    return (
        _safe_int(turn.get("tick"), 0),
        _safe_int(turn.get("sequence_index"), 0),
        _safe_str(turn.get("actor_id")),
    )


def _sort_key_chunk(chunk: Dict[str, Any]) -> Tuple[str, int, str]:
    return (
        _safe_str(chunk.get("turn_id")),
        _safe_int(chunk.get("chunk_index"), 0),
        _safe_str(chunk.get("actor_id")),
    )


def _sort_key_pending_interrupt(item: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        _safe_int(item.get("priority"), 0) * -1,
        _safe_str(item.get("actor_id")),
        _safe_str(item.get("target_id")),
    )


def _sort_key_interrupt_log(item: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        _safe_int(item.get("tick"), 0),
        _safe_str(item.get("actor_id")),
        _safe_str(item.get("target_id")),
    )


def _normalize_stream_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    chunk = _safe_dict(chunk)
    return {
        "turn_id": _safe_str(chunk.get("turn_id")),
        "chunk_index": max(0, _safe_int(chunk.get("chunk_index"), 0)),
        "actor_id": _safe_str(chunk.get("actor_id")),
        "speaker_id": _safe_str(chunk.get("speaker_id")),
        "text": _safe_str(chunk.get("text")),
        "final": bool(chunk.get("final")),
    }


def _normalize_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    turn = _safe_dict(turn)
    chunks = [
        _normalize_stream_chunk(v)
        for v in _safe_list(turn.get("chunks"))
        if isinstance(v, dict)
    ]
    chunks = sorted(chunks, key=_sort_key_chunk)[:_MAX_TURN_CHUNKS]

    return {
        "turn_id": _safe_str(turn.get("turn_id")),
        "sequence_id": _safe_str(turn.get("sequence_id")),
        "tick": _safe_int(turn.get("tick"), 0),
        "sequence_index": max(0, _safe_int(turn.get("sequence_index"), 0)),
        "actor_id": _safe_str(turn.get("actor_id")),
        "speaker_id": _safe_str(turn.get("speaker_id")),
        "speaker_name": _safe_str(turn.get("speaker_name")),
        "role": _normalize_role(turn.get("role")),
        "text": _safe_str(turn.get("text")),
        "status": _normalize_status(turn.get("status")),
        "emotion": _normalize_emotion_name(turn.get("emotion")),
        "interruption": bool(turn.get("interruption")),
        "interrupt_target_id": _safe_str(turn.get("interrupt_target_id")),
        "chunks": chunks,
    }


def _normalize_pending_interruption(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    return {
        "actor_id": _safe_str(item.get("actor_id")),
        "target_id": _safe_str(item.get("target_id")),
        "reason": _safe_str(item.get("reason")),
        "priority": _safe_int(item.get("priority"), 0),
    }


def _normalize_interrupt_log_item(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    return {
        "tick": _safe_int(item.get("tick"), 0),
        "actor_id": _safe_str(item.get("actor_id")),
        "target_id": _safe_str(item.get("target_id")),
        "reason": _safe_str(item.get("reason")),
        "turn_id": _safe_str(item.get("turn_id")),
    }


def _normalize_emotion_entry(actor_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    entry = _safe_dict(entry)
    return {
        "actor_id": _safe_str(actor_id),
        "emotion": _normalize_emotion_name(entry.get("emotion")),
        "intensity": _clamp(_safe_float(entry.get("intensity"), 0.0), 0.0, 1.0),
        "updated_tick": _safe_int(entry.get("updated_tick"), 0),
    }


def _normalize_emotions_map(emotions_in: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    emotions_in = _safe_dict(emotions_in)
    pairs: List[Tuple[str, Dict[str, Any]]] = []

    for actor_id in sorted(emotions_in.keys()):
        normalized = _normalize_emotion_entry(actor_id, cast(Any, emotions_in.get(actor_id)))
        pairs.append((_safe_str(actor_id), normalized))

    pairs = pairs[:_MAX_EMOTION_ACTORS]

    return {
        actor_id: normalized
        for actor_id, normalized in pairs
        if actor_id
    }


def _normalize_stream_state(stream_state: Dict[str, Any]) -> Dict[str, Any]:
    stream_state = _safe_dict(stream_state)
    chunks = [
        _normalize_stream_chunk(v)
        for v in _safe_list(stream_state.get("chunks"))
        if isinstance(v, dict)
    ]
    chunks = sorted(chunks, key=_sort_key_chunk)[:_MAX_STREAM_CHUNKS]
    return {
        "active": bool(stream_state.get("active")),
        "active_turn_id": _safe_str(stream_state.get("active_turn_id")),
        "chunks": chunks,
    }


def _role_precedence(role: str) -> int:
    role = _normalize_role(role)
    if role == "player":
        return 0
    if role == "companion":
        return 1
    if role == "npc":
        return 2
    return 3


def _normalize_sequence_actor(item: Dict[str, Any], default_role: str = "npc") -> Dict[str, Any]:
    item = _safe_dict(item)
    actor_id = _safe_str(item.get("actor_id"))
    return {
        "actor_id": actor_id,
        "speaker_id": _safe_str(item.get("speaker_id")) or actor_id,
        "speaker_name": _safe_str(item.get("speaker_name")),
        "role": _normalize_role(item.get("role") or default_role),
        "sequence_index": max(0, _safe_int(item.get("sequence_index"), 0)),
        "priority": _safe_int(item.get("priority"), 0),
        "present": bool(item.get("present", True)),
        "can_speak": bool(item.get("can_speak", True)),
        "interrupt_priority": _safe_int(item.get("interrupt_priority"), 0),
        "interjection_score": _safe_int(item.get("interjection_score"), 0),
    }


def _sort_key_sequence_actor(item: Dict[str, Any]) -> Tuple[int, int, str]:
    item = _normalize_sequence_actor(item)
    return (
        _role_precedence(cast(Any, item.get("role"))),
        _safe_int(item.get("priority"), 0),
        _safe_str(item.get("actor_id")),
    )


def _normalize_interruption_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    item = _safe_dict(item)
    actor_id = _safe_str(item.get("actor_id"))
    target_id = _safe_str(item.get("target_id"))
    role = _normalize_role(item.get("role") or "companion")
    return {
        "actor_id": actor_id,
        "target_id": target_id,
        "turn_id": _safe_str(item.get("turn_id")),
        "reason": _safe_str(item.get("reason")),
        "priority": _safe_int(item.get("priority"), 0),
        "role": role,
        "target_sequence_index": max(0, _safe_int(item.get("target_sequence_index"), 0)),
    }


def _sort_key_interruption_candidate(item: Dict[str, Any]) -> Tuple[int, int, int, str, str]:
    item = _normalize_interruption_candidate(item)
    return (
        _safe_int(item.get("priority"), 0) * -1,
        _safe_int(item.get("target_sequence_index"), 0),
        _role_precedence(cast(Any, item.get("role"))),
        _safe_str(item.get("actor_id")),
        _safe_str(item.get("target_id")),
    )


def _get_default_runtime_companions(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve companion participants from the real simulation/player party shape.

    Preference order:
        1. player_state.party_state.companions
        2. top-level companions (legacy / fallback)
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    party_state = _safe_dict(player_state.get("party_state"))
    companions = _safe_list(party_state.get("companions"))
    if companions:
        return companions
    return _safe_list(simulation_state.get("companions"))


def _get_default_runtime_npcs(
    simulation_state: Dict[str, Any],
    *,
    scene_state: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Resolve nearby/present NPC participants from scene-facing state.

    Preference order:
        1. explicit scene_state.present_npcs
        2. explicit scene_state.nearby_npcs
        3. top-level nearby_npcs (legacy / fallback)
    """
    simulation_state = _safe_dict(simulation_state)
    scene_state = _safe_dict(scene_state)
    present_npcs = _safe_list(scene_state.get("present_npcs"))
    if present_npcs:
        return present_npcs
    nearby_from_scene = _safe_list(scene_state.get("nearby_npcs"))
    if nearby_from_scene:
        return nearby_from_scene
    return _safe_list(simulation_state.get("nearby_npcs"))


def _extract_actor_list(values: Any, role: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(_safe_list(values)):
        if isinstance(item, dict):
            normalized = _normalize_sequence_actor({
                "actor_id": item.get("actor_id") or item.get("id") or item.get("speaker_id"),
                "speaker_id": item.get("speaker_id") or item.get("actor_id") or item.get("id"),
                "speaker_name": item.get("speaker_name") or item.get("name"),
                "role": item.get("role") or role,
                "sequence_index": idx,
                "priority": _safe_int(item.get("priority"), idx),
                "present": item.get("present", True),
                "can_speak": item.get("can_speak", True),
                "interrupt_priority": item.get("interrupt_priority", 0),
                "interjection_score": item.get("interjection_score", 0),
            }, default_role=role)
        else:
            normalized = _normalize_sequence_actor({
                "actor_id": item,
                "speaker_id": item,
                "speaker_name": "",
                "role": role,
                "sequence_index": idx,
                "priority": idx,
                "present": True,
                "can_speak": True,
                "interrupt_priority": 0,
                "interjection_score": 0,
            }, default_role=role)

        actor_id = _safe_str(normalized.get("actor_id"))
        if actor_id:
            out.append(normalized)
    return out


def _normalize_dialogue_state(dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    dialogue_state = _safe_dict(dialogue_state)

    turns = [
        _normalize_turn(v)
        for v in _safe_list(dialogue_state.get("turns"))
        if isinstance(v, dict)
    ]
    turns = sorted(turns, key=_sort_key_turn)[-_MAX_RUNTIME_TURNS:]

    pending_interruptions = [
        _normalize_pending_interruption(v)
        for v in _safe_list(dialogue_state.get("pending_interruptions"))
        if isinstance(v, dict)
    ]
    pending_interruptions = sorted(
        pending_interruptions,
        key=_sort_key_pending_interrupt,
    )[:_MAX_PENDING_INTERRUPTION]

    interruption_log = [
        _normalize_interrupt_log_item(v)
        for v in _safe_list(dialogue_state.get("interruption_log"))
        if isinstance(v, dict)
    ]
    interruption_log = sorted(
        interruption_log,
        key=_sort_key_interrupt_log,
    )[-_MAX_INTERRUPTION_LOG:]

    emotions = _normalize_emotions_map(cast(Any, dialogue_state.get("emotions")))
    stream_state = _normalize_stream_state(cast(Any, dialogue_state.get("stream")))

    sequence_participants_in = dialogue_state.get("sequence_participants", [])
    sequence_participants = [
        _normalize_sequence_actor(v)
        for v in _safe_list(sequence_participants_in)
        if isinstance(v, dict)
    ]
    sequence_participants = sorted(
        sequence_participants,
        key=lambda item: (
            _safe_int(item.get("sequence_index"), 0),
            _role_precedence(cast(Any, item.get("role"))),
            _safe_str(item.get("actor_id")),
        ),
    )[:_MAX_SEQUENCE_ACTORS]

    active_sequence_id = _safe_str(dialogue_state.get("active_sequence_id"))
    active_turn_id = _safe_str(dialogue_state.get("active_turn_id"))
    sequence_tick = _safe_int(dialogue_state.get("sequence_tick"), 0)
    turn_cursor = max(0, _safe_int(dialogue_state.get("turn_cursor"), 0))

    return {
        "active_sequence_id": active_sequence_id,
        "active_turn_id": active_turn_id,
        "sequence_tick": sequence_tick,
        "turn_cursor": turn_cursor,
        "turns": turns,
        "pending_interruptions": pending_interruptions,
        "interruption_log": interruption_log,
        "stream": stream_state,
        "emotions": emotions,
        "sequence_participants": sequence_participants,
    }
