from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.narration.ai.conversation_threads import (
    add_thread_line as add_thread_line, build_conversation_thread_prompt_context as build_conversation_thread_prompt_context,
    expire_conversation_threads as expire_conversation_threads, normalize_conversation_threads as normalize_conversation_threads, seed_or_update_thread as seed_or_update_thread,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _normalize_runtime_settings as _normalize_runtime_settings, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list,
    _safe_str as _safe_str,
)
from app.apps.rpg.narration.ai.ambient_dialogue import (
    apply_dialogue_cooldowns as apply_dialogue_cooldowns, build_ambient_dialogue_candidates as build_ambient_dialogue_candidates,
    build_ambient_dialogue_request as build_ambient_dialogue_request, select_ambient_dialogue_candidate as select_ambient_dialogue_candidate,
)
from app.apps.rpg.session.combat_intent import (
    _compact_active_interactions as _compact_active_interactions, _emit_scene_beats_from_active_interactions as _emit_scene_beats_from_active_interactions,
    _filter_salient_player_events as _filter_salient_player_events, _log_interaction_trace as _log_interaction_trace, _utc_now_iso as _utc_now_iso,
    ensure_ambient_runtime_state as ensure_ambient_runtime_state, get_effective_world_behavior as get_effective_world_behavior,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _MAX_RECENT_WORLD_EVENT_ROWS as _MAX_RECENT_WORLD_EVENT_ROWS, _has_blocking_player_turn_narration as _has_blocking_player_turn_narration,
)
from app.apps.rpg.session.player_activity_runtime import (
    _CRITICAL_REACTION_KINDS as _CRITICAL_REACTION_KINDS, _seconds_since_iso as _seconds_since_iso, advance_actor_activities_for_tick as advance_actor_activities_for_tick,
    emit_activity_beats_for_tick as emit_activity_beats_for_tick,
)
from app.apps.rpg.session.idle_runtime import (
    advance_simulation_for_idle as advance_simulation_for_idle, build_idle_player_context as build_idle_player_context,
)
from app.apps.rpg.session.ambient_builder import (
    build_ambient_updates as build_ambient_updates, coalesce_ambient_updates as coalesce_ambient_updates, enqueue_ambient_updates as enqueue_ambient_updates,
    get_pending_ambient_updates as get_pending_ambient_updates, is_player_visible_update as is_player_visible_update, normalize_ambient_state as normalize_ambient_state,
    score_ambient_salience as score_ambient_salience,
)
from app.apps.rpg.narration.ai.npc_initiative import (
    apply_initiative_cooldowns as apply_initiative_cooldowns, apply_world_behavior_bias as apply_world_behavior_bias,
    build_npc_initiative_candidates as build_npc_initiative_candidates, select_npc_initiative_candidate as select_npc_initiative_candidate,
)
from app.apps.rpg.session.ambient_tick_runtime import (
    advance_autonomous_ambient_tick as advance_autonomous_ambient_tick,
)
from app.apps.rpg.narration.ai.scene_continuity import (
    advance_scene as advance_scene, build_continuation_beats as build_continuation_beats, compact_finished_scenes as compact_finished_scenes,
    maybe_build_scene_consequence as maybe_build_scene_consequence, select_continuing_scene as select_continuing_scene, start_persistent_scene as start_persistent_scene,
)
from app.apps.rpg.narration.ai.scene_weaver import (
    apply_scene_cooldowns as apply_scene_cooldowns, build_scene_beats as build_scene_beats, build_scene_candidates as build_scene_candidates,
    select_scene_candidate as select_scene_candidate,
)
from app.apps.rpg.world.world_event_director import (
    apply_world_behavior_to_events as apply_world_behavior_to_events, build_world_event_candidates as build_world_event_candidates,
    convert_events_to_ambient_updates as convert_events_to_ambient_updates, filter_world_events as filter_world_events,
)
from app.apps.rpg.session.world_consequence_runtime import (
    decay_world_consequences_for_tick as decay_world_consequences_for_tick, propagate_activity_consequences_for_tick as propagate_activity_consequences_for_tick,
)
from app.apps.rpg.session.semantic_state_changes import (
    maybe_enqueue_llm_semantic_state_change_proposals as maybe_enqueue_llm_semantic_state_change_proposals,
    process_semantic_state_change_proposals as process_semantic_state_change_proposals,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _clear_stale_last_player_action as _clear_stale_last_player_action, _expire_stale_active_interactions as _expire_stale_active_interactions,
    _refresh_active_interactions_for_tick as _refresh_active_interactions_for_tick, _run_npc_reaction_pass as _run_npc_reaction_pass,
)
from app.apps.rpg.session.action_execution import (
    _check_opening_resolution as _check_opening_resolution, _update_known_npc_ids as _update_known_npc_ids,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)

