from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
from app.apps.rpg.session.semantic_state_changes import (
    _get_combat_state as _get_combat_state, _set_combat_state as _set_combat_state,
)
from app.apps.rpg.rules.combat.state import (
    get_current_actor_id as get_current_actor_id, normalize_combat_state as normalize_combat_state,
)
from app.apps.rpg.session.combat_intent import (
    _actor_is_player as _actor_is_player, _build_combat_gate_result as _build_combat_gate_result, _derive_grounded_scene_context as _derive_grounded_scene_context,
    _infer_inventory_item_id_from_text as _infer_inventory_item_id_from_text,
)
from app.apps.rpg.narration.ai.conversation_threads import (
    build_conversation_thread_prompt_context as build_conversation_thread_prompt_context,
)
from app.apps.rpg.rules.combat.resolver import (
    resolve_defend as resolve_defend, resolve_flee as resolve_flee,
)
from app.apps.rpg.rules.combat.apply import (
    apply_defense_resolution as apply_defense_resolution, apply_flee_resolution as apply_flee_resolution,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _build_turn_id as _build_turn_id, _fallback_general_interaction_narration as _fallback_general_interaction_narration,
    _player_input_requests_general_interaction as _player_input_requests_general_interaction, _player_input_requests_reposition as _player_input_requests_reposition,
    _requested_reposition_values as _requested_reposition_values,
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
from app.apps.rpg.rules.combat.encounters import (
    build_encounter_from_preset as build_encounter_from_preset,
)
from app.apps.rpg.rules.combat.world_consequences import (
    emit_combat_world_consequence as emit_combat_world_consequence,
)
from app.apps.rpg.session.session_runtime_store import (
    _find_active_combat_state_deep as _find_active_combat_state_deep, _safe_parse_mapping_payload as _safe_parse_mapping_payload,
)
from app.apps.rpg.rules.combat.positioning import (
    can_attack_target as can_attack_target, reposition_participant as reposition_participant,
)
from app.apps.rpg.rules.interactions.resolver import (
    resolve_general_interaction as resolve_general_interaction_v2,
)
from app.apps.rpg.world.social.npc_backbone import (
    resolve_npc_backbone_decision as resolve_npc_backbone_decision,
)
from app.apps.rpg.narration.quality import (
    update_narration_quality_memory as update_narration_quality_memory, validate_narration_quality as validate_narration_quality,
)
from app.apps.rpg.narration.contradictions import (
    validate_narration_contradictions as validate_narration_contradictions,
)
from typing import (
    Any as Any, Dict as Dict,
)


def _reconcile_narration_quality_memory_and_warnings(
    final_result: dict[str, Any],
) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    session = _safe_dict(final_result.get("session"))
    runtime_state = dict(_safe_dict(session.get("runtime_state") or final_result.get("runtime_state")))
    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))

    narration_text = _safe_str(
        final_result.get("final_narration")
        or final_result.get("narration")
        or final_result.get("summary")
        or _safe_dict(final_result.get("result")).get("final_narration")
        or _safe_dict(final_result.get("result")).get("narration")
    )

    warnings = validate_narration_quality(
        narration_text,
        runtime_state,
        resolved_result,
    )
    warnings.extend(validate_narration_contradictions(final_result))
    warnings = list(dict.fromkeys(warnings))

    if narration_text:
        runtime_state = update_narration_quality_memory(runtime_state, narration_text)
        session["runtime_state"] = runtime_state
        final_result["runtime_state"] = runtime_state
        final_result["session"] = session

        result_obj = dict(
            _safe_dict(final_result.get("result"))
            or _safe_parse_mapping_payload(final_result.get("result"))
        )
        if result_obj:
            result_obj["runtime_state"] = runtime_state
            final_result["result"] = result_obj

    if warnings:
        existing = list(_safe_list(final_result.get("narration_quality_warnings")))
        existing.extend(warnings)
        final_result["narration_quality_warnings"] = list(dict.fromkeys(existing))
        resolved_result["narration_quality_warnings"] = final_result["narration_quality_warnings"]
        final_result["resolved_result"] = resolved_result

        result_obj = dict(_safe_dict(final_result.get("result")) or _safe_parse_mapping_payload(final_result.get("result")))
        if result_obj:
            result_obj["narration_quality_warnings"] = final_result["narration_quality_warnings"]
            final_result["result"] = result_obj

    return final_result


