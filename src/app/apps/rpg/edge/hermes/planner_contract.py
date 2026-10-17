from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict

_ALLOWED_PREFIXES = (
    "ask",
    "talk",
    "say",
    "tell",
    "look",
    "inspect",
    "check",
    "travel",
    "go",
    "walk",
    "buy",
    "sell",
    "rest",
    "attack",
    "defend",
    "cast",
    "equip",
    "use",
    "focus",
    "journal",
)
_ALIAS_PREFIXES = {
    "examine": "inspect",
    "inventory": "check inventory",
    "move": "travel",
    "purchase": "buy",
    "speak": "talk",
}
_MUTATION_KEYS = {"state_patch", "state_changes", "mutation", "before", "after", "delta"}
_MULTI_COMMAND_MARKERS = ("\n", ";", " and then ", " then ")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def hermes_planner_contract_schema() -> dict[str, Any]:
    return {
        "request": {
            "session_id": "string",
            "turn_id": "string|number|null",
            "context_hash": "string|null",
            "context": "bounded visible RPG context",
            "available_commands": "list[string]",
        },
        "response": {
            "command": "single RPG command string",
            "reason": "string",
            "confidence": "number 0..1",
            "risk": "low|medium|high",
            "expected_effect": "string",
            "direct_state_write": False,
        },
    }


def normalize_hermes_planner_response(payload: Any) -> dict[str, Any]:
    data = _safe_dict(payload)
    if not data:
        return _rejected("invalid_response")
    proposal = _safe_dict(data.get("proposal")) or data
    mutation_keys = sorted(key for key in _MUTATION_KEYS if key in data or key in proposal)
    if mutation_keys:
        return _rejected("state_mutation_not_allowed", mutation_keys=mutation_keys)

    command = _normalize_command(_safe_str(proposal.get("command") or proposal.get("command_text")).strip())
    if not command:
        return _rejected("empty_command")
    if _has_multiple_commands(command):
        return _rejected("multi_command_not_allowed", command=command)
    if not _known_command(command):
        return _rejected("unknown_command", command=command)

    return {
        "ok": True,
        "accepted": True,
        "source": "hermes_planner_contract",
        "proposal": {
            "command": command,
            "reason": _safe_str(proposal.get("reason")).strip(),
            "confidence": _confidence(proposal.get("confidence")),
            "risk": _risk(_safe_str(proposal.get("risk") or "medium")),
            "expected_effect": _safe_str(proposal.get("expected_effect")).strip(),
            "requires_review": True,
            "direct_state_write": False,
        },
        "metadata": {
            "planner": _safe_str(proposal.get("planner") or data.get("planner")).strip() or None,
            "raw_kind": _safe_str(proposal.get("kind") or data.get("kind")).strip() or None,
        },
    }


def _normalize_command(command: str) -> str:
    if not command:
        return ""
    parts = command.split(maxsplit=1)
    head = parts[0].lower()
    tail = parts[1] if len(parts) > 1 else ""
    alias = _ALIAS_PREFIXES.get(head)
    if alias is None:
        return command
    return f"{alias} {tail}".strip()


def _has_multiple_commands(command: str) -> bool:
    lowered = command.lower()
    return any(marker in lowered for marker in _MULTI_COMMAND_MARKERS)


def _known_command(command: str) -> bool:
    head = command.lower().split(maxsplit=1)[0]
    return head in _ALLOWED_PREFIXES


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(parsed, 1.0))


def _risk(value: str) -> str:
    lowered = value.strip().lower()
    return lowered if lowered in {"low", "medium", "high"} else "medium"


def _rejected(error: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "accepted": False,
        "source": "hermes_planner_contract",
        "error": error,
        **extra,
    }
