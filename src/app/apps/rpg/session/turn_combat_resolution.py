from __future__ import annotations

from typing import (
    Any as Any, Dict as Dict,
)
from app.apps.rpg.session.combat_intent import (
    _action_requests_combat_defend as _action_requests_combat_defend, _action_requests_combat_flee as _action_requests_combat_flee,
    _action_requests_combat_use_item as _action_requests_combat_use_item, _action_requests_hostile_combat as _action_requests_hostile_combat,
    _extract_active_combat_state_for_turn as _extract_active_combat_state_for_turn, _lookup_actor_by_id as _lookup_actor_by_id,
)
from app.apps.rpg.session.semantic_state_changes import (
    _active_combat_state_from_runtime_or_simulation as _active_combat_state_from_runtime_or_simulation, _get_combat_state as _get_combat_state,
    _set_combat_state as _set_combat_state,
)
from app.apps.rpg.session.combat_action_runtime import (
    _apply_active_non_attack_combat_action as _apply_active_non_attack_combat_action, _apply_attack_combat_action as _apply_attack_combat_action,
)
from app.apps.rpg.session.action_execution import (
    _apply_authoritative_action as _apply_authoritative_action,
)
from app.apps.rpg.session.session_runtime_store import (
    _combat_utility_kind_from_semantic_or_text as _combat_utility_kind_from_semantic_or_text, _extract_semantic_action_record_for_turn as _extract_semantic_action_record_for_turn,
    _find_active_combat_state_deep as _find_active_combat_state_deep,
)
from app.apps.rpg.session.state_normalization import (
    _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_str as _safe_str,
)
from app.apps.rpg.session.visible_response_core import (
    _has_pending_conversation_response as _has_pending_conversation_response,
)
from app.apps.rpg.session.special_combat_turns import (
    _maybe_gate_non_player_combat_turn as _maybe_gate_non_player_combat_turn, _maybe_resolve_companion_combat_command_turn as _maybe_resolve_companion_combat_command_turn,
    _maybe_resolve_reposition_turn as _maybe_resolve_reposition_turn, _maybe_resolve_revive_turn as _maybe_resolve_revive_turn,
    _maybe_resolve_stabilize_turn as _maybe_resolve_stabilize_turn, _resolve_post_authoritative_combat_utility_turn as _resolve_post_authoritative_combat_utility_turn,
)
from app.apps.rpg.session.combat_turn_actions import (
    _repair_generated_encounter_player_turn as _repair_generated_encounter_player_turn,
)
import copy
from app.apps.rpg.session.service_runtime import (
    mirror_service_result as mirror_service_result,
)




