from __future__ import annotations

from app.apps.rpg.session.combat_action_runtime import (
    _apply_ambient_conversation_result as _apply_ambient_conversation_result, _build_and_apply_turn_contract_phase as _build_and_apply_turn_contract_phase,
    _build_fallback_turn_contract_phase as _build_fallback_turn_contract_phase, _maybe_resolve_general_interaction_turn as _maybe_resolve_general_interaction_turn,
)
from app.apps.rpg.session.special_combat_turns import (
    _apply_deterministic_travel_resolution as _apply_deterministic_travel_resolution,
)
from app.apps.rpg.session.player_activity_runtime import (
    _apply_semantic_action_to_runtime as _apply_semantic_action_to_runtime,
)
from app.apps.rpg.session.action_execution import (
    _award_progression as _award_progression, _get_player_location_id as _get_player_location_id,
)
from app.apps.rpg.session.semantic_interaction_runtime import (
    _clean_resolved_interaction_world_event_rows as _clean_resolved_interaction_world_event_rows,
)
from app.apps.rpg.session.combat_intent import (
    _compact_active_interactions as _compact_active_interactions, _log_interaction_trace as _log_interaction_trace,
)
from app.apps.rpg.session.state_normalization import (
    _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _expire_stale_active_interactions as _expire_stale_active_interactions, _persist_player_interaction_state_after_turn as _persist_player_interaction_state_after_turn,
    _refresh_active_interactions_for_tick as _refresh_active_interactions_for_tick, _resolve_until_next_command_interactions as _resolve_until_next_command_interactions,
)
from app.apps.rpg.session.session_runtime_store import (
    _extract_semantic_action_record_for_turn as _extract_semantic_action_record_for_turn, _fallback_scene as _fallback_scene,
    _find_active_combat_state_deep as _find_active_combat_state_deep, _resolved_result_is_unsupported_combat_utility as _resolved_result_is_unsupported_combat_utility,
)
import time as _time
from app.apps.rpg.session.ambient_tick_runtime import (
    advance_autonomous_ambient_tick as advance_autonomous_ambient_tick,
)
from app.apps.rpg.session.conversation_thread_runtime import (
    advance_conversation_threads_for_turn as advance_conversation_threads_for_turn,
)
from app.apps.rpg.world.memory.social_effects import (
    apply_general_social_effects as apply_general_social_effects,
)
from app.apps.rpg.narration.presentation.speaker_cards import (
    build_nearby_npc_cards as build_nearby_npc_cards,
)
from app.apps.rpg.session.narration_runtime import (
    build_turn_narration_context as build_turn_narration_context,
)
from app.apps.rpg.rules.location_registry import (
    ensure_location_state as ensure_location_state,
)
from app.apps.rpg.narration.ai.conversation_threads import (
    expire_conversation_threads as expire_conversation_threads, normalize_conversation_threads as normalize_conversation_threads,
)
from app.apps.rpg.genesis.creator.world_scene_generator import (
    generate_scenes_from_simulation as generate_scenes_from_simulation,
)
from app.apps.rpg.rules.ambient_intent import (
    is_ambient_wait_or_listen_intent as is_ambient_wait_or_listen_intent,
)
from app.apps.rpg.rules.items.world_items import (
    list_scene_items as list_scene_items,
)
from app.apps.rpg.session.conversation_hooks import SESSION_CONVERSATION_HOOKS
from app.apps.rpg.genesis.creator.world_simulation import (
    step_simulation_state as step_simulation_state,
)


from .state_normalization import _merge_stepped_simulation_state


