from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _lower(value: Any) -> str:
    return _safe_str(value).strip().lower()


def _relationship_score(
    simulation_state: Dict[str, Any],
    npc_id: str,
    runtime_state: Dict[str, Any] | None = None,
) -> int:
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)

    candidates = [
        _safe_dict(simulation_state.get("relationships")),
        _safe_dict(simulation_state.get("relationship_state")),
        _safe_dict(_safe_dict(simulation_state.get("social_state")).get("relationships")),
        _safe_dict(_safe_dict(simulation_state.get("interaction_state")).get("relationships")),
        _safe_dict(_safe_dict(simulation_state.get("interaction_state")).get("relationship_state")),
        _safe_dict(_safe_dict(_safe_dict(simulation_state.get("interaction_state")).get("social_state")).get("relationships")),
        _safe_dict(runtime_state.get("relationships")),
        _safe_dict(runtime_state.get("relationship_state")),
        _safe_dict(_safe_dict(runtime_state.get("social_state")).get("relationships")),
    ]

    npc_aliases = {
        npc_id,
        npc_id.lower(),
        npc_id.replace("npc:", ""),
        npc_id.replace("npc:", "").lower(),
        "Bran",
        "bran",
        "npc:Bran",
        "npc:bran",
    }

    best = 0
    for relationships in candidates:
        for key in npc_aliases:
            npc_rel = _safe_dict(relationships.get(key))
            if not npc_rel:
                continue
            score = _safe_int(
                npc_rel.get("trust")
                if npc_rel.get("trust") is not None
                else npc_rel.get("relationship")
                if npc_rel.get("relationship") is not None
                else npc_rel.get("score"),
                0,
            )
            if abs(score) > abs(best):
                best = score

    return best


def _recent_negative_memory_count(simulation_state: Dict[str, Any], npc_id: str) -> int:
    simulation_state = _safe_dict(simulation_state)
    memories = []
    memories.extend(_safe_list(simulation_state.get("npc_memories")))
    memories.extend(_safe_list(_safe_dict(simulation_state.get("memory_state")).get("npc_memories")))
    memories.extend(_safe_list(_safe_dict(simulation_state.get("interaction_state")).get("npc_memories")))
    count = 0
    for memory in memories:
        memory = _safe_dict(memory)
        if _safe_str(memory.get("npc_id")) != npc_id:
            continue
        sentiment = _safe_int(memory.get("sentiment"), 0)
        if sentiment < 0:
            count += 1
    return count


def _player_has_paid_for_service(simulation_state: Dict[str, Any], service_id: str) -> bool:
    service_state = _safe_dict(_safe_dict(simulation_state).get("service_state"))
    paid_services = set(_safe_list(service_state.get("paid_services")))
    return service_id in paid_services


def detect_social_request(player_input: str) -> Dict[str, Any]:
    text = _lower(player_input)

    if any(phrase in text for phrase in ["free room", "room for free", "give me a room", "rent a room", "room to rent", "better room"]):
        return {
            "detected": True,
            "request_type": "service_room",
            "service_id": "service:room",
            "npc_id": "npc:bran",
            "target": "room",
        }

    if any(phrase in text for phrase in ["give me", "hand over", "for free"]):
        return {
            "detected": True,
            "request_type": "demand_free_goods",
            "npc_id": "npc:bran",
            "target": "goods",
        }

    if any(phrase in text for phrase in ["threaten", "intimidate", "punch", "attack bran"]):
        return {
            "detected": True,
            "request_type": "threat",
            "npc_id": "npc:bran",
            "target": "npc",
        }

    return {}


def resolve_npc_backbone_decision(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_input: str,
    *,
    npc_id: str = "npc:bran",
) -> Dict[str, Any]:
    simulation_state = _safe_dict(simulation_state)
    request = detect_social_request(player_input)
    if not request:
        return {
            "detected": False,
            "npc_id": npc_id,
            "decision": "",
            "reason": "no_social_request",
        }

    npc_id = _safe_str(request.get("npc_id") or npc_id)
    request_type = _safe_str(request.get("request_type"))
    trust = _relationship_score(simulation_state, npc_id, runtime_state)
    negative_memories = _recent_negative_memory_count(simulation_state, npc_id)

    decision = "refuse"
    reason = "default_boundary"
    tone = "firm"
    hard_boundary = True
    escalation = ""
    may_offer_alternative = ""

    if request_type == "service_room":
        service_id = _safe_str(request.get("service_id"))
        paid = _player_has_paid_for_service(simulation_state, service_id)

        if paid:
            decision = "accept"
            reason = "service_paid"
            tone = "businesslike"
            hard_boundary = False
        elif trust >= 50 and negative_memories == 0:
            decision = "negotiate"
            reason = "trusted_but_unpaid"
            tone = "cautious"
            hard_boundary = False
            may_offer_alternative = "offer_discount_or_request_payment"
        else:
            decision = "refuse"
            reason = "unpaid_service"
            tone = "firm"
            hard_boundary = True
            may_offer_alternative = "pay_standard_price"

    elif request_type == "demand_free_goods":
        if trust >= 70 and negative_memories == 0:
            decision = "negotiate"
            reason = "high_trust_but_unreasonable_request"
            tone = "wary"
            hard_boundary = False
            may_offer_alternative = "small_discount"
        else:
            decision = "refuse"
            reason = "unreasonable_demand"
            tone = "firm"
            hard_boundary = True
            may_offer_alternative = "pay_or_trade"

    elif request_type == "threat":
        decision = "escalate"
        reason = "player_threatened_npc"
        tone = "alarmed"
        hard_boundary = True
        escalation = "call_guards_if_threat_continues"

    return {
        "detected": True,
        "npc_id": npc_id,
        "request_type": request_type,
        "decision": decision,
        "accepted": decision == "accept",
        "reason": reason,
        "tone": tone,
        "hard_boundary": hard_boundary,
        "escalation": escalation,
        "may_offer_alternative": may_offer_alternative,
        "relationship_score": trust,
        "negative_memory_count": negative_memories,
        "forbidden_outcomes": _forbidden_outcomes_for_decision(decision, request_type),
    }


def _forbidden_outcomes_for_decision(decision: str, request_type: str) -> List[str]:
    decision = _safe_str(decision)
    request_type = _safe_str(request_type)
    forbidden: List[str] = []

    if decision in {"refuse", "escalate"}:
        if request_type == "service_room":
            forbidden.extend([
                "Do not say the NPC gives the player a room.",
                "Do not say the player receives lodging.",
                "Do not say payment was accepted unless service_state says so.",
            ])
        if request_type == "demand_free_goods":
            forbidden.extend([
                "Do not say the NPC gives free goods.",
                "Do not add items to inventory.",
            ])
    if decision == "escalate":
        forbidden.append("Do not make the NPC calm or agreeable unless a later turn resolves de-escalation.")

    return forbidden