from .idle_time import recorded_idle_tick_time
from app.runtime.clock import utc_now as _runtime_utc_now
from typing import cast

def _apply_idle_tick_to_session(
    session: dict[str, Any],
    *,
    reason: str = "heartbeat",
) -> dict[str, Any]:
    """Apply one idle tick to an in-memory session.

    This is the canonical implementation for idle ticking.
    Public wrappers should load/save around this helper rather than
    recursively calling apply_idle_tick() in a loop.
    """
    from app.apps.rpg.session.idle_narration_delivery import (
        _apply_ambient_narration_and_delivery as _apply_ambient_narration_and_delivery, _make_initiative_update_from_candidate as _make_initiative_update_from_candidate,
        _make_scene_update_from_beat as _make_scene_update_from_beat,
    )

    session = _copy_dict(session)
    runtime_state = ensure_ambient_runtime_state(_copy_dict(session.get("runtime_state")))
    simulation_state = _safe_dict(session.get("simulation_state"))
    current_tick = int(_safe_dict(session.get("simulation_state")).get("tick", runtime_state.get("tick", 0)) or 0)
    idle_capture_key = f"idle_tick:{current_tick}"
    mode = _safe_str(runtime_state.get("mode")).strip().lower() or "live"
    captured = _safe_dict(_safe_dict(runtime_state.get("llm_records_index")).get(idle_capture_key))
    turn_now, replay_time_error = recorded_idle_tick_time(session)
    if replay_time_error:
        return {"ok": False, "error": replay_time_error}
    if turn_now is None:
        turn_now = _runtime_utc_now()

    if _has_blocking_player_turn_narration(runtime_state):
        return {
            "ok": True,
            "session": session,
            "updates": [],
            "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
            "idle_streak": int(runtime_state.get("idle_streak", 0) or 0),
            "idle_debug_trace": {
                "idle_suppressed": True,
                "reason": "blocking_player_turn_narration",
            },
            "idle_seconds": _seconds_since_iso(
                _safe_str(runtime_state.get("last_real_player_activity_at")),
                now=turn_now,
            ),
            "idle_gate_open": False,
            "settings": _normalize_runtime_settings(_safe_dict(runtime_state.get("runtime_settings"))),
        }

    _log_interaction_trace(
        "idle_tick_start",
        {
            "tick": _safe_int(simulation_state.get("tick"), 0),
            "count": len(_safe_list(simulation_state.get("active_interactions"))),
            "items": _compact_active_interactions(_safe_list(simulation_state.get("active_interactions"))),
            "last_player_action": _safe_dict(runtime_state.get("last_player_action")),
        },
        runtime_state,
    )

    # Simulation tick is authoritative; runtime tick is only a mirror/cache.
    if mode == "replay":
        if not captured:
            return {"ok": False, "error": f"missing_replay_idle_tick_for_tick:{current_tick}"}
        replay_updates = _safe_list(captured.get("updates"))
        if not replay_updates:
            return {"ok": False, "error": f"missing_replay_ambient_updates_for_tick:{current_tick}"}

        # Harden replay contract: updates must already be presentation-ready.
        for idx, update in enumerate(replay_updates):
            update = _safe_dict(update)
            if not _safe_str(update.get("text")):
                return {"ok": False, "error": f"missing_replay_ambient_text_for_tick:{current_tick}:index:{idx}"}
            if not _safe_str(update.get("delivery")):
                return {"ok": False, "error": f"missing_replay_ambient_delivery_for_tick:{current_tick}:index:{idx}"}

        return {
            "ok": True,
            "session": session,
            "updates": replay_updates,
            "latest_seq": int(captured.get("latest_seq", 0) or 0),
            "idle_streak": int(captured.get("idle_streak", 0) or 0),
        }

    advance_result = advance_simulation_for_idle(session, reason=reason)
    if not advance_result.get("ok"):
        return {"ok": False, "error": "idle_advance_failed"}

    before_state = _safe_dict(advance_result.get("before_state"))
    after_state = _safe_dict(advance_result.get("after_state"))
    next_setup = _safe_dict(advance_result.get("next_setup"))

    # Phase 3D: quiet-window suppression after player action
    quiet_ticks = int(runtime_state.get("post_player_quiet_ticks", 0) or 0)
    if quiet_ticks > 0:
        runtime_state["post_player_quiet_ticks"] = quiet_ticks - 1

    # Phase F: effective world behavior config
    session["runtime_state"] = runtime_state
    world_behavior = get_effective_world_behavior(session)

    # Debug trace for full observability
    debug_trace: dict[str, Any] = {
        "reason": reason,
        "tick_before": int(before_state.get("tick", 0) or 0),
        "quiet_ticks_before": int(runtime_state.get("post_player_quiet_ticks", 0) or 0),
        "world_behavior": dict(world_behavior),
        "last_player_action_context": _safe_dict(runtime_state.get("last_player_action_context")),
        "raw_counts": {},
        "selected": {},
        "visibility": {},
        "delivery": {},
        "filters": [],
    }

    # Real idle-seconds calculation
    idle_seconds = _seconds_since_iso(
        _safe_str(runtime_state.get("last_real_player_activity_at")),
        now=turn_now,
    )
    settings = _normalize_runtime_settings(_safe_dict(runtime_state.get("runtime_settings")))
    conversation_idle_seconds = int(settings.get("idle_conversation_seconds", 15) or 15)
    prior_idle_streak = int(runtime_state.get("idle_streak", 0) or 0)
    idle_gate_open = bool(settings.get("idle_conversations_enabled")) and (
        idle_seconds >= conversation_idle_seconds
        or prior_idle_streak >= 2
    )
    debug_trace["idle_seconds"] = idle_seconds
    debug_trace["idle_gate_open"] = idle_gate_open
    debug_trace["idle_gate_reason"] = (
        "time_threshold"
        if idle_seconds >= conversation_idle_seconds else
        ("idle_streak" if prior_idle_streak >= 2 else "closed")
    )
    debug_trace["conversation_idle_seconds"] = conversation_idle_seconds

    player_context = build_idle_player_context(
        after_state,
        runtime_state,
        filter_salient_player_events=_filter_salient_player_events,
    )
    context = {
        "player_location": _safe_str(player_context.get("player_location")),
        "nearby_npc_ids": _safe_list(player_context.get("nearby_npc_ids")),
        "recent_ambient_ids": _safe_list(runtime_state.get("recent_ambient_ids")),
    }

    raw_updates = build_ambient_updates(before_state, after_state, runtime_state)

    # Phase 2: NPC initiative candidates (in addition to ambient dialogue)
    initiative_candidates = build_npc_initiative_candidates(
        after_state, runtime_state, player_context,
    )
    # Phase F4: apply world behavior bias to initiative candidates
    initiative_candidates = apply_world_behavior_bias(initiative_candidates, world_behavior)
    selected_initiative = select_npc_initiative_candidate(initiative_candidates, runtime_state)

    # ── Phase G / G+1: scene weaving + continuity ───────────────────
    scene_beats = []
    current_tick = int(after_state.get("tick", runtime_state.get("tick", 0)) or 0)

    autonomous_conversation_result = advance_autonomous_ambient_tick(
        player_input="__autonomous_idle__",
        simulation_state=after_state,
        runtime_state=runtime_state,
        tick=current_tick,
    )

    continuing_scene = select_continuing_scene(runtime_state, after_state, current_tick)
    selected_scene = None

    if continuing_scene:
        scene_beats = build_continuation_beats(continuing_scene, after_state)[:3]
        runtime_state = advance_scene(
            runtime_state,
            _safe_str(continuing_scene.get("scene_id")),
            current_tick,
            player_ignored=True,
        )

        for beat in scene_beats:
            raw_updates.append(_make_scene_update_from_beat(beat))

    if autonomous_conversation_result.get("applied"):
        conversation_result = _safe_dict(autonomous_conversation_result.get("conversation_result"))
        beat = _safe_dict(conversation_result.get("beat"))
        if beat:
            raw_updates.append({
                "tick": current_tick,
                "kind": "npc_to_npc",
                "priority": 0.6,
                "interrupt": False,
                "speaker_id": _safe_str(beat.get("speaker_id")),
                "speaker_name": _safe_str(beat.get("speaker_name")),
                "target_id": _safe_str(beat.get("listener_id")),
                "target_name": _safe_str(beat.get("listener_name")),
                "location_id": _safe_str(
                    beat.get("location_id")
                    or _safe_dict(conversation_result.get("topic")).get("location_id")
                ),
                "text": _safe_str(beat.get("line")),
                "structured": {
                    "lane": "autonomous_living_conversation",
                    "thread_id": _safe_str(beat.get("thread_id")),
                    "topic_id": _safe_str(beat.get("topic_id")),
                    "topic_type": _safe_str(beat.get("topic_type")),
                },
                "source_event_ids": [],
                "source": "deterministic_ambient_tick_runtime",
                "created_at": _utc_now_iso(),
                "lane": "idle",
            })

        # When continuing a scene, suppress same-speaker standalone initiative.
        continuing_participants = set(_safe_list(cast(dict[str, Any], continuing_scene).get("participants")))
        if selected_initiative and _safe_str(selected_initiative.get("speaker_id")) in continuing_participants:
            selected_initiative = None
    else:
        scene_candidates = build_scene_candidates(
            after_state,
            runtime_state,
            player_context,
        )
        selected_scene = select_scene_candidate(scene_candidates, runtime_state)
        scene_runtime = _safe_dict(runtime_state.get("scene_runtime"))
        last_scene_tick = int(scene_runtime.get("last_scene_tick", -999) or -999)

        if selected_scene and (current_tick - last_scene_tick) < 2:
            selected_scene = None

        if selected_scene and selected_initiative:
            scene_participants = set(_safe_list(selected_scene.get("participants")))
            if _safe_str(selected_initiative.get("speaker_id")) in scene_participants:
                selected_initiative = None

        if selected_scene:
            runtime_state = apply_scene_cooldowns(runtime_state, selected_scene)
            runtime_state = start_persistent_scene(runtime_state, selected_scene, current_tick)
            scene_beats = build_scene_beats(
                selected_scene,
                after_state,
                runtime_state,
            )[:3]

            scene_runtime = _safe_dict(runtime_state.get("scene_runtime"))
            scene_runtime["last_scene_tick"] = current_tick
            runtime_state["scene_runtime"] = scene_runtime

            for beat in scene_beats:
                raw_updates.append(_make_scene_update_from_beat(beat))

    if selected_initiative:
        runtime_state = apply_initiative_cooldowns(runtime_state, selected_initiative)
        raw_updates.append(
            _make_initiative_update_from_candidate(selected_initiative)
        )

    # Phase 6: world event director
    world_event_candidates = build_world_event_candidates(
        after_state, runtime_state, player_context,
    )
    world_event_candidates = apply_world_behavior_to_events(
        world_event_candidates, world_behavior,
    )
    filtered_events = filter_world_events(world_event_candidates, session)
    event_updates = convert_events_to_ambient_updates(filtered_events, runtime_state)
    raw_updates.extend(event_updates)

    # ── Reaction lane: immediate NPC reactions to player actions ──
    reaction_candidates = build_ambient_dialogue_candidates(
        after_state, runtime_state, player_context, lane="reaction",
    )
    reaction_initiative = build_npc_initiative_candidates(
        after_state, runtime_state, player_context, lane="reaction",
    )
    reaction_initiative = apply_world_behavior_bias(reaction_initiative, world_behavior)
    reaction_candidates.extend(reaction_initiative)
    selected_reaction = select_ambient_dialogue_candidate(reaction_candidates, runtime_state)
    if selected_reaction and quiet_ticks > 0:
        # Reaction lane bypasses quiet suppression for important kinds
        rk = _safe_str(selected_reaction.get("kind"))
        if rk not in _CRITICAL_REACTION_KINDS:
            selected_reaction = None
    if selected_reaction:
        runtime_state = apply_dialogue_cooldowns(runtime_state, selected_reaction)
        dialogue_update = _make_dialogue_update_from_candidate(
            selected_reaction,
            {
                "scene_id": _safe_str(player_context.get("scene_id")),
                "world_summary": _safe_str(player_context.get("world_summary")),
            },
        )
        runtime_state = _record_dialogue_update_into_conversation_thread(
            runtime_state,
            dialogue_update,
            current_tick,
        )
        raw_updates.append(dialogue_update)

    # ── Idle conversation lane: only if idle gate is open ──
    idle_dialogue_candidates: list[dict[str, Any]] = []
    selected_dialogue = None
    if idle_gate_open:
        idle_dialogue_candidates = build_ambient_dialogue_candidates(
            after_state, runtime_state, player_context, lane="idle",
        )
        selected_dialogue = select_ambient_dialogue_candidate(idle_dialogue_candidates, runtime_state)
    if selected_dialogue:
        runtime_state = apply_dialogue_cooldowns(runtime_state, selected_dialogue)
        dialogue_update = _make_dialogue_update_from_candidate(
            selected_dialogue,
            {
                "scene_id": _safe_str(player_context.get("scene_id")),
                "world_summary": _safe_str(player_context.get("world_summary")),
            },
        )
        runtime_state = _record_dialogue_update_into_conversation_thread(
            runtime_state,
            dialogue_update,
            current_tick,
        )
        raw_updates.append(dialogue_update)

    # ── Phase F2: Autonomous conversation tick (real idle/world tick loop) ──
    # This is the canonical integration point: pseudo __ambient_tick__ commands
    # are for forced/test use only. Real idle ticks gate by autonomous_ticks_enabled.
    _idle_autonomous_tick_result = advance_autonomous_ambient_tick(
        player_input="__real_idle_world_tick__",
        simulation_state=after_state,
        runtime_state=runtime_state,
        tick=current_tick,
    )
    debug_trace["autonomous_conversation_tick"] = _idle_autonomous_tick_result
    if _idle_autonomous_tick_result.get("applied"):
        _idle_conv_result = _safe_dict(_idle_autonomous_tick_result.get("conversation_result"))
        _idle_beat = _safe_dict(_idle_conv_result.get("beat"))
        if _idle_beat.get("line"):
            raw_updates.append({
                "type": "npc_conversation",
                "kind": "npc_conversation",
                "text": _safe_str(_idle_beat.get("line")),
                "speaker_id": _safe_str(_idle_beat.get("speaker_id")),
                "speaker_name": _safe_str(_idle_beat.get("speaker_name")),
                "listener_id": _safe_str(_idle_beat.get("listener_id")),
                "listener_name": _safe_str(_idle_beat.get("listener_name")),
                "delivery": "ambient",
                "thread_id": _safe_str(_idle_beat.get("thread_id")),
                "topic": _safe_str(
                    _safe_dict(_idle_conv_result.get("topic")).get("title") or ""
                ),
                "tick": current_tick,
                "source": "deterministic_ambient_tick_runtime",
            })

    # Record debug trace counts
    debug_trace["raw_counts"] = {
        "ambient_updates": len(raw_updates),
        "initiative_candidates": len(initiative_candidates),
        "reaction_candidates": len(reaction_candidates),
        "idle_dialogue_candidates": len(idle_dialogue_candidates),
        "scene_beats": len(scene_beats) if scene_beats else 0,
        "world_event_candidates": len(world_event_candidates),
    }
    debug_trace["autonomous_living_conversation"] = _safe_dict(autonomous_conversation_result)
    debug_trace["selected"] = {
        "initiative": _safe_dict(selected_initiative) if selected_initiative else {},
        "reaction": _safe_dict(selected_reaction) if selected_reaction else {},
        "idle_dialogue": _safe_dict(selected_dialogue) if selected_dialogue else {},
        "scene": _safe_dict(selected_scene) if selected_scene else {},
    }

    visible = [u for u in raw_updates if is_player_visible_update(u, session)]
    for u in visible:
        u["priority"] = score_ambient_salience(u, context)
    coalesced = coalesce_ambient_updates(visible, runtime_state)
    debug_trace["visibility"] = {
        "visible_count": len(visible),
        "coalesced_count": len(coalesced),
    }

    runtime_state = enqueue_ambient_updates(runtime_state, coalesced)

    # Scene continuity cleanup + scene-driven consequence
    scene_runtime = _safe_dict(runtime_state.get("scene_runtime"))
    for scene in _safe_list(scene_runtime.get("active_scenes")):
        consequence = maybe_build_scene_consequence(scene, after_state)
        if consequence:
            scene["consequence_emitted"] = True
            runtime_state = enqueue_ambient_updates(runtime_state, [consequence])
    runtime_state = compact_finished_scenes(runtime_state)
    queued_updates = get_pending_ambient_updates(
        {"runtime_state": runtime_state},
        after_seq=max(0, int(runtime_state.get("ambient_seq", 0) or 0) - len(coalesced)),
        limit=max(1, len(coalesced) or 1),
    )
    narrated_updates, runtime_state = _apply_ambient_narration_and_delivery(
        session=session,
        updates=queued_updates,
        after_state=after_state,
        runtime_state=runtime_state,
        idle_capture_key=idle_capture_key,
    )
    if narrated_updates:
        queue = _safe_list(runtime_state.get("ambient_queue"))
        by_seq = {int(_safe_dict(u).get("seq", 0) or 0): u for u in narrated_updates}
        runtime_state["ambient_queue"] = [
            _copy_dict(by_seq.get(int(_safe_dict(item).get("seq", 0) or 0)) or item)
            for item in queue
        ]

    runtime_state["idle_streak"] = int(runtime_state.get("idle_streak", 0) or 0) + 1
    runtime_state["last_idle_tick_at"] = _utc_now_iso()
    runtime_state["tick"] = int(after_state.get("tick", runtime_state.get("tick", 0)) or 0)
    runtime_state = normalize_ambient_state(runtime_state)

    # Advance living-world activities
    runtime_state = advance_actor_activities_for_tick(after_state, runtime_state)
    runtime_state = emit_activity_beats_for_tick(after_state, runtime_state)
    runtime_state = propagate_activity_consequences_for_tick(after_state, runtime_state)
    runtime_state = decay_world_consequences_for_tick(after_state, runtime_state)

    # Use the advanced simulation state for emitted scene beats.
    simulation_state = after_state

    # Derive replay-safe, player-facing scene beats AFTER tick advancement so
    # the emitted beats reflect the newly advanced interaction state.
    runtime_state = _emit_scene_beats_from_active_interactions(simulation_state, runtime_state)

    # Ask the LLM for bounded semantic state-change proposals only when there
    # is no active unresolved interaction and no queued proposals already.
    runtime_state = maybe_enqueue_llm_semantic_state_change_proposals(simulation_state, runtime_state)

    # NPC reaction pass
    authoritative_tick = current_tick
    simulation_state, runtime_state = _run_npc_reaction_pass(
        simulation_state,
        runtime_state,
        authoritative_tick,
    )
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state

    # Compile and apply structured semantic state-change proposals, then emit
    # beats from the accepted canonical deltas.
    simulation_state, runtime_state = process_semantic_state_change_proposals(simulation_state, runtime_state)

    session["runtime_state"] = runtime_state

    # Phase 5C: check opening resolution during idle
    runtime_state["opening_runtime"] = _check_opening_resolution(session)
    session["runtime_state"] = runtime_state

    session["simulation_state"] = simulation_state
    session["setup_payload"] = next_setup
    session["runtime_state"] = runtime_state

    manifest = _safe_dict(session.get("manifest"))
    manifest["updated_at"] = _utc_now_iso()
    session["manifest"] = manifest

    runtime_state.setdefault("llm_records", [])
    runtime_state.setdefault("llm_records_index", {})

    # FIX: prevent [-0:] returning entire queue when no updates were emitted
    queue = _safe_list(runtime_state.get("ambient_queue"))
    emitted_count = len(narrated_updates) if narrated_updates else len(coalesced)
    if emitted_count > 0:
        final_updates = queue[-emitted_count:]
    else:
        final_updates = []
    idle_record = {
        "type": "idle_tick",
        "tick": current_tick,
        "now": turn_now.isoformat(),
        "reason": reason,
        "updates": final_updates,
        "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
        "idle_streak": int(runtime_state.get("idle_streak", 0) or 0),
        # Phase 8: capture initiative and event decisions for replay
        "initiative_candidate": _safe_dict(selected_initiative) if selected_initiative else None,
        "scene_candidate": _safe_dict(selected_scene) if selected_scene else None,
        "scene_beats_emitted": len(scene_beats) if scene_beats else 0,
        "continuing_scene": _safe_dict(continuing_scene) if continuing_scene else None,
        "world_events_emitted": len(event_updates) if event_updates else 0,
        "dialogue_candidate": _safe_dict(selected_dialogue) if selected_dialogue else None,
        "autonomous_living_conversation": _safe_dict(autonomous_conversation_result),
    }
    runtime_state["llm_records"].append(idle_record)
    runtime_state["llm_records_index"][idle_capture_key] = idle_record
    session["runtime_state"] = runtime_state

    # Update known NPC list after idle tick
    runtime_state = _update_known_npc_ids(runtime_state, after_state)

    # Record debug trace
    runtime_state["idle_debug_trace"] = debug_trace

    # Update recent world event rows for frontend
    try:
        from app.apps.rpg.foundation.analytics.world_events import build_incremental_world_event_rows
        new_rows = build_incremental_world_event_rows(after_state, runtime_state, debug_trace)

        existing_rows = _safe_list(runtime_state.get("recent_world_event_rows"))

        merged_rows = existing_rows + new_rows
        deduped_rows: list[dict[str, Any]] = []
        seen_event_ids = set()
        for row in reversed(merged_rows):
            row = _safe_dict(row)
            event_id = _safe_str(row.get("event_id")).strip()
            if not event_id:
                event_id = f"recent_world_event:{len(deduped_rows)}"
                row["event_id"] = event_id
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
            deduped_rows.append(row)
        deduped_rows.reverse()
        runtime_state["recent_world_event_rows"] = deduped_rows[-_MAX_RECENT_WORLD_EVENT_ROWS:]

    except (ImportError, AttributeError):
        pass  # world_events module may not be available yet

    session["runtime_state"] = runtime_state

    # Force authoritative tick persistence
    after_state = _safe_dict(after_state)
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)

    authoritative_tick = (
        int(after_state.get("tick", 0) or 0)
        or int(after_state.get("current_tick", 0) or 0)
        or int(simulation_state.get("tick", 0) or 0)
        or int(simulation_state.get("current_tick", 0) or 0)
        or int(runtime_state.get("tick", 0) or 0)
    )

    simulation_state["tick"] = authoritative_tick
    simulation_state["current_tick"] = authoritative_tick
    simulation_state = _refresh_active_interactions_for_tick(simulation_state, authoritative_tick)
    simulation_state = _expire_stale_active_interactions(simulation_state, authoritative_tick)
    runtime_state = normalize_conversation_threads(runtime_state)
    runtime_state = expire_conversation_threads(
        runtime_state,
        current_tick=authoritative_tick,
    )
    runtime_state["tick"] = authoritative_tick
    simulation_state, runtime_state = _run_npc_reaction_pass(
        simulation_state,
        runtime_state,
        authoritative_tick,
    )
    runtime_state = _clear_stale_last_player_action(runtime_state, authoritative_tick)

    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state



    return {
        "ok": True,
        "session": session,
        "updates": final_updates,
        "conversation_threads": build_conversation_thread_prompt_context(
            runtime_state,
            current_tick=authoritative_tick,
            limit=6,
        ),
        "latest_seq": int(runtime_state.get("ambient_seq", 0) or 0),
        "idle_streak": int(runtime_state.get("idle_streak", 0) or 0),
        "idle_debug_trace": _safe_dict(runtime_state.get("idle_debug_trace")),
        "idle_seconds": idle_seconds,
        "idle_gate_open": idle_gate_open,
        "settings": settings,
    }



