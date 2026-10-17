"""Phase 10.5 — Deterministic expressive runtime dialogue state.

This module owns runtime dialogue state only.

Rules:
    - Deterministic / replay-safe
    - Bounded state
    - Inspector-visible under simulation_state["runtime_state"]
    - No presentation logic
    - No hidden LLM calls
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, cast

from app.apps.rpg.narration.runtime.dialogue_runtime_state import (
    _EMOTION_ORDER,
    _MAX_EMOTION_ACTORS,
    _MAX_INTERRUPTION_LOG,
    _MAX_INTERRUPTS_PER_TICK,
    _MAX_PENDING_INTERRUPTION,
    _MAX_RUNTIME_TURNS,
    _MAX_SEQUENCE_ACTORS,
    _MAX_STREAM_CHUNKS,
    _MAX_TURN_CHUNKS,
    _clamp,
    _extract_actor_list,
    _get_default_runtime_companions,
    _get_default_runtime_npcs,
    _normalize_dialogue_state,
    _normalize_emotion_entry,
    _normalize_emotion_name,
    _normalize_interruption_candidate,
    _normalize_interrupt_log_item,
    _normalize_pending_interruption,
    _normalize_role,
    _normalize_sequence_actor,
    _normalize_status,
    _normalize_stream_chunk,
    _normalize_turn,
    _safe_float,
    _safe_int,
    _sort_key_chunk,
    _sort_key_interruption_candidate,
    _sort_key_interrupt_log,
    _sort_key_pending_interrupt,
    _sort_key_sequence_actor,
    _sort_key_turn,
    build_runtime_sequence_id,
    build_runtime_turn_id,
)
from app.apps.rpg.foundation.safe_values import (
    dict_copy as _safe_dict,
    list_copy as _safe_list,
    safe_str as _safe_str,
)
from app.apps.rpg.narration.runtime.dialogue_runtime_state import (
    _role_precedence as _role_precedence  # noqa: F401,
)



def ensure_runtime_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure simulation_state contains normalized runtime dialogue state.

    This function mutates simulation_state in-place and returns it.
    """
    if not isinstance(simulation_state, dict):
        simulation_state = {}

    runtime_state = simulation_state.setdefault("runtime_state", {})
    if not isinstance(runtime_state, dict):
        runtime_state = simulation_state["runtime_state"] = {}

    dialogue_state = runtime_state.get("dialogue")
    runtime_state["dialogue"] = _normalize_dialogue_state(cast(Any, dialogue_state))
    simulation_state["runtime_state"] = runtime_state
    return simulation_state


def get_runtime_dialogue_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Return normalized runtime dialogue state."""
    simulation_state = ensure_runtime_state(simulation_state)
    runtime_state = _safe_dict(simulation_state.get("runtime_state"))
    return _safe_dict(runtime_state.get("dialogue"))


def get_runtime_emotion(simulation_state: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
    """Return normalized runtime emotional continuity record for an actor."""
    actor_id = _safe_str(actor_id)
    if not actor_id:
        return {
            "actor_id": "",
            "emotion": "neutral",
            "intensity": 0.0,
            "updated_tick": 0,
        }

    dialogue_state = get_runtime_dialogue_state(simulation_state)
    emotions = _safe_dict(dialogue_state.get("emotions"))
    if actor_id not in emotions:
        return {
            "actor_id": actor_id,
            "emotion": "neutral",
            "intensity": 0.0,
            "updated_tick": 0,
        }
    return _normalize_emotion_entry(actor_id, cast(Any, emotions.get(actor_id)))


def _set_runtime_dialogue_state(
    simulation_state: Dict[str, Any],
    dialogue_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist normalized dialogue runtime state back into simulation_state."""
    simulation_state = ensure_runtime_state(simulation_state)
    runtime_state = _safe_dict(simulation_state.get("runtime_state"))
    runtime_state["dialogue"] = _normalize_dialogue_state(cast(Any, dialogue_state))
    simulation_state["runtime_state"] = runtime_state
    return simulation_state


def _find_turn_index_by_id(turns: List[Dict[str, Any]], turn_id: str) -> int:
    turn_id = _safe_str(turn_id)
    for idx, turn in enumerate(turns):
        if _safe_str(_safe_dict(turn).get("turn_id")) == turn_id:
            return idx
    return -1