def _reconcile_npc_backbone_social_decision(
    final_result: dict[str, Any],
    player_input: str,
) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    session = _safe_dict(final_result.get("session"))
    simulation_state = _safe_dict(session.get("simulation_state") or final_result.get("simulation_state"))
    runtime_state = _safe_dict(session.get("runtime_state") or final_result.get("runtime_state"))

    decision = resolve_npc_backbone_decision(
        simulation_state,
        runtime_state,
        player_input,
    )
    if not decision.get("detected"):
        return final_result

    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))

    # Do not override already-authoritative combat/service purchases.
    action_type = _safe_str(resolved_result.get("action_type") or final_result.get("action_type"))
    visible_reason = _safe_str(resolved_result.get("visible_interaction_reason") or final_result.get("visible_interaction_reason"))
    if visible_reason.startswith("combat_"):
        return final_result

    result_reason = f"npc_{decision.get('decision')}"
    narration = _fallback_npc_backbone_narration(decision)

    resolved_result["action_type"] = "npc_social_decision"
    resolved_result["visible_interaction_reason"] = result_reason
    resolved_result["outcome"] = decision.get("reason")
    resolved_result["npc_backbone_decision"] = decision
    resolved_result["conversation_result"] = {
        "triggered": False,
        "reason": "npc_backbone_social_decision",
    }

    final_result["resolved_result"] = resolved_result
    final_result["npc_backbone_decision"] = decision
    final_result["visible_interaction_reason"] = result_reason
    final_result["action_type"] = "npc_social_decision"
    final_result["outcome"] = decision.get("reason")
    final_result["conversation_result"] = resolved_result["conversation_result"]
    final_result["narration"] = narration
    final_result["final_narration"] = narration
    final_result["summary"] = narration

    result_obj = dict(_safe_dict(final_result.get("result")) or _safe_parse_mapping_payload(final_result.get("result")))
    if result_obj:
        result_obj["npc_backbone_decision"] = decision
        result_obj["visible_interaction_reason"] = result_reason
        result_obj["action_type"] = "npc_social_decision"
        result_obj["outcome"] = decision.get("reason")
        result_obj["conversation_result"] = resolved_result["conversation_result"]
        result_obj["narration"] = narration
        result_obj["final_narration"] = narration
        result_obj["summary"] = narration
        final_result["result"] = result_obj

    return final_result


def _fallback_npc_backbone_narration(decision: dict[str, Any]) -> str:
    npc_id = _safe_str(decision.get("npc_id") or "npc")
    decision_kind = _safe_str(decision.get("decision"))
    reason = _safe_str(decision.get("reason"))
    tone = _safe_str(decision.get("tone") or "firm")

    if npc_id == "npc:bran":
        name = "Bran"
    else:
        name = npc_id

    if decision_kind == "accept":
        return f"Result: {name} agrees, his tone {tone}, because {reason}."
    if decision_kind == "negotiate":
        alt = _safe_str(decision.get("may_offer_alternative") or "offers a limited alternative")
        return f"Result: {name} does not fully agree; he {alt.replace('_', ' ')}."
    if decision_kind == "escalate":
        escalation = _safe_str(decision.get("escalation") or "warns you to stop")
        return f"Result: {name} refuses and escalates: {escalation.replace('_', ' ')}."
    return f"Result: {name} refuses. Reason: {reason.replace('_', ' ')}."


