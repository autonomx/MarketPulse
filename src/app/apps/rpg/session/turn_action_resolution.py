from __future__ import annotations

from typing import (
    Any as Any, List as List,
)
from app.apps.rpg.session.combat_turn_actions import (
    _ability_id_from_player_input as _ability_id_from_player_input, _manual_encounter_preset_from_input as _manual_encounter_preset_from_input,
    _resolve_active_combat_utility_turn as _resolve_active_combat_utility_turn,
)
from app.apps.rpg.session.semantic_state_changes import (
    _active_combat_state_from_runtime_or_simulation as _active_combat_state_from_runtime_or_simulation, _active_combat_utility_kind as _active_combat_utility_kind,
)
from app.apps.rpg.session.combat_action_runtime import (
    _apply_advisory_phase as _apply_advisory_phase, _force_combat_utility_action_type as _force_combat_utility_action_type,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _build_last_player_action_record as _build_last_player_action_record,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _build_turn_id as _build_turn_id,
)
from app.apps.rpg.session.semantic_interaction_runtime import (
    _coerce_action_target as _coerce_action_target, _coerce_action_target_to_active_combat_participant as _coerce_action_target_to_active_combat_participant,
)
from app.apps.rpg.session.state_normalization import (
    _copy_dict as _copy_dict, _ensure_simulation_state as _ensure_simulation_state, _normalize_performance_settings as _normalize_performance_settings,
    _normalize_story_policy as _normalize_story_policy, _normalize_structured_action as _normalize_structured_action, _safe_dict as _safe_dict, _safe_int as _safe_int,
    _safe_str as _safe_str, _story_policy_record_replay_artifacts as _story_policy_record_replay_artifacts,
)
from app.apps.rpg.session.combat_intent import (
    _force_active_combat_utility_action as _force_active_combat_utility_action,
)
from app.apps.rpg.session.special_combat_turns import (
    _maybe_resolve_combat_ability_turn as _maybe_resolve_combat_ability_turn, _maybe_start_manual_encounter as _maybe_start_manual_encounter,
)
from app.apps.rpg.session.action_execution import (
    _structured_action_prompt as _structured_action_prompt, select_primary_action as select_primary_action,
)
import time as _time
from app.apps.rpg.session.conversation_thread_runtime import (
    advance_conversation_threads_for_turn as advance_conversation_threads_for_turn,
)
from app.apps.rpg.genesis.creator.defaults import (
    apply_adventure_defaults as apply_adventure_defaults,
)
from app.apps.rpg.session.session_runtime_store import (
    derive_action_candidates as derive_action_candidates, load_runtime_session as load_runtime_session,
)
from app.apps.rpg.session.ambient_tick_runtime import (
    is_ambient_tick_command as is_ambient_tick_command,
)
from app.apps.rpg.session.action_intelligence import (
    merge_action_advisory as merge_action_advisory,
)
from app.apps.rpg.rules.combat.companion_ai import (
    parse_companion_command as parse_companion_command,
)
from app.apps.rpg.world.npc_dialogue_recall import (
    player_input_requests_recall as player_input_requests_recall,
)
from app.apps.rpg.session.turn_perf_trace import (
    record_turn_perf_trace as record_turn_perf_trace, record_turn_perf_trace_stack as record_turn_perf_trace_stack,
)
from app.apps.rpg.rules.economy.service_resolver import (
    resolve_service_turn as resolve_service_turn,
)
from app.apps.rpg.session.service_runtime import (
    service_action_from_result as service_action_from_result, service_semantic_action_from_result as service_semantic_action_from_result,
)
from app.apps.rpg.session.deferred_narration_guard import (
    suppress_provider_runtime_narration as suppress_provider_runtime_narration,
)




