from __future__ import annotations

from typing import Any


def hermes_rpg_flow_error(flow: dict[str, Any]) -> dict[str, Any]:
    if flow.get("ok") is True:
        error = "none"
    else:
        result = raw_result if isinstance(raw_result := flow.get("result"), dict) else {}
        packet = raw_packet if isinstance(raw_packet := flow.get("packet"), dict) else {}
        if packet.get("ready_for_rpg_pipeline") is not True:
            error = "packet_not_ready"
        else:
            error = str(result.get("error") or "rpg_result_not_ok")
    return {
        "ok": flow.get("ok") is True,
        "source": "hermes_rpg_flow_error",
        "error": error,
        "state_changed": flow.get("state_changed") is True,
    }