def _reconcile_player_reposition_action(
    final_result: dict[str, Any],
    player_input: str,
) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    if not _player_input_requests_reposition(player_input):
        return final_result

    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    combat_state = dict(_safe_dict(
        final_result.get("combat_state")
        or resolved_result.get("combat_state")
        or _find_active_combat_state_deep(final_result)
    ))
    if not combat_state.get("active"):
        return final_result

    vals = _requested_reposition_values(player_input)
    combat_state, position_result = reposition_participant(
        combat_state,
        "player",
        zone=vals["zone"],
        range_band=vals["range_band"],
    )

    resolved_result["action_type"] = "reposition"
    resolved_result["visible_interaction_reason"] = "combat_reposition"
    resolved_result["outcome"] = position_result.get("reason")
    resolved_result["position_result"] = position_result
    resolved_result["combat_state"] = combat_state
    resolved_result["interaction_result"] = {}
    resolved_result["general_interaction_result"] = {}
    resolved_result["conversation_result"] = {
        "triggered": False,
        "reason": "combat_reposition",
    }

    final_result["resolved_result"] = resolved_result
    final_result["position_result"] = position_result
    final_result["combat_state"] = combat_state
    final_result["visible_interaction_reason"] = "combat_reposition"
    final_result["action_type"] = "reposition"
    final_result["outcome"] = position_result.get("reason")
    final_result["interaction_result"] = {}
    final_result["general_interaction_result"] = {}
    final_result["conversation_result"] = resolved_result["conversation_result"]
    final_result["narration"] = "Result: combat_reposition"
    final_result["final_narration"] = "Result: combat_reposition"
    final_result["summary"] = "Result: combat_reposition"

    result_obj = dict(_safe_dict(final_result.get("result")) or _safe_parse_mapping_payload(final_result.get("result")))
    if result_obj:
        result_obj["position_result"] = position_result
        result_obj["combat_state"] = combat_state
        result_obj["visible_interaction_reason"] = "combat_reposition"
        result_obj["action_type"] = "reposition"
        result_obj["outcome"] = position_result.get("reason")
        result_obj["interaction_result"] = {}
        result_obj["general_interaction_result"] = {}
        final_result["result"] = result_obj

    session = _safe_dict(final_result.get("session"))
    runtime_state = _safe_dict(session.get("runtime_state") or final_result.get("runtime_state"))
    simulation_state = _safe_dict(session.get("simulation_state") or final_result.get("simulation_state"))
    if runtime_state:
        runtime_state["combat_state"] = combat_state
        session["runtime_state"] = runtime_state
        final_result["runtime_state"] = runtime_state
    if simulation_state:
        simulation_state["combat_state"] = combat_state
        session["simulation_state"] = simulation_state
        final_result["simulation_state"] = simulation_state
    if session:
        final_result["session"] = session

    return final_result


def _reconcile_general_interaction_action(
    final_result: dict[str, Any],
    player_input: str,
) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    if not _player_input_requests_general_interaction(player_input):
        return final_result

    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    visible_reason = _safe_str(
        resolved_result.get("visible_interaction_reason")
        or final_result.get("visible_interaction_reason")
    )

    # Do not override combat.
    if visible_reason.startswith("combat_"):
        return final_result

    session = _safe_dict(final_result.get("session"))
    simulation_state = _safe_dict(session.get("simulation_state") or final_result.get("simulation_state"))
    runtime_state = _safe_dict(session.get("runtime_state") or final_result.get("runtime_state"))

    simulation_state, interaction_result = resolve_general_interaction_v2(
        simulation_state,
        runtime_state,
        player_input,
    )
    narration = _fallback_general_interaction_narration(interaction_result)

    resolved_result["action_type"] = interaction_result.get("action_type", "interact")
    resolved_result["visible_interaction_reason"] = f"interaction_{interaction_result.get('reason')}"
    resolved_result["outcome"] = interaction_result.get("reason")
    resolved_result["interaction_result"] = interaction_result
    resolved_result["general_interaction_result"] = interaction_result
    resolved_result["forbidden_narration"] = interaction_result.get("forbidden_narration", [])
    resolved_result["conversation_result"] = {
        "triggered": False,
        "reason": "general_interaction",
    }

    final_result["resolved_result"] = resolved_result
    final_result["interaction_result"] = interaction_result
    final_result["general_interaction_result"] = interaction_result
    final_result["visible_interaction_reason"] = resolved_result["visible_interaction_reason"]
    final_result["action_type"] = resolved_result["action_type"]
    final_result["outcome"] = resolved_result["outcome"]
    final_result["conversation_result"] = resolved_result["conversation_result"]
    final_result["narration"] = narration
    final_result["final_narration"] = narration
    final_result["summary"] = narration
    final_result["simulation_state"] = simulation_state

    if session:
        session["simulation_state"] = simulation_state
        session["runtime_state"] = runtime_state
        final_result["session"] = session

    result_obj = dict(
        _safe_dict(final_result.get("result"))
        or _safe_parse_mapping_payload(final_result.get("result"))
    )
    if result_obj:
        result_obj["interaction_result"] = interaction_result
        result_obj["general_interaction_result"] = interaction_result
        result_obj["visible_interaction_reason"] = resolved_result["visible_interaction_reason"]
        result_obj["action_type"] = resolved_result["action_type"]
        result_obj["outcome"] = resolved_result["outcome"]
        result_obj["conversation_result"] = resolved_result["conversation_result"]
        result_obj["narration"] = narration
        result_obj["final_narration"] = narration
        result_obj["summary"] = narration
        final_result["result"] = result_obj

    return final_result


