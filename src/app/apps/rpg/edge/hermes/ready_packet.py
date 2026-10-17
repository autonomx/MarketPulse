from __future__ import annotations

from typing import Any


def hermes_rpg_ready_packet(request: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    command = str(request.get("command_text") or guard.get("command_text") or "").strip()
    ok = request.get("ok") is True and guard.get("ok") is True and bool(command)
    return {
        "ok": ok,
        "source": "hermes_rpg_ready_packet",
        "session_id": request.get("session_id"),
        "context_hash": guard.get("context_hash"),
        "command_text": command,
        "ready_for_rpg_pipeline": ok,
        "state_changed": False,
    }
