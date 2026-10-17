from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.apps.rpg.world.memory.dialogue_context import (
    build_dialogue_memory_context,
    build_llm_memory_prompt_block,
)
from app.apps.rpg.narration.presentation import (
    build_runtime_presentation_payload,
    build_scene_presentation_payload,
)
from app.apps.rpg.rules.runtime_promotions import attach_runtime_promotion_payloads
from app.apps.rpg.session.state_normalization import _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list
from app.apps.rpg.session.survival_runtime import attach_survival_runtime_payloads
from app.apps.rpg.rules.survival_action_context import attach_survival_action_context
from app.apps.rpg.rules.survival_tick_runtime import apply_survival_runtime_tick


def _first_dict(*values):
    for value in values:
        value = _safe_dict(value)
        if value:
            return value
    return {}


def _first_list(*values):
    for value in values:
        value = _safe_list(value)
        if value:
            return value
    return []


def _first_value(*values):
    for value in values:
        if value is not None:
            return value
    return None


def _attach_living_world_debug_fields(result_payload: Dict[str, Any]) -> Dict[str, Any]:
    result_payload = _safe_dict(result_payload)
    narration_debug = _safe_dict(result_payload.get("narration_debug"))
    resolved_result = _safe_dict(
        result_payload.get("resolved_result")
        or result_payload.get("resolved_action")
        or result_payload
    )
    service_application = _safe_dict(resolved_result.get("service_application"))

    result_payload["living_world_debug"] = _first_dict(
        result_payload.get("living_world_debug"),
        resolved_result.get("living_world_debug"),
        service_application.get("living_world_debug"),
        {
            "memory_entry": _first_dict(
                resolved_result.get("memory_entry"),
                service_application.get("memory_entry"),
            ),
            "social_effects": _first_dict(
                resolved_result.get("social_effects"),
                service_application.get("social_effects"),
            ),
            "stock_update": _first_dict(
                resolved_result.get("stock_update"),
                service_application.get("stock_update"),
            ),
            "rumor_added": _first_dict(
                resolved_result.get("rumor_added"),
                service_application.get("rumor_added"),
            ),
            "journal_entry": _first_dict(
                resolved_result.get("journal_entry"),
                service_application.get("journal_entry"),
            ),
            "service_world_event": _first_dict(
                resolved_result.get("service_world_event"),
                service_application.get("service_world_event"),
            ),
            "rumor_world_event": _first_dict(
                resolved_result.get("rumor_world_event"),
                service_application.get("rumor_world_event"),
            ),
        },
    )
    result_payload["memory_state"] = _first_dict(
        result_payload.get("memory_state"),
        resolved_result.get("memory_state"),
    )
    result_payload["relationship_state"] = _first_dict(
        result_payload.get("relationship_state"),
        resolved_result.get("relationship_state"),
    )
    result_payload["npc_emotion_state"] = _first_dict(
        result_payload.get("npc_emotion_state"),
        resolved_result.get("npc_emotion_state"),
    )
    result_payload["service_offer_state"] = _first_dict(
        result_payload.get("service_offer_state"),
        resolved_result.get("service_offer_state"),
    )
    result_payload["journal_state"] = _first_dict(
        result_payload.get("journal_state"),
        resolved_result.get("journal_state"),
        service_application.get("journal_state"),
    )
    result_payload["world_event_state"] = _first_dict(
        result_payload.get("world_event_state"),
        resolved_result.get("world_event_state"),
        service_application.get("world_event_state"),
    )
    result_payload["location_state"] = _first_dict(
        result_payload.get("location_state"),
        resolved_result.get("location_state"),
    )
    result_payload["travel_result"] = _first_dict(
        result_payload.get("travel_result"),
        resolved_result.get("travel_result"),
    )
    result_payload["conversation_result"] = _first_dict(
        result_payload.get("conversation_result"),
        resolved_result.get("conversation_result"),
    )
    result_payload["conversation_thread_state"] = _first_dict(
        result_payload.get("conversation_thread_state"),
        resolved_result.get("conversation_thread_state"),
    )

    service_result = _safe_dict(
        resolved_result.get("service_result")
        or result_payload.get("service_result")
    )
    current_location_id = (
        result_payload.get("current_location_id")
        or resolved_result.get("current_location_id")
        or service_result.get("current_location_id")
    )
    if current_location_id:
        result_payload["current_location_id"] = current_location_id
        if not _safe_dict(result_payload.get("location_state")):
            result_payload["location_state"] = {
                "current_location_id": current_location_id,
            }
    result_payload["recalled_service_memories"] = _first_list(
        result_payload.get("recalled_service_memories"),
        resolved_result.get("recalled_service_memories"),
        narration_debug.get("recalled_service_memories"),
    )
    result_payload["service_memory_recall_debug"] = _first_dict(
        result_payload.get("service_memory_recall_debug"),
        resolved_result.get("service_memory_recall_debug"),
        narration_debug.get("service_memory_recall_debug"),
    )
    result_payload["recalled_npc_memories"] = _first_list(
        result_payload.get("recalled_npc_memories"),
        resolved_result.get("recalled_npc_memories"),
        narration_debug.get("recalled_npc_memories"),
    )
    result_payload["npc_memory_recall_debug"] = _first_dict(
        result_payload.get("npc_memory_recall_debug"),
        resolved_result.get("npc_memory_recall_debug"),
        narration_debug.get("npc_memory_recall_debug"),
    )
    result_payload["social_living_world_effects"] = _first_dict(
        result_payload.get("social_living_world_effects"),
        resolved_result.get("social_living_world_effects"),
    )
    return result_payload


