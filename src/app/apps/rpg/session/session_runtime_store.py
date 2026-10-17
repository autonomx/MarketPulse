from __future__ import annotations

import logging

# RPG session runtime responsibility module.
from app.apps.rpg.session.state_normalization import (
    _ensure_simulation_state as _ensure_simulation_state, _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
import json as json
import ast as ast
from app.apps.rpg.rules.combat.state import (
    get_current_actor_id as get_current_actor_id, normalize_combat_state as normalize_combat_state,
)
from app.apps.rpg.session.companion_turn_runtime import (
    _build_turn_id as _build_turn_id,
)
from app.apps.rpg.session.combat_intent import (
    _actor_is_player as _actor_is_player, _build_combat_gate_result as _build_combat_gate_result, _infer_inventory_item_id_from_text as _infer_inventory_item_id_from_text,
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
from app.apps.rpg.session.semantic_state_changes import (
    _set_combat_state as _set_combat_state,
)
from app.apps.rpg.session.narration_queue_runtime import (
    _runtime_compact_save_enabled as _runtime_compact_save_enabled,
)
from app.apps.rpg.session.service import (
    load_session as load_canonical_session,
    save_session as save_canonical_session,
)
from app.apps.rpg.session.response_builder import (
    build_turn_payload as build_turn_payload,
)
from app.apps.rpg.session.player_activity_runtime import (
    _build_transaction_menus_for_state as _build_transaction_menus_for_state,
)
from app.apps.rpg.session.semantic_interaction_runtime import (
    _find_npc_target_by_name as _find_npc_target_by_name,
)
from typing import (
    Any as Any, Dict as Dict, List as List,
)
from typing import cast

logger = logging.getLogger(__name__)


def derive_action_candidates(simulation_state, player_input, runtime_state=None):
    candidates: list[dict[str, Any]] = []
    text = str(player_input.get("text", "") if isinstance(player_input, dict) else player_input).lower()
    target_id = _find_npc_target_by_name(simulation_state, text)

    # Deterministic inn / room rental fallback.
    # This catches flows like:
    #   "i ask bran for a room"
    #   "ill take the best one"
    # without letting "take" become pickup_item.
    inn_words = ("room", "inn", "bed", "stay", "rent", "lodging", "sleep")
    room_selection_words = ("best", "private", "cheap", "common", "standard", "normal")
    active_interactions = _safe_list((runtime_state or {}).get("active_interactions"))
    has_active_inn_interaction = any(
        _safe_str(_safe_dict(i).get("action_type")).lower() in {"rent_room", "rent_bed", "use_service"}
        or "room" in _safe_str(_safe_dict(i).get("subtype")).lower()
        or "inn" in _safe_str(_safe_dict(i).get("subtype")).lower()
        for i in active_interactions
    )
    if any(word in text for word in inn_words) or (
        has_active_inn_interaction and any(word in text for word in room_selection_words)
    ):
        tier = None
        if "best" in text or "private" in text:
            tier = "best"
        elif "cheap" in text or "common" in text:
            tier = "cheap"
        elif "standard" in text or "normal" in text:
            tier = "standard"
        candidates.append(
            {
                "action_type": "rent_room",
                "target": "inn",
                "tier": tier,
                "confidence": 0.92 if tier else 0.82,
                "source": "deterministic_room_rental_fallback",
            }
        )
        return candidates

    # Passive observation: no XP path
    if any(w in text for w in ["look around", "look about", "observe", "glance", "scan", "take in"]):
        candidates.append({"action_type": "observe", "priority": 4})

    # Real investigation: deliberate scrutiny
    if any(w in text for w in ["investigate", "search", "examine", "inspect", "analyze"]):
        candidates.append({"action_type": "investigate", "priority": 6})

    # Unarmed combat
    if any(w in text for w in ["punch", "kick", "headbutt", "slam"]):
        candidates.append({"action_type": "attack_unarmed", "priority": 10})

    # Armed / generic combat
    if any(w in text for w in ["attack", "hit", "strike", "fight", "slash", "stab"]):
        candidates.append({"action_type": "attack_melee", "priority": 9})

    if any(w in text for w in ["shoot", "fire", "aim"]):
        candidates.append({"action_type": "attack_ranged", "priority": 10})

    # Defense
    if any(w in text for w in ["block", "defend", "shield"]):
        candidates.append({"action_type": "block", "priority": 8})
    if any(w in text for w in ["dodge", "evade", "roll"]):
        candidates.append({"action_type": "dodge", "priority": 8})
    # Social
    if any(w in text for w in ["persuade", "convince", "talk", "negotiate"]):
        candidates.append({"action_type": "persuade", "priority": 7, "target_id": target_id})
    if any(w in text for w in ["threaten", "intimidate", "scare"]):
        candidates.append({"action_type": "intimidate", "priority": 7, "target_id": target_id})

    # Broad open-ended social / activity lane.
    # The semantic action interpreter will determine whether this is darts,
    # singing, drinking, hugging, competing, trading, ritual, etc.
    if any(w in text for w in [
        "play", "challenge", "invite", "join", "dance", "sing", "perform",
        "hug", "embrace", "kiss", "toast", "drink with", "buy", "trade",
        "bet", "gamble", "pray", "ritual", "compete", "contest",
    ]):
        candidates.append({"action_type": "social_activity", "priority": 8, "target_id": target_id})
    # Stealth
    if any(w in text for w in ["sneak", "hide", "stealth"]):
        candidates.append({"action_type": "sneak", "priority": 6})
    if any(w in text for w in ["hack", "crack", "decrypt"]):
        candidates.append({"action_type": "hack", "priority": 6})
    if any(w in text for w in ["cast", "spell", "magic"]):
        candidates.append({"action_type": "cast_spell", "priority": 7})
    if any(w in text for w in ["threat", "warn", "menace"]):
        candidates.append({"action_type": "threat", "priority": 7, "target_id": target_id})
    # --- Inn / room rental intent (deterministic fallback) ---
    if any(k in text for k in ["room", "inn", "bed", "stay", "rent", "lodging"]):
        tier = None
        if "best" in text or "private" in text:
            tier = "best"
        elif "cheap" in text or "common" in text:
            tier = "cheap"
        elif "standard" in text or "normal" in text:
            tier = "standard"

        candidates.append({
            "action_type": "rent_room",
            "target": "inn",
            "tier": tier,
            "confidence": 0.9
        })

        return candidates
    # Items
    prior_contract = _safe_dict((runtime_state or {}).get("last_turn_contract"))
    prior_service = _safe_dict(prior_contract.get("service_result"))
    if not prior_service:
        prior_resolved = _safe_dict(
            prior_contract.get("resolved_result") or prior_contract.get("resolved_action")
        )
        prior_service = _safe_dict(prior_resolved.get("service_result"))
    selects_prior_offer = any(
        _safe_str(_safe_dict(offer).get("label")).strip().lower() in text
        for offer in _safe_list(prior_service.get("offers"))
        if _safe_str(_safe_dict(offer).get("label")).strip()
    )
    if (
        prior_service.get("matched")
        and selects_prior_offer
        and any(phrase in text for phrase in ("i'll take", "ill take", "i will take"))
    ):
        candidates.append(
            {
                "action_type": "service_purchase",
                "priority": 11,
                "target_id": _safe_str(prior_service.get("provider_id")),
                "target_name": _safe_str(prior_service.get("provider_name")),
                "service_kind": _safe_str(prior_service.get("service_kind")),
                "confidence": 0.98,
                "source": "deterministic_prior_service_offer_selection",
            }
        )
        return candidates
    if "take" in text or "pick up" in text:
        active_interactions = _safe_list((runtime_state or {}).get("active_interactions"))
        if active_interactions and any(
            word in text for word in ("best", "private", "cheap", "common", "standard", "normal", "one")
        ):
            return []
        candidates.append(
            {
                "action_type": "pickup_item",
                "confidence": 0.6,
                "source": "keyword_pickup",
            }
        )
    if any(w in text for w in ["equip", "wear", "wield"]):
        candidates.append({"action_type": "equip_item", "priority": 5})
    if any(w in text for w in ["use", "drink", "eat", "consume", "quaff"]):
        candidates.append({"action_type": "use_item", "priority": 9})

    if any(w in text for w in ["flee", "retreat", "escape", "withdraw", "run away"]):
        candidates.append({"action_type": "flee", "priority": 10})

    if not candidates:
        # Open-ended fallback: use observe as the safe minimum,
        # then let the semantic layer refine this into a bounded semantic action.
        candidates.append({"action_type": "observe", "priority": 1, "target_id": target_id})

    candidates.sort(key=lambda c: c.get("priority", 0), reverse=True)
    return candidates


def _fallback_scene(simulation_state: dict[str, Any], player_input: str) -> dict[str, Any]:
    return {
        "scene_id": f"scene:tick:{int(simulation_state.get('tick', 0) or 0)}",
        "scene": f"You act: {player_input}",
        "summary": f"You act: {player_input}",
        "location_id": _safe_str(_safe_dict(simulation_state.get("player_state")).get("location_id")),
        "actors": [],
        "options": [],
        "meta": {"origin": "fallback"},
        "metadata": {"origin": "fallback"},
    }




def _build_turn_payload(session: dict[str, Any], narration_result: dict[str, Any], summary: list[str]) -> dict[str, Any]:
    return build_turn_payload(
        session,
        narration_result,
        summary,
        build_transaction_menus_for_state=cast(Any, _build_transaction_menus_for_state),
    )


def load_runtime_session(session_id: str) -> dict[str, Any] | None:
    if not session_id:
        return None
    return load_canonical_session(session_id)


def save_runtime_session(session: dict[str, Any]) -> dict[str, Any]:
    compact = _runtime_compact_save_enabled(_safe_dict(session.get("runtime_state")))
    return save_canonical_session(session, compact=compact)


def _find_active_combat_state_deep(payload: Any, *, max_depth: int = 6) -> dict[str, Any]:
    """Find an active combat_state nested anywhere inside a turn payload.

    J19-J21 rescue path:
    In the current turn pipeline, active combat state can be present in the
    authoritative result payload even when runtime_state has not yet been
    updated. This helper lets combat utility actions recover that state before
    unsupported_interaction_kind becomes the final visible result.
    """
    seen: set[int] = set()

    def walk(value: Any, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            return {}
        if not isinstance(value, (dict, list)):
            return {}

        obj_id = id(value)
        if obj_id in seen:
            return {}
        seen.add(obj_id)

        if isinstance(value, dict):
            direct = _safe_dict(value.get("combat_state"))
            if direct.get("active"):
                return normalize_combat_state(direct)

            for nested in value.values():
                found = walk(nested, depth + 1)
                if found.get("active"):
                    return found

        if isinstance(value, list):
            for nested in value:
                found = walk(nested, depth + 1)
                if found.get("active"):
                    return found

        return {}

    return walk(payload, 0)


def _combat_utility_kind_from_semantic_or_text(
    semantic_action_record: dict[str, Any],
    player_input: str,
) -> str:
    from app.apps.rpg.session.combat_turn_actions import (
        _player_input_requests_combat_ability as _player_input_requests_combat_ability,
    )

    text = _safe_str(player_input).strip().lower()

    # J34-J36:
    # Ability commands often contain "use", e.g. "I use power attack".
    # They must not be routed as combat use_item.
    if _player_input_requests_combat_ability(player_input):
        return ""

    semantic_kind = _safe_str(
        _safe_dict(semantic_action_record).get("kind")
        or _safe_dict(semantic_action_record).get("action_type")
    ).strip().lower()

    if semantic_kind == "defend" or any(
        term in text for term in ("defend", "guard", "block", "brace", "take cover")
    ):
        return "defend"

    if semantic_kind == "flee" or any(
        term in text for term in ("flee", "run away", "retreat", "escape", "withdraw")
    ):
        return "flee"

    if semantic_kind == "use_item" or any(
        term in text for term in ("use ", "drink ", "quaff ", "consume ", "eat ")
    ):
        return "use_item"

    return ""


def _extract_semantic_action_record_for_turn(
    semantic_action_record: dict[str, Any],
    authoritative: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the best semantic action record available for this turn.

    Some semantic action records are produced inside _apply_authoritative_action(...)
    and therefore are not available in the earlier local semantic_action_record
    variable. J19-J21 post-authoritative combat utility rescue must inspect the
    authoritative result shape too, otherwise commands like "I flee" remain
    stuck as unsupported_interaction_kind even though authoritative.result has
    semantic_action_v2.kind == "flee".
    """
    local = _safe_dict(semantic_action_record)
    if _safe_str(local.get("kind") or local.get("action_type")).strip():
        return local

    authoritative = _safe_dict(authoritative)
    result = _safe_dict(authoritative.get("result"))

    direct = _safe_dict(result.get("semantic_action_v2"))
    if _safe_str(direct.get("kind") or direct.get("action_type")).strip():
        return direct

    interaction_result = _safe_dict(result.get("interaction_result"))
    interaction_semantic = _safe_dict(interaction_result.get("semantic_action_v2"))
    if _safe_str(interaction_semantic.get("kind") or interaction_semantic.get("action_type")).strip():
        return interaction_semantic

    general_interaction_result = _safe_dict(result.get("general_interaction_result"))
    general_semantic = _safe_dict(general_interaction_result.get("semantic_action_v2"))
    if _safe_str(general_semantic.get("kind") or general_semantic.get("action_type")).strip():
        return general_semantic

    nested_interaction = _safe_dict(general_interaction_result.get("interaction_result"))
    nested_semantic = _safe_dict(nested_interaction.get("semantic_action_v2"))
    if _safe_str(nested_semantic.get("kind") or nested_semantic.get("action_type")).strip():
        return nested_semantic

    return local


def _resolved_result_is_unsupported_combat_utility(
    resolved_result: dict[str, Any],
    player_input: str,
) -> str:
    from app.apps.rpg.session.combat_turn_actions import (
        _player_input_requests_combat_ability as _player_input_requests_combat_ability,
    )

    if _player_input_requests_combat_ability(player_input):
        return ""

    """Return defend/flee/use_item when a finished result should be rescued.

    This is the last-chance J19-J21 guard. Earlier branches can miss because
    combat_state is assembled late in the current runtime pipeline. By the time
    resolved_result is fully populated, we can reliably see both:
    - semantic_action_v2.kind
    - combat_state.active
    """
    resolved_result = _safe_dict(resolved_result)
    combat_state = _safe_dict(resolved_result.get("combat_state"))
    if not combat_state.get("active"):
        combat_state = _find_active_combat_state_deep(resolved_result)
    if not combat_state.get("active"):
        return ""

    reason = _safe_str(
        resolved_result.get("visible_interaction_reason")
        or _safe_dict(resolved_result.get("interaction_result")).get("reason")
    ).strip()

    if reason not in {
        "unsupported_interaction_kind",
        "no_supported_semantic_action_detected",
        "",
    }:
        return ""

    semantic = _safe_dict(resolved_result.get("semantic_action_v2"))
    if not semantic:
        semantic = _safe_dict(_safe_dict(resolved_result.get("interaction_result")).get("semantic_action_v2"))
    if not semantic:
        semantic = _safe_dict(_safe_dict(resolved_result.get("general_interaction_result")).get("semantic_action_v2"))
    if not semantic:
        semantic = _safe_dict(
            _safe_dict(
                _safe_dict(resolved_result.get("general_interaction_result")).get("interaction_result")
            ).get("semantic_action_v2")
        )

    return _combat_utility_kind_from_semantic_or_text(semantic, player_input)


def _rescue_final_apply_turn_combat_utility_result(
    final_result: dict[str, Any],
    player_input: str,
) -> dict[str, Any]:
    """Last-mile J19-J21 rescue after build_apply_turn_response(...).

    Some combat utility failures only become visible after the final apply-turn
    response is assembled. At that point the useful fields may be siblings:

    - final_result.semantic_action_v2.kind == flee / defend / use_item
    - final_result.combat_state.active == true
    - final_result.visible_interaction_reason == unsupported_interaction_kind
    - final_result.resolved_result == {}

    The earlier in-authoritative rescue cannot see that final sibling shape.
    This wrapper-level rescue rewrites the completed unsupported result into an
    authoritative combat utility result before the manual transcript and UI see it.
    """
    from app.apps.rpg.session.combat_turn_actions import (
        _player_input_requests_combat_ability as _player_input_requests_combat_ability,
    )

    if _player_input_requests_combat_ability(player_input):
        return _safe_dict(final_result)

    final_result = dict(_safe_dict(final_result))

    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    result_obj = _safe_dict(final_result.get("result")) or _safe_parse_mapping_payload(
        final_result.get("result")
    )

    candidate = dict(resolved_result)

    if not _safe_dict(candidate.get("combat_state")).get("active"):
        candidate["combat_state"] = _safe_dict(final_result.get("combat_state"))
    if not _safe_dict(candidate.get("combat_state")).get("active"):
        candidate["combat_state"] = _safe_dict(result_obj.get("combat_state"))
    if not _safe_dict(candidate.get("combat_state")).get("active"):
        candidate["combat_state"] = _find_active_combat_state_deep(final_result)

    if not _safe_dict(candidate.get("semantic_action_v2")):
        candidate["semantic_action_v2"] = _safe_dict(final_result.get("semantic_action_v2"))
    if not _safe_dict(candidate.get("semantic_action_v2")):
        candidate["semantic_action_v2"] = _safe_dict(result_obj.get("semantic_action_v2"))

    if not _safe_dict(candidate.get("interaction_result")):
        candidate["interaction_result"] = _safe_dict(final_result.get("interaction_result"))
    if not _safe_dict(candidate.get("interaction_result")):
        candidate["interaction_result"] = _safe_dict(result_obj.get("interaction_result"))

    if not _safe_dict(candidate.get("general_interaction_result")):
        candidate["general_interaction_result"] = _safe_dict(final_result.get("general_interaction_result"))
    if not _safe_dict(candidate.get("general_interaction_result")):
        candidate["general_interaction_result"] = _safe_dict(result_obj.get("general_interaction_result"))

    if not _safe_str(candidate.get("visible_interaction_reason")).strip():
        candidate["visible_interaction_reason"] = _safe_str(
            final_result.get("visible_interaction_reason")
            or result_obj.get("visible_interaction_reason")
            or _safe_dict(candidate.get("interaction_result")).get("reason")
            or _safe_dict(
                _safe_dict(candidate.get("general_interaction_result")).get("interaction_result")
            ).get("reason")
        ).strip()

    utility_kind = _resolved_result_is_unsupported_combat_utility(candidate, player_input)
    if not utility_kind:
        return final_result

    combat_state = normalize_combat_state(_safe_dict(candidate.get("combat_state")))
    if not combat_state.get("active"):
        return final_result

    session = _safe_dict(final_result.get("session"))
    simulation_state = _ensure_simulation_state(
        _safe_dict(session.get("simulation_state"))
        or _safe_dict(final_result.get("simulation_state"))
    )
    runtime_state = (
        _safe_dict(session.get("runtime_state"))
        or _safe_dict(final_result.get("runtime_state"))
        or {}
    )

    turn_id = _safe_str(final_result.get("turn_id")).strip() or _build_turn_id(runtime_state)
    tick = _safe_int(final_result.get("tick"), _safe_int(runtime_state.get("tick"), 0))
    player_actor_id = "player"

    combat_result: dict[str, Any] = {}
    npc_combat_result: dict[str, Any] = {}

    current_actor_id = get_current_actor_id(combat_state)
    if current_actor_id and _safe_str(current_actor_id) != player_actor_id:
        resolved_result = _build_combat_gate_result(current_actor_id, player_actor_id)

    elif utility_kind == "defend":
        defense_resolution = resolve_defend(
            simulation_state,
            combat_state,
            player_actor_id,
        )
        combat_result = defense_resolution.to_dict()
        simulation_state, combat_state = apply_defense_resolution(
            simulation_state,
            combat_state,
            combat_result,
        )
        resolved_result["action_type"] = "defend"
        resolved_result["outcome"] = "defended"
        resolved_result["visible_interaction_reason"] = "combat_defend"
        resolved_result["combat_result"] = combat_result

    elif utility_kind == "flee":
        flee_resolution = resolve_flee(
            simulation_state,
            combat_state,
            player_actor_id,
            turn_id=turn_id,
            tick=tick,
        )
        combat_result = flee_resolution.to_dict()
        simulation_state, combat_state = apply_flee_resolution(
            simulation_state,
            combat_state,
            combat_result,
        )
        resolved_result["action_type"] = "flee"
        resolved_result["outcome"] = "fled" if combat_result.get("success") else "flee_failed"
        resolved_result["visible_interaction_reason"] = "combat_flee"
        resolved_result["combat_result"] = combat_result

    elif utility_kind == "use_item":
        # J20:
        # The normal item/consumable runtime may already have resolved and
        # applied the item before this final rescue runs. Prefer that successful
        # result instead of trying to infer/apply the item a second time, which
        # can produce item_id="" and unknown_item after the item was consumed.
        prior_consumable_result = _safe_dict(
            final_result.get("consumable_result")
            or result_obj.get("consumable_result")
            or resolved_result.get("consumable_result")
        )
        if not _is_successful_consumable_result(prior_consumable_result):
            prior_consumable_result = _extract_successful_consumable_result_from_payload(
                {
                    "final_result": final_result,
                    "result": result_obj,
                    "resolved_result": resolved_result,
                }
            )
        if not _is_successful_consumable_result(prior_consumable_result):
            prior_consumable_result = _extract_successful_consumable_result_from_string_payload(
                final_result.get("result")
            )

        prior_inventory_result = _safe_dict(
            final_result.get("inventory_result")
            or result_obj.get("inventory_result")
            or resolved_result.get("inventory_result")
        )

        combat_result = (
            _combat_result_from_consumable_result(prior_consumable_result)
            or _combat_result_from_consumable_result(prior_inventory_result)
        )

        if not combat_result:
            item_id = _infer_inventory_item_id_from_text(simulation_state, {}, player_input)
            item_result = apply_item_effects(simulation_state, item_id)
            simulation_state = _ensure_simulation_state(_safe_dict(item_result.get("simulation_state")))
            combat_result = _safe_dict(item_result.get("result"))
            combat_result.setdefault("action_type", "use_item")
            combat_result.setdefault("combat_id", _safe_str(combat_state.get("combat_id")))
            combat_result.setdefault(
                "notes",
                ["combat_item_used"] if combat_result.get("ok") else ["combat_item_failed"],
            )

        resolved_result["action_type"] = "use_item"
        resolved_result["outcome"] = "item_used" if combat_result.get("ok") else "item_use_failed"
        resolved_result["visible_interaction_reason"] = "combat_use_item"
        resolved_result["combat_result"] = combat_result
        resolved_result["inventory_result"] = combat_result
        resolved_result["consumable_result"] = prior_consumable_result or combat_result

    if combat_state.get("active"):
        combat_state = advance_turn(combat_state)
        current_after_player = get_current_actor_id(combat_state)
        if current_after_player and not _actor_is_player(simulation_state, current_after_player):
            simulation_state, combat_state, npc_combat_result = run_npc_turn(
                simulation_state,
                combat_state,
                tick=tick,
            )
            combat_state = evaluate_combat_exit(simulation_state, combat_state)

    runtime_state = _set_combat_state(runtime_state, combat_state)
    resolved_result["combat_state"] = combat_state
    resolved_result["interaction_result"] = {}
    resolved_result["general_interaction_result"] = {}

    if npc_combat_result:
        resolved_result["npc_combat_result"] = npc_combat_result

    if not session:
        session = {}
    session["simulation_state"] = simulation_state
    session["runtime_state"] = runtime_state

    reason = _safe_str(resolved_result.get("visible_interaction_reason")).strip()

    final_result["session"] = session
    final_result["simulation_state"] = simulation_state
    final_result["runtime_state"] = runtime_state
    final_result["resolved_result"] = resolved_result
    final_result["combat_result"] = combat_result
    final_result["npc_combat_result"] = npc_combat_result
    final_result["combat_state"] = combat_state
    final_result["interaction_result"] = {}
    final_result["general_interaction_result"] = {}
    final_result["visible_interaction_reason"] = reason
    final_result["action_type"] = utility_kind
    final_result["outcome"] = _safe_str(resolved_result.get("outcome"))

    if utility_kind == "use_item":
        final_result["inventory_result"] = combat_result

    # Keep non-LLM fallback narration deterministic for manual/UI visibility.
    final_result["narration"] = f"Result: {reason}"
    final_result["final_narration"] = f"Result: {reason}"
    final_result["summary"] = f"Result: {reason}"

    return final_result


def _combat_result_from_consumable_result(consumable_result: dict[str, Any]) -> dict[str, Any]:
    consumable_result = _safe_dict(consumable_result)
    if not consumable_result:
        return {}

    reason = _safe_str(consumable_result.get("reason")).strip()
    effect_result = _safe_dict(consumable_result.get("effect_result"))
    applied = bool(effect_result.get("applied"))

    if reason != "consumable_used" and not applied:
        return {}

    item_id = _safe_str(consumable_result.get("item_id")).strip()
    combat_result = dict(consumable_result)
    combat_result["ok"] = True
    combat_result["action_type"] = "use_item"
    combat_result["item_id"] = item_id
    combat_result["reason"] = reason or "consumable_used"
    combat_result["notes"] = ["combat_item_used"]
    return combat_result


def _is_successful_consumable_result(value: dict[str, Any]) -> bool:
    value = _safe_dict(value)
    if not value:
        return False
    if _safe_str(value.get("reason")).strip() == "consumable_used":
        return True
    if bool(_safe_dict(value.get("effect_result")).get("applied")):
        return True
    return False


def _extract_successful_consumable_result_from_payload(payload: Any, *, max_depth: int = 7) -> dict[str, Any]:
    """Find a successful consumable_result anywhere in a nested apply-turn payload."""
    seen: set[int] = set()

    def walk(value: Any, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            return {}
        if not isinstance(value, (dict, list)):
            return {}

        obj_id = id(value)
        if obj_id in seen:
            return {}
        seen.add(obj_id)

        if isinstance(value, dict):
            direct = _safe_dict(value.get("consumable_result"))
            if _is_successful_consumable_result(direct):
                return direct

            # Some payloads expose the consumable result as the interaction
            # result itself rather than under a consumable_result key.
            if _is_successful_consumable_result(value):
                return _safe_dict(value)

            for nested in value.values():
                found = walk(nested, depth + 1)
                if found:
                    return found

        if isinstance(value, list):
            for nested in value:
                found = walk(nested, depth + 1)
                if found:
                    return found

        return {}

    return walk(payload, 0)


def _safe_parse_mapping_payload(value: Any) -> dict[str, Any]:
    """Parse dict payloads that were serialized as JSON or Python repr strings.

    Manual/apply-turn payloads sometimes carry the authoritative result as a
    stringified Python dict. J20 consumable success can live only inside that
    string, so plain _safe_dict(...) cannot see it.
    """
    if isinstance(value, dict):
        return _safe_dict(value)
    if not isinstance(value, str):
        return {}

    text = value.strip()
    if not text or not text.startswith("{"):
        return {}

    try:
        parsed = json.loads(text)
        return _safe_dict(parsed)
    except Exception:
        logger.debug("suppressed error in %s", "_safe_parse_mapping_payload", exc_info=True)

    try:
        parsed = ast.literal_eval(text)
        return _safe_dict(parsed)
    except Exception:
        return {}


def _extract_successful_consumable_result_from_string_payload(value: Any) -> dict[str, Any]:
    parsed = _safe_parse_mapping_payload(value)
    if not parsed:
        return {}
    return _extract_successful_consumable_result_from_payload(parsed)


def _mirror_rescued_combat_utility_result(final_result: dict[str, Any]) -> dict[str, Any]:
    """Ensure rescued J19-J21 results are consistent at top level.

    The wrapper-level rescue can successfully rewrite final_result["resolved_result"],
    but later apply_turn enrichment can leave old sibling fields in place:

    - final_result["combat_result"] == {}
    - final_result["visible_interaction_reason"] == "unsupported_interaction_kind"
    - final_result["interaction_result"]["reason"] == "unsupported_interaction_kind"

    Manual validation and UI code often read the top-level fields, so mirror the
    rescued combat utility result back to the final top-level payload.
    """
    final_result = dict(_safe_dict(final_result))
    resolved_result = _safe_dict(final_result.get("resolved_result"))

    reason = _safe_str(resolved_result.get("visible_interaction_reason")).strip()
    if reason not in {"combat_defend", "combat_flee", "combat_use_item"}:
        return final_result

    combat_result = _safe_dict(resolved_result.get("combat_result"))
    if not combat_result:
        return final_result

    final_result["combat_result"] = combat_result
    final_result["visible_interaction_reason"] = reason
    final_result["action_type"] = _safe_str(resolved_result.get("action_type")).strip()
    final_result["outcome"] = _safe_str(resolved_result.get("outcome")).strip()
    final_result["interaction_result"] = {}
    final_result["general_interaction_result"] = {}

    if _safe_dict(resolved_result.get("combat_state")):
        final_result["combat_state"] = _safe_dict(resolved_result.get("combat_state"))

    if _safe_dict(resolved_result.get("npc_combat_result")):
        final_result["npc_combat_result"] = _safe_dict(resolved_result.get("npc_combat_result"))

    if reason == "combat_use_item":
        final_result["inventory_result"] = _safe_dict(
            resolved_result.get("inventory_result") or combat_result
        )
        final_result["consumable_result"] = _safe_dict(
            resolved_result.get("consumable_result")
            or final_result.get("consumable_result")
            or combat_result
        )

    final_result["narration"] = f"Result: {reason}"
    final_result["final_narration"] = f"Result: {reason}"
    final_result["summary"] = f"Result: {reason}"

    # Some transcript/debug builders stringify final_result["result"]. Keep it
    # aligned too when it is a dict payload.
    result_obj = dict(
        _safe_dict(final_result.get("result"))
        or _safe_parse_mapping_payload(final_result.get("result"))
    )
    if result_obj:
        result_obj["combat_result"] = combat_result
        result_obj["visible_interaction_reason"] = reason
        result_obj["action_type"] = final_result["action_type"]
        result_obj["outcome"] = final_result["outcome"]
        result_obj["interaction_result"] = {}
        result_obj["general_interaction_result"] = {}
        if reason == "combat_use_item":
            result_obj["inventory_result"] = _safe_dict(
                final_result.get("inventory_result") or combat_result
            )
            result_obj["consumable_result"] = _safe_dict(
                final_result.get("consumable_result") or combat_result
            )
        final_result["result"] = result_obj

    return final_result


def _mirror_enemy_ai_combat_results(final_result: dict[str, Any]) -> dict[str, Any]:
    final_result = dict(_safe_dict(final_result))
    resolved_result = dict(_safe_dict(final_result.get("resolved_result")))
    combat_state = _safe_dict(final_result.get("combat_state") or resolved_result.get("combat_state"))

    npc_combat_result = _safe_dict(
        final_result.get("npc_combat_result")
        or final_result.get("enemy_combat_result")
        or resolved_result.get("npc_combat_result")
        or resolved_result.get("enemy_combat_result")
    )

    if not npc_combat_result:
        npc_combat_result = _safe_dict(combat_state.get("last_npc_combat_result"))
    if not npc_combat_result:
        npc_combat_result = _safe_dict(combat_state.get("last_enemy_combat_result"))

    enemy_intent_result = _safe_dict(
        npc_combat_result.get("enemy_intent_result")
        or combat_state.get("last_enemy_intent_result")
    )
    target_selection_result = _safe_dict(
        npc_combat_result.get("target_selection_result")
        or combat_state.get("last_target_selection_result")
    )
    morale_result = _safe_dict(
        npc_combat_result.get("morale_result")
        or combat_state.get("last_morale_result")
    )

    if not enemy_intent_result and not target_selection_result and not morale_result:
        return final_result

    if enemy_intent_result:
        final_result["enemy_intent_result"] = enemy_intent_result
        resolved_result["enemy_intent_result"] = enemy_intent_result
    if target_selection_result:
        final_result["target_selection_result"] = target_selection_result
        resolved_result["target_selection_result"] = target_selection_result
    if morale_result:
        final_result["morale_result"] = morale_result
        resolved_result["morale_result"] = morale_result

    if npc_combat_result:
        final_result["npc_combat_result"] = npc_combat_result
        final_result["enemy_combat_result"] = npc_combat_result
        resolved_result["npc_combat_result"] = npc_combat_result
        resolved_result["enemy_combat_result"] = npc_combat_result

    final_result["resolved_result"] = resolved_result

    result_obj = dict(
        _safe_dict(final_result.get("result"))
        or _safe_parse_mapping_payload(final_result.get("result"))
    )
    if result_obj:
        if enemy_intent_result:
            result_obj["enemy_intent_result"] = enemy_intent_result
        if target_selection_result:
            result_obj["target_selection_result"] = target_selection_result
        if morale_result:
            result_obj["morale_result"] = morale_result
        if npc_combat_result:
            result_obj["npc_combat_result"] = npc_combat_result
            result_obj["enemy_combat_result"] = npc_combat_result
        final_result["result"] = result_obj

    return final_result

__all__ = ['_build_turn_payload', '_combat_result_from_consumable_result', '_combat_utility_kind_from_semantic_or_text', '_extract_semantic_action_record_for_turn', '_extract_successful_consumable_result_from_payload', '_extract_successful_consumable_result_from_string_payload', '_fallback_scene', '_find_active_combat_state_deep', '_is_successful_consumable_result', '_mirror_enemy_ai_combat_results', '_mirror_rescued_combat_utility_result', '_rescue_final_apply_turn_combat_utility_result', '_resolved_result_is_unsupported_combat_utility', '_safe_parse_mapping_payload', 'derive_action_candidates', 'load_runtime_session', 'save_runtime_session']
