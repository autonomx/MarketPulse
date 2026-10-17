from __future__ import annotations

import logging

from app.apps.rpg.session.public_state_bridge import merge_authoritative_session_state

# RPG session runtime responsibility module.
from app.apps.rpg.session.turn_perf_trace import (
    record_elapsed_turn_stage as record_elapsed_turn_stage, record_turn_perf_trace as record_turn_perf_trace, record_turn_perf_trace_stack as record_turn_perf_trace_stack,
)
from app.apps.rpg.session.session_runtime_store import (
    _mirror_enemy_ai_combat_results as _mirror_enemy_ai_combat_results, _mirror_rescued_combat_utility_result as _mirror_rescued_combat_utility_result,
    _rescue_final_apply_turn_combat_utility_result as _rescue_final_apply_turn_combat_utility_result, load_runtime_session as load_runtime_session,
)
import copy
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_str as _safe_str,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _sync_session_if_companion_runtime_mutated as _sync_session_if_companion_runtime_mutated,
    _sync_session_simulation_state_for_early_return as _sync_session_simulation_state_for_early_return,
    _try_resolve_pending_companion_offer_at_turn_start as _try_resolve_pending_companion_offer_at_turn_start,
)
from app.apps.rpg.rules.party.companion_presence import (
    build_party_aware_turn_context as build_party_aware_turn_context, companion_presence_summary as companion_presence_summary,
    project_active_companions_into_presence as project_active_companions_into_presence,
)
from app.apps.rpg.rules.party.companion_memory import (
    companion_loyalty_projection as companion_loyalty_projection, companion_memory_summary as companion_memory_summary,
    record_companion_join_memory as record_companion_join_memory,
)
from app.apps.rpg.rules.party.companion_quests import (
    companion_quest_summary as companion_quest_summary, seed_companion_quest_from_arc as seed_companion_quest_from_arc,
)
from app.apps.rpg.rules.party.party_composition import (
    project_party_composition_effects as project_party_composition_effects,
)
from app.apps.rpg.rules.party.companion_turns import (
    maybe_build_direct_companion_turn_response as maybe_build_direct_companion_turn_response,
)
from app.apps.rpg.rules.party.companion_commands import (
    maybe_apply_companion_command as maybe_apply_companion_command,
)
from app.apps.rpg.rules.interactions.interaction_runtime import (
    resolve_general_interaction as resolve_general_interaction,
)
from app.apps.rpg.narration.combat_contract import (
    combat_contract_requires_llm as combat_contract_requires_llm,
)
from app.apps.rpg.session.visible_response_core import (
    _apply_combat_narration_if_needed as _apply_combat_narration_if_needed,
    _apply_visible_interaction_reason_to_resolved_result as _apply_visible_interaction_reason_to_resolved_result,
    _interaction_visible_result_reason as _interaction_visible_result_reason,
    _patch_visible_interaction_reason_into_payload_text as _patch_visible_interaction_reason_into_payload_text,
)
from app.apps.rpg.session.combat_turn_actions import (
    _apply_manual_start_encounter_turn as _apply_manual_start_encounter_turn, _manual_encounter_preset_from_input as _manual_encounter_preset_from_input,
    _reconcile_combat_world_consequences as _reconcile_combat_world_consequences, _reconcile_general_interaction_action as _reconcile_general_interaction_action,
    _reconcile_narration_quality_memory_and_warnings as _reconcile_narration_quality_memory_and_warnings,
    _reconcile_npc_backbone_social_decision as _reconcile_npc_backbone_social_decision, _reconcile_player_reposition_action as _reconcile_player_reposition_action,
    _reconcile_position_attack_range_gate as _reconcile_position_attack_range_gate,
)
from app.apps.rpg.session.combat_result_reconciliation import (
    _mirror_ability_results as _mirror_ability_results, _mirror_encounter_result as _mirror_encounter_result,
    _reconcile_combat_use_item_with_successful_consumable as _reconcile_combat_use_item_with_successful_consumable,
    _reconcile_combat_victory_rewards_and_loot as _reconcile_combat_victory_rewards_and_loot,
    _reconcile_manual_forced_generated_victory_attack as _reconcile_manual_forced_generated_victory_attack,
    _reconcile_player_combat_ability_action as _reconcile_player_combat_ability_action,
)
from app.apps.rpg.session.response_builder import (
    build_apply_turn_response as build_apply_turn_response,
)
from app.apps.rpg.session.companion_turn_enrichment import (
    _apply_post_action_companion_enrichment as _apply_post_action_companion_enrichment,
)
from app.apps.rpg.narration.campaign_journal_runtime import (
    advance_campaign_journal_for_turn as advance_campaign_journal_for_turn,
)
from app.apps.rpg.session.combat_action_reconciliation import (
    _attach_narration_quality_and_backbone_context as _attach_narration_quality_and_backbone_context,
    _reconcile_ability_cooldown_tick_for_manual_current_actor as _reconcile_ability_cooldown_tick_for_manual_current_actor,
    _reconcile_combat_recovery_action as _reconcile_combat_recovery_action,
    _reconcile_companion_command_conversation_suppression as _reconcile_companion_command_conversation_suppression,
    _reconcile_companion_turn_result as _reconcile_companion_turn_result, _reconcile_condition_tick_for_manual_current_actor as _reconcile_condition_tick_for_manual_current_actor,
    _reconcile_forced_combat_conditions as _reconcile_forced_combat_conditions, _reconcile_generated_attack_not_actor_turn as _reconcile_generated_attack_not_actor_turn,
    _reconcile_invalid_companion_command as _reconcile_invalid_companion_command,
)
from app.apps.rpg.session.deferred_narration_guard import (
    runtime_narration_follows_context,
    suppress_provider_runtime_narration as suppress_provider_runtime_narration,
)
from app.apps.rpg.session.narration_trace import (
    record_narration_trace as record_narration_trace, record_narration_trace_stack as record_narration_trace_stack,
)
from app.apps.rpg.session.runtime_provider import (
    get_runtime_llm_provider as get_runtime_llm_provider,
)
from app.apps.rpg.narration.response_generation.runtime_bridge import (
    build_runtime_narration_payload as build_runtime_narration_payload,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _apply_dialogue_state_update_from_narration as _apply_dialogue_state_update_from_narration,
)
from app.apps.rpg.session.visible_response_selection import (
    _select_final_visible_presentation as _select_final_visible_presentation,
)
from typing import (
    Any as Any, Dict as Dict,
)