def _resolve_companion_reposition_and_authoritative_action(ctx) -> bool:
    ctx._maybe_resolve_companion_combat_command_turn_result = (
        _maybe_resolve_companion_combat_command_turn(
            player_input=ctx.player_input,
            simulation_state=ctx.simulation_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            active_combat_state=ctx.active_combat_state,
            companion_command=ctx.companion_command,
        )
    )
    if ctx._maybe_resolve_companion_combat_command_turn_result is not None:
        ctx._result = ctx._maybe_resolve_companion_combat_command_turn_result
        return True
    ctx.active_combat_state = _active_combat_state_from_runtime_or_simulation(
        ctx.runtime_state,
        ctx.simulation_state,
    )
    ctx._maybe_resolve_reposition_turn_result = _maybe_resolve_reposition_turn(
        player_input=ctx.player_input,
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        active_combat_state=ctx.active_combat_state,
    )
    if ctx._maybe_resolve_reposition_turn_result is not None:
        ctx._result = ctx._maybe_resolve_reposition_turn_result
        return True
    ctx.before_state = copy.deepcopy(ctx.simulation_state)
    ctx.combat_state = _safe_dict(
        _get_combat_state(ctx.runtime_state)
    )
    if (
        ctx.combat_state.get("active")
        and _safe_str(ctx.combat_state.get("source")).strip()
        == "deterministic_encounter_builder"
    ):
        ctx.repaired_combat_state = _repair_generated_encounter_player_turn(
            ctx.combat_state
        )
        if ctx.repaired_combat_state != ctx.combat_state:
            ctx.runtime_state = _set_combat_state(
                ctx.runtime_state, ctx.repaired_combat_state
            )
            ctx.simulation_state["combat_state"] = ctx.repaired_combat_state
            ctx.combat_state = ctx.repaired_combat_state
    ctx.authoritative = _apply_authoritative_action(
        ctx.simulation_state, ctx.runtime_state, ctx.action
    )
    ctx.authoritative_simulation_state = _safe_dict(
        ctx.authoritative.get("simulation_state")
    )
    ctx.post_authoritative_semantic_action_record = (
        _extract_semantic_action_record_for_turn(
            ctx.semantic_action_record,
            ctx.authoritative,
        )
    )
    ctx.post_authoritative_utility_kind = (
        _combat_utility_kind_from_semantic_or_text(
            ctx.post_authoritative_semantic_action_record,
            ctx.player_input,
        )
    )
    ctx.post_authoritative_combat_state = _find_active_combat_state_deep(
        ctx.authoritative
    )
    # J19-J21:
    # Active-combat utility commands may not see active combat in runtime_state
    # before _apply_authoritative_action(...). In that case, the generic
    # interaction runtime can return unsupported_interaction_kind while carrying
    # the active combat_state inside the authoritative payload. Rescue those
    # commands here and resolve them as combat actions before the unsupported
    # interaction becomes the final visible result.
    if ctx.post_authoritative_utility_kind and ctx.post_authoritative_combat_state.get(
        "active"
    ):
        ctx._result = _resolve_post_authoritative_combat_utility_turn(
            runtime_state=ctx.runtime_state,
            post_authoritative_utility_kind=ctx.post_authoritative_utility_kind,
            post_authoritative_combat_state=ctx.post_authoritative_combat_state,
            post_authoritative_semantic_action_record=ctx.post_authoritative_semantic_action_record,
            authoritative_simulation_state=ctx.authoritative_simulation_state,
            simulation_state=ctx.simulation_state,
            action=ctx.action,
            player_input=ctx.player_input,
            player_actor_id=ctx.player_actor_id,
            current_tick=ctx.current_tick,
            session_id=ctx.session_id,
        )
        return True
    if ctx.authoritative_simulation_state:
        ctx.simulation_state = ctx.authoritative_simulation_state
    ctx.after_action_state = _ensure_simulation_state(
        _safe_dict(ctx.authoritative.get("simulation_state"))
    )
    ctx.resolved_result = _safe_dict(ctx.authoritative.get("result"))
    ctx.resolved_result.setdefault("action_type", ctx.action_type)
    ctx.service_resolution = mirror_service_result(
        ctx.resolved_result,
        ctx.action,
        action_type=ctx.action_type,
        semantic_action_record=ctx.semantic_action_record,
    )
    ctx.resolved_result = _safe_dict(
        ctx.service_resolution.get("resolved_result")
    )
    ctx.action = _safe_dict(ctx.service_resolution.get("action"))
    ctx.combat_state = _extract_active_combat_state_for_turn(
        ctx.runtime_state, ctx.resolved_result
    )
    if ctx.combat_state.get("active"):
        ctx.runtime_state = _set_combat_state(
            ctx.runtime_state, ctx.combat_state
        )
    ctx.combat_result = {}
    return False


