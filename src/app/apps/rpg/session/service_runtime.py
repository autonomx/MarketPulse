from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict

from app.apps.rpg.rules.economy.service_effects import apply_service_purchase_result
from app.apps.rpg.session.service_living_world import apply_service_living_world_effects
from app.apps.rpg.session.state_normalization import _safe_int, _safe_str
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list
from app.apps.rpg.rules.pending_interactions import (
    close_pending_service_offer,
    record_service_offer,
)
from app.apps.rpg.session.duration_actions import apply_duration_action
from app.apps.rpg.session.quest_evidence import (
    apply_quest_evidence,
    clue_for_service_interaction,
)


def service_action_from_result(
    player_input: str,
    action: Dict[str, Any],
    service_result: Dict[str, Any],
) -> Dict[str, Any]:
    action = _safe_dict(action)
    service_result = _safe_dict(service_result)
    if not service_result.get("matched"):
        return action

    service_action = dict(action)
    service_action["action_type"] = _safe_str(service_result.get("kind") or "service_inquiry")
    service_action["service_kind"] = _safe_str(service_result.get("service_kind"))
    service_action["target_id"] = _safe_str(service_result.get("provider_id"))
    service_action["target_name"] = _safe_str(service_result.get("provider_name"))
    service_action["provider_id"] = _safe_str(service_result.get("provider_id"))
    service_action["provider_name"] = _safe_str(service_result.get("provider_name"))
    service_action["source"] = "deterministic_service_resolver"

    metadata = _safe_dict(service_action.get("metadata"))
    metadata["player_input"] = _safe_str(player_input)
    metadata["service_result"] = service_result
    metadata["service_kind"] = _safe_str(service_result.get("service_kind"))
    metadata["service_status"] = _safe_str(service_result.get("status"))
    service_action["metadata"] = metadata
    return service_action


def service_semantic_action_from_result(
    player_input: str,
    service_result: Dict[str, Any],
    *,
    tick: int = 0,
    existing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    existing = _safe_dict(existing)
    service_result = _safe_dict(service_result)
    if not service_result.get("matched"):
        return existing

    service_kind = _safe_str(service_result.get("service_kind"))
    action_type = _safe_str(service_result.get("kind") or "service_inquiry")
    provider_id = _safe_str(service_result.get("provider_id"))
    provider_name = _safe_str(service_result.get("provider_name"))
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))

    if action_type == "service_purchase":
        activity_label = f"{service_kind}_purchase" if service_kind else "service_purchase"
    else:
        activity_label = f"{service_kind}_inquiry" if service_kind else "service_inquiry"

    semantic_id = _safe_str(existing.get("semantic_action_id"))
    if not semantic_id:
        seed = f"{_safe_str(player_input)}|{provider_id}|{service_kind}|{_safe_int(tick, 0)}"
        semantic_id = f"semantic_service_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"

    return {
        **existing,
        "semantic_action_id": semantic_id,
        "tick": _safe_int(existing.get("tick"), tick),
        "player_input": _safe_str(player_input),
        "action_type": action_type,
        "semantic_family": "commerce",
        "interaction_mode": "solo",
        "activity_label": activity_label,
        "target_id": provider_id,
        "target_name": provider_name,
        "secondary_actor_ids": [],
        "location_id": _safe_str(service_result.get("location_id")),
        "visibility": "local",
        "intensity": 1,
        "stakes": 1,
        "social_axes": [],
        "observer_hooks": [],
        "scene_impact": "none",
        "reason": _safe_str(service_result.get("status")),
        "summary": f"{provider_name} / {activity_label}".strip(" /"),
        "tags": sorted(
            {
                "commerce",
                action_type or "service_inquiry",
                service_kind or "service",
                "player_action",
            }
        ),
        "service_result": service_result,
        "selected_offer_id": selected_offer_id,
    }