def _prepare_turn_action_context(ctx) -> bool:
    record_turn_perf_trace_stack(
        "authoritative_enter",
        function="_apply_turn_authoritative_base",
    )
    ctx._t0 = _time.monotonic()
    ctx.session = load_runtime_session(ctx.session_id)
    if ctx.session is None:
        record_turn_perf_trace(
            "authoritative_before_return",
            reason="session_not_found",
            return_keys=[],
            ok=False,
        )
        ctx._result = {"ok": False, "error": "session_not_found"}
        return True
    # IMPORTANT: keep the old apply_turn authoritative pipeline intact.
    # This function should be the previous apply_turn() minus the live
    # narration-generation block, not a redesign of the turn engine.
    ctx.session = _copy_dict(ctx.session)
    ctx.manifest = _safe_dict(ctx.session.get("manifest"))
    ctx.runtime_state = _copy_dict(ctx.session.get("runtime_state"))
    ctx.setup = apply_adventure_defaults(
        _copy_dict(ctx.session.get("setup_payload"))
    )
    ctx.simulation_state = _ensure_simulation_state(
        _safe_dict(ctx.session.get("simulation_state"))
    )
    ctx.player_actor_id = "player"
    ctx.current_tick = _safe_int(ctx.runtime_state.get("tick"), 0)
    ctx._t_load = _time.monotonic()
    if ctx.performance_override:
        ctx.existing_perf = ctx.runtime_state.get("performance") or {}
        if isinstance(ctx.existing_perf, dict):
            ctx.runtime_state["performance"] = {
                **ctx.existing_perf,
                **ctx.performance_override,
            }
        else:
            ctx.runtime_state["performance"] = dict(ctx.performance_override)
    ctx.perf = _normalize_performance_settings(ctx.runtime_state)
    # Playable/deferred mode: keep the authoritative turn synchronous,
    # deterministic, and fast. LLM advisory is useful for richer interpretation,
    # but it is not allowed to block the turn path when narration/LLM work is
    # being deferred. The deterministic semantic action fallback still runs
    # below via _build_fast_semantic_action_record(...).
    ctx.defer_runtime_llm_work = bool(
        suppress_provider_runtime_narration()
        or ctx.runtime_state.get("autoplay_deferred_narration")
        or ctx.runtime_state.get("deferred_runtime_narration")
        or ctx.runtime_state.get("narration_mode") == "deferred"
    )
    if ctx.defer_runtime_llm_work:
        ctx.perf["enable_action_advisory"] = False
        ctx.perf["enable_semantic_action_advisory"] = False
        ctx.runtime_state["deferred_runtime_advisory_suppressed"] = True
    ctx.runtime_state["performance"] = ctx.perf
    ctx.story_policy = _normalize_story_policy(ctx.runtime_state)
    ctx.runtime_state["story_policy"] = ctx.story_policy
    ctx.player_input = _safe_str(ctx.player_input).strip()
    ctx.manual_encounter_preset = _manual_encounter_preset_from_input(
        ctx.player_input
    )
    ctx._maybe_start_manual_encounter_result = _maybe_start_manual_encounter(
        player_input=ctx.player_input,
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        manual_encounter_preset=ctx.manual_encounter_preset,
    )
    if ctx._maybe_start_manual_encounter_result is not None:
        ctx._result = ctx._maybe_start_manual_encounter_result
        return True
    ctx.action = _normalize_structured_action(ctx.action, ctx.player_input)
    ctx.action = _coerce_action_target(
        ctx.simulation_state, ctx.action, ctx.player_input
    )
    ctx.action = _coerce_action_target_to_active_combat_participant(
        ctx.runtime_state,
        ctx.action,
        ctx.player_input,
    )
    if not ctx.action:
        ctx.candidates = derive_action_candidates(
            ctx.simulation_state,
            ctx.player_input,
            runtime_state=ctx.runtime_state,
        )
        ctx.action = select_primary_action(
            ctx.simulation_state, ctx.candidates
        )
    ctx.action = _safe_dict(ctx.action)
    ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    if not ctx.player_input:
        ctx.player_input = _structured_action_prompt(ctx.action)
    ctx.player_input = (
        ctx.player_input or ctx.action_type.replace("_", " ").strip() or "Wait"
    )
    ctx.service_first_result = resolve_service_turn(
        player_input=ctx.player_input,
        action=ctx.action,
        resolved_action={},
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
    )
    if ctx.service_first_result.get("matched"):
        ctx.action = service_action_from_result(
            ctx.player_input, ctx.action, ctx.service_first_result
        )
        ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    # Lazy LLM gateway: build at most once per authoritative turn.
    ctx._llm_gw_holder = []

    ctx.advisory = {}
    ctx.semantic_advisory = {}
    ctx.semantic_action_record = {}
    ctx._stage_started = __import__("time").perf_counter()
    ctx.key = ""
    ctx.record = {}
    ctx.recorded_policy = {}
    ctx.semantic_key = ""
    ctx.semantic_record = {}
    ctx.semantic_record_capture = {}
    return False


