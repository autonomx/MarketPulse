from __future__ import annotations

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_str as _safe_str,
)
from app.apps.rpg.rules.party.companion_presence import (
    build_party_aware_turn_context as build_party_aware_turn_context, companion_presence_summary as companion_presence_summary,
    project_active_companions_into_presence as project_active_companions_into_presence,
)
from app.apps.rpg.session.turn_perf_trace import (
    record_elapsed_turn_stage as record_elapsed_turn_stage, record_turn_perf_trace as record_turn_perf_trace,
)
from app.apps.rpg.rules.party.companion_memory import (
    companion_memory_summary as companion_memory_summary, maybe_apply_companion_relationship_drift_from_player_input as maybe_apply_companion_relationship_drift_from_player_input,
)
from app.apps.rpg.rules.party.companion_quests import (
    companion_quest_summary as companion_quest_summary, maybe_progress_companion_quest_from_player_input as maybe_progress_companion_quest_from_player_input,
)
from app.apps.rpg.rules.party.companion_turns import (
    maybe_build_direct_companion_turn_response as maybe_build_direct_companion_turn_response,
)
from app.apps.rpg.rules.party.party_composition import (
    project_party_composition_effects as project_party_composition_effects,
)
import copy
from app.apps.rpg.session.companion_turn_runtime import (
    _active_companion_profiles_summary as _active_companion_profiles_summary, _sync_session_if_companion_runtime_mutated as _sync_session_if_companion_runtime_mutated,
)
from app.apps.rpg.genesis.profiles.character_cards import (
    list_character_cards_for_simulation_state as list_character_cards_for_simulation_state,
)
from app.apps.rpg.session.visible_response_core import (
    _apply_visible_interaction_reason_to_resolved_result as _apply_visible_interaction_reason_to_resolved_result,
    _interaction_visible_result_reason as _interaction_visible_result_reason,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)