_RECAP_LOW_VALUE_PHRASES = (
    "watches the situation carefully",
    "checks in with",
    "waits nearby",
    "remains nearby",
    "observes quietly",
    "stands by",
    "keeps watch",
    "lingers nearby",
    "looks on",
)


def _is_meaningful_recap_text(text):
    text = _safe_str(text).strip().lower()
    if not text:
        return False
    for phrase in _RECAP_LOW_VALUE_PHRASES:
        if phrase in text:
            return False
    return True


def _score_recap_text(text):
    text = _safe_str(text).strip().lower()
    if not text:
        return -100

    score = 0

    strong_terms = (
        "attacks", "wounded", "killed", "defeated", "escapes", "stolen",
        "discovers", "reveals", "unlocked", "opens", "collapses", "burns",
        "ambush", "fight", "combat", "injured", "dies", "arrested",
        "quest", "objective", "rumor", "secret", "clue", "evidence",
        "arrives", "departs", "missing", "threat", "danger", "pressure",
        "faction", "betray", "alliance", "consequence", "changed",
        "moved", "travel", "entered", "left", "scene", "location",
    )

    medium_terms = (
        "argues", "warns", "demands", "refuses", "agrees", "offers",
        "searches", "investigates", "hides", "prepares", "gathers",
        "reports", "announces", "tracks", "follows", "negotiates",
    )

    for token in strong_terms:
        if token in text:
            score += 5

    for token in medium_terms:
        if token in text:
            score += 2

    for phrase in _RECAP_LOW_VALUE_PHRASES:
        if phrase in text:
            score -= 10

    return score