def _record_turn_policy_and_apply_advisory(ctx) -> bool:
    ctx.turn_exec_index = {}
    ctx.semantic_compiled_capture = {}
    ctx.semantic_compiled_record = {}
    ctx.runtime_state.setdefault("conversation_settings", {})
    ctx.runtime_state.setdefault("offscreen_conversation_summaries", [])
    ctx.runtime_state.setdefault("last_player_action", {})
    ctx.runtime_state.setdefault("last_conversation_intervention", {})
    ctx.record_replay_artifacts = _story_policy_record_replay_artifacts(
        ctx.runtime_state
    )
    if ctx.record_replay_artifacts:
        ctx.runtime_state.setdefault("llm_records", [])
        ctx.runtime_state["llm_records_index"] = _safe_dict(
            ctx.runtime_state.get("llm_records_index")
        )
        ctx.runtime_state.setdefault("turn_execution_index", {})
    ctx.mode = (
        _safe_str(ctx.runtime_state.get("mode")).strip().lower() or "live"
    )
    ctx.current_tick = _safe_int(ctx.runtime_state.get("tick"), 0)
    # Stable turn identifiers for every authoritative path.
    #
    # J19-J21 added early/post-authoritative combat utility paths for:
    # - defend
    # - use_item
    # - flee
    #
    # Those branches can return before the older lower-scope turn_id/final_tick
    # locals are initialized. Define them here so all branches can safely use
    # the same deterministic turn metadata.
    ctx.turn_id = _build_turn_id(ctx.runtime_state)
    ctx.final_tick = ctx.current_tick
    ctx.ambient_tick_command = is_ambient_tick_command(ctx.player_input)
    ctx.ambient_tick_result = {}
    ctx.conversation_result = {}
    ctx.before_state_for_contract = {}
    ctx.contract_resolved = {}
    ctx.resolved_for_contract = {}
    ctx.resolved_from_contract = {}
    ctx.general_interaction_result = {}
    ctx.combat_narration_contract = {}
    ctx.combat_narration_validation = {}
    ctx.combat_narration_payload = {}
    ctx.combat_llm_called = False
    ctx.combat_llm_error = ""
    ctx.recall_request_conversation_result = {}
    if player_input_requests_recall(ctx.player_input):
        ctx.recall_request_conversation_result = (
            advance_conversation_threads_for_turn(
                player_input=ctx.player_input,
                simulation_state=ctx.simulation_state,
                resolved_result={
                    "action_type": "player_conversation_recall",
                    "semantic_action_type": "player_conversation_recall",
                    "semantic_family": "conversation",
                },
                tick=ctx.current_tick,
                runtime_state=ctx.runtime_state,
            )
        )
    ctx.turn_exec_key = f"turn:{ctx.current_tick}"
    ctx.turn_execution_policy = {
        "enable_action_advisory": ctx.perf["enable_action_advisory"],
        "enable_semantic_action_advisory": ctx.perf["enable_semantic_action_advisory"],
        "enable_live_narration_llm": ctx.perf["enable_live_narration_llm"],
        "enable_narration_retry": ctx.perf["enable_narration_retry"],
        "fast_turn_mode": ctx.perf["fast_turn_mode"],
        "save_load_stable": ctx.story_policy["save_load_stable"],
        "strict_replay": ctx.story_policy["strict_replay"],
    }
    if ctx.mode == "live" and ctx.record_replay_artifacts:
        ctx.runtime_state["turn_execution_index"][ctx.turn_exec_key] = (
            ctx.turn_execution_policy
        )
    if ctx.mode == "replay" and not ctx.record_replay_artifacts:
        raise RuntimeError("replay_disabled_for_save_load_stable_sessions")
    ctx.runtime_state["last_player_action"] = {
        "action_id": f"player_action:{ctx.current_tick + 1}",
        "action_type": ctx.action_type,
        "target_id": _safe_str(ctx.action.get("target_id"))
        if isinstance(ctx.action, dict)
        else "",
        "npc_id": _safe_str(ctx.action.get("npc_id"))
        if isinstance(ctx.action, dict)
        else "",
        "item_id": _safe_str(ctx.action.get("item_id"))
        if isinstance(ctx.action, dict)
        else "",
    }
    ctx.runtime_state, ctx._stage_started, ctx.advisory, ctx.semantic_advisory = (
        _apply_advisory_phase(
            player_input=ctx.player_input,
            action=ctx.action,
            simulation_state=ctx.simulation_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            _get_llm_gateway=ctx._get_llm_gateway,
            _stage_started=ctx._stage_started,
            advisory=ctx.advisory,
            key=ctx.key,
            mode=ctx.mode,
            perf=ctx.perf,
            record=ctx.record,
            record_replay_artifacts=ctx.record_replay_artifacts,
            recorded_policy=ctx.recorded_policy,
            semantic_advisory=ctx.semantic_advisory,
            semantic_key=ctx.semantic_key,
            semantic_record=ctx.semantic_record,
            semantic_record_capture=ctx.semantic_record_capture,
            turn_exec_index=ctx.turn_exec_index,
            turn_exec_key=ctx.turn_exec_key,
        )
    )
    ctx._t_advisory = _time.monotonic()
    if ctx.advisory:
        ctx.action = merge_action_advisory(ctx.action, ctx.advisory)
        ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    return False


