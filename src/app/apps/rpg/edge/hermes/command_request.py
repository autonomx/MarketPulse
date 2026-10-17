from __future__ import annotations

from typing import Any


def hermes_rpg_command_request(handoff: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    command = str(handoff.get("command_text") or "").strip()
    clean_session_id = str(session_id or "").strip()
    ok = handoff.get("ok") is True and bool(command) and bool(clean_session_id)
    return {
        "ok": ok,
        "source": "hermes_rpg_command_request",
        "session_id": clean_session_id,
        "command_text": command,
        "canonical_path": handoff.get("canonical_path") or "rpg_command_input",
        "ready_for_rpg_pipeline": ok,
        "state_changed": False,
    }
