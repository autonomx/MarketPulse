"""Core contracts for world reasoning, response authority, and turn planning.

These helpers are intentionally pure and deterministic. They describe system
decisions and presentation constraints; they do not execute runtime mechanics or
call language models.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

CONFIDENCE_VALUES = ("high", "medium", "low", "unknown")

AUTHORITY_SOURCES = (
    "narrator",
    "addressed_npc",
    "present_npc",
    "world_simulation",
    "deterministic_runtime",
    "system",
)

ACTIONABILITY_VALUES = (
    "runtime_action",
    "respond_only",
    "clarify",
    "reject",
    "observe",
    "unknown",
)

PRESENTATION_TYPES = (
    "npc_dialogue",
    "narration",
    "runtime_action",
    "system_clarification",
    "diegetic_reaction",
    "world_consequence",
    "none",
)

_ALLOWED_CLAIMS_BY_AUTHORITY = {
    "addressed_npc": (
        "belief",
        "opinion",
        "rumor",
        "uncertainty",
        "refusal",
        "known_personal_memory",
    ),
    "present_npc": (
        "belief",
        "opinion",
        "rumor",
        "uncertainty",
        "refusal",
        "known_personal_memory",
    ),
    "narrator": (
        "visible_scene_description",
        "observable_consequence",
        "environmental_detail",
    ),
    "world_simulation": (
        "visible_world_consequence",
        "plausibility_boundary",
        "environmental_state",
    ),
    "deterministic_runtime": (
        "state_mutation",
        "currency_transfer",
        "inventory_change",
        "combat_resolution",
        "quest_update",
    ),
    "system": (
        "parse_clarification",
        "input_error",
        "unsupported_format",
    ),
}

_FORBIDDEN_CLAIMS_BY_AUTHORITY = {
    "addressed_npc": (
        "omniscient_narration",
        "hidden_world_state",
        "state_mutation",
        "other_npc_private_thoughts",
    ),
    "present_npc": (
        "omniscient_narration",
        "hidden_world_state",
        "state_mutation",
        "other_npc_private_thoughts",
    ),
    "narrator": (
        "secret_npc_thoughts",
        "unverified_player_history",
        "mechanical_state_change",
        "hidden_quest_state",
    ),
    "world_simulation": (
        "private_npc_dialogue",
        "unverified_player_history",
        "inventory_mutation_without_runtime",
    ),
    "deterministic_runtime": (
        "invented_flavor_not_derived_from_result",
        "unverified_dialogue",
        "retconned_intent",
    ),
    "system": (
        "diegetic_fact_creation",
        "state_mutation",
        "npc_private_thoughts",
    ),
}


def normalize_confidence(value: Any, *, default: str = "unknown") -> str:
    candidate = _s(value).casefold().strip()
    return candidate if candidate in CONFIDENCE_VALUES else default


def normalize_authority_source(value: Any, *, default: str = "system") -> str:
    candidate = _s(value).casefold().strip()
    return candidate if candidate in AUTHORITY_SOURCES else default


def normalize_presentation_type(value: Any, *, default: str = "system_clarification") -> str:
    candidate = _s(value).casefold().strip()
    return candidate if candidate in PRESENTATION_TYPES else default


def build_intent_result(
    *,
    kind: str = "unknown",
    target_id: str = "",
    target_name: str = "",
    confidence: str = "unknown",
    legacy_category: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format_version": "intent_result_v1",
        "kind": _s(kind) or "unknown",
        "target_id": _s(target_id),
        "target_name": _s(target_name),
        "confidence": normalize_confidence(confidence),
        "legacy_category": _s(legacy_category),
        "metadata": deepcopy(_d(metadata)),
    }


def build_world_assessment(
    *,
    plausibility: str = "unknown",
    verification: str = "unknown",
    actionability: str = "unknown",
    state_change_allowed: bool = False,
    confidence: str = "unknown",
    knowledge_scope: str = "unknown",
    physical_result: str = "unknown",
    social_result: str = "unknown",
    lore_result: str = "unknown",
    constraints: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action = _s(actionability).casefold().strip()
    if action not in ACTIONABILITY_VALUES:
        action = "unknown"
    return {
        "format_version": "world_assessment_v1",
        "plausibility": _s(plausibility) or "unknown",
        "verification": _s(verification) or "unknown",
        "actionability": action,
        "state_change_allowed": bool(state_change_allowed),
        "confidence": normalize_confidence(confidence),
        "knowledge_scope": _s(knowledge_scope) or "unknown",
        "physical_result": _s(physical_result) or "unknown",
        "social_result": _s(social_result) or "unknown",
        "lore_result": _s(lore_result) or "unknown",
        "constraints": deepcopy(_d(constraints)),
        "metadata": deepcopy(_d(metadata)),
    }


def build_response_authority(
    *,
    source: str = "system",
    authority_id: str = "",
    display_name: str = "",
    confidence: str = "unknown",
    allowed_claims: list[str] | tuple[str, ...] | None = None,
    forbidden_claims: list[str] | tuple[str, ...] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    authority = normalize_authority_source(source)
    allowed = tuple(allowed_claims) if allowed_claims is not None else _ALLOWED_CLAIMS_BY_AUTHORITY.get(authority, ())
    forbidden = tuple(forbidden_claims) if forbidden_claims is not None else _FORBIDDEN_CLAIMS_BY_AUTHORITY.get(authority, ())
    return {
        "format_version": "response_authority_v1",
        "source": authority,
        "id": _s(authority_id),
        "display_name": _s(display_name),
        "confidence": normalize_confidence(confidence),
        "allowed_claims": [_s(item) for item in allowed if _s(item)],
        "forbidden_claims": [_s(item) for item in forbidden if _s(item)],
        "metadata": deepcopy(_d(metadata)),
    }


def build_turn_plan(
    *,
    runtime_required: bool = False,
    runtime_action: dict[str, Any] | None = None,
    state_mutation_allowed: bool = False,
    presentation_type: str = "system_clarification",
    authority_source: str = "system",
    narrative_renderer_allowed: bool = True,
    renderer_may_decide_truth: bool = False,
    confidence: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format_version": "turn_plan_v1",
        "runtime_required": bool(runtime_required),
        "runtime_action": deepcopy(_d(runtime_action)),
        "state_mutation_allowed": bool(state_mutation_allowed),
        "presentation_type": normalize_presentation_type(presentation_type),
        "authority_source": normalize_authority_source(authority_source),
        "narrative_renderer_allowed": bool(narrative_renderer_allowed),
        "renderer_may_decide_truth": bool(renderer_may_decide_truth),
        "confidence": normalize_confidence(confidence),
        "metadata": deepcopy(_d(metadata)),
    }


def build_reasoning_trace(
    *,
    intent_result: dict[str, Any] | None = None,
    world_assessment: dict[str, Any] | None = None,
    response_authority: dict[str, Any] | None = None,
    turn_plan: dict[str, Any] | None = None,
    input_source: str = "semantic_classifier",
    assessment_source: str = "world_reasoner",
    authority_source: str = "response_authority",
) -> dict[str, Any]:
    intent = _d(intent_result)
    assessment = _d(world_assessment)
    authority = _d(response_authority)
    plan = _d(turn_plan)
    trace = {
        "format_version": "reasoning_trace_v1",
        "input_classification": {
            "source": _s(input_source),
            "intent_kind": _s(intent.get("kind") or "unknown"),
            "legacy_category": _s(intent.get("legacy_category")),
            "confidence": normalize_confidence(intent.get("confidence")),
        },
        "entity_resolution": {
            "target_id": _s(intent.get("target_id")),
            "target_name": _s(intent.get("target_name")),
            "confidence": normalize_confidence(intent.get("confidence")),
        },
        "world_assessment": {
            "source": _s(assessment_source),
            "verification": _s(assessment.get("verification") or "unknown"),
            "plausibility": _s(assessment.get("plausibility") or "unknown"),
            "actionability": _s(assessment.get("actionability") or "unknown"),
            "state_change_allowed": bool(assessment.get("state_change_allowed")),
            "confidence": normalize_confidence(assessment.get("confidence")),
        },
        "authority_resolution": {
            "source": _s(authority_source),
            "authority": normalize_authority_source(authority.get("source")),
            "authority_id": _s(authority.get("id")),
            "display_name": _s(authority.get("display_name")),
            "confidence": normalize_confidence(authority.get("confidence")),
        },
        "runtime_decision": {
            "runtime_required": bool(plan.get("runtime_required")),
            "state_mutation_allowed": bool(plan.get("state_mutation_allowed")),
            "decision": "required" if bool(plan.get("runtime_required")) else "not_required",
        },
        "presentation_decision": {
            "presentation_type": normalize_presentation_type(plan.get("presentation_type")),
            "renderer_may_decide_truth": bool(plan.get("renderer_may_decide_truth")),
        },
    }
    trace["events"] = build_reasoning_trace_events(trace)
    return trace


def build_reasoning_trace_events(trace: dict[str, Any]) -> list[str]:
    trace = _d(trace)
    intent = _d(trace.get("input_classification"))
    entity = _d(trace.get("entity_resolution"))
    assessment = _d(trace.get("world_assessment"))
    authority = _d(trace.get("authority_resolution"))
    runtime = _d(trace.get("runtime_decision"))
    presentation = _d(trace.get("presentation_decision"))
    events = [
        f"intent={_s(intent.get('intent_kind') or 'unknown')}",
        f"legacy_category={_s(intent.get('legacy_category') or 'unknown')}",
    ]
    if entity.get("target_id"):
        events.append(f"target={_s(entity.get('target_id'))}")
    events.extend(
        [
            f"verification={_s(assessment.get('verification') or 'unknown')}",
            f"plausibility={_s(assessment.get('plausibility') or 'unknown')}",
            f"actionability={_s(assessment.get('actionability') or 'unknown')}",
            f"authority={_s(authority.get('authority') or 'system')}",
            f"runtime_required={str(bool(runtime.get('runtime_required'))).lower()}",
            f"state_mutation_allowed={str(bool(runtime.get('state_mutation_allowed'))).lower()}",
            f"presentation={_s(presentation.get('presentation_type') or 'system_clarification')}",
        ]
    )
    return events


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