def service_authoritative_result(
    simulation_state: Dict[str, Any],
    action: Dict[str, Any],
) -> Dict[str, Any]:
    action = _safe_dict(action)
    metadata = _safe_dict(action.get("metadata"))
    service_result = copy.deepcopy(_safe_dict(metadata.get("service_result")))
    service_kind = _safe_str(service_result.get("service_kind"))
    purchase = _safe_dict(service_result.get("purchase"))

    tick = _safe_int(_safe_dict(simulation_state).get("tick"), 0)
    purchase_application = {
        "applied": False,
        "blocked": bool(purchase.get("blocked")) if purchase else False,
        "blocked_reason": _safe_str(purchase.get("blocked_reason")) if purchase and purchase.get("blocked") else "",
        "currency_before": {},
        "currency_after": {},
        "items_added": [],
        "active_service": {},
        "rumor_added": {},
        "transaction_record": {},
        "memory_entry": {},
        "social_effects": {},
        "stock_update": {},
        "journal_entry": {},
        "service_world_event": {},
        "rumor_world_event": {},
        "journal_state": {},
        "world_event_state": {},
    }

    if (
        _safe_str(service_result.get("kind")) == "service_purchase"
        and purchase
        and _safe_str(service_result.get("selected_offer_id"))
    ):
        purchase_application = apply_service_purchase_result(
            simulation_state,
            service_result,
            tick=tick,
        )
        simulation_state = _safe_dict(purchase_application.get("simulation_state"))
        service_result = _safe_dict(purchase_application.get("service_result"))
        purchase = _safe_dict(service_result.get("purchase"))

    if service_result.get("matched"):
        living_world = apply_service_living_world_effects(
            simulation_state,
            service_result,
            purchase_application,
            tick=tick,
        )
        purchase_application["memory_entry"] = living_world.get("memory_entry") or {}
        purchase_application["social_effects"] = living_world.get("social_effects") or {}
        purchase_application["stock_update"] = living_world.get("stock_update") or {}
        purchase_application["rumor_added"] = living_world.get("rumor_added") or {}
        purchase_application["journal_entry"] = living_world.get("journal_entry") or {}
        purchase_application["service_world_event"] = living_world.get("service_world_event") or {}
        purchase_application["rumor_world_event"] = living_world.get("rumor_world_event") or {}
        purchase_application["journal_state"] = living_world.get("journal_state") or {}
        purchase_application["world_event_state"] = living_world.get("world_event_state") or {}
        canonical_rumor = _safe_dict(purchase_application.get("rumor_added"))
        transaction_record = _safe_dict(purchase_application.get("transaction_record"))
        if canonical_rumor and transaction_record:
            transaction_record["rumor_added"] = canonical_rumor
            transaction_record["rumor_id"] = _safe_str(canonical_rumor.get("rumor_id"))
            purchase_application["transaction_record"] = transaction_record

    pending_interaction = {}
    if (
        service_result.get("matched")
        and _safe_str(service_result.get("kind")) == "service_inquiry"
        and _safe_list(service_result.get("offers"))
    ):
        pending_interaction = record_service_offer(
            simulation_state,
            service_result,
            tick=tick,
        )
    elif purchase_application.get("applied"):
        pending_interaction = close_pending_service_offer(
            simulation_state,
            provider_id=_safe_str(service_result.get("provider_id")),
            service_kind=_safe_str(service_result.get("service_kind")),
            selected_offer_id=_safe_str(service_result.get("selected_offer_id")),
            tick=tick,
        )

    player_input = _safe_str(_safe_dict(action.get("metadata")).get("player_input"))
    duration_application = {}
    semantic_interaction = _safe_dict(_safe_dict(action.get("metadata")).get("semantic_interaction"))
    requested_duration_policy = _safe_str(semantic_interaction.get("duration_policy"))
    duration_requested = bool(requested_duration_policy) or any(
        term in player_input.casefold()
        for term in ("sleep", "rest", "until morning", "through the night", "overnight")
    )
    if (
        _safe_str(service_result.get("kind")) == "service_consumption"
        or (purchase_application.get("applied") and duration_requested)
    ):
        duration_application = apply_duration_action(
            simulation_state,
            player_input=player_input,
            service_kind=_safe_str(service_result.get("service_kind")),
            tick=tick,
            policy=requested_duration_policy,
        )

    quest_transition = {}
    if (
        service_result.get("matched")
        and _safe_str(service_result.get("kind")) == "service_inquiry"
        and _safe_str(service_result.get("service_kind")) == "paid_information"
    ):
        clue = clue_for_service_interaction(service_result, tick=tick)
        if clue:
            quest_transition = apply_quest_evidence(simulation_state, clue)

    blocked = bool(purchase_application.get("blocked"))
    blocked_reason = _safe_str(purchase_application.get("blocked_reason")) if blocked else ""
    applied = bool(purchase_application.get("applied"))

    result = {
        "ok": not blocked,
        "outcome": "blocked" if blocked else "success",
        "action_type": _safe_str(action.get("action_type") or service_result.get("kind")),
        "service_kind": service_kind,
        "target_name": _safe_str(service_result.get("provider_name") or action.get("target_name")),
        "target_id": _safe_str(service_result.get("provider_id") or action.get("target_id")),
        "blocked": blocked,
        "blocked_reason": blocked_reason,
        "purchase_applied": applied,
        "service_result": service_result,
        "action_metadata": {
            "transaction_kind": _safe_str(service_result.get("kind")),
            "service_kind": service_kind,
            "provider_id": _safe_str(service_result.get("provider_id")),
            "provider_name": _safe_str(service_result.get("provider_name")),
            "price_source": "deterministic_service_registry" if service_result.get("offers") else "",
        },
        "resource_changes": {
            "currency": {"gold": 0, "silver": 0, "copper": 0},
        },
        "service_application": {
            "applied": applied,
            "currency_before": purchase_application.get("currency_before") or {},
            "currency_after": purchase_application.get("currency_after") or {},
            "items_added": purchase_application.get("items_added") or [],
            "active_service": purchase_application.get("active_service") or {},
            "rumor_added": purchase_application.get("rumor_added") or {},
            "transaction_record": purchase_application.get("transaction_record") or {},
            "memory_entry": purchase_application.get("memory_entry") or {},
            "social_effects": purchase_application.get("social_effects") or {},
            "stock_update": purchase_application.get("stock_update") or {},
            "journal_entry": purchase_application.get("journal_entry") or {},
            "service_world_event": purchase_application.get("service_world_event") or {},
            "rumor_world_event": purchase_application.get("rumor_world_event") or {},
            "journal_state": purchase_application.get("journal_state") or {},
            "world_event_state": purchase_application.get("world_event_state") or {},
            "pending_interaction": pending_interaction,
            "duration_application": duration_application,
            "quest_transition": quest_transition,
        },
        "pending_interaction": pending_interaction,
        "duration_application": duration_application,
        "quest_transition": quest_transition,
        "transaction_record": purchase_application.get("transaction_record") or {},
        "memory_entry": purchase_application.get("memory_entry") or {},
        "social_effects": purchase_application.get("social_effects") or {},
        "stock_update": purchase_application.get("stock_update") or {},
        "rumor_added": purchase_application.get("rumor_added") or {},
        "journal_entry": purchase_application.get("journal_entry") or {},
        "service_world_event": purchase_application.get("service_world_event") or {},
        "rumor_world_event": purchase_application.get("rumor_world_event") or {},
        "journal_state": _safe_dict(simulation_state.get("journal_state")),
        "world_event_state": _safe_dict(simulation_state.get("world_event_state")),
        # Expose canonical living-world roots after deterministic service
        # effects have mutated simulation_state. This keeps response builders
        # and manual transcript tools from seeing stale copies.
        "memory_state": _safe_dict(simulation_state.get("memory_state")),
        "relationship_state": _safe_dict(simulation_state.get("relationship_state")),
        "npc_emotion_state": _safe_dict(simulation_state.get("npc_emotion_state")),
        "service_offer_state": _safe_dict(simulation_state.get("service_offer_state")),
        "living_world_debug": {
            "memory_entry": purchase_application.get("memory_entry") or {},
            "social_effects": purchase_application.get("social_effects") or {},
            "stock_update": purchase_application.get("stock_update") or {},
            "rumor_added": purchase_application.get("rumor_added") or {},
            "journal_entry": purchase_application.get("journal_entry") or {},
            "service_world_event": purchase_application.get("service_world_event") or {},
            "rumor_world_event": purchase_application.get("rumor_world_event") or {},
            "memory_state_count": len(
                _safe_list(
                    _safe_dict(simulation_state.get("memory_state")).get("service_memories")
                )
            ),
            "relationship_keys": sorted(_safe_dict(simulation_state.get("relationship_state")).keys()),
            "emotion_keys": sorted(_safe_dict(simulation_state.get("npc_emotion_state")).keys()),
            "offer_state_keys": sorted(
                _safe_dict(
                    _safe_dict(simulation_state.get("service_offer_state")).get("offers")
                ).keys()
            ),
        },
    }

    if purchase:
        result["resource_changes"] = _safe_dict(
            purchase.get("resource_changes")
            or {"currency": {"gold": 0, "silver": 0, "copper": 0}}
        )
        result["effect_result"] = {
            "items_added": _safe_list(
                _safe_dict(purchase.get("applied_effects")).get("items_added")
                or _safe_dict(purchase.get("effects")).get("items_added")
            ),
            "service_effects": _safe_dict(purchase.get("effects")),
            "applied_effects": _safe_dict(purchase.get("applied_effects")),
        }
        result["purchase_note"] = _safe_str(purchase.get("note"))

    return {
        "simulation_state": simulation_state,
        "result": result,
    }