def build_turn_payload(
    session: Dict[str, Any],
    narration_result: Dict[str, Any],
    summary: List[str],
    *,
    build_transaction_menus_for_state: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    session = _safe_dict(session)
    simulation_state = _safe_dict(session.get("simulation_state"))
    runtime_state = _safe_dict(session.get("runtime_state"))
    current_scene = _safe_dict(runtime_state.get("current_scene"))
    memory_context = build_dialogue_memory_context(
        simulation_state,
        actor_id="player",
    )
    player_state = _safe_dict(simulation_state.get("player_state"))
    last_turn = _safe_dict(runtime_state.get("last_turn_result"))
    inventory_state = _safe_dict(player_state.get("inventory_state"))
    equipment = _safe_dict(inventory_state.get("equipment"))
    transaction_menus = build_transaction_menus_for_state(simulation_state, runtime_state)

    payload = {
        "success": True,
        "session_id": _safe_str(_safe_dict(session.get("manifest")).get("id")),
        "narration": _safe_str(narration_result.get("narrative") or current_scene.get("summary")),
        "choices": _safe_list(narration_result.get("choices")),
        "npcs": _safe_list(runtime_state.get("npcs")),
        "player": {
            "stats": _safe_dict(player_state.get("stats")),
            "skills": _safe_dict(player_state.get("skills")),
            "level": int(player_state.get("level", 1) or 1),
            "xp": int(player_state.get("xp", 0) or 0),
            "xp_to_next": int(player_state.get("xp_to_next", 0) or 0),
            "inventory_state": inventory_state,
            "equipment": equipment,
            "currency": _safe_dict(inventory_state.get("currency")),
            "inventory_items": _safe_list(inventory_state.get("items")),
            "nearby_npc_ids": _safe_list(player_state.get("nearby_npc_ids")),
            "available_checks": _safe_list(player_state.get("available_checks")),
        },
        "memory": _safe_list(memory_context.get("items")),
        "worldEvents": _safe_list(simulation_state.get("events"))[-8:],
        "world_events": _safe_list(simulation_state.get("events"))[-8:],
        "summary": summary[:8],
        "scene": current_scene,
        "scene_presentation": build_scene_presentation_payload(simulation_state, current_scene),
        "presentation": build_runtime_presentation_payload(simulation_state, runtime_state),
        "dialogue_memory_context": memory_context,
        "llm_memory_prompt_block": build_llm_memory_prompt_block(memory_context),
        "voice_assignments": _safe_dict(runtime_state.get("voice_assignments")),
        "npc_reactions": _safe_list(narration_result.get("npc_reactions")),
        "dialogue_blocks": _safe_list(narration_result.get("dialogue_blocks")),
        "metadata": _safe_dict(narration_result.get("metadata")),
        "turn": int(runtime_state.get("tick", 0) or 0),
        "player_level": int(player_state.get("level", 1) or 1),
        "player_xp": int(player_state.get("xp", 0) or 0),
        "player_skills": _safe_dict(player_state.get("skills")),
        "level_up": bool(last_turn.get("level_up")),
        "skill_level_ups": _safe_list(last_turn.get("skill_level_ups")),
        "combat_result": _safe_dict(last_turn.get("combat_result")),
        "xp_result": _safe_dict(last_turn.get("xp_result")),
        "resource_changes": _safe_dict(last_turn.get("resource_changes")),
        "player_resources": _safe_dict(last_turn.get("player_resources")),
        "effect_result": _safe_dict(last_turn.get("effect_result")),
        "transaction_menus": transaction_menus,
    }
    payload = attach_runtime_promotion_payloads(payload, simulation_state, runtime_state)
    if simulation_state:
        payload = attach_survival_action_context(payload, simulation_state)
    return payload



def build_apply_turn_response(authoritative_result: Dict[str, Any]) -> Dict[str, Any]:
    authoritative_result = _safe_dict(authoritative_result)
    authoritative = _safe_dict(authoritative_result.get("authoritative"))
    result_sub = _safe_dict(authoritative_result.get("result"))
    session = _safe_dict(authoritative_result.get("session"))
    simulation_state = _safe_dict(session.get("simulation_state"))
    runtime_state = _safe_dict(session.get("runtime_state"))
    resolved_result = _first_dict(
        authoritative.get("resolved_result"),
        result_sub.get("resolved_result"),
    )
    turn_contract = _safe_dict(
        authoritative_result.get("turn_contract")
        or authoritative.get("turn_contract")
    )
    if simulation_state:
        turn_contract = attach_runtime_promotion_payloads(
            turn_contract,
            simulation_state,
            runtime_state,
        )
        turn_contract = attach_survival_action_context(turn_contract, simulation_state)
    narration = result_sub.get("narration")
    if narration is None:
        narration = authoritative.get("deterministic_fallback_narration")
    if narration is None:
        narration = result_sub.get("deterministic_fallback_narration")
    raw_llm_narrative = result_sub.get("raw_llm_narrative")
    if raw_llm_narrative is None:
        raw_llm_narrative = ""
    used_llm = result_sub.get("used_llm")
    if used_llm is None:
        used_llm = False
    narration_status = result_sub.get("narration_status")
    if narration_status is None:
        narration_status = "queued"

    result_payload = {
        "turn_id": _first_value(authoritative.get("turn_id"), result_sub.get("turn_id")),
        "tick": _first_value(authoritative.get("tick"), result_sub.get("tick")),
        "resolved_result": resolved_result,
        "combat_result": _first_dict(
            authoritative.get("combat_result"),
            result_sub.get("combat_result"),
        ),
        "xp_result": _first_dict(
            authoritative.get("xp_result"),
            result_sub.get("xp_result"),
        ),
        "skill_xp_result": _first_dict(
            authoritative.get("skill_xp_result"),
            result_sub.get("skill_xp_result"),
        ),
        "level_up": _first_list(
            authoritative.get("level_up"),
            result_sub.get("level_up"),
        ),
        "skill_level_ups": _first_list(
            authoritative.get("skill_level_ups"),
            result_sub.get("skill_level_ups"),
        ),
        "summary": _first_value(authoritative.get("summary"), result_sub.get("summary")),
        "presentation": _first_dict(
            authoritative.get("presentation"),
            result_sub.get("presentation"),
        ),
        "response_length": _first_value(
            authoritative.get("response_length"),
            result_sub.get("response_length"),
        ),
        "narration": narration,
        "raw_llm_narrative": raw_llm_narrative,
        "used_llm": used_llm,
        "grounding_validation": _first_dict(
            authoritative.get("grounding_validation"),
            result_sub.get("grounding_validation"),
        ),
        "grounding_fallback": bool(
            _first_value(
                authoritative.get("grounding_fallback"),
                result_sub.get("grounding_fallback"),
            )
        ),
        "settings": _first_dict(
            authoritative.get("settings"),
            result_sub.get("settings"),
        ),
        "narration_status": narration_status,
        "narration_debug": _safe_dict(
            result_sub.get("narration_debug") or authoritative.get("narration_debug")
        ),
        "living_world_debug": _safe_dict(resolved_result.get("living_world_debug")),
        "memory_state": _safe_dict(resolved_result.get("memory_state")),
        "relationship_state": _safe_dict(resolved_result.get("relationship_state")),
        "npc_emotion_state": _safe_dict(resolved_result.get("npc_emotion_state")),
        "service_offer_state": _safe_dict(resolved_result.get("service_offer_state")),
        "journal_state": _safe_dict(resolved_result.get("journal_state")),
        "world_event_state": _safe_dict(resolved_result.get("world_event_state")),
        "recalled_service_memories": _safe_list(resolved_result.get("recalled_service_memories")),
        "service_memory_recall_debug": _safe_dict(resolved_result.get("service_memory_recall_debug")),
        "recalled_npc_memories": _safe_list(resolved_result.get("recalled_npc_memories")),
        "npc_memory_recall_debug": _safe_dict(resolved_result.get("npc_memory_recall_debug")),
        "social_living_world_effects": _safe_dict(resolved_result.get("social_living_world_effects")),
    }

    result_payload = _attach_living_world_debug_fields(result_payload)
    if simulation_state:
        result_payload = attach_runtime_promotion_payloads(
            result_payload,
            simulation_state,
            runtime_state,
        )
        result_payload = attach_survival_action_context(result_payload, simulation_state)

    survival_projection = attach_survival_runtime_payloads(
        authoritative_result=authoritative_result,
        session=session,
        turn_contract=turn_contract,
        result_payload=result_payload,
        resolved_result=_safe_dict(result_payload.get("resolved_result") or resolved_result),
    )
    session = _safe_dict(survival_projection.get("session") or session)
    turn_contract = _safe_dict(survival_projection.get("turn_contract") or turn_contract)
    result_payload = _safe_dict(survival_projection.get("result_payload") or result_payload)

    tick_projection = apply_survival_runtime_tick(
        authoritative_result=authoritative_result,
        session=session,
        turn_contract=turn_contract,
        result_payload=result_payload,
        resolved_result=_safe_dict(result_payload.get("resolved_result") or resolved_result),
    )
    session = _safe_dict(tick_projection.get("session") or session)
    turn_contract = _safe_dict(tick_projection.get("turn_contract") or turn_contract)
    result_payload = _safe_dict(tick_projection.get("result_payload") or result_payload)

    if session:
        simulation_state = _safe_dict(session.get("simulation_state")) or simulation_state
    if simulation_state:
        turn_contract = attach_survival_action_context(turn_contract, simulation_state)
        result_payload = attach_survival_action_context(result_payload, simulation_state)

    return {
        "ok": True,
        "session": session,
        "turn_contract": turn_contract,
        "result": result_payload,
    }
