from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.hermes.flow_audit import hermes_rpg_flow_audit
from app.apps.rpg.edge.hermes.flow_error import hermes_rpg_flow_error


def hermes_rpg_flow_readout(flow: dict[str, Any]) -> dict[str, Any]:
    audit = hermes_rpg_flow_audit(flow)
    error = hermes_rpg_flow_error(flow)
    status = "accepted" if audit.get("ok") is True else "blocked"
    return {
        "ok": audit.get("ok") is True,
        "source": "hermes_rpg_flow_readout",
        "status": status,
        "summary": f"Hermes RPG command {status}",
        "session_id": audit.get("session_id"),
        "context_hash": audit.get("context_hash"),
        "command_text": audit.get("command_text"),
        "rpg_ok": audit.get("rpg_ok") is True,
        "error": error.get("error"),
        "state_changed": audit.get("state_changed") is True,
        "audit": audit,
    }