logger = logging.getLogger(__name__)


def apply_turn(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.apps.rpg.session.turn_authoritative_guards import (
        _apply_turn_authoritative as _apply_turn_authoritative,
    )

    record_turn_perf_trace_stack(
        "runtime_apply_turn_enter",
        function="app.apps.rpg.session.player_turn_execution.apply_turn",
    )
    _apply_turn_started = __import__("time").perf_counter()
    session = load_runtime_session(session_id)
    if session is None:
        record_turn_perf_trace(
            "runtime_apply_turn_before_return",
            elapsed_seconds=round(__import__("time").perf_counter() - _apply_turn_started, 3),
            return_keys=sorted(list({"ok": False, "error": "session_not_found"}.keys()))[:80],
        )
        return {"ok": False, "error": "session_not_found"}

    simulation_state = copy.deepcopy(_safe_dict(session.get("simulation_state")))
    tick = int(_safe_dict(session.get("runtime_state")).get("tick", 0) or 0)
    companion_acceptance_precheck = _try_resolve_pending_companion_offer_at_turn_start(
        simulation_state,
        player_input=player_input,
        tick=tick,
    )
    party_aware_turn_context = build_party_aware_turn_context(
        simulation_state,
        player_input=player_input,
        tick=tick,
    )
    if companion_acceptance_precheck.get("resolved"):
        conversation_result = _safe_dict(
            companion_acceptance_precheck.get("conversation_result")
        )

        project_active_companions_into_presence(
            simulation_state,
            location_id=_safe_str(_safe_dict(simulation_state.get("player_state")).get("location_id"))
            or _safe_str(simulation_state.get("location_id")),
            tick=tick,
            reason="companion_acceptance_turn_start",
        )
        companion_presence = companion_presence_summary(simulation_state)
        party_aware_turn_context = build_party_aware_turn_context(
            simulation_state,
            player_input=player_input,
            tick=tick,
        )

        # AU-AV-AW Patch 3.1: record join memory and loyalty projection on acceptance.
        companion_memory_result: dict = {}
        companion_loyalty_result: dict = {}

        acceptance = _safe_dict(companion_acceptance_precheck.get("companion_acceptance_result"))
        accepted_npc_id = _safe_str(acceptance.get("npc_id"))

        if acceptance.get("accepted") and accepted_npc_id:
            companion_memory_result = record_companion_join_memory(
                simulation_state,
                npc_id=accepted_npc_id,
                tick=tick,
            )
            companion_loyalty_result = companion_loyalty_projection(
                simulation_state,
                npc_id=accepted_npc_id,
            )

        companion_quest_seed_result: dict = {}
        companion_quest_summary_result: dict = {}

        if acceptance.get("accepted") and accepted_npc_id:
            companion_quest_seed_result = seed_companion_quest_from_arc(
                simulation_state,
                npc_id=accepted_npc_id,
                tick=tick,
            )
            companion_quest_summary_result = companion_quest_summary(
                simulation_state,
                npc_id=accepted_npc_id,
            )

        companion_memory_summary_result = (
            companion_memory_summary(simulation_state, npc_id=accepted_npc_id)
            if accepted_npc_id
            else companion_memory_summary(simulation_state)
        )

        party_composition_result = project_party_composition_effects(simulation_state)

        resolved_result = {
            "ok": True,
            "action_type": "companion_acceptance",
            "semantic_action_type": "companion_acceptance",
            "semantic_family": "social",
            "summary": "The pending companion offer is resolved.",
            "conversation_result": copy.deepcopy(conversation_result),
            "companion_acceptance_result": copy.deepcopy(
                companion_acceptance_precheck.get("companion_acceptance_result")
            ),
            "companion_dialogue_result": copy.deepcopy(
                companion_acceptance_precheck.get("companion_dialogue_result")
            ),
            "party_state": copy.deepcopy(
                companion_acceptance_precheck.get("party_state")
            ),
            "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
            "companion_presence_summary": copy.deepcopy(companion_presence),
            "companion_presence_projection": copy.deepcopy(
                _safe_dict(simulation_state.get("companion_presence_projection"))
            ),
            "companion_memory_result": copy.deepcopy(companion_memory_result),
            "companion_loyalty_projection": copy.deepcopy(companion_loyalty_result),
            "companion_memory_summary": copy.deepcopy(companion_memory_summary_result),
            "companion_quest_seed_result": copy.deepcopy(companion_quest_seed_result),
            "companion_quest_summary": copy.deepcopy(companion_quest_summary_result),
            "party_composition_effects": copy.deepcopy(party_composition_result),
            "source": "deterministic_session_runtime",
        }

        turn_contract = {
            "ok": True,
            "action_type": "companion_acceptance",
            "semantic_action_type": "companion_acceptance",
            "semantic_family": "social",
            "resolved_result": copy.deepcopy(resolved_result),
            "conversation_result": copy.deepcopy(conversation_result),
            "simulation_state": simulation_state,
            "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
            "companion_presence_summary": copy.deepcopy(companion_presence),
            "companion_presence_projection": copy.deepcopy(
                _safe_dict(simulation_state.get("companion_presence_projection"))
            ),
            "source": "deterministic_session_runtime",
        }

        result: dict[str, Any] = {
            "ok": True,
            "result": {
                "ok": True,
                "resolved_result": copy.deepcopy(resolved_result),
                "conversation_result": copy.deepcopy(conversation_result),
                "simulation_state": simulation_state,
                "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
                "companion_presence_summary": copy.deepcopy(companion_presence),
                "companion_presence_projection": copy.deepcopy(
                    _safe_dict(simulation_state.get("companion_presence_projection"))
                ),
                "companion_memory_result": copy.deepcopy(companion_memory_result),
                "companion_loyalty_projection": copy.deepcopy(companion_loyalty_result),
                "companion_memory_summary": copy.deepcopy(companion_memory_summary_result),
                "companion_quest_seed_result": copy.deepcopy(companion_quest_seed_result),
                "companion_quest_summary": copy.deepcopy(companion_quest_summary_result),
                "party_composition_effects": copy.deepcopy(party_composition_result),
            },
            "turn_contract": turn_contract,
            "conversation_result": copy.deepcopy(conversation_result),
            "simulation_state": simulation_state,
            "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
            "companion_presence_summary": copy.deepcopy(companion_presence),
            "companion_presence_projection": copy.deepcopy(
                _safe_dict(simulation_state.get("companion_presence_projection"))
            ),
            "companion_memory_result": copy.deepcopy(companion_memory_result),
            "companion_loyalty_projection": copy.deepcopy(companion_loyalty_result),
            "companion_memory_summary": copy.deepcopy(companion_memory_summary_result),
            "companion_quest_seed_result": copy.deepcopy(companion_quest_seed_result),
            "companion_quest_summary": copy.deepcopy(companion_quest_summary_result),
            "party_composition_effects": copy.deepcopy(party_composition_result),
            "session": session,
        }
        session = _sync_session_simulation_state_for_early_return(
            session,
            simulation_state,
            reason="companion_acceptance_turn_start",
        )
        result["session"] = session

        # AO-AP-AQ Patch 4.3: project companions into presence after acceptance
        project_active_companions_into_presence(
            simulation_state,
            location_id=_safe_str(_safe_dict(simulation_state.get("player_state")).get("location_id"))
            or _safe_str(simulation_state.get("location_id")),
            tick=tick,
            reason="companion_acceptance_turn_start",
        )
        companion_presence = companion_presence_summary(simulation_state)
        party_aware_turn_context = build_party_aware_turn_context(
            simulation_state,
            player_input=player_input,
            tick=tick,
        )
        direct_companion_turn_result = maybe_build_direct_companion_turn_response(
            simulation_state,
            player_input=player_input,
            tick=tick,
        )

        resolved_result["party_aware_turn_context"] = copy.deepcopy(party_aware_turn_context)
        resolved_result["companion_presence_summary"] = copy.deepcopy(companion_presence)
        resolved_result["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(simulation_state.get("companion_presence_projection"))
        )
        resolved_result["direct_companion_turn_result"] = copy.deepcopy(direct_companion_turn_result)

        turn_contract["resolved_result"] = copy.deepcopy(resolved_result)
        turn_contract["party_aware_turn_context"] = copy.deepcopy(party_aware_turn_context)
        turn_contract["companion_presence_summary"] = copy.deepcopy(companion_presence)
        turn_contract["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(simulation_state.get("companion_presence_projection"))
        )

        result["result"]["party_aware_turn_context"] = copy.deepcopy(party_aware_turn_context)
        result["result"]["companion_presence_summary"] = copy.deepcopy(companion_presence)
        result["result"]["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(simulation_state.get("companion_presence_projection"))
        )
        result["result"]["direct_companion_turn_result"] = copy.deepcopy(direct_companion_turn_result)

        result["party_aware_turn_context"] = copy.deepcopy(party_aware_turn_context)
        result["companion_presence_summary"] = copy.deepcopy(companion_presence)
        result["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(simulation_state.get("companion_presence_projection"))
        )
        result["direct_companion_turn_result"] = copy.deepcopy(direct_companion_turn_result)
        companion_dialogue_result = _safe_dict(
            companion_acceptance_precheck.get("companion_dialogue_result")
        )
        companion_line = _safe_str(companion_dialogue_result.get("line"))
        npc_name = _safe_str(companion_dialogue_result.get("npc_name") or acceptance.get("name") or "Bran")
        if acceptance.get("accepted") and companion_line:
            companion_narration = f"{npc_name} joins your party and falls in beside you."
            companion_npc = {"speaker": npc_name, "line": companion_line}
            resolved_result["narration"] = companion_narration
            resolved_result["npc"] = copy.deepcopy(companion_npc)
            turn_contract["resolved_result"] = copy.deepcopy(resolved_result)
            result["turn_contract"] = turn_contract
            result["narration"] = companion_narration
            result["npc"] = copy.deepcopy(companion_npc)
            result["presentation_narration_selection"] = {
                "source": "companion_acceptance",
                "runtime_payload_source": "",
            }
            result["result"]["narration"] = companion_narration
            result["result"]["npc"] = copy.deepcopy(companion_npc)
            result["result"]["resolved_result"] = copy.deepcopy(resolved_result)
            result["result"]["presentation_narration_selection"] = copy.deepcopy(
                result["presentation_narration_selection"]
            )

        record_turn_perf_trace(
            "runtime_apply_turn_before_return",
            elapsed_seconds=round(__import__("time").perf_counter() - _apply_turn_started, 3),
            return_keys=sorted(list(result.keys()))[:80] if isinstance(result, dict) else [],
        )
        return result

    # AR-AS-AT: Companion command runtime (bounded, deterministic).
    # This must run before the authoritative turn so that commands like
    # "Bran, stay here." are treated as commands, not generic dialogue.
    companion_command_result = maybe_apply_companion_command(
        simulation_state,
        player_input=player_input,
        tick=tick,
    )

    if companion_command_result.get("recognized"):
        conversation_result = {
            "triggered": True,
            "reason": (
                "companion_command_applied"
                if companion_command_result.get("accepted")
                else "companion_command_rejected"
            ),
            "participation_mode": "companion_command",
            "companion_command_result": copy.deepcopy(companion_command_result),
            "npc_response_beat": copy.deepcopy(
                _safe_dict(companion_command_result.get("npc_response_beat"))
            ),
            "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
            "companion_presence_summary": copy.deepcopy(
                companion_presence_summary(simulation_state)
            ),
            "companion_presence_projection": copy.deepcopy(
                _safe_dict(simulation_state.get("companion_presence_projection"))
            ),
            "companion_relationship_drift_result": None,  # Not computed in this path
            "companion_quest_progress_result": None,  # Not computed in this path
            "companion_quest_summary": None,  # Not computed in this path
            "companion_memory_summary": None,  # Not computed in this path
            "source": "deterministic_companion_command_runtime",
        }

        resolved_result = {
            "ok": True,
            "action_type": "companion_command",
            "semantic_action_type": "companion_command",
            "semantic_family": "social",
            "summary": (
                "The companion command is applied."
                if companion_command_result.get("accepted")
                else "The companion command is rejected."
            ),
            "conversation_result": copy.deepcopy(conversation_result),
            "companion_command_result": copy.deepcopy(companion_command_result),
            "party_aware_turn_context": copy.deepcopy(party_aware_turn_context),
            "companion_presence_summary": copy.deepcopy(
                companion_presence_summary(simulation_state)
            ),
            "companion_presence_projection": copy.deepcopy(
                _safe_dict(simulation_state.get("companion_presence_projection"))
            ),
            "companion_relationship_drift_result": None,  # Not computed in this path
            "companion_quest_progress_result": None,  # Not computed in this path
            "companion_quest_summary": None,  # Not computed in this path
            "companion_memory_summary": None,  # Not computed in this path
            "source": "deterministic_companion_command_runtime",
        }

        turn_contract = {
            "ok": True,
            "action_type": "companion_command",
            "semantic_action_type": "companion_command",
            "semantic_family": "social",
            "resolved_result": copy.deepcopy(resolved_result),
            "conversation_result": copy.deepcopy(conversation_result),
            "simulation_state": simulation_state,
            "source": "deterministic_companion_command_runtime",
        }

        result = {
            "ok": True,
            "result": {
                "ok": True,
                "resolved_result": copy.deepcopy(resolved_result),
                "conversation_result": copy.deepcopy(conversation_result),
                "simulation_state": simulation_state,
                "companion_command_result": copy.deepcopy(companion_command_result),
                "companion_relationship_drift_result": None,  # Not computed in this path
                "companion_quest_progress_result": None,  # Not computed in this path
                "companion_quest_summary": None,  # Not computed in this path
                "companion_memory_summary": None,  # Not computed in this path
            },
            "turn_contract": turn_contract,
            "conversation_result": copy.deepcopy(conversation_result),
            "companion_command_result": copy.deepcopy(companion_command_result),
            "simulation_state": simulation_state,
            "companion_relationship_drift_result": None,  # Not computed in this path
            "companion_quest_progress_result": None,  # Not computed in this path
            "companion_quest_summary": None,  # Not computed in this path
            "companion_memory_summary": None,  # Not computed in this path
            "session": session,
        }

        session = _sync_session_if_companion_runtime_mutated(
            session,
            simulation_state,
            reason="companion_command",
            companion_relationship_drift_result=None,  # Not computed in this path
            companion_quest_progress_result=None,  # Not computed in this path
            companion_memory_result=None,  # Not computed in this path
            companion_command_result=companion_command_result,
        )
        result["session"] = session

        record_turn_perf_trace(
            "runtime_apply_turn_before_return",
            elapsed_seconds=round(__import__("time").perf_counter() - _apply_turn_started, 3),
            return_keys=sorted(list(result.keys()))[:80] if isinstance(result, dict) else [],
        )
        return result

    record_turn_perf_trace_stack("runtime_checkpoint_03_before_core_turn")

    # Everything between checkpoint_03 and checkpoint_04 is currently the
    # remaining live-blocking bottleneck. Keep these stages broad but tied to
    # concrete subsystem calls/assignments so the next artifact names the owner.

    _general_interaction_started = __import__("time").perf_counter()
    record_turn_perf_trace_stack("runtime_core_before_pre_authoritative_general_interaction")
    general_interaction_result = resolve_general_interaction(
        simulation_state,
        player_input=player_input,
        actor_id="player",
        tick=tick,
    )
    record_elapsed_turn_stage(
        "pre_authoritative_general_interaction",
        _general_interaction_started,
        result_keys=sorted(list(_safe_dict(general_interaction_result).keys()))[:80],
        llm_called=bool(_safe_dict(general_interaction_result).get("llm_called")),
        combat_narration_attempted=bool(_safe_dict(general_interaction_result).get("combat_narration_attempted")),
    )

    inventory_result = _safe_dict(general_interaction_result.get("inventory_result"))

    companion_item_acceptance_result = _safe_dict(
        general_interaction_result.get("companion_item_acceptance_result")
    )
    companion_auto_equip_result = _safe_dict(
        general_interaction_result.get("companion_auto_equip_result")
    )

    container_result = _safe_dict(general_interaction_result.get("container_result"))
    repair_result = _safe_dict(general_interaction_result.get("repair_result"))
    consumable_result = _safe_dict(general_interaction_result.get("consumable_result"))
    equipment_stats = _safe_dict(general_interaction_result.get("equipment_stats"))
    crafting_result = _safe_dict(general_interaction_result.get("crafting_result"))
    _merchant_started = __import__("time").perf_counter()
    merchant_result = _safe_dict(general_interaction_result.get("merchant_result"))
    record_elapsed_turn_stage("merchant", _merchant_started)
    loot_result = _safe_dict(general_interaction_result.get("loot_result"))

    combat_result = _safe_dict(general_interaction_result.get("combat_result"))
    combat_state = _safe_dict(
        general_interaction_result.get("combat_state")
        or simulation_state.get("combat_state")
    )

    # J13-J15: Combat narration must run in the active service-scenario path,
    # before fallback "Result: ..." narration is finalized.
    if combat_contract_requires_llm(combat_result):
        general_interaction_result = _apply_combat_narration_if_needed(
            general_interaction_result,
            combat_result=combat_result,
            combat_state=combat_state,
        )
    _combat_started = __import__("time").perf_counter()
    combat_result = _safe_dict(general_interaction_result.get("combat_result"))
    record_elapsed_turn_stage("combat", _combat_started)
    record_turn_perf_trace("runtime_checkpoint_before_companion_systems")
    combat_state = _safe_dict(
        general_interaction_result.get("combat_state")
        or simulation_state.get("combat_state")
    )
    combat_narration_contract = _safe_dict(
        general_interaction_result.get("combat_narration_contract")
    )
    combat_narration_validation = _safe_dict(
        general_interaction_result.get("combat_narration_validation")
    )
    combat_narration_payload = _safe_dict(
        general_interaction_result.get("combat_narration_payload")
    )
    combat_llm_called = bool(general_interaction_result.get("llm_called"))
    combat_llm_error = _safe_str(
        general_interaction_result.get("combat_narration_error")
    )
    combat_narration_attempted = bool(
        general_interaction_result.get("combat_narration_attempted")
    )
    combat_loot_result = _safe_dict(combat_result.get("loot_result"))
    combat_ammo_result = _safe_dict(combat_result.get("ammo_result"))

    manual_encounter_preset = _manual_encounter_preset_from_input(player_input)
    if manual_encounter_preset:
        final_result = _apply_manual_start_encounter_turn(
            session,
            player_input,
            tick=tick,
        )
        final_result = _mirror_encounter_result(final_result)
        try:
            from app.apps.rpg.session.service import save_session
            save_session(_safe_dict(final_result.get("session")))
        except Exception:
            logger.debug("suppressed error in %s", "apply_turn", exc_info=True)
        return final_result

    _stage_started = __import__("time").perf_counter()
    record_turn_perf_trace_stack("runtime_core_before_apply_turn_authoritative")
    # The runtime narration stage below calls the provider for this turn, so the
    # authoritative chain need not narrate it too (WP-8.6: one narration call).
    runtime_narration_will_follow = (
        not suppress_provider_runtime_narration() and get_runtime_llm_provider() is not None
    )
    with runtime_narration_follows_context(runtime_narration_will_follow):
        authoritative_result = _apply_turn_authoritative(
            session_id,
            player_input,
            action=action,
            performance_override=performance_override,
        )
    record_elapsed_turn_stage(
        "apply_turn_authoritative",
        _stage_started,
        ok=bool(_safe_dict(authoritative_result).get("ok")),
        result_keys=sorted(list(_safe_dict(authoritative_result).keys()))[:80],
    )
    if not authoritative_result.get("ok"):
        return authoritative_result

    session = merge_authoritative_session_state(session, authoritative_result)

    _stage_started = __import__("time").perf_counter()
    final_result = build_apply_turn_response(authoritative_result)
    record_elapsed_turn_stage(
        "build_apply_turn_response",
        _stage_started,
        result_keys=sorted(list(_safe_dict(final_result).keys()))[:80],
    )

    _stage_started = __import__("time").perf_counter()
    final_result = _rescue_final_apply_turn_combat_utility_result(
        final_result,
        player_input,
    )
    record_elapsed_turn_stage("rescue_final_apply_turn_combat_utility", _stage_started)

    # Defaults for post-action companion runtime.
    # Some authoritative paths, including J19-J21 combat utility rescue paths,
    # may return before a full session-shaped payload exists. Keep the normal
    # companion enrichment code safe by initializing these before the optional
    # _post_action_sim block.
    session, final_result = _apply_post_action_companion_enrichment(
        session=session,
        final_result=final_result,
        authoritative_result=authoritative_result,
        tick=tick,
        player_input=player_input,
        general_interaction_result=general_interaction_result,
        inventory_result=inventory_result,
        container_result=container_result,
        repair_result=repair_result,
        consumable_result=consumable_result,
        equipment_stats=equipment_stats,
        crafting_result=crafting_result,
        merchant_result=merchant_result,
        loot_result=loot_result,
        companion_item_acceptance_result=companion_item_acceptance_result,
        companion_auto_equip_result=companion_auto_equip_result,
        combat_result=combat_result,
        combat_state=combat_state,
        combat_llm_called=combat_llm_called,
        combat_llm_error=combat_llm_error,
        combat_narration_contract=combat_narration_contract,
        combat_narration_validation=combat_narration_validation,
        combat_narration_payload=combat_narration_payload,
        combat_loot_result=combat_loot_result,
        combat_ammo_result=combat_ammo_result,
    )

    if (
        inventory_result.get("changed_state") is True
        or container_result.get("changed_state") is True
        or repair_result.get("changed_state") is True
        or consumable_result.get("changed_state") is True
        or crafting_result.get("changed_state") is True
        or merchant_result.get("changed_state") is True
        or loot_result.get("changed_state") is True
        or companion_auto_equip_result.get("changed_state") is True
        or combat_result.get("changed_state") is True
        or combat_loot_result.get("changed_state") is True
    ):
        session = _sync_session_simulation_state_for_early_return(
            session,
            simulation_state,
            reason="general_item_interaction",
        )

    final_result["session"] = session

    # Advance campaign journal after the deterministic turn result exists.
    # Important: not every apply_turn path assigns a local named
    # `turn_contract`, so never reference it directly here. Resolve it from
    # returned/session structures first, with locals() as a safe last fallback.
    journal_turn_contract = (
        _safe_dict(final_result.get("turn_contract"))
        or _safe_dict(_safe_dict(final_result.get("result")).get("turn_contract"))
        or _safe_dict(_safe_dict(session.get("last_turn")).get("turn_contract"))
        or _safe_dict(locals().get("turn_contract"))
        or {}
    )
    journal_player_input = (
        _safe_str(locals().get("player_input"))
        or _safe_str(final_result.get("player_input"))
        or _safe_str(journal_turn_contract.get("player_input"))
        or _safe_str(journal_turn_contract.get("action"))
    )
    journal_narration_payload = _safe_dict(locals().get("narration_payload"))
    runtime_state = session.setdefault("runtime_state", {})
    journal_state = _safe_dict(session.get("state"))
    journal_metadata = _safe_dict(journal_state.get("metadata"))
    journal_identity = _safe_dict(journal_state.get("character_identity"))
    journal_setup = _safe_dict(session.get("setup_payload"))
    journal_genesis = _safe_dict(journal_setup.get("genesis"))
    journal_drivers = _safe_dict(journal_genesis.get("drivers"))
    journal_environment = _safe_dict(
        _safe_dict(journal_state.get("world")).get("environment")
        or journal_state.get("environment_snapshot")
    )
    turn_index = int(
        journal_turn_contract.get("turn_index")
        or final_result.get("turn_index")
        or session.get("turn_index")
        or len(session.get("turns", []))
        or 1
    )
    session["runtime_state"] = advance_campaign_journal_for_turn(
        runtime_state=runtime_state,
        turn_index=turn_index,
        player_input=journal_player_input,
        turn_contract=journal_turn_contract,
        turn_result={
            "narration_payload": journal_narration_payload,
            "player_action": journal_player_input,
        },
        calendar_snapshot=journal_environment,
        player_context={
            "personality_profile": _safe_dict(
                journal_state.get("player_personality_profile")
                or runtime_state.get("player_personality_profile")
            ),
            "metadata": journal_metadata,
            "character_identity": journal_identity,
            "drivers": journal_drivers,
            "genre": _safe_str(
                journal_setup.get("genre")
                or journal_metadata.get("genre")
                or journal_identity.get("genre")
            ),
            "background": _safe_str(
                journal_identity.get("background")
                or journal_setup.get("background")
            ),
        },
    )

    visible_interaction_reason = _interaction_visible_result_reason(general_interaction_result)
    if visible_interaction_reason:
        final_result["visible_interaction_reason"] = visible_interaction_reason
        result = _patch_visible_interaction_reason_into_payload_text(
            final_result,
            visible_reason=visible_interaction_reason,
        )

        nested = _safe_dict(result.get("result"))
        if nested:
            nested["visible_interaction_reason"] = visible_interaction_reason
            nested = _patch_visible_interaction_reason_into_payload_text(
                nested,
                visible_reason=visible_interaction_reason,
            )

            nested_resolved = _safe_dict(
                nested.get("resolved_result")
                or nested.get("resolved_action")
            )
            if nested_resolved:
                nested_resolved = _apply_visible_interaction_reason_to_resolved_result(
                    nested_resolved,
                    general_interaction_result=general_interaction_result,
                )
                nested_resolved = _patch_visible_interaction_reason_into_payload_text(
                    nested_resolved,
                    visible_reason=visible_interaction_reason,
                )
                if "resolved_result" in nested:
                    nested["resolved_result"] = nested_resolved
                else:
                    nested["resolved_action"] = nested_resolved

            result["result"] = nested

        final_result = result

    final_result = _mirror_rescued_combat_utility_result(final_result)
    final_result = _reconcile_combat_use_item_with_successful_consumable(final_result)
    final_result = _reconcile_manual_forced_generated_victory_attack(
        final_result,
        player_input,
    )
    final_result = _reconcile_combat_victory_rewards_and_loot(final_result)
    final_result = _reconcile_combat_world_consequences(final_result)
    final_result = _reconcile_combat_recovery_action(final_result, player_input)
    final_result = _reconcile_condition_tick_for_manual_current_actor(final_result, player_input)
    final_result = _reconcile_forced_combat_conditions(final_result)
    final_result = _mirror_enemy_ai_combat_results(final_result)
    final_result = _reconcile_position_attack_range_gate(final_result, player_input)
    final_result = _reconcile_generated_attack_not_actor_turn(final_result, player_input)
    final_result = _mirror_encounter_result(final_result)
    final_result = _reconcile_ability_cooldown_tick_for_manual_current_actor(final_result, player_input)
    final_result = _reconcile_player_combat_ability_action(final_result, player_input)
    final_result = _mirror_ability_results(final_result)
    final_result = _reconcile_companion_turn_result(final_result, player_input)
    final_result = _reconcile_invalid_companion_command(final_result, player_input)
    final_result = _reconcile_player_reposition_action(final_result, player_input)
    final_result = _reconcile_companion_command_conversation_suppression(final_result, player_input)
    final_result = _reconcile_general_interaction_action(final_result, player_input)
    final_result = _reconcile_npc_backbone_social_decision(final_result, player_input)
    final_result = _attach_narration_quality_and_backbone_context(final_result, player_input)
    final_result = _reconcile_narration_quality_memory_and_warnings(final_result)
    turn_contract = final_result.get("turn_contract") or final_result.get("turnContract") or {}
    simulation_state = (
        final_result.get("simulation_state")
        or final_result.get("state")
        or final_result.get("session", {}).get("simulation_state")
        or session.get("simulation_state")
        or {}
    )

    runtime_state = (
        _safe_dict(_safe_dict(final_result.get("session")).get("runtime_state"))
        or _safe_dict(session.get("runtime_state"))
        or {}
    )
    setup_metadata = _safe_dict(_safe_dict(session.get("setup_payload")).get("metadata"))
    performance_settings = _safe_dict(runtime_state.get("performance"))
    prior_visible_narration = _safe_str(
        final_result.get("narration")
        or _safe_dict(final_result.get("result")).get("narration")
        or final_result.get("final_narration")
        or _safe_dict(final_result.get("result")).get("final_narration")
    )
    prior_visible_npc = _safe_dict(
        final_result.get("npc")
        or _safe_dict(final_result.get("result")).get("npc")
    )
    prior_visible_llm_called = bool(
        final_result.get("llm_called")
        or _safe_dict(final_result.get("result")).get("llm_called")
    )

    defer_runtime_narration = bool(
        suppress_provider_runtime_narration()
        or performance_settings.get("enable_live_narration_llm") is False
    )
    if defer_runtime_narration:
        runtime_provider = None
    else:
        suppressed = suppress_provider_runtime_narration()
        record_narration_trace_stack(
            "get_runtime_llm_provider_called",
            suppressed=suppressed,
        )
        if suppressed:
            record_narration_trace("get_runtime_llm_provider_return_none_due_to_suppression")
            runtime_provider = None
        else:
            runtime_provider = get_runtime_llm_provider()
    record_turn_perf_trace("runtime_checkpoint_04_after_core_turn")
    record_turn_perf_trace_stack("runtime_checkpoint_05_before_narration")
    _defer_trace_value = defer_runtime_narration
    record_narration_trace_stack(
        "before_build_runtime_narration_payload",
        defer_runtime_narration=_defer_trace_value,
        provider_will_be_none=_defer_trace_value,
    )
    narration_payload = build_runtime_narration_payload(
        provider=None if _defer_trace_value else runtime_provider,
        player_action=player_input,
        simulation_state=simulation_state,
        turn_contract=turn_contract,
        prefer_provider=not _defer_trace_value,
    )
    _apply_dialogue_state_update_from_narration(simulation_state, runtime_state, narration_payload)
    record_narration_trace(
        "after_build_runtime_narration_payload",
        source=(narration_payload.get("source") if isinstance(narration_payload, dict) else ""),
        defer_runtime_narration=_defer_trace_value,
    )
    record_turn_perf_trace("runtime_checkpoint_06_after_narration")
    if _defer_trace_value and isinstance(narration_payload, dict):
        narration_payload["source"] = "deferred_runtime_narration_pending"
        narration_payload["deferred"] = True
        narration_payload["narration_status"] = "pending"
        narration_payload["narration"] = narration_payload.get("narration") or "Narration is being prepared..."
    selected_presentation = _select_final_visible_presentation(
        final_result,
        runtime_narration_payload=narration_payload,
        prior_narration=prior_visible_narration,
        prior_npc=prior_visible_npc,
        prior_llm_called=prior_visible_llm_called,
    )
    final_result["narration_payload"] = narration_payload
    final_result["structured_narration"] = narration_payload
    final_result["presentation_narration_selection"] = {
        "source": selected_presentation.get("source"),
        "runtime_payload_source": selected_presentation.get("runtime_payload_source"),
    }
    final_result["npc"] = selected_presentation.get("npc") or {}
    if selected_presentation.get("narration"):
        final_result["narration"] = selected_presentation["narration"]
    final_result["llm_called"] = bool(selected_presentation.get("llm_called"))
    nested_result = _safe_dict(final_result.get("result"))
    if nested_result:
        nested_result["presentation_narration_selection"] = copy.deepcopy(
            final_result["presentation_narration_selection"]
        )
        nested_result["npc"] = copy.deepcopy(final_result["npc"])
        if final_result.get("narration"):
            nested_result["narration"] = final_result["narration"]
        nested_result["llm_called"] = final_result["llm_called"]
        final_result["result"] = nested_result
    record_turn_perf_trace(
        "runtime_apply_turn_before_return",
        elapsed_seconds=round(__import__("time").perf_counter() - _apply_turn_started, 3),
        return_keys=sorted(list(final_result.keys()))[:80] if isinstance(final_result, dict) else [],
    )
    return final_result

__all__ = []