def _resolve_companion_reposition_and_authoritative_action_continued_1(ctx) -> bool:
    ctx.npc_combat_result = {}
    ctx.normalized_action_type = (
        _safe_str(_safe_dict(ctx.action).get("action_type"))
        .strip()
        .lower()
    )
    ctx.target_id = _safe_str(
        _safe_dict(ctx.action).get("target_id")
    ).strip()
    # J19-J21:
    # Combat utility actions must be recognized directly from player text when
    # combat is already active. Do not require the general semantic-action layer
    # to classify them first, because otherwise clear commands like "I flee."
    # can fall through to no_supported_semantic_action_detected before the
    # combat runtime sees them.
    if ctx.combat_state.get("active"):
        ctx.action_obj = _safe_dict(ctx.action)
        if not ctx.normalized_action_type:
            if _action_requests_combat_defend(
                ctx.action_obj, ctx.player_input
            ):
                ctx.action_obj = dict(ctx.action_obj)
                ctx.action_obj["action_type"] = "defend"
                ctx.action = ctx.action_obj
                ctx.normalized_action_type = "defend"
            elif _action_requests_combat_flee(
                ctx.action_obj, ctx.player_input
            ):
                ctx.action_obj = dict(ctx.action_obj)
                ctx.action_obj["action_type"] = "flee"
                ctx.action = ctx.action_obj
                ctx.normalized_action_type = "flee"
            elif _action_requests_combat_use_item(
                ctx.action_obj, ctx.player_input
            ):
                ctx.action_obj = dict(ctx.action_obj)
                ctx.action_obj["action_type"] = "use_item"
                ctx.action = ctx.action_obj
                ctx.normalized_action_type = "use_item"
    ctx.is_combat_attack = _action_requests_hostile_combat(
        ctx.action, ctx.player_input
    )
    return False


def _normalize_active_combat_action(ctx) -> bool:
    ctx.is_combat_defend = _action_requests_combat_defend(
        ctx.action, ctx.player_input
    )
    ctx.is_combat_flee = _action_requests_combat_flee(
        ctx.action, ctx.player_input
    )
    ctx.is_combat_use_item = _action_requests_combat_use_item(
        ctx.action, ctx.player_input
    )
    ctx.is_combat_action = ctx.is_combat_attack or (
        ctx.combat_state.get("active")
        and (ctx.is_combat_defend or ctx.is_combat_flee or ctx.is_combat_use_item)
    )
    if ctx.is_combat_attack and ctx.target_id:
        ctx.target_actor = _lookup_actor_by_id(
            ctx.after_action_state, ctx.target_id
        )
        if not ctx.target_actor:
            ctx.is_combat_action = False
    ctx._maybe_resolve_stabilize_turn_result = _maybe_resolve_stabilize_turn(
        player_input=ctx.player_input,
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        combat_state=ctx.combat_state,
    )
    if ctx._maybe_resolve_stabilize_turn_result is not None:
        ctx._result = ctx._maybe_resolve_stabilize_turn_result
        return True
    ctx._maybe_resolve_revive_turn_result = _maybe_resolve_revive_turn(
        player_input=ctx.player_input,
        simulation_state=ctx.simulation_state,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        combat_state=ctx.combat_state,
    )
    if ctx._maybe_resolve_revive_turn_result is not None:
        ctx._result = ctx._maybe_resolve_revive_turn_result
        return True
    ctx._maybe_gate_non_player_combat_turn_result = (
        _maybe_gate_non_player_combat_turn(
            player_input=ctx.player_input,
            after_action_state=ctx.after_action_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            turn_id=ctx.turn_id,
            player_actor_id=ctx.player_actor_id,
            combat_state=ctx.combat_state,
            normalized_action_type=ctx.normalized_action_type,
        )
    )
    if ctx._maybe_gate_non_player_combat_turn_result is not None:
        ctx._result = ctx._maybe_gate_non_player_combat_turn_result
        return True
    ctx._apply_active_non_attack_combat_action_context = (
        _apply_active_non_attack_combat_action(
            player_input=ctx.player_input,
            action=ctx.action,
            after_action_state=ctx.after_action_state,
            runtime_state=ctx.runtime_state,
            current_tick=ctx.current_tick,
            turn_id=ctx.turn_id,
            final_tick=ctx.final_tick,
            player_actor_id=ctx.player_actor_id,
            resolved_result=ctx.resolved_result,
            authoritative=ctx.authoritative,
            combat_state=ctx.combat_state,
            combat_result=ctx.combat_result,
            npc_combat_result=ctx.npc_combat_result,
            is_combat_action=ctx.is_combat_action,
            is_combat_attack=ctx.is_combat_attack,
            is_combat_defend=ctx.is_combat_defend,
            is_combat_flee=ctx.is_combat_flee,
            is_combat_use_item=ctx.is_combat_use_item,
            normalized_action_type=ctx.normalized_action_type,
        )
    )
    if (
        ctx._apply_active_non_attack_combat_action_context.get("return_result")
        is not None
    ):
        ctx._result = _safe_dict(
            ctx._apply_active_non_attack_combat_action_context.get("return_result")
        )
        return True
    ctx.after_action_state = ctx._apply_active_non_attack_combat_action_context[
        "after_action_state"
    ]
    ctx.runtime_state = ctx._apply_active_non_attack_combat_action_context[
        "runtime_state"
    ]
    ctx.resolved_result = ctx._apply_active_non_attack_combat_action_context[
        "resolved_result"
    ]
    ctx.combat_state = ctx._apply_active_non_attack_combat_action_context[
        "combat_state"
    ]
    ctx.combat_result = ctx._apply_active_non_attack_combat_action_context[
        "combat_result"
    ]
    ctx.npc_combat_result = ctx._apply_active_non_attack_combat_action_context[
        "npc_combat_result"
    ]
    return False