def _reconcile_position_attack_range_gate(final_result: dict[str, Any], player_input: str) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    text = _safe_str(player_input).strip().lower()
    if "attack" not in text:
        return final_result

    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    combat_state = dict(_safe_dict(final_result.get("combat_state") or resolved_result.get("combat_state") or _find_active_combat_state_deep(final_result)))
    if not combat_state.get("active"):
        return final_result

    participants = _safe_dict(combat_state.get("participants"))
    player = _safe_dict(participants.get("player"))
    target = {}
    target_id = ""

    for actor_id, participant in participants.items():
        participant = _safe_dict(participant)
        if _safe_str(participant.get("side")).strip().lower() != "enemy":
            continue
        if _safe_int(participant.get("hp"), 0) <= 0:
            continue
        name = _safe_str(participant.get("name")).strip().lower()
        if name and name in text:
            target = participant
            target_id = str(actor_id)
            break

    if not target:
        for actor_id, participant in participants.items():
            participant = _safe_dict(participant)
            if _safe_str(participant.get("side")).strip().lower() == "enemy" and _safe_int(participant.get("hp"), 0) > 0:
                target = participant
                target_id = str(actor_id)
                break

    if not target:
        return final_result

    can_attack, reason = can_attack_target(player, target)
    if can_attack:
        return final_result

    position_result = {
        "changed": False,
        "actor_id": "player",
        "target_actor_id": target_id,
        "reason": reason,
        "blocked": True,
    }

    resolved_result["action_type"] = "attack"
    resolved_result["visible_interaction_reason"] = "combat_position_blocked"
    resolved_result["outcome"] = reason
    resolved_result["position_result"] = position_result
    resolved_result["combat_result"] = {
        "resolved": False,
        "action_type": "attack",
        "reason": reason,
        "target_id": target_id,
    }
    resolved_result["combat_state"] = combat_state

    final_result["resolved_result"] = resolved_result
    final_result["position_result"] = position_result
    final_result["combat_result"] = resolved_result["combat_result"]
    final_result["combat_state"] = combat_state
    final_result["visible_interaction_reason"] = "combat_position_blocked"
    final_result["action_type"] = "attack"
    final_result["outcome"] = reason
    final_result["narration"] = f"Result: {reason}"
    final_result["final_narration"] = f"Result: {reason}"
    final_result["summary"] = f"Result: {reason}"

    return final_result


def _reconcile_combat_world_consequences(final_result: dict[str, Any]) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    combat_state = dict(_safe_dict(final_result.get("combat_state") or resolved_result.get("combat_state")))
    combat_result = dict(_safe_dict(final_result.get("combat_result") or resolved_result.get("combat_result")))
    if not combat_state:
        combat_state = dict(_safe_dict(combat_result.get("combat_state")))

    exit_reason = _safe_str(combat_state.get("exit_reason") or combat_result.get("exit_reason")).strip()
    if not exit_reason:
        return final_result

    simulation_state = _safe_dict(_safe_dict(final_result.get("session")).get("simulation_state") or final_result.get("simulation_state"))
    if not simulation_state:
        return final_result

    simulation_state, world_event_result = emit_combat_world_consequence(
        simulation_state,
        combat_state,
        combat_result,
    )

    if not world_event_result.get("emitted") and world_event_result.get("reason") == "no_exit_reason":
        return final_result

    resolved_result["world_event_result"] = world_event_result
    final_result["world_event_result"] = world_event_result
    final_result["resolved_result"] = resolved_result

    if world_event_result.get("emitted"):
        kinds = [
            _safe_str(_safe_dict(event).get("kind"))
            for event in _safe_list(world_event_result.get("events"))
        ]
        if "combat_victory" in kinds:
            final_result["narration"] = "Result: combat_victory_world_event"
            final_result["final_narration"] = "Result: combat_victory_world_event"
            final_result["summary"] = "Result: combat_victory_world_event"

    session = _safe_dict(final_result.get("session"))
    session["simulation_state"] = simulation_state
    final_result["simulation_state"] = simulation_state
    final_result["session"] = session

    result_obj = dict(_safe_dict(final_result.get("result")) or _safe_parse_mapping_payload(final_result.get("result")))
    if result_obj:
        result_obj["world_event_result"] = world_event_result
        if final_result.get("narration"):
            result_obj["narration"] = final_result.get("narration")
            result_obj["final_narration"] = final_result.get("final_narration")
            result_obj["summary"] = final_result.get("summary")
        final_result["result"] = result_obj

    return final_result