def _choose_meaningful_recap_lines(items, limit=5):
    candidates = []
    seen = set()

    for item in _safe_list(items):
        label = ""
        if isinstance(item, dict):
            label = (
                _safe_str(item.get("summary")) or
                _safe_str(item.get("description")) or
                _safe_str(item.get("title")) or
                _safe_str(item.get("name")) or
                _safe_str(item.get("label")) or
                _safe_str(item.get("text"))
            )
        else:
            label = _safe_str(item)

        label = label.strip()
        if not label or label in seen:
            continue
        seen.add(label)

        if not _is_meaningful_recap_text(label):
            continue

        candidates.append((_score_recap_text(label), label))

    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return [label for _, label in candidates[:limit]]


def _coerce_recap_labels(items, limit=5):
    out = []
    seen = set()
    for item in _safe_list(items):
        label = ""
        if isinstance(item, dict):
            label = (
                _safe_str(item.get("summary")) or
                _safe_str(item.get("description")) or
                _safe_str(item.get("title")) or
                _safe_str(item.get("name")) or
                _safe_str(item.get("label")) or
                _safe_str(item.get("text"))
            )
        else:
            label = _safe_str(item)
        label = label.strip()
        if label and label not in seen:
            seen.add(label)
            out.append(label)
        if len(out) >= limit:
            break
    return out


