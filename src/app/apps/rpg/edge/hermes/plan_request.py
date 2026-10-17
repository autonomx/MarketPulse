from __future__ import annotations

from app.config.env import environment

from typing import Any, Protocol

from app.providers.hermes_client import HermesSidecarClient
from app.apps.rpg.edge.hermes.planner_contract import normalize_hermes_planner_response
from app.providers.hermes_status import hermes_runtime_config


class HermesRpgPlanClient(Protocol):
    def rpg_plan(self, request: dict[str, Any]) -> dict[str, Any]: ...


def request_hermes_rpg_plan(
    planner_context: dict[str, Any],
    *,
    client: HermesRpgPlanClient | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    config = hermes_runtime_config()
    is_enabled = config.enabled if enabled is None else enabled
    if not is_enabled:
        return _inactive("hermes_disabled")
    active_client = client or HermesSidecarClient(
        base_url=config.base_url,
        api_key=environment().get("HERMES_API_KEY") or None,
        timeout=config.timeout_seconds,
    )
    try:
        raw = active_client.rpg_plan(_request_payload(planner_context))
    except Exception as exc:
        return _inactive("hermes_unavailable", detail=str(exc))
    normalized = normalize_hermes_planner_response(raw)
    if normalized.get("ok") is not True:
        return {**normalized, "state_changed": False}
    return {**normalized, "state_changed": False, "mode": "review_required"}


def _request_payload(planner_context: dict[str, Any]) -> dict[str, Any]:
    context = planner_context.get("context") or {}
    return {
        "session_id": planner_context.get("session_id"),
        "turn_id": planner_context.get("turn_id"),
        "context_hash": planner_context.get("context_hash"),
        "context": context,
        "available_commands": context.get("available_commands") or [],
    }


def _inactive(error: str, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "accepted": False,
        "source": "hermes_rpg_plan_request",
        "error": error,
        "state_changed": False,
        **extra,
    }
