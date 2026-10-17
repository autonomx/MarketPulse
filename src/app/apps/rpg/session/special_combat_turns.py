from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.combat_intent import (
    _action_requests_revive_or_heal_other as _action_requests_revive_or_heal_other, _action_requests_stabilize as _action_requests_stabilize, _actor_is_player as _actor_is_player,
    _build_combat_gate_result as _build_combat_gate_result, _derive_grounded_scene_context as _derive_grounded_scene_context,
    _infer_inventory_item_id_from_text as _infer_inventory_item_id_from_text, _infer_recovery_target_actor_id as _infer_recovery_target_actor_id,
)
from app.apps.rpg.narration.ai.conversation_threads import (
    build_conversation_thread_prompt_context as build_conversation_thread_prompt_context,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _build_turn_id as _build_turn_id, _player_input_requests_reposition as _player_input_requests_reposition, _requested_reposition_values as _requested_reposition_values,
)
from app.apps.rpg.rules.combat.state import (
    get_current_actor_id as get_current_actor_id, normalize_combat_state as normalize_combat_state,
)
from app.apps.rpg.session.state_normalization import (
    _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.rules.combat.recovery import (
    revive_participant_with_healing as revive_participant_with_healing, stabilize_participant as stabilize_participant,
)
from app.apps.rpg.session.semantic_state_changes import (
    _get_combat_state as _get_combat_state, _set_combat_state as _set_combat_state,
)
from app.apps.rpg.rules.combat.positioning import (
    reposition_participant as reposition_participant,
)
from app.apps.rpg.rules.combat.companion_ai import (
    apply_companion_intent as apply_companion_intent, choose_companion_intent as choose_companion_intent,
)
from app.apps.rpg.session.combat_turn_actions import (
    _target_id_for_ability as _target_id_for_ability,
)
from app.apps.rpg.rules.combat.abilities import (
    resolve_combat_ability as resolve_combat_ability,
)
from app.apps.rpg.rules.combat.encounters import (
    build_encounter_from_preset as build_encounter_from_preset,
)
from app.apps.rpg.world.travel_graph import (
    apply_travel_result_to_state as apply_travel_result_to_state, build_travel_state_delta as build_travel_state_delta, build_travel_world_event as build_travel_world_event,
    list_available_routes as list_available_routes, resolve_travel_destination as resolve_travel_destination,
)
from app.apps.rpg.rules.combat.resolver import (
    resolve_defend as resolve_defend, resolve_flee as resolve_flee,
)
from app.apps.rpg.rules.combat.apply import (
    apply_defense_resolution as apply_defense_resolution, apply_flee_resolution as apply_flee_resolution,
)
from app.apps.rpg.rules.items.item_effects import (
    apply_item_effects as apply_item_effects,
)
from app.apps.rpg.rules.combat.initiative import (
    advance_turn as advance_turn,
)
from app.apps.rpg.rules.combat.npc_turns import (
    run_npc_turn as run_npc_turn,
)
from app.apps.rpg.rules.combat.lifecycle import (
    evaluate_combat_exit as evaluate_combat_exit,
)
from typing import (
    Any as Any, Dict as Dict,
)


def _resolve_post_authoritative_combat_utility_turn(
    *,
    runtime_state: dict[str, Any],
    post_authoritative_utility_kind: str,
    post_authoritative_combat_state: dict[str, Any],
    post_authoritative_semantic_action_record: dict[str, Any],
    authoritative_simulation_state: dict[str, Any],
    simulation_state: dict[str, Any],
    action: dict[str, Any],
    player_input: str,
    player_actor_id: str,
    current_tick: int,
    session_id: str,
) -> dict[str, Any]:
    utility_turn_id = _build_turn_id(runtime_state)
    utility_tick = current_tick
    combat_state = normalize_combat_state(post_authoritative_combat_state)
    after_action_state = _ensure_simulation_state(authoritative_simulation_state or simulation_state)

    resolved_result: dict[str, Any] = {
        "action_type": post_authoritative_utility_kind,
        "outcome": post_authoritative_utility_kind,
        "visible_interaction_reason": f"combat_{post_authoritative_utility_kind}",
        "semantic_action_v2": post_authoritative_semantic_action_record,
        "interaction_result": {},
        "conversation_result": {
            "triggered": False,
            "reason": "combat_utility_action",
            "source": "deterministic_conversation_thread_runtime",
        },
    }

    combat_result: dict[str, Any] = {}
    npc_combat_result: dict[str, Any] = {}

    current_actor_id = get_current_actor_id(combat_state)
    if current_actor_id and _safe_str(current_actor_id) != _safe_str(player_actor_id):
        resolved_result = _build_combat_gate_result(current_actor_id, player_actor_id)

    elif post_authoritative_utility_kind == "defend":
        defense_resolution = resolve_defend(
            after_action_state,
            combat_state,
            _safe_str(player_actor_id),
        )
        combat_result = defense_resolution.to_dict()
        after_action_state, combat_state = apply_defense_resolution(
            after_action_state,
            combat_state,
            combat_result,
        )
        resolved_result["outcome"] = "defended"
        resolved_result["combat_result"] = combat_result

    elif post_authoritative_utility_kind == "flee":
        flee_resolution = resolve_flee(
            after_action_state,
            combat_state,
            _safe_str(player_actor_id),
            turn_id=utility_turn_id,
            tick=utility_tick,
        )
        combat_result = flee_resolution.to_dict()
        after_action_state, combat_state = apply_flee_resolution(
            after_action_state,
            combat_state,
            combat_result,
        )
        resolved_result["outcome"] = "fled" if combat_result.get("success") else "flee_failed"
        resolved_result["combat_result"] = combat_result

    elif post_authoritative_utility_kind == "use_item":
        item_id = _infer_inventory_item_id_from_text(after_action_state, action, player_input)
        item_result = apply_item_effects(after_action_state, item_id)
        after_action_state = _ensure_simulation_state(_safe_dict(item_result.get("simulation_state")))
        combat_result = _safe_dict(item_result.get("result"))
        combat_result.setdefault("action_type", "use_item")
        combat_result.setdefault("combat_id", _safe_str(combat_state.get("combat_id")))
        combat_result.setdefault(
            "notes",
            ["combat_item_used"] if combat_result.get("ok") else ["combat_item_failed"],
        )
        resolved_result["outcome"] = "item_used" if combat_result.get("ok") else "item_use_failed"
        resolved_result["combat_result"] = combat_result
        resolved_result["inventory_result"] = combat_result

    if combat_state.get("active"):
        combat_state = advance_turn(combat_state)
        current_after_player = get_current_actor_id(combat_state)
        if current_after_player and not _actor_is_player(after_action_state, current_after_player):
            after_action_state, combat_state, npc_combat_result = run_npc_turn(
                after_action_state,
                combat_state,
                tick=utility_tick,
            )
            combat_state = evaluate_combat_exit(after_action_state, combat_state)

    runtime_state = _set_combat_state(runtime_state, combat_state)

    if npc_combat_result:
        resolved_result["npc_combat_result"] = npc_combat_result

    grounded = _derive_grounded_scene_context(after_action_state, runtime_state, resolved_result)
    narration_context = {
        "player_input": player_input,
        "action_type": post_authoritative_utility_kind,
        "resolved_result": resolved_result,
        "simulation_state": after_action_state,
        "runtime_state": runtime_state,
        "combat_result": combat_result,
        "npc_combat_result": npc_combat_result,
        "combat_state": combat_state,
        "grounded": grounded,
        "xp_result": {},
        "skill_xp_result": {},
        "level_up": [],
        "skill_level_ups": [],
        "settings": runtime_state.get("runtime_settings", {}),
        "conversation_threads": build_conversation_thread_prompt_context(
            runtime_state,
            current_tick=utility_tick,
            limit=4,
        ),
    }

    return {
        "ok": True,
        "simulation_state": after_action_state,
        "runtime_state": runtime_state,
        "session": {
            "id": f"session:{session_id}",
            "session_id": session_id,
            "simulation_state": after_action_state,
            "runtime_state": runtime_state,
        },
        "result": resolved_result,
        "narration_context": narration_context,
        "turn_id": utility_turn_id,
        "tick": utility_tick,
    }


def _apply_deterministic_travel_resolution(
    *,
    ambient_tick_result: dict[str, Any],
    resolved_result: dict[str, Any],
    authoritative: dict[str, Any],
    player_input: str,
    after_action_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    # Phase 9.0: deterministic travel/scene transition runtime.
    # Service turns remain service-first. Travel only claims the turn when no
    # deterministic service result has matched.
    _service_result_for_travel = _safe_dict(resolved_result.get("service_result"))
    if not ambient_tick_result and not _service_result_for_travel.get("matched"):
        travel_result = resolve_travel_destination(
            player_input=player_input,
            state=after_action_state,
        )

        if travel_result.get("ok"):
            after_action_state = apply_travel_result_to_state(
                state=after_action_state,
                travel_result=travel_result,
            )

            state_delta = build_travel_state_delta(travel_result)
            world_event = build_travel_world_event(travel_result)

            resolved_result.update({
                "ok": True,
                "action_type": "travel",
                "semantic_action_type": "travel",
                "summary": world_event.get("summary"),
                "travel_result": travel_result,
                "state_delta": state_delta,
                "world_event": world_event,
                "meaningful_progress": True,
                "progress_category": "location_progression",
            })

            authoritative["action_type"] = "travel"
            authoritative["semantic_action_type"] = "travel"
            authoritative["player_input"] = player_input
            authoritative["current_location"] = travel_result.get("to_location")
            authoritative["previous_location"] = travel_result.get("from_location")
            authoritative["available_routes"] = list_available_routes(state=after_action_state)
            authoritative["state_delta"] = {
                **_safe_dict(authoritative.get("state_delta")),
                **state_delta,
            }
            authoritative["result"] = {
                **_safe_dict(authoritative.get("result")),
                "ok": True,
                "outcome": "success",
                "action_type": "travel",
                "summary": world_event.get("summary"),
                "travel_result": travel_result,
                "state_delta": state_delta,
                "meaningful_progress": True,
                "progress_category": "location_progression",
            }
            authoritative["world_events"] = [
                *_safe_list(authoritative.get("world_events")),
                world_event,
            ]

            # Continue through existing narration/persistence path with this resolved result.

        elif travel_result.get("reason") == "unknown_or_unreachable_destination":
            available_routes = _safe_list(travel_result.get("available_routes"))
            route_labels = [
                route.get("to_name") or route.get("to_location")
                for route in available_routes[:4]
                if route.get("to_name") or route.get("to_location")
            ]

            resolved_result.update({
                "ok": False,
                "action_type": "travel",
                "semantic_action_type": "travel",
                "summary": (
                    "No known route matches that destination. "
                    + (
                        "Available routes: " + ", ".join(str(label) for label in route_labels)
                        if route_labels
                        else "No routes are currently available."
                    )
                ),
                "travel_result": travel_result,
                "meaningful_progress": False,
                "progress_category": "blocked_travel",
            })

            authoritative["action_type"] = "travel"
            authoritative["semantic_action_type"] = "travel"
            authoritative["player_input"] = player_input
            authoritative["current_location"] = travel_result.get("current_location")
            authoritative["available_routes"] = available_routes
            authoritative["suggested_actions"] = [
                {
                    "type": "travel",
                    "label": f"Travel to {label}",
                    "command": f"go to {label}",
                }
                for label in route_labels
                if label
            ]
            authoritative["result"] = {
                **_safe_dict(authoritative.get("result")),
                "ok": False,
                "outcome": "failure",
                "action_type": "travel",
                "summary": resolved_result.get("summary"),
                "travel_result": travel_result,
                "meaningful_progress": False,
                "progress_category": "blocked_travel",
            }

            # Continue through narration path, not generic observe.

        authoritative["travel_result"] = travel_result
        authoritative["resolved_result"] = resolved_result
        authoritative["location_state"] = _safe_dict(after_action_state.get("location_state"))
        authoritative["world_event_state"] = _safe_dict(after_action_state.get("world_event_state"))
        authoritative["simulation_state"] = after_action_state
    return after_action_state, resolved_result, authoritative

def _maybe_start_manual_encounter(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    manual_encounter_preset: Any,
) -> dict[str, Any] | None:
    if manual_encounter_preset:
        encounter = build_encounter_from_preset(
            simulation_state,
            manual_encounter_preset,
        )
        combat_state = _safe_dict(encounter.get("combat_state"))
        encounter_result = _safe_dict(encounter.get("encounter_result"))

        if combat_state.get("active"):
            runtime_state = _set_combat_state(runtime_state, combat_state)

        resolved_result = {
            "action_type": "start_encounter",
            "visible_interaction_reason": (
                "combat_encounter_started"
                if combat_state.get("active")
                else "combat_encounter_not_started"
            ),
            "outcome": (
                "encounter_started"
                if combat_state.get("active")
                else "encounter_not_started"
            ),
            "encounter_result": encounter_result,
            "combat_state": combat_state,
            "conversation_result": {
                "triggered": False,
                "reason": "manual_encounter_start",
            },
        }

        grounded = _derive_grounded_scene_context(
            simulation_state,
            runtime_state,
            resolved_result,
        )

        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
            },
            "result": resolved_result,
            "narration_context": {
                "player_input": player_input,
                "action_type": "start_encounter",
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": {},
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_resolve_combat_ability_turn(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    ability_id: Any,
) -> dict[str, Any] | None:
    if ability_id and _safe_dict(_get_combat_state(runtime_state)).get("active"):
        combat_state = _safe_dict(_get_combat_state(runtime_state))
        target_id = _target_id_for_ability(runtime_state, player_input)
        combat_state, ability_result = resolve_combat_ability(
            combat_state,
            actor_id="player",
            target_id=target_id,
            ability_id=ability_id,
        )
        runtime_state = _set_combat_state(runtime_state, combat_state)

        visible_reason = (
            "combat_ability_used"
            if ability_result.get("used")
            else "combat_ability_failed"
        )

        resolved_result = {
            "action_type": "use_ability",
            "visible_interaction_reason": visible_reason,
            "outcome": ability_result.get("reason"),
            "ability_result": ability_result,
            "combat_result": {
                "action_type": "use_ability",
                "ability_result": ability_result,
                "condition_result": ability_result.get("condition_result", {}),
            },
            "condition_result": ability_result.get("condition_result", {}),
            "combat_state": combat_state,
            "conversation_result": {
                "triggered": False,
                "reason": "combat_ability_action",
            },
        }

        grounded = _derive_grounded_scene_context(simulation_state, runtime_state, resolved_result)
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
            },
            "result": resolved_result,
            "resolved_result": resolved_result,
            "ability_result": ability_result,
            "combat_result": resolved_result["combat_result"],
            "condition_result": ability_result.get("condition_result", {}),
            "combat_state": combat_state,
            "visible_interaction_reason": visible_reason,
            "narration": f"Result: {visible_reason}",
            "final_narration": f"Result: {visible_reason}",
            "summary": f"Result: {visible_reason}",
            "narration_context": {
                "player_input": player_input,
                "action_type": "use_ability",
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": resolved_result["combat_result"],
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_resolve_companion_combat_command_turn(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    active_combat_state: Any,
    companion_command: Any,
) -> dict[str, Any] | None:
    if companion_command and active_combat_state.get("active"):
        combat_state = active_combat_state
        runtime_state = _set_combat_state(runtime_state, combat_state)
        simulation_state["combat_state"] = combat_state
        actor_id = _safe_str(companion_command.get("companion_actor_id")).strip()
        intent_result = choose_companion_intent(combat_state, actor_id, command=companion_command)

        if not intent_result.get("selected"):
            resolved_result = {
                "action_type": "companion_command",
                "visible_interaction_reason": "combat_companion_command_failed",
                "outcome": intent_result.get("reason"),
                "companion_intent_result": intent_result,
                "companion_command_result": intent_result.get("companion_command_result", {}),
                "combat_state": combat_state,
                "conversation_result": {"triggered": False, "reason": "combat_companion_command"},
            }
        else:
            simulation_state, combat_state, companion_result = apply_companion_intent(
                simulation_state,
                combat_state,
                intent_result,
            )
            runtime_state = _set_combat_state(runtime_state, combat_state)
            resolved_result = {
                "action_type": companion_result.get("action_type", "companion_action"),
                "visible_interaction_reason": "combat_companion_action",
                "outcome": companion_result.get("reason"),
                "companion_result": companion_result,
                "companion_intent_result": intent_result,
                "companion_command_result": companion_result.get("companion_command_result", {}),
                "combat_result": companion_result.get("combat_result", {}),
                "ability_result": companion_result.get("ability_result", {}),
                "position_result": companion_result.get("position_result", {}),
                "combat_state": combat_state,
                "conversation_result": {"triggered": False, "reason": "combat_companion_action"},
            }

        grounded = _derive_grounded_scene_context(simulation_state, runtime_state, resolved_result)
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {"simulation_state": simulation_state, "runtime_state": runtime_state},
            "result": resolved_result,
            "resolved_result": resolved_result,
            "combat_result": resolved_result.get("combat_result", {}),
            "ability_result": resolved_result.get("ability_result", {}),
            "companion_result": resolved_result.get("companion_result", {}),
            "companion_intent_result": resolved_result.get("companion_intent_result", {}),
            "companion_command_result": resolved_result.get("companion_command_result", {}),
            "combat_state": combat_state,
            "visible_interaction_reason": resolved_result["visible_interaction_reason"],
            "narration": f"Result: {resolved_result['visible_interaction_reason']}",
            "final_narration": f"Result: {resolved_result['visible_interaction_reason']}",
            "summary": f"Result: {resolved_result['visible_interaction_reason']}",
            "narration_context": {
                "player_input": player_input,
                "action_type": resolved_result.get("action_type"),
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": resolved_result.get("combat_result", {}),
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_resolve_reposition_turn(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    active_combat_state: Any,
) -> dict[str, Any] | None:
    if _player_input_requests_reposition(player_input) and active_combat_state.get("active"):
        combat_state = active_combat_state
        runtime_state = _set_combat_state(runtime_state, combat_state)
        simulation_state["combat_state"] = combat_state
        vals = _requested_reposition_values(player_input)
        combat_state, position_result = reposition_participant(
            combat_state,
            "player",
            zone=vals["zone"],
            range_band=vals["range_band"],
        )
        runtime_state = _set_combat_state(runtime_state, combat_state)
        resolved_result = {
            "action_type": "reposition",
            "visible_interaction_reason": "combat_reposition",
            "outcome": position_result.get("reason"),
            "position_result": position_result,
            "combat_state": combat_state,
            "conversation_result": {"triggered": False, "reason": "combat_reposition"},
        }
        grounded = _derive_grounded_scene_context(simulation_state, runtime_state, resolved_result)
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {"simulation_state": simulation_state, "runtime_state": runtime_state},
            "result": resolved_result,
            "resolved_result": resolved_result,
            "position_result": position_result,
            "combat_state": combat_state,
            "visible_interaction_reason": "combat_reposition",
            "narration": "Result: combat_reposition",
            "final_narration": "Result: combat_reposition",
            "summary": "Result: combat_reposition",
            "narration_context": {
                "player_input": player_input,
                "action_type": "reposition",
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": {},
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_resolve_stabilize_turn(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    combat_state: Any,
) -> dict[str, Any] | None:
    if combat_state.get("active") and _action_requests_stabilize(player_input):
        target_actor_id = _infer_recovery_target_actor_id(runtime_state, player_input)
        combat_state, recovery_result = stabilize_participant(
            combat_state,
            target_actor_id,
            source_actor_id="player",
        )
        runtime_state = _set_combat_state(runtime_state, combat_state)
        resolved_result = {
            "action_type": "stabilize",
            "visible_interaction_reason": "combat_stabilize",
            "outcome": recovery_result.get("reason"),
            "recovery_result": recovery_result,
            "condition_result": recovery_result,
            "combat_state": combat_state,
            "conversation_result": {
                "triggered": False,
                "reason": "combat_recovery_action",
            },
        }
        grounded = _derive_grounded_scene_context(simulation_state, runtime_state, resolved_result)
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
            },
            "result": resolved_result,
            "narration_context": {
                "player_input": player_input,
                "action_type": "stabilize",
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": {},
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_resolve_revive_turn(
    player_input: Any,
    simulation_state: Any,
    runtime_state: Any,
    current_tick: Any,
    combat_state: Any,
) -> dict[str, Any] | None:
    if combat_state.get("active") and _action_requests_revive_or_heal_other(player_input):
        target_actor_id = _infer_recovery_target_actor_id(runtime_state, player_input)
        combat_state, recovery_result = revive_participant_with_healing(
            combat_state,
            target_actor_id,
            amount=5,
            source_actor_id="player",
        )
        runtime_state = _set_combat_state(runtime_state, combat_state)
        resolved_result = {
            "action_type": "revive",
            "visible_interaction_reason": "combat_revive",
            "outcome": recovery_result.get("reason"),
            "recovery_result": recovery_result,
            "combat_state": combat_state,
            "conversation_result": {
                "triggered": False,
                "reason": "combat_recovery_action",
            },
        }
        grounded = _derive_grounded_scene_context(simulation_state, runtime_state, resolved_result)
        return {
            "ok": True,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "session": {
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
            },
            "result": resolved_result,
            "narration_context": {
                "player_input": player_input,
                "action_type": "revive",
                "resolved_result": resolved_result,
                "simulation_state": simulation_state,
                "runtime_state": runtime_state,
                "combat_result": {},
                "npc_combat_result": {},
                "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": [],
            },
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None


def _maybe_gate_non_player_combat_turn(
    player_input: Any,
    after_action_state: Any,
    runtime_state: Any,
    current_tick: Any,
    turn_id: Any,
    player_actor_id: Any,
    combat_state: Any,
    normalized_action_type: Any,
) -> dict[str, Any] | None:
    if combat_state.get("active"):
        current_actor_id = get_current_actor_id(combat_state)
        if current_actor_id and _safe_str(current_actor_id) != _safe_str(player_actor_id):
            resolved_result = _build_combat_gate_result(current_actor_id, player_actor_id)
            grounded = _derive_grounded_scene_context(after_action_state, runtime_state, resolved_result)
            narration_context = {
                "player_input": player_input,
                "action_type": normalized_action_type,
                "resolved_result": resolved_result,
                "simulation_state": after_action_state,
                "runtime_state": runtime_state,
                 "combat_result": {},
                 "npc_combat_result": {},
                 "combat_state": combat_state,
                "grounded": grounded,
                "xp_result": {},
                "skill_xp_result": {},
                "level_up": [],
                "skill_level_ups": [],
                "settings": runtime_state.get("runtime_settings", {}),
                "conversation_threads": build_conversation_thread_prompt_context(
                    runtime_state,
                     current_tick=current_tick,
                    limit=4,
                ),
            }
            return {
                "ok": True,
                "simulation_state": after_action_state,
                "runtime_state": runtime_state,
                "result": resolved_result,
                "narration_context": narration_context,
                "turn_id": turn_id,
                "tick": current_tick,
            }
    return None


def _maybe_return_completed_combat_utility_turn(
    player_input: Any,
    after_action_state: Any,
    runtime_state: Any,
    current_tick: Any,
    combat_result: Any,
    combat_state: Any,
    is_combat_action: Any,
    is_combat_attack: Any,
    normalized_action_type: Any,
    npc_combat_result: Any,
    resolved_result: Any,
) -> dict[str, Any] | None:
    if combat_state.get("active") and is_combat_action and not is_combat_attack:
        grounded = _derive_grounded_scene_context(after_action_state, runtime_state, resolved_result)
        narration_context = {
            "player_input": player_input,
            "action_type": normalized_action_type,
            "resolved_result": resolved_result,
            "simulation_state": after_action_state,
            "runtime_state": runtime_state,
            "combat_result": combat_result,
            "npc_combat_result": npc_combat_result,
            "combat_state": combat_state,
            "grounded": grounded,
            "xp_result": {},
            "skill_xp_result": {},
            "level_up": [],
            "skill_level_ups": [],
            "settings": runtime_state.get("runtime_settings", {}),
            "conversation_threads": build_conversation_thread_prompt_context(
                    runtime_state,
                    current_tick=current_tick,
                    limit=4,
                ),
            }
        return {
            "ok": True,
            "simulation_state": after_action_state,
            "runtime_state": runtime_state,
            "result": resolved_result,
            "narration_context": narration_context,
            "turn_id": _build_turn_id(runtime_state),
            "tick": current_tick,
        }
    return None

__all__ = ['_apply_deterministic_travel_resolution', '_maybe_gate_non_player_combat_turn', '_maybe_resolve_combat_ability_turn', '_maybe_resolve_companion_combat_command_turn', '_maybe_resolve_reposition_turn', '_maybe_resolve_revive_turn', '_maybe_resolve_stabilize_turn', '_maybe_return_completed_combat_utility_turn', '_maybe_start_manual_encounter', '_resolve_post_authoritative_combat_utility_turn']