def _build_player_facing_resume_summary(scene_title, location_name, excess_ticks, has_sections):
    scene_title = _safe_str(scene_title).strip()
    location_name = _safe_str(location_name).strip()
    moments = int(excess_ticks or 0)

    if has_sections:
        if scene_title and location_name:
            return (
                f"While you were away, the world shifted around {location_name} "
                f"and the situation in {scene_title} moved forward over {moments} ticks."
            )
        if scene_title:
            return f"While you were away, the situation in {scene_title} moved forward over {moments} ticks."
        if location_name:
            return f"While you were away, events developed around {location_name} over {moments} ticks."
        return f"While you were away, the world changed in meaningful ways over {moments} ticks."

    # No meaningful sections survived filtering, so keep the summary honest.
    if scene_title and location_name:
        return f"While you were away, {location_name} remained active and the situation in {scene_title} continued to evolve."
    if scene_title:
        return f"While you were away, the situation in {scene_title} continued to evolve."
    return "While you were away, time passed and nearby actors continued their routines."


def _build_resume_fallback_recap(session, runtime_state, excess_ticks):
    session = _safe_dict(session)
    runtime_state = _safe_dict(runtime_state)
    simulation_state = _safe_dict(session.get("simulation_state"))

    scene = _safe_dict(session.get("scene"))
    world = _safe_dict(session.get("world"))
    npcs = _safe_list(session.get("npcs"))

    scene_title = (
        _safe_str(scene.get("title")) or
        _safe_str(simulation_state.get("scene_title")) or
        _safe_str(world.get("title")) or
        "The world moved on in your absence."
    )
    location_name = (
        _safe_str(scene.get("location")) or
        _safe_str(simulation_state.get("location_name")) or
        _safe_str(world.get("setting"))
    )

    npc_updates = _coerce_recap_labels(npcs, limit=4)
    director_activity = _coerce_recap_labels(runtime_state.get("director_log"), limit=4)

    has_sections = bool(npc_updates or director_activity)

    recap = {
        "kind": "world_advance_recap",
        "summary": _build_player_facing_resume_summary(
            scene_title,
            location_name,
            excess_ticks,
            has_sections,
        ),
        "additional_moments": int(excess_ticks or 0),
        "world_events": [],
        "consequences": [],
        "threads": [],
        "npc_updates": npc_updates,
        "director_activity": director_activity,
    }

    return recap


