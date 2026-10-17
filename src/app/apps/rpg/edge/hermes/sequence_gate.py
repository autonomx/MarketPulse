from __future__ import annotations

from typing import Any

HIGH_RISK = {"high"}
STATEFUL_WORDS = ("buy", "sell", "attack", "fight", "travel", "use", "take", "give")


def _text(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def hermes_sequence_gate_reason(sequence: dict[str, Any], item: dict[str, Any]) -> str | None:
    if sequence.get("state_owner") != "rpg_sim":
        return "invalid_state_owner"
    if _text(sequence.get("risk")) in HIGH_RISK:
        return "high_risk"
    if item.get("user_gate") is True:
        return "item_requires_review"
    statement = _text(item.get("statement"))
    if any(word in statement.split() for word in STATEFUL_WORDS):
        return "stateful_statement"
    return None


def hermes_sequence_gate_decision(sequence: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    reason = hermes_sequence_gate_reason(sequence, item)
    return {
        "ok": True,
        "source": "hermes_sequence_gate",
        "allowed": reason is None,
        "reason": reason,
        "sequence_id": sequence.get("sequence_id"),
        "item_id": item.get("item_id"),
    }


def hermes_sequence_apply_gate(sequence: dict[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in sequence.get("items", []) if isinstance(item, dict)]
    decisions = [hermes_sequence_gate_decision(sequence, item) for item in items]
    blocked = [decision for decision in decisions if not decision["allowed"]]
    return {
        "ok": True,
        "source": "hermes_sequence_gate",
        "sequence_id": sequence.get("sequence_id"),
        "allowed": not blocked,
        "decisions": decisions,
        "blocked_count": len(blocked),
    }
