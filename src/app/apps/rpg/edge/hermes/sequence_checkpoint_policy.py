from __future__ import annotations

from typing import Any

SOURCE = "hermes_sequence_checkpoint_policy"

INVENTORY_WORDS = {"buy", "sell", "take", "drop", "give", "use", "equip", "unequip", "craft", "spend", "pay"}
TRAVEL_WORDS = {"travel", "go", "move", "enter", "leave", "north", "south", "east", "west"}
COMBAT_WORDS = {"attack", "fight", "strike", "shoot", "cast", "defend"}
QUEST_WORDS = {"quest", "objective", "complete", "accept", "turn in", "report"}
NPC_WORDS = {"befriend", "threaten", "persuade", "romance", "recruit", "dismiss"}
REPEATED_WORDS = {"repeat", "again", "same thing"}
NOOP_WORDS = {"wait", "do nothing", "idle"}


def _text(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _tokens(statement: str) -> set[str]:
    return set(statement.replace(".", " ").replace(",", " ").split())


def _contains_phrase(statement: str, phrases: set[str]) -> bool:
    return any(phrase in statement for phrase in phrases if " " in phrase)


def hermes_sequence_checkpoint_reason(sequence: dict[str, Any], item: dict[str, Any]) -> str | None:
    if sequence.get("state_owner") != "rpg_sim":
        return "invalid_state_owner"
    if _text(sequence.get("risk")) == "high":
        return "high_risk_sequence"
    statement = _text(item.get("statement"))
    words = _tokens(statement)
    if words & INVENTORY_WORDS:
        return "inventory_currency_change"
    if words & TRAVEL_WORDS:
        return "travel_location_change"
    if words & COMBAT_WORDS:
        return "combat_action"
    if words & QUEST_WORDS or _contains_phrase(statement, QUEST_WORDS):
        return "quest_state_change"
    if words & NPC_WORDS:
        return "npc_relationship_change"
    if words & REPEATED_WORDS or _contains_phrase(statement, REPEATED_WORDS):
        return "repeated_noop_command"
    if words & NOOP_WORDS or _contains_phrase(statement, NOOP_WORDS):
        return "repeated_noop_command"
    return None


def hermes_sequence_checkpoint_policy(sequence: dict[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in sequence.get("items", []) if isinstance(item, dict)]
    decisions = []
    for index, item in enumerate(items):
        reason = hermes_sequence_checkpoint_reason(sequence, item)
        decisions.append(
            {
                "item_index": index,
                "item_id": item.get("item_id"),
                "requires_checkpoint": reason is not None,
                "reason": reason,
            }
        )
    reasons = [decision["reason"] for decision in decisions if decision["reason"]]
    return {
        "ok": True,
        "source": SOURCE,
        "requires_checkpoint": bool(reasons),
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "decisions": decisions,
    }