def _recap_has_renderable_content(recap):
    recap = _safe_dict(recap)
    if not recap:
        return False
    for key in ("world_events", "consequences", "threads", "npc_updates", "director_activity"):
        if _safe_list(recap.get(key)):
            return True
    return bool(_safe_str(recap.get("summary")))


def _make_dialogue_update_from_candidate(
    candidate: dict[str, Any],
    session_context: dict[str, Any],
) -> dict[str, Any]:
    req = build_ambient_dialogue_request(candidate, session_context)
    return {
        "tick": int(req.get("tick", 0) or 0),
        "kind": _safe_str(req.get("kind") or "npc_to_player"),
        "priority": float(_safe_dict(candidate).get("salience", 0.0) or 0.0),
        "interrupt": bool(req.get("interrupt")),
        "speaker_id": _safe_str(req.get("speaker_id")),
        "speaker_name": _safe_str(req.get("speaker_name")),
        "target_id": _safe_str(req.get("target_id")),
        "target_name": _safe_str(req.get("target_name")),
        "scene_id": _safe_str(req.get("scene_id")),
        "location_id": _safe_str(req.get("location_id")),
        "text": _safe_str(req.get("text_hint")),
        "structured": {
            "emotion": _safe_str(req.get("emotion")),
            "lane": _safe_str(candidate.get("lane") or "idle"),
            "world_context": _safe_str(req.get("world_context")),
        },
        "source_event_ids": [],
        "source": "dialogue",
        "created_at": _utc_now_iso(),
        "lane": _safe_str(candidate.get("lane") or "idle"),
    }


