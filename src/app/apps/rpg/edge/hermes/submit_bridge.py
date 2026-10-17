from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.apps.rpg.edge.hermes.pipeline_result import hermes_rpg_pipeline_result
from app.apps.rpg.edge.hermes.submit_adapter import hermes_rpg_submit_adapter

RpgSubmitter = Callable[[dict[str, Any]], dict[str, Any]]


def hermes_rpg_submit_bridge(packet: dict[str, Any], submitter: RpgSubmitter) -> dict[str, Any]:
    request = hermes_rpg_submit_adapter(packet)
    if request.get("ok") is not True:
        return {
            "ok": False,
            "source": "hermes_rpg_submit_bridge",
            "error": request.get("error") or "packet_not_ready",
            "state_changed": False,
        }
    result = submitter(request)
    return hermes_rpg_pipeline_result(packet, result)
