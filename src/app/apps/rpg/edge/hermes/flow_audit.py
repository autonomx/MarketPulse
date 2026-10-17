from __future__ import annotations

from typing import Any


def hermes_rpg_flow_audit(flow: dict[str, Any]) -> dict[str, Any]:
    packet = raw_packet if isinstance(raw_packet := flow.get("packet"), dict) else {}
    result = raw_result if isinstance(raw_result := flow.get("result"), dict) else {}
    return {
        "ok": flow.get("ok") is True,
        "source": "hermes_rpg_flow_audit",
        "session_id": packet.get("session_id"),
        "context_hash": packet.get("context_hash"),
        "command_text": packet.get("command_text"),
        "rpg_ok": result.get("ok") is True,
        "state_changed": flow.get("state_changed") is True,
    }
