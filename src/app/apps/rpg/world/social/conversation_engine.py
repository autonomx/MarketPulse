from __future__ import annotations

import logging

from typing import Any, cast

from .conversation_beats import (
    append_beat,
    build_beat_from_conversation_line,
    compute_beat_caps,
    ensure_beats_state,
    trim_beats_state,
)
from .conversation_participants import (
    find_candidate_conversation_groups,
    select_initiator,
    select_next_speaker,
)
from .conversation_pivots import evaluate_pivots
from .conversation_scheduler import (
    PIVOT_TURN_EXTENSION,
    classify_thread_mode,
    compute_thread_importance,
    compute_world_effect_budget,
    should_start_new_thread,
    thread_is_expired,
    thread_is_stale,
)
from .conversation_settings import (
    get_frequency_multiplier,
    resolve_conversation_settings,
    should_suppress_conversations,
)
from .conversation_templates import build_template_line
from .conversation_topics import build_conversation_topic_candidates
from .conversation_world_signals import (
    apply_pending_signals,
    enqueue_signals,
    ensure_signal_state,
    extract_signals_from_beat,
)
from .npc_conversations import (
    append_conversation_line,
    build_conversation_line,
    build_conversation_state,
    close_conversation,
    ensure_conversation_state,
    get_conversation_lines,
    list_active_conversations,
    trim_conversation_state,
    upsert_conversation,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str
from app.apps.rpg.world.contracts import ConversationHooks, ConversationLineWriter

logger = logging.getLogger(__name__)


def _player_location(simulation_state: dict[str, Any], runtime_state: dict[str, Any]) -> str:
    runtime_state = _safe_dict(runtime_state)
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    return _safe_str(runtime_state.get("current_location_id") or player_state.get("location_id"))


def _conversation_exists_for_group(simulation_state: dict[str, Any], location_id: str, participants: list[str], topic_anchor: str) -> bool:
    target = sorted([_safe_str(x) for x in participants if _safe_str(x)])
    for conv in list_active_conversations(simulation_state, location_id=location_id):
        if sorted(conv.get("participants") or []) == target:
            topic = _safe_dict(conv.get("topic"))
            if _safe_str(topic.get("anchor")) == _safe_str(topic_anchor):
                return True
    return False


def open_conversation(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    *,
    kind: str,
    location_id: str,
    participants: list[str],
    topic: dict[str, Any],
    tick: int,
) -> dict[str, Any]:
    settings = resolve_conversation_settings(simulation_state, runtime_state)
    initiator_id = select_initiator(simulation_state, participants)
    player_loc = _player_location(simulation_state, runtime_state)
    conversation = build_conversation_state(
        kind=kind,
        location_id=location_id,
        participants=participants,
        initiator_id=initiator_id,
        topic=topic,
        max_turns=min(
            settings["max_conversation_turns"],
            max(2, settings["avg_conversation_turns"]),
        ),
        player_can_intervene=settings["player_intervention_enabled"],
        player_present=(player_loc == location_id),
        tick=tick,
    )
    upsert_conversation(simulation_state, conversation)
    return conversation


def _resolve_speaker_name(simulation_state: dict[str, Any], speaker_id: str) -> str:
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    row = _safe_dict(npc_index.get(speaker_id))
    return _safe_str(row.get("name")) or _safe_str(speaker_id)


def build_next_conversation_line(
    conversation: dict[str, Any],
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    tick: int,
    write_line: ConversationLineWriter | None = None,
) -> dict[str, Any]:
    settings = resolve_conversation_settings(simulation_state, runtime_state)
    speaker_id = select_next_speaker(conversation, simulation_state)
    recent_lines = get_conversation_lines(simulation_state, cast(Any, conversation.get("conversation_id")))
    speaker_display = _resolve_speaker_name(simulation_state, speaker_id)

    if settings.get("llm_expand_npc_conversations"):
        try:
            mode = _safe_str(_safe_dict(runtime_state).get("mode")).strip().lower() or "live"
            conv_id = _safe_str(conversation.get("conversation_id"))
            turn_num = int(conversation.get("turn_count", 0) or 0) + 1
            record_key = f"conversation_line:{conv_id}:{turn_num}"

            # A live line is written by the caller's narration (``write_line``);
            # without it the tick uses a template line. Replay reads the records.
            if mode == "live" and write_line is not None:
                record = write_line(
                    conversation,
                    speaker_id,
                    simulation_state,
                    runtime_state,
                    recent_lines,
                )
                if record and _safe_dict(record).get("parsed"):
                    # Record into replay structures
                    llm_records = runtime_state.setdefault("llm_records", []) if isinstance(runtime_state, dict) else []
                    llm_records_index = runtime_state.setdefault("llm_records_index", {}) if isinstance(runtime_state, dict) else {}
                    record_index = len(llm_records)
                    llm_records.append(dict(record))
                    llm_records_index[record_key] = record_index

                    parsed = _safe_dict(record.get("parsed"))
                    return build_conversation_line(
                        conversation_id=conv_id,
                        turn=turn_num,
                        speaker=parsed.get("speaker", speaker_id),
                        speaker_name=speaker_display,
                        text=parsed.get("text", ""),
                        kind=parsed.get("kind", "statement"),
                        created_tick=tick,
                        source="llm",
                    )
            elif mode != "live":
                # Replay mode: read back from recorded data
                llm_records = runtime_state.get("llm_records", []) if isinstance(runtime_state, dict) else []
                llm_records_index = _safe_dict(runtime_state.get("llm_records_index")) if isinstance(runtime_state, dict) else {}
                recorded_index = llm_records_index.get(record_key)
                if isinstance(recorded_index, int) and recorded_index < len(llm_records):
                    replay_record = _safe_dict(llm_records[recorded_index])
                    parsed = _safe_dict(replay_record.get("parsed"))
                    if parsed.get("text"):
                        return build_conversation_line(
                            conversation_id=conv_id,
                            turn=turn_num,
                            speaker=parsed.get("speaker", speaker_id),
                            speaker_name=speaker_display,
                            text=parsed.get("text", ""),
                            kind=parsed.get("kind", "statement"),
                            created_tick=tick,
                            source="llm",
                        )
                raise RuntimeError(f"Missing recorded conversation line for replay: {record_key}")
        except ImportError:
            pass

    line_payload = build_template_line(conversation, speaker_id, simulation_state, runtime_state)
    return build_conversation_line(
        conversation_id=cast(Any, conversation.get("conversation_id")),
        turn=int(conversation.get("turn_count", 0) or 0) + 1,
        speaker=speaker_id,
        speaker_name=line_payload.get("speaker_name", speaker_display),
        text=line_payload.get("text", ""),
        kind=line_payload.get("kind", "statement"),
        created_tick=tick,
        source="template",
    )


def try_start_ambient_conversations(simulation_state: dict[str, Any], runtime_state: dict[str, Any], tick: int) -> dict[str, Any]:
    settings = resolve_conversation_settings(simulation_state, runtime_state)
    if not settings["ambient_conversations_enabled"]:
        return simulation_state

    ensure_conversation_state(simulation_state)
    player_loc = _player_location(simulation_state, runtime_state)
    candidate_groups = find_candidate_conversation_groups(simulation_state, player_loc, tick)
    started = 0

    for group in candidate_groups:
        topics = build_conversation_topic_candidates(simulation_state, runtime_state, player_loc, group, tick)
        if not topics:
            continue
        topic = topics[0]
        if _conversation_exists_for_group(simulation_state, player_loc, group, topic.get("anchor", "")):
            continue
        open_conversation(
            simulation_state,
            runtime_state,
            kind="ambient_npc_conversation",
            location_id=player_loc,
            participants=group,
            topic=topic,
            tick=tick,
        )
        started += 1
        if len(list_active_conversations(simulation_state, location_id=player_loc)) >= 2:
            break
        if started >= 2:
            break
    return simulation_state


def try_start_party_reaction_conversation(simulation_state: dict[str, Any], runtime_state: dict[str, Any], player_action: dict[str, Any], tick: int) -> dict[str, Any]:
    settings = resolve_conversation_settings(simulation_state, runtime_state)
    if not settings["party_reaction_interrupts_enabled"]:
        return simulation_state

    runtime_state["last_player_action"] = dict(player_action or {})
    player_loc = _player_location(simulation_state, runtime_state)
    groups = find_candidate_conversation_groups(simulation_state, player_loc, tick)
    if not groups:
        return simulation_state

    group = groups[0]
    topics = build_conversation_topic_candidates(simulation_state, runtime_state, player_loc, group, tick)
    topics = [t for t in topics if _safe_str(_safe_dict(t).get("type")) in {"plan_reaction", "risk_conflict"}]
    if not topics:
        return simulation_state

    topic = topics[0]
    if not _conversation_exists_for_group(simulation_state, player_loc, group, topic.get("anchor", "")):
        open_conversation(
            simulation_state,
            runtime_state,
            kind="party_reaction",
            location_id=player_loc,
            participants=group,
            topic=topic,
            tick=tick,
        )
    return simulation_state


def advance_active_conversations(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    tick: int,
    hooks: ConversationHooks,
) -> dict[str, Any]:
    ensure_beats_state(simulation_state)
    ensure_signal_state(runtime_state)

    settings = resolve_conversation_settings(simulation_state, runtime_state)

    for conv in list_active_conversations(simulation_state):
        conv = dict(conv)
        cid = _safe_str(conv.get("conversation_id"))

        # Expiry / staleness
        if thread_is_expired(conv, tick):
            close_conversation(simulation_state, cid, reason="expired")
            continue

        if thread_is_stale(conv, tick):
            close_conversation(simulation_state, cid, reason="stale")
            continue

        # Beat cap
        mode = _safe_str(conv.get("mode")) or "ambient"
        _, max_beats = compute_beat_caps(mode)
        beat_count = int(conv.get("beat_count", 0) or 0)
        if beat_count >= max_beats:
            close_conversation(simulation_state, cid, reason="beat_cap_reached")
            continue

        # Generate next line
        line = build_next_conversation_line(conv, simulation_state, runtime_state, tick, hooks.write_line)
        append_conversation_line(simulation_state, cid, line)

        # Build beat
        beat = build_beat_from_conversation_line(conv, line, tick)
        append_beat(simulation_state, beat)

        # Signals
        if settings.get("allow_conversation_world_signals", True):
            signals = extract_signals_from_beat(beat, conv)
            if signals:
                enqueue_signals(runtime_state, signals)
                conv["world_effects_emitted"] = int(conv.get("world_effects_emitted", 0) or 0) + len(signals)

        # Update state
        conv["turn_count"] = int(conv.get("turn_count", 0) or 0) + 1
        conv["beat_count"] = int(conv.get("beat_count", 0) or 0) + 1
        conv["updated_tick"] = int(tick or 0)
        conv["last_speaker_id"] = _safe_str(line.get("speaker"))

        upsert_conversation(simulation_state, conv)

    trim_conversation_state(simulation_state)
    return simulation_state


def _post_player_ambient_delay_active(runtime_state: dict[str, Any], settings: dict[str, Any], tick: int) -> bool:
    runtime_state = _safe_dict(runtime_state)
    settings = _safe_dict(settings)

    delay_ticks = int(settings.get("ambient_delay_after_player_turn", 0) or 0)
    if delay_ticks <= 0:
        return False

    last_turn = _safe_dict(runtime_state.get("last_turn_result"))
    if not last_turn:
        return False

    last_player_tick = int(last_turn.get("tick", runtime_state.get("tick", 0)) or 0)
    return (int(tick or 0) - last_player_tick) < delay_ticks


def _should_attempt_ambient_start(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    tick: int,
    settings: dict[str, Any],
) -> bool:
    if not settings.get("ambient_conversations_enabled", True):
        return False

    if _post_player_ambient_delay_active(runtime_state, settings, tick):
        return False

    active_conversations = list_active_conversations(simulation_state)
    if not should_start_new_thread(active_conversations, settings):
        return False

    frequency = _safe_str(settings.get("conversation_frequency")).strip().lower() or "normal"
    multiplier = float(get_frequency_multiplier(settings))

    # Deterministic cadence gate:
    # sparse  -> start only every 4 ticks
    # normal  -> every 2 ticks
    # lively  -> every tick
    cadence = 2
    if frequency == "sparse":
        cadence = 4
    elif frequency == "lively":
        cadence = 1

    if cadence > 1 and (int(tick or 0) % cadence) != 0:
        return False

    return True


def _trim_ambient_overflow(simulation_state: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    max_ambient = int(settings.get("max_concurrent_ambient_threads", 1) or 1)
    ambient = [dict(c) for c in list_active_conversations(simulation_state) if _safe_str(dict(c).get("mode")) in {"", "ambient"}]
    if len(ambient) <= max_ambient:
        return simulation_state

    # Close oldest/least recently updated overflow threads deterministically.
    ambient_sorted = sorted(ambient, key=lambda c: int(dict(c).get("updated_tick", 0) or 0))
    overflow = ambient_sorted[:-max_ambient]
    for conv in overflow:
        close_conversation(simulation_state, cast(Any, conv.get("conversation_id")), reason="ambient_capacity_trim")
    return simulation_state


def run_conversation_tick(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
    tick: int,
    hooks: ConversationHooks,
) -> dict[str, Any]:
    """Sole authoritative conversation lifecycle entrypoint.

    Pipeline:
    1. Resolve canonical settings
    2. Apply suppression checks (combat/stealth)
    3. Start eligible ambient conversations
    4. Advance active conversations (producing beats + signals)
    5. Evaluate pivots (ambient → directed/group)
    6. Update thread metadata (mode reclassification, importance)
    7. Apply pending world signals
    8. Trim conversation + beat state
    """
    ensure_conversation_state(simulation_state)
    ensure_beats_state(simulation_state)
    ensure_signal_state(runtime_state)

    # 1. Resolve canonical settings
    settings = resolve_conversation_settings(simulation_state, runtime_state)

    # 2. Check combat/stealth suppression
    if should_suppress_conversations(simulation_state, settings):
        trim_conversation_state(simulation_state)
        return simulation_state

    # 3. Start eligible ambient conversations, respecting canonical settings.
    if _should_attempt_ambient_start(simulation_state, runtime_state, tick, settings):
        try_start_ambient_conversations(simulation_state, runtime_state, tick)

    # Enforce ambient capacity after any start pass.
    _trim_ambient_overflow(simulation_state, settings)

    # 4. Advance active conversations (beats + signals built inside)
    advance_active_conversations(simulation_state, runtime_state, tick, hooks)

    # 5. Evaluate pivots after advancing
    evaluate_pivots(simulation_state, runtime_state, tick)

    # 6. Update thread metadata (mode, importance, budgets)
    _update_thread_metadata(simulation_state, runtime_state)

    # 7. Apply pending world signals
    result = apply_pending_signals(simulation_state, runtime_state)
    if isinstance(result, tuple) and len(result) == 2:
        simulation_state, runtime_state = result

    # 8. Trim state to bounded sizes
    trim_conversation_state(simulation_state)
    trim_beats_state(simulation_state)

    session_id = _safe_str(runtime_state.get("session_id")).strip()
    if session_id and hooks.after_tick is not None:
        try:
            ambient_result = hooks.after_tick(
                session_id,
                simulation_state,
                runtime_state,
            )
            updated_runtime_state = _safe_dict(ambient_result.get("runtime_state"))
            if updated_runtime_state:
                runtime_state.clear()
                runtime_state.update(updated_runtime_state)
        except Exception:
            # Ambient narration is presentation-only; never fail the authoritative tick.
            logger.debug("suppressed error in %s", "run_conversation_tick", exc_info=True)

    return simulation_state


def _update_thread_metadata(
    simulation_state: dict[str, Any],
    runtime_state: dict[str, Any],
) -> dict[str, Any]:
    """Update metadata on active threads (mode reclassification, importance recalc).

    Uses helpers from conversation_scheduler.
    """
    for conv in list_active_conversations(simulation_state):
        conv = dict(conv)
        new_mode = classify_thread_mode(conv, simulation_state, runtime_state)
        if new_mode != _safe_str(conv.get("mode")):
            # Record pivot in history
            pivot_history = raw_pivot_history if isinstance(raw_pivot_history := conv.get("pivot_history"), list) else []
            pivot_history.append({
                "from_mode": _safe_str(conv.get("mode")),
                "to_mode": new_mode,
                "at_beat": int(conv.get("beat_count", 0) or 0),
            })
            conv["pivot_history"] = pivot_history[-8:]  # bound history
            conv["mode"] = new_mode

            # Recalculate max_turns based on new mode
            _, max_beats = compute_beat_caps(new_mode)
            current_beats = int(conv.get("beat_count", 0) or 0)
            conv["max_turns"] = max(
                conv.get("max_turns", 0) or 0,
                min(max_beats, current_beats + PIVOT_TURN_EXTENSION),
            )

        conv["importance"] = compute_thread_importance(conv)
        conv["world_effect_budget"] = compute_world_effect_budget(conv)
        upsert_conversation(simulation_state, conv)

    return simulation_state
