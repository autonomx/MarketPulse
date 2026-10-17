from __future__ import annotations

# RPG session runtime responsibility module.
# PR.1.16: deterministic combat reward narrative contract.
from app.apps.rpg.session.state_normalization import (
    _safe_dict as _safe_dict, _safe_int as _safe_int, _safe_list as _safe_list, _safe_str as _safe_str,
)
from typing import (
    Any as Any, Dict as Dict,
)

from .combat_xp_projection import _apply_combat_xp_surface as _base_apply_turn_authoritative

_COMBAT_REWARD_BASE_APPLY_TURN_AUTHORITATIVE = _base_apply_turn_authoritative


def _combat_reward_contract_first_xp_result(*values: Any) -> dict[str, Any]:
    for value in values:
        xp_result = _safe_dict(value)
        if not xp_result:
            continue
        if (
            _safe_int(xp_result.get("xp_awarded"), 0) > 0
            or _safe_int(xp_result.get("xp_gained"), 0) > 0
            or bool(xp_result.get("awarded"))
            or bool(xp_result.get("level_ups"))
        ):
            return xp_result
    return {}


def _combat_reward_contract_result_sources(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = _safe_dict(payload)
    result = _safe_dict(payload.get("result"))
    resolved_result = _safe_dict(payload.get("resolved_result")) or result
    narration_context = _safe_dict(payload.get("narration_context"))
    combat_result = (
        _safe_dict(payload.get("combat_result"))
        or _safe_dict(result.get("combat_result"))
        or _safe_dict(resolved_result.get("combat_result"))
        or _safe_dict(narration_context.get("combat_result"))
    )
    return result, resolved_result, narration_context, combat_result


def _combat_reward_contract_loot_lines(loot_result: dict[str, Any]) -> list[str]:
    loot_result = _safe_dict(loot_result)
    lines: list[str] = []
    for item in _safe_list(loot_result.get("items")):
        item = _safe_dict(item)
        item_id = _safe_str(item.get("item_id") or item.get("id") or item.get("name"))
        quantity = _safe_int(item.get("quantity"), 1)
        if item_id:
            lines.append(f"Loot: {quantity} x {item_id}")
    currency = _safe_dict(loot_result.get("currency"))
    for currency_id, amount in sorted(currency.items()):
        amount_int = _safe_int(amount, 0)
        if amount_int:
            lines.append(f"Loot: {amount_int} {currency_id}")
    return lines


def _build_combat_reward_narrative_contract(payload: dict[str, Any]) -> dict[str, Any]:
    result, resolved_result, narration_context, combat_result = _combat_reward_contract_result_sources(payload)
    loot_result = _safe_dict(combat_result.get("loot_result") or result.get("loot_result") or resolved_result.get("loot_result"))
    xp_result = _combat_reward_contract_first_xp_result(
        payload.get("xp_result"),
        result.get("xp_result"),
        resolved_result.get("xp_result"),
        narration_context.get("xp_result"),
        combat_result.get("xp_result"),
        loot_result.get("xp_result"),
    )
    if not xp_result and not loot_result:
        return {}

    xp_awarded = _safe_int(xp_result.get("xp_awarded"), _safe_int(xp_result.get("xp_gained"), 0))
    reward_lines: list[str] = []
    if xp_awarded > 0:
        reward_lines.append(f"XP +{xp_awarded}")
    reward_lines.extend(_combat_reward_contract_loot_lines(loot_result))

    allowed_claims = list(reward_lines)
    forbidden_claims = [
        "Do not invent XP, gold, items, levels, quest rewards, or loot not listed in allowed_reward_claims.",
        "Do not change awarded XP amounts or claim unlisted level-ups.",
    ]
    if xp_awarded <= 0:
        forbidden_claims.append("Do not claim XP was awarded for this combat turn.")

    return {
        "source": "deterministic_combat_reward_contract",
        "reward_lines": reward_lines,
        "allowed_reward_claims": allowed_claims,
        "forbidden_reward_claims": forbidden_claims,
        "xp_result": xp_result,
        "loot_result": loot_result,
    }


def _apply_combat_reward_narrative_contract(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(payload)
    contract = _build_combat_reward_narrative_contract(payload)
    if not contract:
        return payload

    result, resolved_result, narration_context, combat_result = _combat_reward_contract_result_sources(payload)
    result["combat_reward_narrative_contract"] = contract
    resolved_result["combat_reward_narrative_contract"] = contract
    narration_context["combat_reward_narrative_contract"] = contract
    narration_context["reward_lines"] = list(contract.get("reward_lines") or [])
    narration_context["allowed_reward_claims"] = list(contract.get("allowed_reward_claims") or [])

    forbidden_narration = _safe_list(narration_context.get("forbidden_narration"))
    for claim in _safe_list(contract.get("forbidden_reward_claims")):
        if claim not in forbidden_narration:
            forbidden_narration.append(claim)
    narration_context["forbidden_narration"] = forbidden_narration

    payload["combat_reward_narrative_contract"] = contract
    payload["result"] = result
    payload["resolved_result"] = resolved_result
    payload["narration_context"] = narration_context
    return payload


def _apply_combat_reward_narrative(
    session_id: str,
    player_input: str,
    action: dict[str, Any] | None = None,
    *,
    performance_override: dict[str, Any] | None = None,
    _base_authoritative: Any = _COMBAT_REWARD_BASE_APPLY_TURN_AUTHORITATIVE,
) -> dict[str, Any]:
    payload = _base_authoritative(
        session_id,
        player_input,
        action,
        performance_override=performance_override,
    )
    return _apply_combat_reward_narrative_contract(payload)


__all__ = ['_COMBAT_REWARD_BASE_APPLY_TURN_AUTHORITATIVE', '_apply_combat_reward_narrative', '_apply_combat_reward_narrative_contract', '_build_combat_reward_narrative_contract', '_combat_reward_contract_first_xp_result', '_combat_reward_contract_loot_lines', '_combat_reward_contract_result_sources']