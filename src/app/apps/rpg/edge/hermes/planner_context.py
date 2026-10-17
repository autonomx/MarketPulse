from __future__ import annotations

import hashlib
import json
from typing import Any

from app.apps.rpg.edge.hermes.context import hermes_rpg_context_from_session


def hermes_planner_context_from_session(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    base = hermes_rpg_context_from_session(session_id, session)
    if base.get("ok") is not True:
        return {**base, "planner_ready": False}
    context = dict(base["context"])
    flags = raw_state_flags if isinstance(raw_state_flags := context.get("state_flags"), dict) else {}
    recent_turns = raw_recent_turns if isinstance(raw_recent_turns := context.get("recent_turns"), list) else []
    turn_id = recent_turns[-1].get("turn") if recent_turns and isinstance(recent_turns[-1], dict) else None
    context["available_commands"] = _available_commands(flags)
    context["turn_id"] = turn_id
    context_hash = _context_hash(context)
    return {
        "ok": True,
        "source": "hermes_planner_context",
        "read_only": True,
        "planner_ready": True,
        "session_id": session_id,
        "turn_id": turn_id,
        "context_hash": context_hash,
        "context": context,
    }


def _available_commands(flags: dict[str, Any]) -> list[str]:
    commands = ["look", "inspect", "check", "ask", "talk", "journal"]
    if flags.get("in_combat"):
        commands.extend(["attack", "defend", "use"])
    else:
        commands.extend(["travel", "go", "walk"])
    if flags.get("in_service"):
        commands.extend(["buy", "sell", "rest"])
    return commands


def _context_hash(context: dict[str, Any]) -> str:
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
