from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_WORLD_EVENTS = 160


def _ensure_world_event_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = simulation_state.get("world_event_state")
    if not isinstance(state, dict):
        state = {}
        simulation_state["world_event_state"] = state
    events = state.get("events")
    if not isinstance(events, list):
        events = []
        state["events"] = events
    return state


def add_world_event(
    simulation_state: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    event = deepcopy(_safe_dict(event))
    event_id = _safe_str(event.get("event_id"))
    if not event_id:
        return {}

    state = _ensure_world_event_state(simulation_state)
    events = _safe_list(state.get("events"))
    for existing in events:
        existing = _safe_dict(existing)
        if _safe_str(existing.get("event_id")) == event_id:
            return deepcopy(existing)

    events.append(event)
    if len(events) > MAX_WORLD_EVENTS:
        del events[:-MAX_WORLD_EVENTS]
    return deepcopy(event)


def add_service_world_event(
    simulation_state: Dict[str, Any],
    service_result: Dict[str, Any],
    service_application: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    service_result = _safe_dict(service_result)
    service_application = _safe_dict(service_application)
    provider_name = _safe_str(service_result.get("provider_name") or "Provider")
    service_kind = _safe_str(service_result.get("service_kind") or "service")
    selected_offer_id = _safe_str(service_result.get("selected_offer_id"))
    transaction = _safe_dict(service_application.get("transaction_record"))
    event_id = (
        f"world:event:service:{int(tick or 0)}:"
        f"{_safe_str(service_result.get('provider_id'))}:{selected_offer_id or service_kind}"
    )

    if service_application.get("applied"):
        title = "Service purchase completed"
        summary = f"{provider_name} completed a {service_kind.replace('_', ' ')} service."
        kind = "service_purchase"
    elif service_application.get("blocked"):
        title = "Service purchase blocked"
        summary = f"{provider_name} could not complete the requested {service_kind.replace('_', ' ')} service."
        kind = "service_purchase_blocked"
    else:
        title = "Service inquiry"
        summary = f"{provider_name} presented {service_kind.replace('_', ' ')} options."
        kind = "service_inquiry"

    return add_world_event(
        simulation_state,
        {
            "event_id": event_id,
            "kind": kind,
            "title": title,
            "summary": summary,
            "provider_id": _safe_str(service_result.get("provider_id")),
            "provider_name": provider_name,
            "service_kind": service_kind,
            "offer_id": selected_offer_id,
            "transaction_id": _safe_str(transaction.get("transaction_id")),
            "tick": int(tick or 0),
            "source": "deterministic_service_runtime",
        },
    )


def add_rumor_world_event(
    simulation_state: Dict[str, Any],
    rumor: Dict[str, Any],
    *,
    tick: int = 0,
) -> Dict[str, Any]:
    rumor = _safe_dict(rumor)
    rumor_id = _safe_str(rumor.get("rumor_id"))
    if not rumor_id:
        return {}
    return add_world_event(
        simulation_state,
        {
            "event_id": f"world:event:rumor:{int(tick or 0)}:{rumor_id}",
            "kind": "rumor_lead",
            "title": _safe_str(rumor.get("title")),
            "summary": _safe_str(rumor.get("summary")),
            "source_id": rumor_id,
            "tick": int(tick or 0),
            "source": "deterministic_rumor_registry",
        },
    )


def get_world_event_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(_ensure_world_event_state(simulation_state))