def mirror_service_result(
    resolved_result: Dict[str, Any],
    action: Dict[str, Any],
    *,
    action_type: str,
    semantic_action_record: Dict[str, Any],
) -> Dict[str, Any]:
    resolved_result = _safe_dict(resolved_result)
    action = _safe_dict(action)
    service_metadata_result = _safe_dict(_safe_dict(action.get("metadata")).get("service_result"))
    if not service_metadata_result.get("matched"):
        return {
            "resolved_result": resolved_result,
            "action": action,
        }

    resolved_service_result = _safe_dict(resolved_result.get("service_result")) or service_metadata_result
    resolved_result["service_result"] = resolved_service_result
    if _safe_dict(resolved_result.get("service_application")).get("applied"):
        resolved_service_result["status"] = "purchased"
    resolved_result["action_type"] = _safe_str(resolved_service_result.get("kind") or action_type)
    resolved_result["service_kind"] = _safe_str(resolved_service_result.get("service_kind"))
    resolved_result["target_id"] = _safe_str(resolved_service_result.get("provider_id"))
    resolved_result["target_name"] = _safe_str(resolved_service_result.get("provider_name"))
    resolved_result["semantic_action"] = semantic_action_record

    if resolved_service_result is not service_metadata_result:
        action_metadata = _safe_dict(action.get("metadata"))
        action_metadata["service_result"] = resolved_service_result
        action["metadata"] = action_metadata

    return {
        "resolved_result": resolved_result,
        "action": action,
    }


def merge_service_result_into_contract_resolved(
    resolved_result: Dict[str, Any],
    contract_resolved: Dict[str, Any],
) -> Dict[str, Any]:
    resolved_result = _safe_dict(resolved_result)
    contract_resolved = _safe_dict(contract_resolved)
    for key in (
        "service_application",
        "transaction_record",
        "purchase_applied",
        "effect_result",
        "resource_changes",
        "blocked",
        "blocked_reason",
        "semantic_action",
    ):
        if key in resolved_result and key not in contract_resolved:
            contract_resolved[key] = resolved_result[key]

    if _safe_dict(resolved_result.get("service_result")).get("matched"):
        contract_resolved["service_result"] = _safe_dict(resolved_result.get("service_result"))
    return contract_resolved