def _ability_id_from_player_input(player_input: str) -> str:
    text = _safe_str(player_input).strip().lower()

    aliases = {
        "power attack": "ability:power_attack",
        "power_attack": "ability:power_attack",
        "quick strike": "ability:quick_strike",
        "quick_strike": "ability:quick_strike",
        "shield bash": "ability:shield_bash",
        "shield_bash": "ability:shield_bash",
        "bleeding slash": "ability:bleeding_slash",
        "bleeding_slash": "ability:bleeding_slash",
        "poison strike": "ability:poison_strike",
        "poison_strike": "ability:poison_strike",
        "guard break": "ability:guard_break",
        "guard_break": "ability:guard_break",
    }

    if text.startswith("__manual_use_ability__:"):
        return text.split(":", 1)[1].strip()

    for phrase, ability_id in aliases.items():
        if phrase in text:
            return ability_id

    if "unknown ability" in text:
        return "ability:unknown"

    return ""


def _player_input_requests_combat_ability(player_input: str) -> bool:
    return bool(_ability_id_from_player_input(player_input))


def _target_id_for_ability(runtime_state: dict[str, Any], player_input: str) -> str:
    combat_state = _safe_dict(_get_combat_state(runtime_state))
    participants = _safe_dict(combat_state.get("participants"))
    text = _safe_str(player_input).strip().lower()

    for actor_id, participant in participants.items():
        participant = _safe_dict(participant)
        if _safe_str(participant.get("side")).strip().lower() != "enemy":
            continue
        name = _safe_str(participant.get("name")).strip().lower()
        archetype_id = _safe_str(participant.get("archetype_id")).strip().lower().replace("enemy:", "").replace("_", " ")
        if name and name in text:
            return str(actor_id)
        if archetype_id and any(part and part in text for part in archetype_id.split()):
            return str(actor_id)

    for actor_id, participant in participants.items():
        participant = _safe_dict(participant)
        if _safe_str(participant.get("side")).strip().lower() == "enemy" and _safe_int(participant.get("hp"), 0) > 0:
            return str(actor_id)

    return ""


def _manual_encounter_preset_from_input(player_input: str) -> str:
    text = _safe_str(player_input).strip()
    marker = "__manual_start_encounter__"
    if not text.startswith(marker):
        return ""
    if ":" not in text:
        return "bandit_easy"
    return text.split(":", 1)[1].strip() or "bandit_easy"


def _repair_generated_encounter_player_turn(combat_state: dict[str, Any]) -> dict[str, Any]:
    combat_state = dict(_safe_dict(combat_state))
    if not combat_state.get("active"):
        return combat_state

    participants = _safe_dict(combat_state.get("participants"))
    if "player" not in participants:
        return combat_state

    current_actor_id = _safe_str(combat_state.get("current_actor_id")).strip()
    if current_actor_id:
        return combat_state

    initiative_order = list(_safe_list(combat_state.get("initiative_order")))
    player_index = 0

    for idx, row in enumerate(initiative_order):
        if _safe_str(_safe_dict(row).get("actor_id")).strip() == "player":
            player_index = idx
            break

    combat_state["current_actor_id"] = "player"
    combat_state["turn_index"] = player_index
    return combat_state