def _dedupe_and_sort_turn_chunks(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    for chunk in chunks:
        normalized = _normalize_stream_chunk(chunk)
        key = (
            _safe_str(normalized.get("turn_id")),
            _safe_int(normalized.get("chunk_index"), 0),
            _safe_str(normalized.get("actor_id")),
        )
        deduped[key] = normalized
    out = sorted(deduped.values(), key=_sort_key_chunk)
    return out[:_MAX_TURN_CHUNKS]


def _dedupe_and_sort_global_stream_chunks(
    chunks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    deduped: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    insertion_order: List[Tuple[str, int, str]] = []
    for chunk in chunks:
        normalized = _normalize_stream_chunk(chunk)
        key = (
            _safe_str(normalized.get("turn_id")),
            _safe_int(normalized.get("chunk_index"), 0),
            _safe_str(normalized.get("actor_id")),
        )
        if key not in deduped:
            insertion_order.append(key)
        deduped[key] = normalized
    out = [deduped[key] for key in insertion_order if key in deduped]
    return out[-_MAX_STREAM_CHUNKS:]


def _rebuild_turn_text_from_chunks(turn: Dict[str, Any]) -> str:
    turn = _normalize_turn(turn)
    parts: List[str] = []
    for chunk in _safe_list(turn.get("chunks")):
        chunk = _normalize_stream_chunk(chunk)
        text = _safe_str(chunk.get("text"))
        if text:
            parts.append(text)
    return "".join(parts)


def trim_runtime_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize and trim runtime dialogue state to bounded caps."""
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def begin_runtime_turn(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    sequence_index: int,
    actor_id: str,
    speaker_id: str = "",
    speaker_name: str = "",
    role: str = "npc",
    sequence_id: str = "",
    text: str = "",
    status: str = "pending",
    interruption: bool = False,
    interrupt_target_id: str = "",
) -> Dict[str, Any]:
    """Begin or replace a runtime turn deterministically.

    If a turn with the same turn_id already exists, it is replaced in place.
    """
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    turns = list(_safe_list(dialogue_state.get("turns")))

    tick = _safe_int(tick, 0)
    sequence_index = max(0, _safe_int(sequence_index, 0))
    actor_id = _safe_str(actor_id)
    speaker_id = _safe_str(speaker_id) or actor_id
    speaker_name = _safe_str(speaker_name)
    role = _normalize_role(role)
    status = _normalize_status(status if status else "pending")
    if status == "complete":
        status = "pending"

    if not actor_id:
        return simulation_state

    if not sequence_id:
        sequence_id = build_runtime_sequence_id(tick, sequence_index)
    sequence_id = _safe_str(sequence_id)
    turn_id = build_runtime_turn_id(tick, sequence_index, actor_id)

    emotion_record = get_runtime_emotion(simulation_state, actor_id)
    turn = _normalize_turn({
        "turn_id": turn_id,
        "sequence_id": sequence_id,
        "tick": tick,
        "sequence_index": sequence_index,
        "actor_id": actor_id,
        "speaker_id": speaker_id,
        "speaker_name": speaker_name,
        "role": role,
        "text": _safe_str(text),
        "status": status,
        "emotion": emotion_record.get("emotion", "neutral"),
        "interruption": bool(interruption),
        "interrupt_target_id": _safe_str(interrupt_target_id),
        "chunks": [],
    })

    existing_idx = _find_turn_index_by_id(turns, turn_id)
    if existing_idx >= 0:
        turns[existing_idx] = turn
    else:
        turns.append(turn)

    turns = sorted([_normalize_turn(v) for v in turns], key=_sort_key_turn)[-_MAX_RUNTIME_TURNS:]

    stream_state = _safe_dict(dialogue_state.get("stream"))
    stream_state["active"] = True
    stream_state["active_turn_id"] = turn_id

    dialogue_state["active_sequence_id"] = sequence_id
    dialogue_state["active_turn_id"] = turn_id
    dialogue_state["sequence_tick"] = tick
    dialogue_state["turn_cursor"] = sequence_index
    dialogue_state["turns"] = turns
    dialogue_state["stream"] = stream_state

    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def append_runtime_stream_chunk(
    simulation_state: Dict[str, Any],
    *,
    turn_id: str,
    actor_id: str,
    speaker_id: str = "",
    text: str,
    chunk_index: int,
    final: bool = False,
) -> Dict[str, Any]:
    """Append a structured runtime stream chunk deterministically.

    Duplicate chunk keys overwrite older copies deterministically.
    """
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    turns = list(_safe_list(dialogue_state.get("turns")))
    stream_state = _safe_dict(dialogue_state.get("stream"))

    turn_id = _safe_str(turn_id)
    actor_id = _safe_str(actor_id)
    speaker_id = _safe_str(speaker_id) or actor_id
    text = _safe_str(text)
    chunk_index = max(0, _safe_int(chunk_index, 0))

    if not turn_id or not actor_id:
        return simulation_state

    chunk = _normalize_stream_chunk({
        "turn_id": turn_id,
        "chunk_index": chunk_index,
        "actor_id": actor_id,
        "speaker_id": speaker_id,
        "text": text,
        "final": bool(final),
    })

    turn_idx = _find_turn_index_by_id(turns, turn_id)
    if turn_idx < 0:
        return simulation_state

    turn = _normalize_turn(turns[turn_idx])
    turn_chunks = list(_safe_list(turn.get("chunks")))
    turn_chunks.append(chunk)
    turn_chunks = _dedupe_and_sort_turn_chunks(turn_chunks)
    turn["chunks"] = turn_chunks
    turn["text"] = _rebuild_turn_text_from_chunks(turn)
    if turn["status"] not in {"complete", "interrupted"}:
        turn["status"] = "streaming"

    turns[turn_idx] = _normalize_turn(turn)
    turns = sorted(turns, key=_sort_key_turn)[-_MAX_RUNTIME_TURNS:]

    global_chunks = list(_safe_list(stream_state.get("chunks")))
    global_chunks.append(chunk)
    stream_state["chunks"] = _dedupe_and_sort_global_stream_chunks(global_chunks)
    stream_state["active"] = True
    stream_state["active_turn_id"] = turn_id

    dialogue_state["turns"] = turns
    dialogue_state["active_turn_id"] = turn_id
    dialogue_state["stream"] = stream_state

    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def finalize_runtime_turn(
    simulation_state: Dict[str, Any],
    *,
    turn_id: str,
    final_text: str = "",
    final_chunk_text: str = "",
    allow_emotional_fallback: bool = False,
) -> Dict[str, Any]:
    """Finalize a runtime turn and mark stream completion for that turn."""
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    turns = list(_safe_list(dialogue_state.get("turns")))
    stream_state = _safe_dict(dialogue_state.get("stream"))

    turn_id = _safe_str(turn_id)
    if not turn_id:
        return simulation_state

    turn_idx = _find_turn_index_by_id(turns, turn_id)
    if turn_idx < 0:
        return simulation_state

    turn = _normalize_turn(turns[turn_idx])
    turn_chunks = list(_safe_list(turn.get("chunks")))

    if final_chunk_text:
        next_index = 0
        if turn_chunks:
            next_index = max(
                _safe_int(_safe_dict(v).get("chunk_index"), 0)
                for v in turn_chunks
            ) + 1
        final_chunk = _normalize_stream_chunk({
            "turn_id": turn_id,
            "chunk_index": next_index,
            "actor_id": _safe_str(turn.get("actor_id")),
            "speaker_id": _safe_str(turn.get("speaker_id")),
            "text": _safe_str(final_chunk_text),
            "final": True,
        })
        turn_chunks.append(final_chunk)
        turn_chunks = _dedupe_and_sort_turn_chunks(turn_chunks)
        turn["chunks"] = turn_chunks

        global_chunks = list(_safe_list(stream_state.get("chunks")))
        global_chunks.append(final_chunk)
        stream_state["chunks"] = _dedupe_and_sort_global_stream_chunks(global_chunks)

    if final_text:
        turn["text"] = _safe_str(final_text)
    else:
        rebuilt = _rebuild_turn_text_from_chunks(turn)
        if rebuilt:
            turn["text"] = rebuilt
        elif allow_emotional_fallback:
            turn["text"] = build_runtime_fallback_text(
                simulation_state,
                actor_id=_safe_str(turn.get("actor_id")),
                base_text="",
            )
        else:
            turn["text"] = ""

    # Preserve the emotion snapshot captured on the turn, rather than
    # re-reading live runtime emotion here.
    turn["emotion"] = _normalize_emotion_name(turn.get("emotion"))
    turn["status"] = "complete"
    turns[turn_idx] = _normalize_turn(turn)
    turns = sorted(turns, key=_sort_key_turn)[-_MAX_RUNTIME_TURNS:]

    if _safe_str(stream_state.get("active_turn_id")) == turn_id:
        stream_state["active"] = False
        stream_state["active_turn_id"] = ""

    dialogue_state["turns"] = turns
    dialogue_state["stream"] = stream_state
    dialogue_state["active_turn_id"] = ""

    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def mark_runtime_turn_interrupted(
    simulation_state: Dict[str, Any],
    *,
    turn_id: str,
    interrupt_actor_id: str,
    reason: str = "",
    allow_emotional_fallback: bool = False,
) -> Dict[str, Any]:
    """Mark a runtime turn as interrupted and append bounded interruption log."""
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    turns = list(_safe_list(dialogue_state.get("turns")))
    stream_state = _safe_dict(dialogue_state.get("stream"))
    interruption_log = list(_safe_list(dialogue_state.get("interruption_log")))

    turn_id = _safe_str(turn_id)
    interrupt_actor_id = _safe_str(interrupt_actor_id)
    reason = _safe_str(reason)

    if not turn_id or not interrupt_actor_id:
        return simulation_state

    turn_idx = _find_turn_index_by_id(turns, turn_id)
    if turn_idx < 0:
        return simulation_state

    turn = _normalize_turn(turns[turn_idx])
    turn["status"] = "interrupted"
    turn["interruption"] = True
    turn["interrupt_target_id"] = interrupt_actor_id
    if not turn.get("text"):
        rebuilt = _rebuild_turn_text_from_chunks(turn)
        if rebuilt:
            turn["text"] = rebuilt
        elif allow_emotional_fallback:
            turn["text"] = build_runtime_fallback_text(
                simulation_state,
                actor_id=_safe_str(turn.get("actor_id")),
                base_text="",
            )
        else:
            turn["text"] = ""
    turn["emotion"] = _normalize_emotion_name(turn.get("emotion"))
    turns[turn_idx] = _normalize_turn(turn)
    turns = sorted(turns, key=_sort_key_turn)[-_MAX_RUNTIME_TURNS:]

    interruption_log.append({
        "tick": _safe_int(turn.get("tick"), 0),
        "actor_id": interrupt_actor_id,
        "target_id": _safe_str(turn.get("actor_id")),
        "reason": reason,
        "turn_id": turn_id,
    })
    interruption_log = [
        _normalize_interrupt_log_item(v)
        for v in interruption_log
        if isinstance(v, dict)
    ]
    interruption_log = sorted(interruption_log, key=_sort_key_interrupt_log)[-_MAX_INTERRUPTION_LOG:]

    if _safe_str(stream_state.get("active_turn_id")) == turn_id:
        stream_state["active"] = False
        stream_state["active_turn_id"] = ""

    dialogue_state["turns"] = turns
    dialogue_state["interruption_log"] = interruption_log
    dialogue_state["stream"] = stream_state
    dialogue_state["active_turn_id"] = ""

    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def build_runtime_turn_sequence(
    simulation_state: Dict[str, Any],
    *,
    player_actor_id: str = "",
    player_speaker_name: str = "",
    scene_state: Dict[str, Any] | None = None,
    companions: List[Any] | None = None,
    npcs: List[Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build a deterministic multi-speaker runtime sequence for one exchange.

    Order outside combat:
        1. player
        2. companions (stable sorted)
        3. npcs (stable sorted)
    """
    simulation_state = ensure_runtime_state(simulation_state)

    if companions is None:
        companions = _get_default_runtime_companions(simulation_state)
    if npcs is None:
        npcs = _get_default_runtime_npcs(simulation_state, scene_state=scene_state)

    player_id = _safe_str(player_actor_id or simulation_state.get("player_id") or "player")
    player_record = _normalize_sequence_actor({
        "actor_id": player_id,
        "speaker_id": player_id,
        "speaker_name": player_speaker_name,
        "role": "player",
        "sequence_index": 0,
        "priority": 0,
        "present": True,
        "can_speak": True,
    }, default_role="player")

    companion_records = [
        v for v in _extract_actor_list(companions, "companion")
        if bool(v.get("present")) and bool(v.get("can_speak"))
    ]
    companion_records = sorted(companion_records, key=_sort_key_sequence_actor)

    npc_records = [
        v for v in _extract_actor_list(npcs, "npc")
        if bool(v.get("present")) and bool(v.get("can_speak"))
    ]
    npc_records = sorted(npc_records, key=_sort_key_sequence_actor)

    combined: List[Dict[str, Any]] = [player_record]
    combined.extend(companion_records)
    combined.extend(npc_records)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in combined:
        actor_id = _safe_str(item.get("actor_id"))
        if not actor_id or actor_id in seen:
            continue
        seen.add(actor_id)
        deduped.append(item)

    deduped = deduped[:_MAX_SEQUENCE_ACTORS]

    for idx, item in enumerate(deduped):
        item["sequence_index"] = idx

    return deduped


def start_runtime_sequence(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
    sequence_index: int = 0,
    player_actor_id: str = "",
    player_speaker_name: str = "",
    scene_state: Dict[str, Any] | None = None,
    companions: List[Any] | None = None,
    npcs: List[Any] | None = None,
) -> Dict[str, Any]:
    """Start a new runtime sequence and seed active sequence metadata."""
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)

    tick = _safe_int(tick, 0)
    sequence_index = max(0, _safe_int(sequence_index, 0))
    sequence_id = build_runtime_sequence_id(tick, sequence_index)
    participants = build_runtime_turn_sequence(
        simulation_state,
        player_actor_id=player_actor_id,
        player_speaker_name=player_speaker_name,
        scene_state=scene_state,
        companions=companions,
        npcs=npcs,
    )

    dialogue_state["active_sequence_id"] = sequence_id
    dialogue_state["active_turn_id"] = ""
    dialogue_state["sequence_tick"] = tick
    dialogue_state["turn_cursor"] = 0
    dialogue_state["pending_interruptions"] = []
    dialogue_state["stream"] = {
        "active": False,
        "active_turn_id": "",
        "chunks": list(_safe_list(_safe_dict(dialogue_state.get("stream")).get("chunks")))[-_MAX_STREAM_CHUNKS:],
    }
    dialogue_state["sequence_participants"] = participants

    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def choose_runtime_interruptions(
    simulation_state: Dict[str, Any],
    *,
    active_turn_id: str,
    sequence: List[Dict[str, Any]] | None = None,
    context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Choose deterministic bounded interruption candidates.

    This selector is pure with respect to return value. It does not mutate state.
    """
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    turns = list(_safe_list(dialogue_state.get("turns")))
    interruption_log = list(_safe_list(dialogue_state.get("interruption_log")))
    context = _safe_dict(context)

    active_turn_id = _safe_str(active_turn_id)
    if not active_turn_id:
        return []

    active_turn_idx = _find_turn_index_by_id(turns, active_turn_id)
    if active_turn_idx < 0:
        return []

    active_turn = _normalize_turn(turns[active_turn_idx])
    active_tick = _safe_int(active_turn.get("tick"), 0)
    active_actor_id = _safe_str(active_turn.get("actor_id"))

    if sequence is None:
        sequence = _safe_list(dialogue_state.get("sequence_participants"))

    sequence_items = [
        _normalize_sequence_actor(v)
        for v in _safe_list(sequence)
        if isinstance(v, dict)
    ]

    by_actor_id = {
        _safe_str(v.get("actor_id")): v
        for v in sequence_items
        if _safe_str(v.get("actor_id"))
    }

    interruption_count_this_tick = 0
    interrupted_actor_ids_this_tick = set()
    interrupted_target_ids_this_tick = set()
    for item in interruption_log:
        item = _normalize_interrupt_log_item(item)
        if _safe_int(item.get("tick"), 0) != active_tick:
            continue
        interruption_count_this_tick += 1
        interrupted_actor_ids_this_tick.add(_safe_str(item.get("actor_id")))
        interrupted_target_ids_this_tick.add(_safe_str(item.get("target_id")))

    if interruption_count_this_tick >= _MAX_INTERRUPTS_PER_TICK:
        return []

    target_sequence_index = 0
    if active_actor_id in by_actor_id:
        target_sequence_index = _safe_int(by_actor_id[active_actor_id].get("sequence_index"), 0)

    base_priority_bonus = _safe_int(context.get("interruption_priority_bonus"), 0)
    threat_tag = bool(context.get("threat"))
    tension_tag = bool(context.get("tension"))

    candidates: List[Dict[str, Any]] = []
    for item in sequence_items:
        actor_id = _safe_str(item.get("actor_id"))
        if not actor_id or actor_id == active_actor_id:
            continue
        if actor_id in interrupted_actor_ids_this_tick:
            continue
        if active_actor_id in interrupted_target_ids_this_tick:
            continue

        role = _normalize_role(item.get("role"))
        if role not in {"companion", "npc"}:
            continue

        emotion_record = get_runtime_emotion(simulation_state, actor_id)
        emotion_name = _safe_str(emotion_record.get("emotion"))
        emotion_intensity = _safe_float(emotion_record.get("intensity"), 0.0)

        priority = 0
        priority += _safe_int(item.get("interrupt_priority"), 0)
        priority += _safe_int(item.get("interjection_score"), 0)
        priority += base_priority_bonus
        if threat_tag:
            priority += 2
        if tension_tag:
            priority += 1
        if role == "companion":
            priority += 1
        if emotion_name in {"tense", "stern", "wary", "shaken"}:
            priority += 1
        if emotion_intensity >= 0.75:
            priority += 1

        reason = _safe_str(context.get("reason"))
        if not reason:
            if threat_tag and role == "companion":
                reason = "protective_reaction"
            elif tension_tag:
                reason = "tense_interjection"
            else:
                reason = "runtime_interjection"

        candidates.append(_normalize_interruption_candidate({
            "actor_id": actor_id,
            "target_id": active_actor_id,
            "turn_id": active_turn_id,
            "reason": reason,
            "priority": priority,
            "role": role,
            "target_sequence_index": target_sequence_index,
        }))

    deduped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in candidates:
        key = (
            _safe_str(item.get("actor_id")),
            _safe_str(item.get("target_id")),
        )
        current = deduped.get(key)
        if current is None or _sort_key_interruption_candidate(item) < _sort_key_interruption_candidate(current):
            deduped[key] = item

    out = sorted(deduped.values(), key=_sort_key_interruption_candidate)
    out = out[: max(0, _MAX_INTERRUPTS_PER_TICK - interruption_count_this_tick)]
    out = out[:_MAX_PENDING_INTERRUPTION]
    return out


def apply_runtime_interruptions(
    simulation_state: Dict[str, Any],
    *,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Persist pending interruption candidates to runtime state deterministically."""
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)

    normalized = [
        _normalize_pending_interruption(v)
        for v in _safe_list(candidates)
        if isinstance(v, dict)
    ]
    normalized = sorted(normalized, key=_sort_key_pending_interrupt)[:_MAX_PENDING_INTERRUPTION]
    dialogue_state["pending_interruptions"] = normalized
    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def stream_runtime_text_segments(
    simulation_state: Dict[str, Any],
    *,
    turn_id: str,
    actor_id: str,
    speaker_id: str = "",
    segments: List[str],
    finalize: bool = True,
    final_text: str = "",
) -> Dict[str, Any]:
    """Append a deterministic ordered list of text segments as structured stream chunks."""
    simulation_state = ensure_runtime_state(simulation_state)

    normalized_segments = [_safe_str(v) for v in _safe_list(segments)]
    for idx, segment in enumerate(normalized_segments):
        simulation_state = append_runtime_stream_chunk(
            simulation_state,
            turn_id=turn_id,
            actor_id=actor_id,
            speaker_id=speaker_id,
            text=segment,
            chunk_index=idx,
            final=False,
        )

    if finalize:
        simulation_state = finalize_runtime_turn(
            simulation_state,
            turn_id=turn_id,
            final_text=final_text,
        )

    return simulation_state


def _sort_key_emotion_actor_entry(item: Tuple[str, Dict[str, Any]]) -> Tuple[int, str, int]:
    actor_id, payload = item
    payload = _normalize_emotion_entry(actor_id, payload)
    return (
        _safe_int(payload.get("updated_tick"), 0),
        _safe_str(actor_id),
        _EMOTION_ORDER.get(_safe_str(payload.get("emotion")), 999),
    )


def update_runtime_emotion(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
    emotion: str,
    intensity: float,
    tick: int,
) -> Dict[str, Any]:
    """Update bounded short-term emotional continuity for one actor."""
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    emotions = _safe_dict(dialogue_state.get("emotions"))

    actor_id = _safe_str(actor_id)
    if not actor_id:
        return simulation_state

    normalized = _normalize_emotion_entry(actor_id, {
        "emotion": emotion,
        "intensity": intensity,
        "updated_tick": tick,
    })
    emotions[actor_id] = normalized

    items = sorted(emotions.items(), key=_sort_key_emotion_actor_entry)
    if len(items) > _MAX_EMOTION_ACTORS:
        items = items[-_MAX_EMOTION_ACTORS:]

    dialogue_state["emotions"] = {
        _safe_str(k): _normalize_emotion_entry(k, v)
        for k, v in items
        if _safe_str(k)
    }
    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def decay_runtime_emotions(
    simulation_state: Dict[str, Any],
    *,
    tick: int,
) -> Dict[str, Any]:
    """Deterministically decay emotional continuity by tick delta.

    Decay rule:
        - subtract 0.15 per elapsed tick
        - if resulting intensity <= 0.10 => neutral / 0.0
    """
    simulation_state = ensure_runtime_state(simulation_state)
    dialogue_state = get_runtime_dialogue_state(simulation_state)
    emotions = _safe_dict(dialogue_state.get("emotions"))
    tick = _safe_int(tick, 0)

    out: Dict[str, Dict[str, Any]] = {}
    for actor_id in sorted(emotions.keys()):
        current = _normalize_emotion_entry(actor_id, cast(Any, emotions.get(actor_id)))
        updated_tick = _safe_int(current.get("updated_tick"), 0)
        delta = max(0, tick - updated_tick)
        intensity = _safe_float(current.get("intensity"), 0.0)
        if delta > 0:
            intensity = _clamp(intensity - (0.15 * delta), 0.0, 1.0)

        emotion_name = _safe_str(current.get("emotion"))
        if intensity <= 0.10:
            emotion_name = "neutral"
            intensity = 0.0

        out[actor_id] = _normalize_emotion_entry(actor_id, {
            "emotion": emotion_name,
            "intensity": intensity,
            "updated_tick": tick if delta > 0 else updated_tick,
        })

    items = sorted(out.items(), key=_sort_key_emotion_actor_entry)
    if len(items) > _MAX_EMOTION_ACTORS:
        items = items[-_MAX_EMOTION_ACTORS:]

    dialogue_state["emotions"] = {
        _safe_str(k): _normalize_emotion_entry(k, v)
        for k, v in items
        if _safe_str(k)
    }
    return _set_runtime_dialogue_state(simulation_state, dialogue_state)


def build_runtime_style_tags(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
    base_tags: List[Any] | None = None,
) -> List[str]:
    """Build read-only style tags with runtime emotional overlay."""
    actor_id = _safe_str(actor_id)
    tags = [_safe_str(v).strip() for v in _safe_list(base_tags) if _safe_str(v).strip()]
    deduped = set(tags)

    emotion = get_runtime_emotion(simulation_state, actor_id)
    emotion_name = _safe_str(emotion.get("emotion") or "neutral")
    intensity = _safe_float(emotion.get("intensity"), 0.0)

    if actor_id and emotion_name and emotion_name != "neutral" and intensity > 0.0:
        deduped.add(f"emotion:{emotion_name}")

    return sorted(deduped)


def build_runtime_fallback_text(
    simulation_state: Dict[str, Any],
    *,
    actor_id: str,
    base_text: str = "",
) -> str:
    """Build deterministic fallback text influenced by short-term emotion.

    This is used only when upstream generation is absent or empty.
    """
    base_text = _safe_str(base_text).strip()
    if base_text:
        return base_text

    emotion = get_runtime_emotion(simulation_state, actor_id)
    emotion_name = _safe_str(emotion.get("emotion") or "neutral")

    if emotion_name == "warm":
        return "I am with you."
    if emotion_name == "supportive":
        return "We can handle this together."
    if emotion_name == "tense":
        return "Stay ready."
    if emotion_name == "wary":
        return "Something feels off."
    if emotion_name == "stern":
        return "Focus."
    if emotion_name == "shaken":
        return "Give me a moment."
    return "..."
