from __future__ import annotations

from typing import Any

from app.apps.rpg.edge.hermes.planner_context import hermes_planner_context_from_session
from app.apps.rpg.edge.hermes.plan_request import HermesRpgPlanClient, request_hermes_rpg_plan
from app.apps.rpg.edge.hermes.ticket import hermes_rpg_ticket_payload
from app.apps.rpg.edge.hermes.validator import validate_hermes_rpg_proposal


def hermes_rpg_plan_payload(request: dict[str, Any], *, client: HermesRpgPlanClient | None = None) -> dict[str, Any]:
    planner_context = _planner_context(request)
    if planner_context.get("ok") is not True:
        return {**planner_context, "state_changed": False}
    plan = request_hermes_rpg_plan(
        planner_context,
        client=client,
        enabled=request.get("enabled"),
    )
    if plan.get("ok") is not True:
        return {**plan, "planner_context": _context_meta(planner_context), "state_changed": False}
    validation = validate_hermes_rpg_proposal(planner_context["context"], plan)
    payload = {
        "ok": validation.get("ok") is True,
        "source": "hermes_rpg_plan",
        "mode": "review_required",
        "planner_context": _context_meta(planner_context),
        "plan": plan,
        "validation": validation,
        "state_changed": False,
    }
    return {**payload, "ticket": hermes_rpg_ticket_payload(payload)}


def _planner_context(request: dict[str, Any]) -> dict[str, Any]:
    context = raw_context if isinstance(raw_context := request.get("context"), dict) else {}
    if context:
        return {
            "ok": True,
            "source": "hermes_rpg_plan_inline_context",
            "read_only": True,
            "planner_ready": True,
            "session_id": request.get("session_id") or context.get("session_id") or "inline",
            "turn_id": request.get("turn_id") or context.get("turn_id"),
            "context_hash": request.get("context_hash") or "inline",
            "context": context,
        }
    session_id = str(request.get("session_id") or "").strip()
    if not session_id:
        return {"ok": False, "error": "missing_session_id", "source": "hermes_rpg_plan", "read_only": True}
    from app.apps.rpg.session.service import load_session

    session = load_session(session_id)
    if not session:
        return {"ok": False, "error": "session_not_found", "source": "hermes_rpg_plan", "session_id": session_id, "read_only": True}
    return hermes_planner_context_from_session(session_id, session)


def _context_meta(planner_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": planner_context.get("session_id"),
        "turn_id": planner_context.get("turn_id"),
        "context_hash": planner_context.get("context_hash"),
        "source": planner_context.get("source"),
    }