def _resolve_semantic_action_and_special_turns(ctx) -> bool:
    ctx.service_after_advisory = resolve_service_turn(
        player_input=ctx.player_input,
        action=ctx.action,
        resolved_action={},
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
    )
    if ctx.service_after_advisory.get("matched"):
        ctx.action = service_action_from_result(
            ctx.player_input, ctx.action, ctx.service_after_advisory
        )
        ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    ctx.semantic_compiled_key = f"semantic_action_compiled:{ctx.current_tick}"
    ctx.runtime_state, ctx.semantic_action_record = (
        _force_combat_utility_action_type(
            player_input=ctx.player_input,
            action=ctx.action,
            simulation_state=ctx.simulation_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            semantic_action_record=ctx.semantic_action_record,
            _stage_started=ctx._stage_started,
            mode=ctx.mode,
            perf=ctx.perf,
            record_replay_artifacts=ctx.record_replay_artifacts,
            semantic_advisory=ctx.semantic_advisory,
            semantic_compiled_capture=ctx.semantic_compiled_capture,
            semantic_compiled_key=ctx.semantic_compiled_key,
            semantic_compiled_record=ctx.semantic_compiled_record,
        )
    )
    ctx._t_semantic = _time.monotonic()
    ctx.action_metadata = _safe_dict(ctx.action.get("metadata"))
    ctx.action_metadata["semantic_action"] = ctx.semantic_action_record
    ctx.action["metadata"] = ctx.action_metadata
    ctx.action = _force_active_combat_utility_action(
        ctx.runtime_state,
        ctx.action,
        ctx.semantic_action_record,
        ctx.player_input,
    )
    ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    ctx.service_after_semantic = resolve_service_turn(
        player_input=ctx.player_input,
        action=ctx.action,
        resolved_action={},
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
    )
    if ctx.service_after_semantic.get("matched"):
        ctx.action = service_action_from_result(
            ctx.player_input, ctx.action, ctx.service_after_semantic
        )
        ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
        ctx.semantic_action_record = service_semantic_action_from_result(
            ctx.player_input,
            ctx.service_after_semantic,
            tick=_safe_int(ctx.current_tick, 0),
            existing=ctx.semantic_action_record,
        )
        ctx.action_metadata = _safe_dict(ctx.action.get("metadata"))
        ctx.action_metadata["semantic_action"] = ctx.semantic_action_record
        ctx.action["metadata"] = ctx.action_metadata
    ctx.runtime_state["last_player_action"] = _build_last_player_action_record(
        tick=ctx.current_tick,
        player_input=ctx.player_input,
        action=ctx.action,
        semantic_action_record=ctx.semantic_action_record,
    )
    ctx.action = _force_active_combat_utility_action(
        ctx.runtime_state,
        ctx.action,
        ctx.semantic_action_record,
        ctx.player_input,
    )
    ctx.action_type = _safe_str(ctx.action.get("action_type")).strip()
    ctx.active_combat_utility_kind = _active_combat_utility_kind(
        ctx.runtime_state,
        ctx.semantic_action_record,
        ctx.player_input,
    )
    if ctx.active_combat_utility_kind:
        ctx._result = _resolve_active_combat_utility_turn(
            runtime_state=ctx.runtime_state,
            semantic_action_record=ctx.semantic_action_record,
            player_input=ctx.player_input,
            simulation_state=ctx.simulation_state,
            action=ctx.action,
            player_actor_id=ctx.player_actor_id,
            active_combat_utility_kind=ctx.active_combat_utility_kind,
            current_tick=ctx.current_tick,
            turn_id=ctx.turn_id,
        )
        return True
    ctx.ability_id = _ability_id_from_player_input(ctx.player_input)
    ctx._maybe_resolve_combat_ability_turn_result = (
        _maybe_resolve_combat_ability_turn(
            player_input=ctx.player_input,
            simulation_state=ctx.simulation_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            ability_id=ctx.ability_id,
        )
    )
    if ctx._maybe_resolve_combat_ability_turn_result is not None:
        ctx._result = ctx._maybe_resolve_combat_ability_turn_result
        return True
    ctx.companion_command = parse_companion_command(ctx.player_input)
    ctx.active_combat_state = _active_combat_state_from_runtime_or_simulation(
        ctx.runtime_state,
        ctx.simulation_state,
    )
    return False
