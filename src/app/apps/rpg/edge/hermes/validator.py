from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def validate_hermes_rpg_proposal(context_payload: dict[str, Any], plan_payload: dict[str, Any]) -> dict[str, Any]:
    context = _safe_dict(context_payload.get("context") or context_payload)
    flags = _safe_dict(context.get("state_flags"))
    player = _safe_dict(context.get("player"))
    proposal = _safe_dict(plan_payload.get("proposal") or plan_payload)
    command = _safe_str(proposal.get("command")).strip()
    if not command:
        return _invalid("missing_command")
    head = command.lower().split(maxsplit=1)[0]
    if head in {"buy", "sell", "rest"} and not flags.get("in_service"):
        return _invalid("service_unavailable", command=command)
    if head in {"attack", "cast", "defend"} and not flags.get("in_combat"):
        return _invalid("combat_unavailable", command=command)
    if head in {"travel", "go", "walk"} and flags.get("in_combat"):
        return _invalid("travel_blocked_by_combat", command=command)
    if head == "focus" and not context.get("objectives"):
        return _invalid("objective_unavailable", command=command)
    if head in {"buy", "rest"} and not _safe_dict(player.get("currency")):
        return _invalid("currency_unavailable", command=command)
    return {
        "ok": True,
        "valid": True,
        "source": "hermes_rpg_validator",
        "command": command,
        "state_changed": False,
    }


def _invalid(error: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "valid": False,
        "source": "hermes_rpg_validator",
        "error": error,
        "state_changed": False,
        **extra,
    }