def _apply_ambient_conversation_and_world_effects(ctx) -> bool:
    ctx.action = _safe_dict(ctx.service_resolution.get("action"))
    if ctx.ambient_tick_command:
        ctx.ambient_conversation_tick = _safe_int(
            ctx.after_action_state.get("tick")
            or ctx.after_action_state.get("current_tick")
            or ctx.runtime_state.get("tick"),
            ctx.current_tick,
        )
        ctx.ambient_tick_result = advance_autonomous_ambient_tick(
            player_input=ctx.player_input,
            simulation_state=ctx.after_action_state,
            runtime_state=ctx.runtime_state,
            tick=ctx.ambient_conversation_tick,
        )
        ctx.authoritative["simulation_state"] = ctx.after_action_state
    ctx.conversation_result = _apply_ambient_conversation_result(
        after_action_state=ctx.after_action_state,
        resolved_result=ctx.resolved_result,
        authoritative=ctx.authoritative,
        ambient_tick_result=ctx.ambient_tick_result,
        conversation_result=ctx.conversation_result,
    )
    ctx.after_action_state, ctx.resolved_result, ctx.authoritative = (
        _apply_deterministic_travel_resolution(
            ambient_tick_result=ctx.ambient_tick_result,
            resolved_result=ctx.resolved_result,
            authoritative=ctx.authoritative,
            player_input=ctx.player_input,
            after_action_state=ctx.after_action_state,
        )
    )
    ctx.social_living_world_effects = apply_general_social_effects(
        ctx.after_action_state,
        ctx.resolved_result,
        tick=ctx.current_tick,
    )
    if ctx.social_living_world_effects:
        ctx.resolved_result["social_living_world_effects"] = (
            ctx.social_living_world_effects
        )
        ctx.resolved_result["memory_entry"] = (
            ctx.resolved_result.get("memory_entry")
            or ctx.social_living_world_effects.get("memory_entry")
            or {}
        )
        ctx.resolved_result["relationship_state"] = _safe_dict(
            ctx.after_action_state.get("relationship_state")
        )
        ctx.resolved_result["npc_emotion_state"] = _safe_dict(
            ctx.after_action_state.get("npc_emotion_state")
        )
        ctx.resolved_result["memory_state"] = _safe_dict(
            ctx.after_action_state.get("memory_state")
        )
        ctx.authoritative["social_living_world_effects"] = (
            ctx.social_living_world_effects
        )
        ctx.authoritative["relationship_state"] = _safe_dict(
            ctx.after_action_state.get("relationship_state")
        )
        ctx.authoritative["npc_emotion_state"] = _safe_dict(
            ctx.after_action_state.get("npc_emotion_state")
        )
        ctx.authoritative["memory_state"] = _safe_dict(
            ctx.after_action_state.get("memory_state")
        )
        ctx.authoritative["result"] = ctx.resolved_result
    # Always refresh location debug after the turn. This keeps service turns,
    # social turns, and travel turns consistent for transcript/UI inspection.
    ensure_location_state(ctx.after_action_state)
    ctx.resolved_result["location_state"] = _safe_dict(
        ctx.after_action_state.get("location_state")
    )
    ctx.resolved_result["current_location_id"] = _safe_str(
        _safe_dict(ctx.after_action_state.get("location_state")).get(
            "current_location_id"
        )
        or ctx.after_action_state.get("location_id")
        or ctx.after_action_state.get("current_location_id")
    )
    ctx.authoritative["location_state"] = _safe_dict(
        ctx.after_action_state.get("location_state")
    )
    ctx.authoritative["current_location_id"] = ctx.resolved_result[
        "current_location_id"
    ]
    ctx.authoritative["world_event_state"] = _safe_dict(
        ctx.after_action_state.get("world_event_state")
    )
    ctx.authoritative["simulation_state"] = ctx.after_action_state
    if ctx.pending_conversation_reply:
        ctx.resolved_result["action_type"] = "player_conversation_reply"
        ctx.resolved_result["semantic_action_type"] = "player_conversation_reply"
        ctx.resolved_result["semantic_family"] = "conversation"
        ctx.resolved_result["activity_label"] = "player_conversation_reply"
        ctx.service_result = {
            "matched": False,
            "kind": "not_service",
            "status": "not_service",
            "reason": "pending_conversation_response_takes_precedence",
        }
        ctx.resolved_result["service_result"] = ctx.service_result
        ctx.authoritative["action_type"] = "player_conversation_reply"
        ctx.authoritative["semantic_action_type"] = "player_conversation_reply"
        ctx.authoritative["semantic_family"] = "conversation"
        ctx.authoritative["activity_label"] = "player_conversation_reply"
        ctx.authoritative["service_result"] = ctx.service_result
    ctx.service_result = _safe_dict(ctx.resolved_result.get("service_result"))
    if ctx.ambient_tick_result:
        ctx.service_result = {
            "matched": False,
            "kind": "not_service",
            "status": "not_service",
            "reason": "ambient_tick",
        }
        ctx.resolved_result["service_result"] = ctx.service_result
    if is_ambient_wait_or_listen_intent(
        ctx.player_input
    ) and not ctx.service_result.get("matched"):
        ctx.resolved_result["action_type"] = "ambient_wait"
        ctx.resolved_result["semantic_action_type"] = "ambient_wait"
        ctx.resolved_result["semantic_family"] = "ambient"
        ctx.resolved_result["activity_label"] = "wait_and_listen"
        ctx.authoritative["action_type"] = "ambient_wait"
        ctx.authoritative["semantic_action_type"] = "ambient_wait"
        ctx.authoritative["semantic_family"] = "ambient"
        ctx.authoritative["activity_label"] = "wait_and_listen"
    ctx.conversation_result = _safe_dict(
        ctx.recall_request_conversation_result
        or ctx.resolved_result.get("conversation_result")
    )
    return False