def _record_dialogue_update_into_conversation_thread(
    runtime_state: dict[str, Any],
    update: dict[str, Any],
    current_tick: int,
) -> dict[str, Any]:
    runtime_state = normalize_conversation_threads(_safe_dict(runtime_state))
    update = _safe_dict(update)
    speaker_id = _safe_str(update.get("speaker_id")).strip()
    target_id = _safe_str(update.get("target_id")).strip()
    text = _safe_str(update.get("text")).strip()
    if not speaker_id or not text:
        return runtime_state
    participants = [speaker_id]
    if target_id:
        participants.append(target_id)
    kind = _safe_str(update.get("kind") or "npc_to_player")
    topic_key = _safe_str(update.get("source_event_ids") or kind)
    topic_summary = _safe_str(update.get("text") or kind)
    runtime_state = seed_or_update_thread(
        runtime_state,
        kind=kind,
        participants=participants,
        topic={
            "key": f"dialogue:{kind}:{topic_key}",
            "type": kind,
            "summary": topic_summary[:180],
            "allowed_world_signal_types": ["rumor", "tension", "relationship_shift"],
        },
        current_tick=current_tick,
        location_id=_safe_str(update.get("location_id")),
        scene_id=_safe_str(update.get("scene_id")),
    )
    thread_context = build_conversation_thread_prompt_context(
        runtime_state,
        current_tick=current_tick,
        limit=8,
    )
    matching_thread_id = ""
    for thread in thread_context:
        t_participants = set(_safe_list(_safe_dict(thread).get("participants")))
        if speaker_id in t_participants and (not target_id or target_id in t_participants):
            matching_thread_id = _safe_str(_safe_dict(thread).get("thread_id"))
            break
    if not matching_thread_id:
        return runtime_state
    runtime_state = add_thread_line(
        runtime_state,
        thread_id=matching_thread_id,
        speaker_id=speaker_id,
        speaker_name=_safe_str(update.get("speaker_name") or speaker_id),
        target_id=target_id,
        target_name=_safe_str(update.get("target_name")),
        text=text,
        kind=kind,
        current_tick=current_tick,
    )
    return runtime_state

__all__ = ['_CRITICAL_REACTION_KINDS', '_MAX_RECENT_WORLD_EVENT_ROWS', '_RECAP_LOW_VALUE_PHRASES', '_apply_idle_tick_to_session', '_build_player_facing_resume_summary', '_build_resume_fallback_recap', '_choose_meaningful_recap_lines', '_coerce_recap_labels', '_is_meaningful_recap_text', '_make_dialogue_update_from_candidate', '_recap_has_renderable_content', '_record_dialogue_update_into_conversation_thread', '_runtime_utc_now', '_score_recap_text']