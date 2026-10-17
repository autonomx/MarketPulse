from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.hermes.command_request import hermes_rpg_command_request
from app.apps.rpg.edge.hermes.pipeline_handoff import hermes_rpg_pipeline_handoff
from app.apps.rpg.edge.hermes.ready_packet import hermes_rpg_ready_packet
from app.apps.rpg.edge.hermes.request_guard import hermes_rpg_request_guard
from app.apps.rpg.edge.hermes.submit_bridge import RpgSubmitter, hermes_rpg_submit_bridge


def hermes_rpg_approved_flow(
    user_step: dict[str, Any],
    replay_entry: dict[str, Any],
    context: dict[str, Any],
    submitter: RpgSubmitter,
) -> dict[str, Any]:
    handoff = hermes_rpg_pipeline_handoff(user_step, replay_entry)
    request = hermes_rpg_command_request(handoff, session_id=str(context.get("session_id") or ""))
    if context.get("context_hash"):
        request = {**request, "context_hash": context.get("context_hash")}
    guard = hermes_rpg_request_guard(request, context)
    packet = hermes_rpg_ready_packet(request, guard)
    result = hermes_rpg_submit_bridge(packet, submitter)
    return {
        "ok": result.get("ok") is True,
        "source": "hermes_rpg_approved_flow",
        "handoff": handoff,
        "request": request,
        "guard": guard,
        "packet": packet,
        "result": result,
        "state_changed": result.get("state_changed") is True,
    }
