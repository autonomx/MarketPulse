from __future__ import annotations

from typing import Any


def hermes_rpg_submit_adapter(packet: dict[str, Any]) -> dict[str, Any]:
    """Adapt a guarded Hermes ready packet to the canonical RPG submit payload."""
    command_text = str(packet.get("command_text") or "").strip()
    session_id = str(packet.get("session_id") or "").strip()
    ready = packet.get("ready_for_rpg_pipeline") is True
    ok = ready and bool(session_id) and bool(command_text)
    error = None
    if not ready:
        error = "packet_not_ready"
    elif not session_id:
        error = "missing_session_id"
    elif not command_text:
        error = "missing_command"

    payload: dict[str, Any] = {
        "ok": ok,
        "source": "hermes_rpg_submit_adapter",
        "session_id": session_id,
        "command_text": command_text,
        "input": command_text,
        "context_hash": packet.get("context_hash"),
        "canonical_path": "rpg_turn_execute",
        "state_changed": False,
    }
    if error:
        payload["error"] = error
    return payload
