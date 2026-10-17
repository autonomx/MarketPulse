from __future__ import annotations

"""Runtime presentation grounding guardrails.

These helpers are presentation-only: they never create authoritative outcomes.
They remove or soften LLM claims that are not backed by the turn contract.
"""

import re
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


_COMBAT_CLAIM_RE = re.compile(
    r"\b(hit|hits|wound|wounds|wounded|injur|blood|bleed|bleeding|kill|kills|killed|dead|death|dies|defeat|defeated|victory|slain|damage|knockdown|knocked down)\b",
    re.IGNORECASE,
)


def _turn_contract(narration_context: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(
        narration_context.get("turn_contract")
        or _safe_dict(narration_context.get("resolved_result")).get("turn_contract")
    )


def has_authoritative_combat_support(narration_context: Dict[str, Any]) -> bool:
    ctx = _safe_dict(narration_context)
    contract = _turn_contract(ctx)
    resolved = _safe_dict(ctx.get("resolved_result"))
    combat_candidates = (
        ctx.get("combat_result"),
        resolved.get("combat_result"),
        contract.get("combat_result"),
        _safe_dict(contract.get("state_delta")).get("combat"),
        ctx.get("combat_state"),
    )
    for candidate in combat_candidates:
        if isinstance(candidate, dict) and candidate:
            if candidate.get("damage") or candidate.get("hit") or candidate.get("defeated") or candidate.get("injury") or candidate.get("state"):
                return True
            if _safe_str(candidate.get("status")) in {"hit", "damage_applied", "defeated", "resolved"}:
                return True
    return False


def contains_unsupported_combat_claim(text: Any, narration_context: Dict[str, Any]) -> bool:
    value = _safe_str(text)
    if not value or has_authoritative_combat_support(narration_context):
        return False
    return bool(_COMBAT_CLAIM_RE.search(value))


def sanitize_unsupported_combat_text(text: Any, narration_context: Dict[str, Any]) -> str:
    value = _safe_str(text).strip()
    if not contains_unsupported_combat_claim(value, narration_context):
        return value

    sentences = re.split(r"(?<=[.!?])\s+", value)
    kept: List[str] = []
    for sentence in sentences:
        if sentence and not _COMBAT_CLAIM_RE.search(sentence):
            kept.append(sentence)
    if kept:
        return " ".join(kept).strip()
    return "The confrontation remains tense, but no injury is resolved."


def sanitize_unsupported_combat_payload(payload: Dict[str, Any], narration_context: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(_safe_dict(payload))
    payload["narration"] = sanitize_unsupported_combat_text(payload.get("narration"), narration_context)
    payload["action"] = sanitize_unsupported_combat_text(payload.get("action"), narration_context)
    npc = dict(_safe_dict(payload.get("npc")))
    if npc:
        npc["line"] = sanitize_unsupported_combat_text(npc.get("line"), narration_context)
        payload["npc"] = npc
    return payload


def build_runtime_presentation_guardrails_block(narration_context: Dict[str, Any]) -> str:
    combat_supported = has_authoritative_combat_support(_safe_dict(narration_context))
    return (
        "RUNTIME PRESENTATION GUARDRAILS:\n"
        "- Simulation/turn_contract is truth; narration is presentation only.\n"
        "- Do not invent rewards, payment, items, travel, quest completion, injuries, hits, deaths, or victory.\n"
        f"- Authoritative combat support this turn: {bool(combat_supported)}.\n"
        "- If combat support is false, describe tension/positioning only; do not claim damage, wounds, defeat, or death.\n"
        "- NPC profile and memory may shape tone only; they cannot create current-turn outcomes.\n"
    )
