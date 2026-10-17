"""Adapt interpretive adjudication payloads into world reasoning contracts."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.session.world_reasoning_contracts import build_intent_result, build_world_assessment

_CLAIM_INTENTS = {
    "unverified_player_claim",
    "unverified_debt_claim",
    "memory_claim",
    "lore_conflict_claim",
}

_KIND_BY_FAMILY = {
    "observation": "observation",
    "npc_request": "request",
    "claim": "claim",
    "social": "social_probe",
    "unsupported_mechanic": "mechanic_candidate",
    "diegetic_noop": "diegetic_action",
    "hypothetical": "hypothetical",
}


def build_world_reasoning_from_interpretive_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return contract-shaped intent and assessment for an interpretive result."""

    top = _d(result)
    payload = _d(top.get("result") or top.get("resolved_result") or top)
    semantic = _d(top.get("first_call_semantic_advisory"))
    grounding = _d(top.get("grounding_validation") or payload.get("grounding_validation"))
    constraints = _d(
        payload.get("interpretive_fact_constraints")
        or top.get("interpretive_fact_constraints")
        or grounding.get("interpretive_fact_constraints")
    )
    intent = _s(payload.get("interpretive_intent") or top.get("interpretive_intent") or constraints.get("intent"))
    family = _s(payload.get("interpretive_intent_family") or constraints.get("intent_family"))
    target_id, target_name = _target_from_result(top=top, payload=payload, semantic=semantic, grounding=grounding)

    intent_result = build_intent_result(
        kind=_KIND_BY_FAMILY.get(family, intent or "unknown"),
        target_id=target_id,
        target_name=target_name,
        confidence=_confidence_for_intent(intent),
        legacy_category=intent,
        metadata={"interpretive_intent_family": family, "source": _s(top.get("source") or payload.get("source"))},
    )
    world_assessment = build_world_assessment(
        plausibility=_plausibility_for_intent(intent),
        verification=_verification_for_intent(intent),
        actionability=_actionability_for_intent(intent),
        state_change_allowed=bool(constraints.get("may_mutate_state")) and not bool(payload.get("no_state_mutation")),
        confidence=_confidence_for_intent(intent),
        knowledge_scope="addressed_npc" if target_id.startswith("npc:") else "world_visible",
        physical_result=_physical_result_for_intent(intent),
        social_result=_social_result_for_intent(intent),
        lore_result=_lore_result_for_intent(intent),
        constraints=constraints,
        metadata={
            "legacy_family": family,
            "no_state_mutation": bool(payload.get("no_state_mutation") or top.get("no_state_mutation")),
            "needs_runtime_resolution": bool(payload.get("needs_runtime_resolution") or top.get("needs_runtime_resolution")),
        },
    )
    return {
        "format_version": "world_reasoning_adapter_v1",
        "intent_result": intent_result,
        "world_assessment": world_assessment,
    }


def _target_from_result(
    *,
    top: dict[str, Any],
    payload: dict[str, Any],
    semantic: dict[str, Any],
    grounding: dict[str, Any],
) -> tuple[str, str]:
    npc = _d(top.get("npc") or payload.get("npc"))
    target_id = _s(semantic.get("target_id"))
    if not target_id:
        addressed_ids = grounding.get("first_call_addressed_npc_ids")
        if isinstance(addressed_ids, list) and addressed_ids:
            target_id = _s(addressed_ids[0])
    target_name = _s(semantic.get("target_name") or npc.get("speaker"))
    if target_id and not target_id.startswith("npc:") and target_name:
        target_id = f"npc:{target_id}"
    return target_id, target_name


def _confidence_for_intent(intent: str) -> str:
    return "high" if intent else "unknown"


def _verification_for_intent(intent: str) -> str:
    if intent == "hypothetical_counterfactual":
        return "counterfactual"
    if intent in _CLAIM_INTENTS:
        return "unverified"
    if intent == "observation_request":
        return "observable"
    if intent == "social_probe":
        return "subjective"
    if intent == "unsupported_mechanic_request":
        return "not_resolved"
    if intent == "npc_capability_request":
        return "requires_world_validation"
    return "unknown"


def _plausibility_for_intent(intent: str) -> str:
    if intent == "hypothetical_counterfactual":
        return "possible"
    if intent == "lore_conflict_claim":
        return "contradictory"
    if intent in {"npc_capability_request", "unsupported_mechanic_request"}:
        return "unlikely"
    if intent in {"social_probe", "observation_request", "unsupported_but_diegetic_action"}:
        return "possible"
    if intent in _CLAIM_INTENTS:
        return "unverified"
    return "unknown"


def _actionability_for_intent(intent: str) -> str:
    if intent == "observation_request":
        return "observe"
    if intent == "unsupported_mechanic_request":
        return "reject"
    return "respond_only" if intent else "unknown"


def _physical_result_for_intent(intent: str) -> str:
    if intent == "hypothetical_counterfactual":
        return "not_applied"
    return "unlikely" if intent in {"npc_capability_request", "unsupported_mechanic_request"} else "none"


def _social_result_for_intent(intent: str) -> str:
    if intent == "hypothetical_counterfactual":
        return "speculative_answer"
    if intent == "social_probe":
        return "relationship_question"
    if intent in _CLAIM_INTENTS:
        return "skeptical"
    if intent == "npc_capability_request":
        return "boundary_set"
    return "none"


def _lore_result_for_intent(intent: str) -> str:
    if intent == "hypothetical_counterfactual":
        return "not_asserted"
    if intent == "lore_conflict_claim":
        return "inconsistent_or_unverified"
    return "not_applicable" if intent else "unknown"


def _d(value: Any) -> dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
