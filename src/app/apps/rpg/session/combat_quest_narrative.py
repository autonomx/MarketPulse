from __future__ import annotations

# RPG session runtime responsibility module.
# PR.1.19: deterministic combat quest sync narrative contract.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_list as _safe_list, _safe_str as _safe_str,
)
from typing import (
    Any as Any, Dict as Dict,
)

from .combat_quest_progress import _apply_turn_with_combat_quest_sync as _base_apply_turn_authoritative

_COMBAT_QUEST_BASE_APPLY_TURN_AUTHORITATIVE = _base_apply_turn_authoritative


def _combat_quest_contract_sources(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = _safe_dict(payload)
    result = _safe_dict(payload.get("result"))
    resolved_result = _safe_dict(payload.get("resolved_result")) or result
    narration_context = _safe_dict(payload.get("narration_context"))
    sync_result = _safe_dict(payload.get("combat_quest_sync_result")) or _safe_dict(result.get("combat_quest_sync_result")) or _safe_dict(resolved_result.get("combat_quest_sync_result")) or _safe_dict(narration_context.get("combat_quest_sync_result"))
    return result, resolved_result, narration_context, sync_result


def _combat_quest_contract_lines(sync_result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for objective in _safe_list(_safe_dict(sync_result).get("updated_objectives")):
        objective = _safe_dict(objective)
        objective_id = _safe_str(objective.get("objective_id") or "combat objective")
        quest_id = _safe_str(objective.get("quest_id"))
        status = _safe_str(objective.get("status") or "updated")
        if quest_id:
            lines.append(f"Quest progress: {quest_id} / {objective_id} -> {status}")
        else:
            lines.append(f"Quest progress: {objective_id} -> {status}")
    for quest_id in _safe_list(_safe_dict(sync_result).get("completed_quests")):
        quest_id = _safe_str(quest_id)
        if quest_id:
            lines.append(f"Quest completed: {quest_id}")
    return lines


def _build_combat_quest_narrative_contract(payload: dict[str, Any]) -> dict[str, Any]:
    _, _, _, sync_result = _combat_quest_contract_sources(payload)
    if not sync_result:
        return {}
    quest_lines = _combat_quest_contract_lines(sync_result)
    if not quest_lines:
        return {}
    return {
        "source": "deterministic_combat_quest_sync_contract",
        "quest_progress_lines": quest_lines,
        "allowed_quest_claims": list(quest_lines),
        "forbidden_quest_claims": [
            "Do not invent quest objectives, quest completions, faction progress, rewards, or story outcomes not listed in allowed_quest_claims.",
            "Do not claim unmatched objectives were completed by this combat turn.",
            "Do not rename quest IDs, objective IDs, or target IDs from the deterministic quest sync result.",
        ],
        "combat_quest_sync_result": sync_result,
    }


def _apply_combat_quest_narrative_contract(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    contract = _build_combat_quest_narrative_contract(payload)
    if not contract:
        return payload
    result, resolved_result, narration_context, _ = _combat_quest_contract_sources(payload)
    result["combat_quest_narrative_contract"] = contract
    resolved_result["combat_quest_narrative_contract"] = contract
    narration_context["combat_quest_narrative_contract"] = contract
    narration_context["quest_progress_lines"] = list(contract.get("quest_progress_lines") or [])
    narration_context["allowed_quest_claims"] = list(contract.get("allowed_quest_claims") or [])
    forbidden = _safe_list(narration_context.get("forbidden_narration"))
    for claim in _safe_list(contract.get("forbidden_quest_claims")):
        if claim not in forbidden:
            forbidden.append(claim)
    narration_context["forbidden_narration"] = forbidden
    payload["combat_quest_narrative_contract"] = contract
    payload["result"] = result
    payload["resolved_result"] = resolved_result
    payload["narration_context"] = narration_context
    return payload


def _apply_combat_quest_narrative(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _COMBAT_QUEST_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(session_id, player_input, action, performance_override=performance_override)
    return _apply_combat_quest_narrative_contract(payload)


__all__ = ['_COMBAT_QUEST_BASE_APPLY_TURN_AUTHORITATIVE', '_apply_combat_quest_narrative', '_apply_combat_quest_narrative_contract', '_build_combat_quest_narrative_contract', '_combat_quest_contract_lines', '_combat_quest_contract_sources']