def _normalize_active_combat_action_continued_1(ctx) -> bool:
    ctx._apply_attack_combat_action_context = _apply_attack_combat_action(
        player_input=ctx.player_input,
        after_action_state=ctx.after_action_state,
        runtime_state=ctx.runtime_state,
        current_tick=ctx.current_tick,
        turn_id=ctx.turn_id,
        final_tick=ctx.final_tick,
        player_actor_id=ctx.player_actor_id,
        resolved_result=ctx.resolved_result,
        authoritative=ctx.authoritative,
        combat_state=ctx.combat_state,
        combat_result=ctx.combat_result,
        npc_combat_result=ctx.npc_combat_result,
        is_combat_attack=ctx.is_combat_attack,
        normalized_action_type=ctx.normalized_action_type,
        target_id=ctx.target_id,
    )
    if ctx._apply_attack_combat_action_context.get("return_result") is not None:
        ctx._result = _safe_dict(
            ctx._apply_attack_combat_action_context.get("return_result")
        )
        return True
    ctx.after_action_state = ctx._apply_attack_combat_action_context[
        "after_action_state"
    ]
    ctx.runtime_state = ctx._apply_attack_combat_action_context["runtime_state"]
    ctx.resolved_result = ctx._apply_attack_combat_action_context["resolved_result"]
    ctx.combat_state = ctx._apply_attack_combat_action_context["combat_state"]
    ctx.combat_result = ctx._apply_attack_combat_action_context["combat_result"]
    ctx.npc_combat_result = ctx._apply_attack_combat_action_context["npc_combat_result"]
    ctx.after_action_state = _ensure_simulation_state(
        _safe_dict(ctx.authoritative.get("simulation_state"))
    )
    ctx.resolved_result = _safe_dict(ctx.authoritative.get("result"))
    ctx.resolved_result.setdefault("action_type", ctx.action_type)
    ctx.pending_conversation_reply = _has_pending_conversation_response(
        ctx.after_action_state
    )
    ctx.service_resolution = mirror_service_result(
        ctx.resolved_result,
        ctx.action,
        action_type=ctx.action_type,
        semantic_action_record=ctx.semantic_action_record,
    )
    ctx.resolved_result = _safe_dict(
        ctx.service_resolution.get("resolved_result")
    )
    return False
