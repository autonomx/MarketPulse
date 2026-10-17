from __future__ import annotations

import logging

from typing import (
    Any as Any, Dict as Dict, List as List,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _DEFAULT_POST_PLAYER_QUIET_TICKS as _DEFAULT_POST_PLAYER_QUIET_TICKS, _MAX_HISTORY as _MAX_HISTORY, _MAX_PERF_TRACE_ENTRIES as _MAX_PERF_TRACE_ENTRIES,
    _build_turn_id as _build_turn_id,
)
from app.apps.rpg.session.combat_intent import (
    _apply_grounded_scene_overlay as _apply_grounded_scene_overlay, _compact_active_interactions as _compact_active_interactions,
    _derive_grounded_scene_context as _derive_grounded_scene_context, _log_interaction_trace as _log_interaction_trace, _utc_now_iso as _utc_now_iso,
    ensure_ambient_runtime_state as ensure_ambient_runtime_state,
)
from app.apps.rpg.session.combat_action_runtime import (
    _apply_last_chance_combat_utility_result as _apply_last_chance_combat_utility_result,
)
from app.apps.rpg.session.semantic_state_changes import (
    _build_recent_authoritative_turn_facts as _build_recent_authoritative_turn_facts, _build_recent_narration_continuity as _build_recent_narration_continuity,
)
from app.apps.rpg.session.action_execution import (
    _check_opening_resolution as _check_opening_resolution, _update_known_npc_ids as _update_known_npc_ids,
)
from app.apps.rpg.session.player_activity_runtime import (
    _classify_player_action_context as _classify_player_action_context, _record_real_player_activity as _record_real_player_activity,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _clear_stale_last_player_action as _clear_stale_last_player_action, _runtime_continuity_grounding_enabled as _runtime_continuity_grounding_enabled,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
import time as _time
from app.apps.rpg.session.narration_runtime import (
    assemble_turn_narration_response as assemble_turn_narration_response, build_turn_narration_request as build_turn_narration_request,
)
from app.apps.rpg.session.session_runtime_store import (
    save_runtime_session as save_runtime_session,
)
from app.apps.rpg.genesis.creator.world_simulation_reports import (
    summarize_simulation_step as summarize_simulation_step,
)

logger = logging.getLogger(__name__)




def _finish_scene_and_persist_turn_state(ctx) -> bool:
    ctx.after_state, ctx.runtime_state, ctx.resolved_result = (
        _apply_last_chance_combat_utility_result(
            player_input=ctx.player_input,
            action=ctx.action,
            after_state=ctx.after_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            resolved_result=ctx.resolved_result,
            combat_state=ctx.combat_state,
            combat_result=ctx.combat_result,
            npc_combat_result=ctx.npc_combat_result,
            last_chance_candidate=ctx.last_chance_candidate,
            last_chance_utility_kind=ctx.last_chance_utility_kind,
        )
    )
    ctx.grounded = _derive_grounded_scene_context(
        ctx.after_state, ctx.runtime_state, ctx.resolved_result
    )
    ctx.current_scene = _apply_grounded_scene_overlay(
        ctx.current_scene, ctx.grounded
    )
    ctx.runtime_state["grounded_scene_context"] = ctx.grounded
    ctx.runtime_state["current_scene"] = ctx.current_scene
    ctx.runtime_state["tick"] = int(
        ctx.after_state.get("tick", ctx.runtime_state.get("tick", 0)) or 0
    )
    ctx.summary = summarize_simulation_step(ctx.step_result)
    ctx.runtime_state["last_turn_result"] = {
        "player_input": ctx.player_input,
        "action": ctx.action,
        "semantic_action": ctx.semantic_action_record,
        "resolved_result": ctx.resolved_result,
        "combat_result": _safe_dict(ctx.resolved_result.get("combat_result")),
        "xp_result": _safe_dict(ctx.progression.get("xp_result")),
        "skill_xp_result": _safe_dict(ctx.progression.get("skill_xp_result")),
        "level_up": _safe_list(ctx.progression.get("level_up")),
        "skill_level_ups": _safe_list(ctx.progression.get("skill_level_ups")),
        "summary": ctx.summary[:8],
    }
    ctx.runtime_state = _clear_stale_last_player_action(
        ctx.runtime_state,
        _safe_int(ctx.runtime_state.get("tick"), ctx.current_tick),
    )
    ctx.turn_history = _safe_list(ctx.runtime_state.get("turn_history"))
    ctx.turn_history.append(_copy_dict(ctx.runtime_state["last_turn_result"]))
    ctx.runtime_state["turn_history"] = ctx.turn_history[-_MAX_HISTORY :]
    ctx.runtime_state = ensure_ambient_runtime_state(ctx.runtime_state)
    ctx.runtime_state["last_player_turn_at"] = _utc_now_iso()
    ctx.runtime_state = _record_real_player_activity(ctx.runtime_state)
    ctx.runtime_state["last_player_action_context"] = (
        _classify_player_action_context(
            ctx.player_input,
            ctx.resolved_result,
            ctx.after_state,
            ctx.runtime_state,
        )
    )
    ctx.runtime_state["post_player_quiet_ticks"] = (
        _DEFAULT_POST_PLAYER_QUIET_TICKS
    )
    ctx.session["runtime_state"] = ctx.runtime_state
    ctx.runtime_state["opening_runtime"] = _check_opening_resolution(
        ctx.session
    )
    ctx.runtime_state = _update_known_npc_ids(
        ctx.runtime_state, ctx.after_state
    )
    ctx.session["runtime_state"] = ctx.runtime_state
    _log_interaction_trace(
        "apply_turn_before_session_save",
        {
            "tick": _safe_int(ctx.after_state.get("tick"), ctx.current_tick),
            "last_player_action": _safe_dict(
                ctx.runtime_state.get("last_player_action")
            ),
            "count": len(
                _safe_list(ctx.after_state.get("active_interactions"))
            ),
            "items": _compact_active_interactions(
                _safe_list(ctx.after_state.get("active_interactions"))
            ),
        },
        ctx.runtime_state,
    )
    ctx.session["simulation_state"] = ctx.after_state
    ctx.session["runtime_state"] = ctx.runtime_state
    ctx.session["setup_payload"] = ctx.next_setup
    ctx.manifest["updated_at"] = _utc_now_iso()
    ctx.session["manifest"] = ctx.manifest
    ctx._t_pre_save = _time.monotonic()
    ctx.session = save_runtime_session(ctx.session)
    ctx._t_save = _time.monotonic()
    ctx.perf_entry = {
        "tick": ctx.current_tick,
        "t_load": round(ctx._t_load - ctx._t0, 4),
        "t_advisory": round(ctx._t_advisory - ctx._t_load, 4),
        "t_semantic": round(ctx._t_semantic - ctx._t_advisory, 4),
        "t_authoritative": round(ctx._t_authoritative - ctx._t_semantic, 4),
        "t_step": round(ctx._t_step - ctx._t_authoritative, 4),
        "t_narration": 0.0,
        "t_pre_save": round(ctx._t_pre_save - ctx._t_step, 4),
        "t_save": round(ctx._t_save - ctx._t_pre_save, 4),
        "t_total": round(ctx._t_save - ctx._t0, 4),
        "fast_turn_mode": ctx.perf["fast_turn_mode"],
    }
    ctx.perf_entry.update(
        {
            "session_id": ctx.session_id,
            "player_input_len": len(ctx.player_input or ""),
            "save_count": len(ctx.runtime_state.get("perf_trace", [])),
            "simulation_tick_before": ctx.current_tick,
            "tick_after": int(
                ctx.after_state.get("tick", ctx.current_tick) or ctx.current_tick
            ),
        }
    )
    logger.info(
        "[RPG TURN PERF] session=%s tick=%s load=%.3fs advisory=%.3fs semantic=%.3fs authoritative=%.3fs step=%.3fs pre_save=%.3fs save=%.3fs total=%.3fs fast_turn=%s",
        ctx.session_id,
        ctx.perf_entry["tick_after"],
        ctx.perf_entry["t_load"],
        ctx.perf_entry["t_advisory"],
        ctx.perf_entry["t_semantic"],
        ctx.perf_entry["t_authoritative"],
        ctx.perf_entry["t_step"],
        ctx.perf_entry["t_pre_save"],
        ctx.perf_entry["t_save"],
        ctx.perf_entry["t_total"],
        ctx.perf_entry["fast_turn_mode"],
    )
    ctx.runtime_state = _copy_dict(ctx.session.get("runtime_state"))
    ctx.runtime_state.setdefault("perf_trace", [])
    ctx.runtime_state["perf_trace"].append(ctx.perf_entry)
    ctx.runtime_state["perf_trace"] = ctx.runtime_state["perf_trace"][
        -_MAX_PERF_TRACE_ENTRIES :
    ]
    ctx.session["runtime_state"] = ctx.runtime_state
    ctx.session = save_runtime_session(ctx.session)
    ctx.runtime_state = _copy_dict(ctx.session.get("runtime_state"))
    return False


def _prepare_narration_response(ctx) -> bool:
    ctx.turn_id = _build_turn_id(ctx.runtime_state)
    ctx.final_tick = int(
        ctx.runtime_state.get("tick", ctx.current_tick) or ctx.current_tick
    )
    ctx.continuity_rows = []
    ctx.continuity_facts = []
    if _runtime_continuity_grounding_enabled(ctx.runtime_state):
        ctx.continuity_rows = _build_recent_narration_continuity(
            ctx.runtime_state,
            _safe_str(ctx.turn_id).strip(),
            limit=int(ctx.perf.get("continuity_turn_window", 3) or 3),
        )
        ctx.continuity_facts = _build_recent_authoritative_turn_facts(
            ctx.runtime_state,
            _safe_str(ctx.turn_id).strip(),
            limit=int(ctx.perf.get("continuity_turn_window", 3) or 3),
        )
    ctx.narration_context["recent_turns"] = ctx.continuity_rows
    ctx.narration_context["recent_authoritative_facts"] = ctx.continuity_facts
    ctx.narration_request = build_turn_narration_request(
        turn_id=ctx.turn_id,
        tick=ctx.final_tick,
        session_id=ctx.session_id,
        scene=ctx.current_scene,
        narration_context=ctx.narration_context,
        performance=ctx.perf,
    )
    ctx._result = assemble_turn_narration_response(
        session=ctx.session,
        authoritative=ctx.authoritative,
        turn_contract=ctx.turn_contract,
        narration_request=ctx.narration_request,
        runtime_state=ctx.runtime_state,
        perf=ctx.perf,
        resolved_result=ctx.resolved_result,
    )
    return True
    return False
