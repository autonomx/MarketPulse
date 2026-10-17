from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.apps.rpg.edge.hermes.sequence_checkpoint_policy import hermes_sequence_checkpoint_policy
from app.apps.rpg.edge.hermes.sequence_contract import hermes_sequence_contract_validate
from app.apps.rpg.edge.hermes.sequence_loop_guard import hermes_sequence_loop_guard

SOURCE = "hermes_sequence_planner_loop"
UNSUPPORTED_ACTIONS = {"delete", "teleport", "rewrite", "spawn", "grant"}


def _text(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _items(sequence: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in sequence.get("items", []) if isinstance(item, dict)]


def _unsupported(statement: str) -> str | None:
    words = set(statement.split())
    hit = words & UNSUPPORTED_ACTIONS
    return sorted(hit)[0] if hit else None


def hermes_sequence_planner_loop(raw_sequence: dict[str, Any] | None, context_pack: dict[str, Any] | None = None) -> dict[str, Any]:
    checked = hermes_sequence_contract_validate(raw_sequence or {})
    sequence = deepcopy(checked["sequence"])
    context = dict(context_pack) if isinstance(context_pack, dict) else {}
    critique: list[dict[str, Any]] = []
    if not checked["ok"]:
        critique.extend({"kind": "validation", "detail": error} for error in checked["errors"])
    if not context.get("current_location"):
        critique.append({"kind": "missing_context", "detail": "current_location"})

    refined_items: list[dict[str, Any]] = []
    seen_commands: set[str] = set()
    for item in _items(sequence):
        statement = _text(item.get("statement"))
        unsupported = _unsupported(statement)
        if unsupported:
            critique.append({"kind": "unsupported_action", "item_id": item.get("item_id"), "detail": unsupported})
            continue
        if statement in seen_commands:
            critique.append({"kind": "likely_loop", "item_id": item.get("item_id"), "detail": "duplicate_command"})
            continue
        seen_commands.add(statement)
        refined_items.append(item)

    sequence["items"] = refined_items
    checkpoint = hermes_sequence_checkpoint_policy(sequence)
    for decision in checkpoint["decisions"]:
        if decision.get("requires_checkpoint"):
            critique.append({"kind": "risky_step", "item_id": decision.get("item_id"), "detail": decision.get("reason")})
            for item in sequence["items"]:
                if item.get("item_id") == decision.get("item_id"):
                    item["user_gate"] = True

    loop_guard = hermes_sequence_loop_guard(sequence)
    if loop_guard.get("ok") is False:
        critique.append({"kind": "likely_loop", "detail": loop_guard.get("stop_reason")})

    summary = {
        "issue_count": len(critique),
        "issues": critique,
        "blocked": bool(critique and not refined_items),
    }
    sequence["critique_summary"] = summary
    return {
        "ok": not summary["blocked"],
        "source": SOURCE,
        "sequence": sequence,
        "critique_summary": summary,
        "checkpoint": checkpoint,
        "loop_guard": loop_guard,
        "state_changed": False,
    }