def _advance_turn_contract_and_simulation(ctx) -> bool:
    if not ctx.conversation_result and not ctx.ambient_tick_result:
        ctx.conversation_result = advance_conversation_threads_for_turn(
            player_input=ctx.player_input,
            simulation_state=ctx.after_action_state,
            resolved_result=ctx.resolved_result,
            tick=_safe_int(
                ctx.after_action_state.get("tick")
                or ctx.after_action_state.get("current_tick"),
                ctx.current_tick,
            ),
            runtime_state=ctx.runtime_state,
        )
    if ctx.conversation_result:
        ctx.resolved_result["conversation_result"] = ctx.conversation_result
        ctx.resolved_result["conversation_thread_state"] = _safe_dict(
            ctx.conversation_result.get("conversation_thread_state")
            or ctx.after_action_state.get("conversation_thread_state")
        )
        ctx.resolved_result["world_event_state"] = _safe_dict(
            ctx.after_action_state.get("world_event_state")
        )
        ctx.authoritative["conversation_result"] = ctx.conversation_result
        ctx.authoritative["conversation_thread_state"] = _safe_dict(
            ctx.after_action_state.get("conversation_thread_state")
        )
        ctx.authoritative["world_event_state"] = _safe_dict(
            ctx.after_action_state.get("world_event_state")
        )
        ctx.authoritative["simulation_state"] = ctx.after_action_state
    ctx._t_authoritative = _time.monotonic()
    ctx.runtime_settings_for_contract = _safe_dict(
        ctx.runtime_state.get("runtime_settings") or ctx.runtime_state.get("settings")
    )
    ctx.turn_contract = {}
    ctx.after_action_state, ctx.resolved_result, ctx.turn_contract = (
        _build_and_apply_turn_contract_phase(
            player_input=ctx.player_input,
            action=ctx.action,
            simulation_state=ctx.simulation_state,
            before_state=ctx.before_state,
            after_action_state=ctx.after_action_state,
            runtime_state=ctx.runtime_state,
            resolved_result=ctx.resolved_result,
            semantic_action_record=ctx.semantic_action_record,
            before_state_for_contract=ctx.before_state_for_contract,
            contract_resolved=ctx.contract_resolved,
            resolved_for_contract=ctx.resolved_for_contract,
            resolved_from_contract=ctx.resolved_from_contract,
            runtime_settings_for_contract=ctx.runtime_settings_for_contract,
            turn_contract=ctx.turn_contract,
            general_interaction_result=ctx.general_interaction_result,
            combat_narration_contract=ctx.combat_narration_contract,
            combat_narration_validation=ctx.combat_narration_validation,
            combat_narration_payload=ctx.combat_narration_payload,
            combat_llm_called=ctx.combat_llm_called,
            combat_llm_error=ctx.combat_llm_error,
        )
    )
    ctx.turn_contract = _build_fallback_turn_contract_phase(
        player_input=ctx.player_input,
        action=ctx.action,
        simulation_state=ctx.simulation_state,
        before_state=ctx.before_state,
        runtime_state=ctx.runtime_state,
        resolved_result=ctx.resolved_result,
        semantic_action_record=ctx.semantic_action_record,
        ambient_tick_result=ctx.ambient_tick_result,
        service_result=ctx.service_result,
        turn_contract=ctx.turn_contract,
    )
    ctx.progression = _award_progression(
        ctx.after_action_state, ctx.resolved_result
    )
    ctx.after_progression_state = _ensure_simulation_state(
        _safe_dict(ctx.progression.get("simulation_state"))
    )
    ctx.metadata = _safe_dict(ctx.setup.get("metadata"))
    ctx.metadata["simulation_state"] = ctx.after_progression_state
    ctx.setup["metadata"] = ctx.metadata
    ctx.step_result = step_simulation_state(ctx.setup, conversation_hooks=SESSION_CONVERSATION_HOOKS)
    ctx.next_setup = _safe_dict(ctx.step_result.get("next_setup")) or ctx.setup
    # step_simulation_state rebuilds a world-sim slice from scratch. Merge it
    # back over the authoritative per-turn state so player/service/social roots
    # persist across turns.
    ctx.after_state = _merge_stepped_simulation_state(
        ctx.after_progression_state,
        _safe_dict(ctx.step_result.get("after_state")),
    )
    ctx._t_step = _time.monotonic()
    ctx._maybe_resolve_general_interaction_turn_result = (
        _maybe_resolve_general_interaction_turn(
            player_input=ctx.player_input,
            simulation_state=ctx.simulation_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
        )
    )
    if ctx._maybe_resolve_general_interaction_turn_result is not None:
        ctx._result = ctx._maybe_resolve_general_interaction_turn_result
        return True
    _log_interaction_trace(
        "apply_turn_before_semantic_apply",
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
    ctx.after_state, ctx.runtime_state = _apply_semantic_action_to_runtime(
        simulation_state=ctx.after_state,
        runtime_state=ctx.runtime_state,
        record=ctx.semantic_action_record,
    )
    return False


def _update_interactions_and_combat_fallback(ctx) -> bool:
    _log_interaction_trace(
        "apply_turn_after_semantic_apply",
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
    ctx.after_state, ctx.runtime_state = (
        _persist_player_interaction_state_after_turn(
            ctx.after_state,
            ctx.runtime_state,
            ctx.player_input,
            ctx.semantic_action_record,
            ctx.current_tick,
        )
    )
    ctx.after_state = _refresh_active_interactions_for_tick(
        ctx.after_state,
        _safe_int(ctx.after_state.get("tick"), ctx.current_tick),
    )
    _log_interaction_trace(
        "apply_turn_after_interaction_creation",
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
    ctx.after_state = _resolve_until_next_command_interactions(
        ctx.after_state,
        ctx.runtime_state,
        ctx.semantic_action_record,
        ctx.current_tick,
    )
    ctx.after_state = _expire_stale_active_interactions(
        ctx.after_state,
        _safe_int(ctx.after_state.get("tick"), ctx.current_tick),
    )
    ctx.runtime_state = _clean_resolved_interaction_world_event_rows(
        ctx.after_state, ctx.runtime_state
    )
    ctx.runtime_state = normalize_conversation_threads(ctx.runtime_state)
    ctx.runtime_state = expire_conversation_threads(
        ctx.runtime_state,
        current_tick=_safe_int(ctx.after_state.get("tick"), ctx.current_tick),
    )
    ctx.scenes = generate_scenes_from_simulation(ctx.after_state)
    ctx.current_scene = (
        _safe_dict(ctx.scenes[0])
        if ctx.scenes
        else _fallback_scene(ctx.after_state, ctx.player_input)
    )
    ctx.current_location_id = _get_player_location_id(
        ctx.after_state, ctx.runtime_state
    )
    ctx.current_scene["items"] = list_scene_items(
        ctx.after_state, ctx.current_location_id
    )
    ctx.current_scene["nearby_npcs"] = build_nearby_npc_cards(
        ctx.after_state, ctx.current_scene
    )
    ctx.narration_context = build_turn_narration_context(
        after_state=ctx.after_state,
        player_input=ctx.player_input,
        resolved_result=ctx.resolved_result,
        turn_contract=ctx.turn_contract,
        progression=ctx.progression,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        combat_result=ctx.combat_result,
        npc_combat_result=ctx.npc_combat_result,
        combat_state=ctx.combat_state,
    )
    # J19-J21 final rescue:
    # If the generic interaction runtime produced unsupported_interaction_kind
    # but the completed resolved_result contains an active combat_state plus a
    # combat utility semantic action, resolve it here before narration sees the
    # unsupported fallback.
    # Build a rescue candidate from both resolved_result and sibling turn fields.
    # In the current pipeline, unsupported_interaction_kind can leave
    # resolved_result as {}, while combat_state and semantic_action_v2 live
    # beside it in the assembled turn payload.
    ctx.last_chance_candidate = dict(_safe_dict(ctx.resolved_result))
    if not _safe_dict(ctx.last_chance_candidate.get("combat_state")).get(
        "active"
    ):
        ctx.last_chance_candidate["combat_state"] = _safe_dict(
            ctx.combat_state
        )
    if not _safe_dict(ctx.last_chance_candidate.get("combat_state")).get(
        "active"
    ):
        ctx.last_chance_candidate["combat_state"] = (
            _find_active_combat_state_deep(ctx.authoritative)
        )
    return False


def _update_interactions_and_combat_fallback_continued_1(ctx) -> bool:
    if not _safe_dict(ctx.last_chance_candidate.get("semantic_action_v2")):
        ctx.last_chance_candidate["semantic_action_v2"] = (
            _extract_semantic_action_record_for_turn(
                ctx.semantic_action_record,
                ctx.authoritative,
            )
        )
    if not _safe_str(
        ctx.last_chance_candidate.get("visible_interaction_reason")
    ).strip():
        ctx.last_chance_candidate["visible_interaction_reason"] = _safe_str(
            ctx.resolved_result.get("visible_interaction_reason")
            or _safe_dict(ctx.resolved_result.get("interaction_result")).get(
                "reason"
            )
            or _safe_dict(
                _safe_dict(ctx.authoritative.get("result")).get(
                    "interaction_result"
                )
            ).get("reason")
            or _safe_dict(
                _safe_dict(
                    _safe_dict(ctx.authoritative.get("result")).get(
                        "general_interaction_result"
                    )
                ).get("interaction_result")
            ).get("reason")
        ).strip()
    if not _safe_dict(ctx.last_chance_candidate.get("interaction_result")):
        ctx.last_chance_candidate["interaction_result"] = _safe_dict(
            _safe_dict(ctx.authoritative.get("result")).get(
                "interaction_result"
            )
        )
    if not _safe_dict(
        ctx.last_chance_candidate.get("general_interaction_result")
    ):
        ctx.last_chance_candidate["general_interaction_result"] = _safe_dict(
            _safe_dict(ctx.authoritative.get("result")).get(
                "general_interaction_result"
            )
        )
    ctx.last_chance_utility_kind = (
        _resolved_result_is_unsupported_combat_utility(
            ctx.last_chance_candidate,
            ctx.player_input,
        )
    )
    return False