def _apply_manual_start_encounter_turn(
    session: dict[str, Any],
    player_input: str,
    *,
    tick: int = 0,
) -> dict[str, Any]:
    """Start a generated encounter from the outer apply_turn wrapper.

    J31-J33 manual encounter starts must run before the generic semantic action
    resolver, otherwise __manual_start_encounter__:bandit_easy becomes
    no_supported_semantic_action_detected.
    """
    session = dict(_safe_dict(session))
    simulation_state = _ensure_simulation_state(_safe_dict(session.get("simulation_state")))
    runtime_state = _safe_dict(session.get("runtime_state"))

    preset_id = _manual_encounter_preset_from_input(player_input)
    encounter = build_encounter_from_preset(simulation_state, preset_id)
    combat_state = _safe_dict(encounter.get("combat_state"))
    combat_state = _repair_generated_encounter_player_turn(combat_state)
    encounter_result = _safe_dict(encounter.get("encounter_result"))

    if combat_state.get("active"):
        runtime_state = _set_combat_state(runtime_state, combat_state)
        simulation_state["combat_state"] = combat_state

    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state

    visible_reason = (
        "combat_encounter_started"
        if combat_state.get("active")
        else "combat_encounter_not_started"
    )
    resolved_result = {
        "action_type": "start_encounter",
        "visible_interaction_reason": visible_reason,
        "outcome": "encounter_started" if combat_state.get("active") else "encounter_not_started",
        "encounter_result": encounter_result,
        "combat_state": combat_state,
        "conversation_result": {
            "triggered": False,
            "reason": "manual_encounter_start",
        },
    }

    result_obj = dict(resolved_result)

    return {
        "ok": True,
        "session": session,
        "simulation_state": simulation_state,
        "runtime_state": runtime_state,
        "result": result_obj,
        "resolved_result": resolved_result,
        "encounter_result": encounter_result,
        "combat_state": combat_state,
        "visible_interaction_reason": visible_reason,
        "action_type": "start_encounter",
        "outcome": resolved_result["outcome"],
        "conversation_result": resolved_result["conversation_result"],
        "narration": f"Result: {visible_reason}",
        "final_narration": f"Result: {visible_reason}",
        "summary": f"Result: {visible_reason}",
        "narration_context": {
            "player_input": player_input,
            "action_type": "start_encounter",
            "resolved_result": resolved_result,
            "simulation_state": simulation_state,
            "runtime_state": runtime_state,
            "combat_result": {},
            "npc_combat_result": {},
            "combat_state": combat_state,
            "grounded": {},
            "xp_result": {},
            "skill_xp_result": {},
            "level_up": [],
            "skill_level_ups": [],
            "settings": runtime_state.get("runtime_settings", {}),
            "conversation_threads": [],
        },
        "turn_id": _build_turn_id(runtime_state),
        "tick": tick,
    }



def _resolve_active_combat_utility_turn(
    *,
    runtime_state: dict[str, Any],
    semantic_action_record: dict[str, Any],
    player_input: str,
    simulation_state: dict[str, Any],
    action: dict[str, Any],
    player_actor_id: str,
    active_combat_utility_kind: str,
    current_tick: int,
    turn_id: str,
) -> dict[str, Any]:
    combat_state = _safe_dict(_get_combat_state(runtime_state))
    combat_state = normalize_combat_state(combat_state)

    after_action_state = _ensure_simulation_state(simulation_state)
    resolved_result: dict[str, Any] = {
        "action_type": active_combat_utility_kind,
        "outcome": active_combat_utility_kind,
        "visible_interaction_reason": f"combat_{active_combat_utility_kind}",
        "semantic_action_v2": semantic_action_record,
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
        grounded = _derive_grounded_scene_context(after_action_state, runtime_state, resolved_result)
        narration_context = {
            "player_input": player_input,
            "action_type": active_combat_utility_kind,
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

    if active_combat_utility_kind == "defend":
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

    elif active_combat_utility_kind == "flee":
        flee_resolution = resolve_flee(
            after_action_state,
            combat_state,
            _safe_str(player_actor_id),
            turn_id=_build_turn_id(runtime_state),
            tick=current_tick,
        )
        combat_result = flee_resolution.to_dict()
        after_action_state, combat_state = apply_flee_resolution(
            after_action_state,
            combat_state,
            combat_result,
        )
        resolved_result["outcome"] = "fled" if combat_result.get("success") else "flee_failed"
        resolved_result["combat_result"] = combat_result

    elif active_combat_utility_kind == "use_item":
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
                tick=current_tick,
            )
            combat_state = evaluate_combat_exit(after_action_state, combat_state)

    runtime_state = _set_combat_state(runtime_state, combat_state)

    if npc_combat_result:
        resolved_result["npc_combat_result"] = npc_combat_result

    grounded = _derive_grounded_scene_context(after_action_state, runtime_state, resolved_result)
    narration_context = {
        "player_input": player_input,
        "action_type": active_combat_utility_kind,
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

__all__ = ['_ability_id_from_player_input', '_apply_manual_start_encounter_turn', '_fallback_npc_backbone_narration', '_manual_encounter_preset_from_input', '_player_input_requests_combat_ability', '_reconcile_combat_world_consequences', '_reconcile_general_interaction_action', '_reconcile_narration_quality_memory_and_warnings', '_reconcile_npc_backbone_social_decision', '_reconcile_player_reposition_action', '_reconcile_position_attack_range_gate', '_repair_generated_encounter_player_turn', '_resolve_active_combat_utility_turn', '_target_id_for_ability']
