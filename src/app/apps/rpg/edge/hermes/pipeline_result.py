from __future__ import annotations

from typing import Any


def hermes_rpg_pipeline_result(packet: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    ok = result.get("ok") is True or result.get("success") is True
    return {
        "ok": ok,
        "source": "hermes_rpg_pipeline_result",
        "session_id": packet.get("session_id"),
        "context_hash": packet.get("context_hash"),
        "command_text": packet.get("command_text"),
        "rpg_result": result,
        "state_changed": ok,
    }