def _apply_post_action_companion_enrichment(
    *,
    session: dict[str, Any],
    final_result: dict[str, Any],
    authoritative_result: dict[str, Any],
    tick: int,
    player_input: str,
    general_interaction_result: dict[str, Any],
    inventory_result: dict[str, Any],
    container_result: dict[str, Any],
    repair_result: dict[str, Any],
    consumable_result: dict[str, Any],
    equipment_stats: dict[str, Any],
    crafting_result: dict[str, Any],
    merchant_result: dict[str, Any],
    loot_result: dict[str, Any],
    companion_item_acceptance_result: dict[str, Any],
    companion_auto_equip_result: dict[str, Any],
    combat_result: dict[str, Any],
    combat_state: dict[str, Any],
    combat_llm_called: bool,
    combat_llm_error: str,
    combat_narration_contract: dict[str, Any],
    combat_narration_validation: dict[str, Any],
    combat_narration_payload: dict[str, Any],
    combat_loot_result: dict[str, Any],
    combat_ammo_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _post_action_sim: dict[str, Any] = {}
    _party_aware_ctx: dict[str, Any] = {}
    _companion_presence: dict[str, Any] = {}
    _direct_companion: dict[str, Any] = {}
    _companion_drift: dict[str, Any] = {
        "applied": False,
        "reason": "no_post_action_simulation_state",
        "results": [],
        "source": "deterministic_companion_memory_runtime",
    }
    _companion_mem_summary: dict[str, Any] = {
        "by_npc": {},
        "source": "deterministic_companion_memory_runtime",
    }
    _companion_quest_progress: dict[str, Any] = {
        "progressed": False,
        "reason": "no_post_action_simulation_state",
        "source": "deterministic_companion_quest_runtime",
    }
    _companion_quest_sum: dict[str, Any] = {
        "quests": [],
        "source": "deterministic_companion_quest_runtime",
    }
    _party_composition: dict[str, Any] = {}
    _nps: Any = []
    _ccs: Any = []

    # AO-AP-AQ Patch 4.2 + 6: post-action companion presence projection
    _post_action_sim = _safe_dict(
        _safe_dict(final_result.get("session")).get("simulation_state")
    ) or _safe_dict(
        _safe_dict(authoritative_result.get("session")).get("simulation_state")
    )
    if _post_action_sim:
        _post_loc = (
            _safe_str(_safe_dict(_post_action_sim.get("player_state")).get("location_id"))
            or _safe_str(_post_action_sim.get("location_id"))
        )
        project_active_companions_into_presence(
            _post_action_sim,
            location_id=_post_loc,
            tick=tick,
            reason="apply_turn_post_action",
        )
        _stage_started = __import__("time").perf_counter()
        project_active_companions_into_presence(
            _post_action_sim,
            location_id=_post_loc,
            tick=tick,
            reason="apply_turn_post_action",
        )
        record_elapsed_turn_stage("companion_presence_projection", _stage_started)

        _stage_started = __import__("time").perf_counter()
        _companion_presence = companion_presence_summary(_post_action_sim)
        record_elapsed_turn_stage("companion_presence_summary", _stage_started)

        _party_aware_ctx = build_party_aware_turn_context(
            _post_action_sim,
            player_input=player_input,
            tick=tick,
        )
        # AU-AV-AW Patch 3.2: apply personality-aware relationship drift on normal party turns.
        _stage_started = __import__("time").perf_counter()
        _companion_drift = maybe_apply_companion_relationship_drift_from_player_input(
            _post_action_sim,
            player_input=player_input,
            tick=tick,
        )
        record_elapsed_turn_stage("companion_relationship_drift", _stage_started)

        _stage_started = __import__("time").perf_counter()
        _companion_mem_summary = companion_memory_summary(_post_action_sim)
        record_elapsed_turn_stage("companion_memory_summary", _stage_started)

        # AX-AY-AZ Patch 3: progress companion quests from player input.
        _stage_started = __import__("time").perf_counter()
        _companion_quest_progress = maybe_progress_companion_quest_from_player_input(
            _post_action_sim,
            player_input=player_input,
            tick=tick,
        )
        record_elapsed_turn_stage("companion_quest_progress", _stage_started)

        _stage_started = __import__("time").perf_counter()
        _companion_quest_sum = companion_quest_summary(_post_action_sim)
        record_elapsed_turn_stage("companion_quest_summary", _stage_started)

        record_turn_perf_trace("runtime_checkpoint_after_companion_systems")

        _direct_companion = maybe_build_direct_companion_turn_response(
            _post_action_sim,
            player_input=player_input,
            tick=tick,
        )

        # AX-AY-AZ Patch 5: re-project companion presence after quest stage changes.
        project_active_companions_into_presence(
            _post_action_sim,
            location_id=_safe_str(_safe_dict(_post_action_sim.get("player_state")).get("location_id"))
            or _safe_str(_post_action_sim.get("location_id")),
            tick=tick,
            reason="companion_quest_progress",
        )
        _companion_presence = companion_presence_summary(_post_action_sim)
        _party_composition = project_party_composition_effects(_post_action_sim)

        final_result["party_aware_turn_context"] = copy.deepcopy(_party_aware_ctx)
        final_result["companion_presence_summary"] = copy.deepcopy(_companion_presence)
        final_result["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(_post_action_sim.get("companion_presence_projection"))
        )
        final_result["direct_companion_turn_result"] = copy.deepcopy(_direct_companion)
        final_result["companion_relationship_drift_result"] = copy.deepcopy(_companion_drift)
        final_result["companion_memory_summary"] = copy.deepcopy(_companion_mem_summary)
        final_result["companion_quest_progress_result"] = copy.deepcopy(_companion_quest_progress)
        final_result["companion_quest_summary"] = copy.deepcopy(_companion_quest_sum)
        final_result["party_composition_effects"] = copy.deepcopy(_party_composition)

        _npc_profile_started = __import__("time").perf_counter()
        _nps = copy.deepcopy(_active_companion_profiles_summary(_post_action_sim))
        record_elapsed_turn_stage("npc_profile_summary", _npc_profile_started)
        _ccs = copy.deepcopy(list_character_cards_for_simulation_state(_post_action_sim))
        final_result["npc_profile_summary"] = _nps
        final_result["character_cards_summary"] = _ccs
        _semantic_action_started = __import__("time").perf_counter()
        final_result["semantic_action_v2"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("semantic_action_v2"))
        )
        record_elapsed_turn_stage("semantic_action", _semantic_action_started)
        _interaction_started = __import__("time").perf_counter()
        final_result["interaction_result"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("interaction_result"))
        )
        record_elapsed_turn_stage("interaction", _interaction_started)
        final_result["general_interaction_result"] = copy.deepcopy(general_interaction_result)
        final_result["inventory_result"] = copy.deepcopy(inventory_result)
        final_result["container_result"] = copy.deepcopy(container_result)
        final_result["repair_result"] = copy.deepcopy(repair_result)
        final_result["consumable_result"] = copy.deepcopy(consumable_result)
        final_result["equipment_stats"] = copy.deepcopy(equipment_stats)
        final_result["crafting_result"] = copy.deepcopy(crafting_result)
        final_result["merchant_result"] = copy.deepcopy(merchant_result)
        final_result["loot_result"] = copy.deepcopy(loot_result)
        final_result["companion_item_acceptance_result"] = copy.deepcopy(companion_item_acceptance_result)
        final_result["companion_auto_equip_result"] = copy.deepcopy(companion_auto_equip_result)
        final_result["combat_result"] = copy.deepcopy(combat_result)
        final_result["combat_state"] = copy.deepcopy(combat_state)
        final_result["llm_called"] = combat_llm_called
        final_result["llm_purpose"] = "combat_narration" if combat_llm_called or combat_llm_error else ""
        final_result["combat_narration_contract"] = copy.deepcopy(combat_narration_contract)
        final_result["combat_narration_validation"] = copy.deepcopy(combat_narration_validation)
        final_result["combat_narration_payload"] = copy.deepcopy(combat_narration_payload)
        final_result["combat_narration_error"] = combat_llm_error
        final_result["combat_loot_result"] = copy.deepcopy(combat_loot_result)
        final_result["combat_ammo_result"] = copy.deepcopy(combat_ammo_result)
        _visible_interaction_started = __import__("time").perf_counter()
        final_result["visible_interaction_reason"] = _interaction_visible_result_reason(general_interaction_result)
        record_elapsed_turn_stage("visible_interaction_reason", _visible_interaction_started)

        _nested = _safe_dict(final_result.get("result"))
        _nested["party_aware_turn_context"] = copy.deepcopy(_party_aware_ctx)
        _nested["companion_presence_summary"] = copy.deepcopy(_companion_presence)
        _nested["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(_post_action_sim.get("companion_presence_projection"))
        )
        _nested["direct_companion_turn_result"] = copy.deepcopy(_direct_companion)
        _nested["companion_relationship_drift_result"] = copy.deepcopy(_companion_drift)
        _nested["companion_memory_summary"] = copy.deepcopy(_companion_mem_summary)
        _nested["companion_quest_progress_result"] = copy.deepcopy(_companion_quest_progress)
        _nested["companion_quest_summary"] = copy.deepcopy(_companion_quest_sum)
        _nested["party_composition_effects"] = copy.deepcopy(_party_composition)
        _nested["npc_profile_summary"] = _nps
        _nested["character_cards_summary"] = _ccs
        _nested["semantic_action_v2"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("semantic_action_v2"))
        )
        _nested["interaction_result"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("interaction_result"))
        )
        _nested["general_interaction_result"] = copy.deepcopy(general_interaction_result)
        _nested["inventory_result"] = copy.deepcopy(inventory_result)
        _nested["container_result"] = copy.deepcopy(container_result)
        _nested["repair_result"] = copy.deepcopy(repair_result)
        _nested["consumable_result"] = copy.deepcopy(consumable_result)
        _nested["equipment_stats"] = copy.deepcopy(equipment_stats)
        _nested["crafting_result"] = copy.deepcopy(crafting_result)
        _nested["merchant_result"] = copy.deepcopy(merchant_result)
        _nested["loot_result"] = copy.deepcopy(loot_result)
        _nested["companion_item_acceptance_result"] = copy.deepcopy(companion_item_acceptance_result)
        _nested["companion_auto_equip_result"] = copy.deepcopy(companion_auto_equip_result)
        _nested["combat_result"] = copy.deepcopy(combat_result)
        _nested["combat_state"] = copy.deepcopy(combat_state)
        _nested["llm_called"] = combat_llm_called
        _nested["llm_purpose"] = "combat_narration" if combat_llm_called or combat_llm_error else ""
        _nested["combat_narration_contract"] = copy.deepcopy(combat_narration_contract)
        _nested["combat_narration_validation"] = copy.deepcopy(combat_narration_validation)
        _nested["combat_narration_payload"] = copy.deepcopy(combat_narration_payload)
        _nested["combat_narration_error"] = combat_llm_error
        _nested["combat_loot_result"] = copy.deepcopy(combat_loot_result)
        _nested["combat_ammo_result"] = copy.deepcopy(combat_ammo_result)
        _nested["combat_narration_contract"] = copy.deepcopy(combat_narration_contract)
        _nested["combat_narration_validation"] = copy.deepcopy(combat_narration_validation)
        final_result["result"] = _nested

        _tc = _safe_dict(final_result.get("turn_contract"))
        _rr = _safe_dict(_tc.get("resolved_result"))
        _rr["party_aware_turn_context"] = copy.deepcopy(_party_aware_ctx)
        _rr["companion_presence_summary"] = copy.deepcopy(_companion_presence)
        _rr["companion_presence_projection"] = copy.deepcopy(
            _safe_dict(_post_action_sim.get("companion_presence_projection"))
        )
        _rr["direct_companion_turn_result"] = copy.deepcopy(_direct_companion)
        _rr["companion_relationship_drift_result"] = copy.deepcopy(_companion_drift)
        _rr["companion_memory_summary"] = copy.deepcopy(_companion_mem_summary)
        _rr["companion_quest_progress_result"] = copy.deepcopy(_companion_quest_progress)
        _rr["companion_quest_summary"] = copy.deepcopy(_companion_quest_sum)
        _rr["party_composition_effects"] = copy.deepcopy(_party_composition)
        _rr["npc_profile_summary"] = _nps
        _rr["character_cards_summary"] = _ccs
        _rr["semantic_action_v2"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("semantic_action_v2"))
        )
        _rr["interaction_result"] = copy.deepcopy(
            _safe_dict(general_interaction_result.get("interaction_result"))
        )
        _rr["general_interaction_result"] = copy.deepcopy(general_interaction_result)
        _rr["inventory_result"] = copy.deepcopy(inventory_result)
        _rr["container_result"] = copy.deepcopy(container_result)
        _rr["repair_result"] = copy.deepcopy(repair_result)
        _rr["consumable_result"] = copy.deepcopy(consumable_result)
        _rr["equipment_stats"] = copy.deepcopy(equipment_stats)
        _rr["crafting_result"] = copy.deepcopy(crafting_result)
        _rr["merchant_result"] = copy.deepcopy(merchant_result)
        _rr["loot_result"] = copy.deepcopy(loot_result)
        _rr["companion_item_acceptance_result"] = copy.deepcopy(companion_item_acceptance_result)
        _rr["companion_auto_equip_result"] = copy.deepcopy(companion_auto_equip_result)
        _rr["combat_narration_contract"] = copy.deepcopy(combat_narration_contract)
        _rr["combat_narration_validation"] = copy.deepcopy(combat_narration_validation)

        _rr = _apply_visible_interaction_reason_to_resolved_result(
            _rr,
            general_interaction_result=general_interaction_result,
        )

        _tc["resolved_result"] = _rr
        final_result["turn_contract"] = _tc

        if _direct_companion.get("matched") and not _safe_dict(final_result.get("conversation_result")):
            final_result["conversation_result"] = {
                "triggered": True,
                "reason": "direct_active_companion_addressed",
                "participation_mode": "direct_companion_response",
                "npc_response_beat": copy.deepcopy(_direct_companion.get("npc_response_beat")),
                "direct_companion_turn_result": copy.deepcopy(_direct_companion),
                "party_aware_turn_context": copy.deepcopy(
                    _direct_companion.get("party_aware_turn_context")
                ),
                "companion_relationship_drift_result": copy.deepcopy(_companion_drift),
                "companion_memory_summary": copy.deepcopy(_companion_mem_summary),
                "companion_quest_progress_result": copy.deepcopy(_companion_quest_progress),
                "companion_quest_summary": copy.deepcopy(_companion_quest_sum),
                "party_composition_effects": copy.deepcopy(_party_composition),
                "npc_profile_summary": _nps,
                "character_cards_summary": _ccs,
                "source": "deterministic_companion_turn_runtime",
            }
        elif _safe_dict(final_result.get("conversation_result")):
            _conv = _safe_dict(final_result.get("conversation_result"))
            _conv["companion_relationship_drift_result"] = copy.deepcopy(_companion_drift)
            _conv["companion_memory_summary"] = copy.deepcopy(_companion_mem_summary)
            _conv["companion_quest_progress_result"] = copy.deepcopy(_companion_quest_progress)
            _conv["companion_quest_summary"] = copy.deepcopy(_companion_quest_sum)
            _conv["party_composition_effects"] = copy.deepcopy(_party_composition)
            _conv["npc_profile_summary"] = _nps
            _conv["character_cards_summary"] = _ccs
            final_result["conversation_result"] = _conv

    if _post_action_sim:
        session = _sync_session_if_companion_runtime_mutated(
            session,
            _post_action_sim,
            reason="normal_apply_turn_companion_runtime",
            companion_relationship_drift_result=_companion_drift,
            companion_quest_progress_result=_companion_quest_progress,
        )
    return session, final_result

__all__ = ['_apply_post_action_companion_enrichment']