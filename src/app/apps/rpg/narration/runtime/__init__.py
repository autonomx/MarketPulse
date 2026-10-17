"""Phase 10.5 — Expressive runtime layer.

Owns deterministic runtime dialogue state used between simulation/narrative
systems and read-only presentation builders.
"""
from app.apps.rpg.narration.runtime.dialogue_runtime import (
    append_runtime_stream_chunk,
    apply_runtime_interruptions,
    begin_runtime_turn,
    build_runtime_fallback_text,
    build_runtime_sequence_id,
    build_runtime_style_tags,
    build_runtime_turn_id,
    build_runtime_turn_sequence,
    choose_runtime_interruptions,
    decay_runtime_emotions,
    ensure_runtime_state,
    finalize_runtime_turn,
    get_runtime_dialogue_state,
    get_runtime_emotion,
    mark_runtime_turn_interrupted,
    start_runtime_sequence,
    stream_runtime_text_segments,
    trim_runtime_state,
    update_runtime_emotion,
)

__all__ = [
    "ensure_runtime_state",
    "get_runtime_dialogue_state",
    "build_runtime_turn_id",
    "build_runtime_sequence_id",
    "get_runtime_emotion",
    "begin_runtime_turn",
    "append_runtime_stream_chunk",
    "finalize_runtime_turn",
    "mark_runtime_turn_interrupted",
    "trim_runtime_state",
    "build_runtime_turn_sequence",
    "choose_runtime_interruptions",
    "apply_runtime_interruptions",
    "start_runtime_sequence",
    "stream_runtime_text_segments",
    "update_runtime_emotion",
    "decay_runtime_emotions",
    "build_runtime_style_tags",
    "build_runtime_fallback_text",
]