from __future__ import annotations

from typing import Any, Dict, List

from app.apps.rpg.narration.quality import normalize_text
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def validate_narration_contradictions(
    final_result: Dict[str, Any],
) -> List[str]:
    warnings: List[str] = []
    final_result = _safe_dict(final_result)

    narration = normalize_text(
        final_result.get("final_narration")
        or final_result.get("narration")
        or final_result.get("summary")
        or _safe_dict(final_result.get("result")).get("final_narration")
        or _safe_dict(final_result.get("result")).get("narration")
    )

    resolved_result = _safe_dict(final_result.get("resolved_result"))
    combat_result = _safe_dict(final_result.get("combat_result") or resolved_result.get("combat_result"))
    npc_decision = _safe_dict(final_result.get("npc_backbone_decision") or resolved_result.get("npc_backbone_decision"))

    if combat_result:
        hit = combat_result.get("hit")
        defeated = combat_result.get("defeated")
        if hit is True and "miss" in narration:
            warnings.append("narration_contradicts_combat_hit")
        if hit is False and any(token in narration for token in [" hit ", "strike lands", "wound", "damage"]):
            warnings.append("narration_contradicts_combat_miss")
        if defeated is True and any(token in narration for token in ["still standing", "shrugs it off", "unharmed"]):
            warnings.append("narration_contradicts_defeat")

    if npc_decision:
        decision = _safe_str(npc_decision.get("decision"))
        if decision in {"refuse", "escalate"}:
            forbidden_acceptance = [
                "agrees",
                "gives you",
                "hands you",
                "lets you have",
                "shows you to your room",
                "you receive a room",
                "the room is yours",
            ]
            if any(token in narration for token in forbidden_acceptance):
                warnings.append("narration_contradicts_npc_refusal")
        if decision == "accept":
            if any(token in narration for token in ["refuses", "will not", "won't", "denies you"]):
                warnings.append("narration_contradicts_npc_acceptance")

    # Generic inventory/lock wording hooks for later L-bundle interaction resolver.
    reason = normalize_text(
        final_result.get("outcome")
        or resolved_result.get("outcome")
        or resolved_result.get("reason")
    )
    interaction_result = _safe_dict(
        final_result.get("general_interaction_result")
        or final_result.get("interaction_result")
        or resolved_result.get("general_interaction_result")
        or resolved_result.get("interaction_result")
    )
    if interaction_result:
        reason = normalize_text(interaction_result.get("reason") or reason)

    if reason == "missing_required_item" and any(token in narration for token in ["opens", "unlocks", "clicks open"]):
        warnings.append("narration_contradicts_missing_required_item")
    if reason in {"target_locked", "container_closed"} and any(token in narration for token in ["opens", "inside you find", "you take"]):
        warnings.append("narration_contradicts_locked_or_closed_object")
    if reason == "container_closed" and any(token in narration for token in ["take", "pocket", "loot"]):
        warnings.append("narration_contradicts_closed_container_take")

    return list(dict.fromkeys(warnings))