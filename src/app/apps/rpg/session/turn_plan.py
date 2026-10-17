"""Pure turn-plan assembly for RPG response contracts."""
from __future__ import annotations

from typing import Any

from app.apps.rpg.session.world_reasoning_contracts import build_turn_plan

_PRESENTATION_BY_AUTHORITY = {
    "addressed_npc": "npc_dialogue",
    "present_npc": "npc_dialogue",
    "narrator": "narration",
    "world_simulation": "world_consequence",
    "deterministic_runtime": "runtime_action",
    "system": "system_clarification",
}


def build_turn_plan_for_response(
    *,
    intent_result: dict[str, Any] | None = None,
    world_assessment: dict[str, Any] | None = None,
    response_authority: dict[str, Any] | None = None,
    semantic_advisory: dict[str, Any] | None = None,
    candidate_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic plan for runtime and presentation orchestration."""

    intent = _d(intent_result)
    assessment = _d(world_assessment)
    authority = _d(response_authority)
    semantic = _d(semantic_advisory)
    candidate = _d(candidate_action)
    authority_source = _s(authority.get("source") or "system")
    runtime_required = _runtime_required(
        intent=intent,
        assessment=assessment,
        authority_source=authority_source,
        semantic=semantic,
        candidate=candidate,
    )
    state_mutation_allowed = bool(assessment.get("state_change_allowed")) and runtime_required
    presentation_type = _presentation_type(
        authority_source=authority_source,
        runtime_required=runtime_required,
        assessment=assessment,
    )
    return build_turn_plan(
        runtime_required=runtime_required,
        runtime_action=candidate if runtime_required else {},
        state_mutation_allowed=state_mutation_allowed,
        presentation_type=presentation_type,
        authority_source=authority_source,
        narrative_renderer_allowed=authority_source != "system" or presentation_type == "system_clarification",
        renderer_may_decide_truth=False,
        confidence=_confidence(intent=intent, assessment=assessment, authority=authority),
        metadata={
            "intent_kind": _s(intent.get("kind")),
            "actionability": _s(assessment.get("actionability")),
            "authority_reason": _s(_d(authority.get("metadata")).get("reason")),
        },
    )


def _runtime_required(
    *,
    intent: dict[str, Any],
    assessment: dict[str, Any],
    authority_source: str,
    semantic: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    if authority_source == "deterministic_runtime":
        return True
    if _s(assessment.get("actionability")) == "runtime_action":
        return True
    if bool(assessment.get("state_change_allowed")):
        return True
    if candidate:
        return True
    if bool(semantic.get("needs_runtime_resolution")) and not bool(semantic.get("stateful") is False):
        return True
    return False


def _presentation_type(*, authority_source: str, runtime_required: bool, assessment: dict[str, Any]) -> str:
    if runtime_required:
        return "runtime_action"
    actionability = _s(assessment.get("actionability"))
    if actionability == "reject":
        return "diegetic_reaction"
    return _PRESENTATION_BY_AUTHORITY.get(authority_source, "system_clarification")


def _confidence(*, intent: dict[str, Any], assessment: dict[str, Any], authority: dict[str, Any]) -> str:
    values = [_s(authority.get("confidence")), _s(assessment.get("confidence")), _s(intent.get("confidence"))]
    if "low" in values:
        return "low"
    if "unknown" in values or not any(values):
        return "unknown"
    if "medium" in values:
        return "medium"
    return "high"


def _d(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _s(value: Any) -> str:
    return str(value) if value is not None else ""
