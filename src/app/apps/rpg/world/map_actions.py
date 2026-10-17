"""Single-entry authoritative mutation core for RPG map actions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from app.apps.rpg.world.map_hierarchy import switch_active_map
from app.apps.rpg.world.map_projection import increment_map_overlay_revision, project_session_map_overlay
from app.apps.rpg.world.map_repository import MapDefinitionRepository, default_map_repository

MapActionType = Literal["travel", "inspect", "enter", "talk", "trade"]


@dataclass(frozen=True)
class MapActionRequest:
    action: MapActionType
    target_object_id: str
    definition_revision: str
    overlay_revision: int
    route_id: str | None = None
    client_action_id: str | None = None


class MapActionError(ValueError):
    def __init__(self, code: str, *, reason: str = "", status_code: int = 400) -> None:
        self.code = code
        self.reason = reason or code
        self.status_code = status_code
        super().__init__(self.reason)


def apply_map_action(
    session: Mapping[str, Any],
    map_id: str,
    request: MapActionRequest,
    repository: MapDefinitionRepository | None = None,
) -> dict[str, Any]:
    """Validate and apply one map action without bypassing session truth."""

    repository = repository or default_map_repository()
    definition = repository.get(map_id)
    overlay = project_session_map_overlay(session, map_id, repository)
    if overlay.availability != "ready":
        raise MapActionError("map_overlay_unavailable", reason=overlay.unavailable_reason, status_code=409)
    if request.definition_revision != definition.definition_revision:
        raise MapActionError("stale_definition_revision", status_code=409)
    if request.overlay_revision != overlay.overlay_revision:
        raise MapActionError("stale_overlay_revision", status_code=409)

    target = next((item for item in definition.objects if item.id == request.target_object_id), None)
    if target is None:
        raise MapActionError("map_object_not_found", status_code=404)
    if target.id not in overlay.discovered_object_ids:
        raise MapActionError("map_object_undiscovered", status_code=404)
    if target.id not in overlay.visible_object_ids:
        raise MapActionError("map_object_not_visible", status_code=409)

    existing = _existing_action(session, request.client_action_id)
    if existing is not None:
        return {
            "ok": True,
            "idempotent": True,
            "map_id": map_id,
            "action_result": existing,
            "session": deepcopy(dict(session)),
            "overlay": overlay,
        }

    capability = next(
        (
            item
            for item in overlay.capabilities
            if item.type == request.action and item.target_object_id == target.id
        ),
        None,
    )
    hierarchy_enter = request.action == "enter" and bool(target.child_map_id)
    if capability is None and not hierarchy_enter:
        raise MapActionError("map_action_not_available", status_code=409)
    if capability and request.route_id and capability.route_id and request.route_id != capability.route_id:
        raise MapActionError("route_identity_mismatch", status_code=409)
    if capability and not capability.enabled:
        reason = _authoritative_route_reason(session, capability.route_id) or capability.disabled_reason or "map_action_disabled"
        raise MapActionError(reason, reason=reason, status_code=409)
    if request.action == "travel" and request.route_id:
        route = next((item for item in overlay.routes if item.route_id == request.route_id), None)
        if route is None:
            raise MapActionError("route_unknown", status_code=409)
        reason = _route_block_reason(route)
        if reason:
            raise MapActionError(reason, reason=reason, status_code=409)

    updated = deepcopy(dict(session))
    state = raw_state if isinstance(raw_state := updated.get("state"), dict) else {}
    map_state = raw_map_state if isinstance(raw_map_state := state.get("map_state"), dict) else {}
    result_map_id = map_id
    result: dict[str, Any] = {
        "action": request.action,
        "target_object_id": target.id,
        "target_location_id": target.location_id,
        "route_id": capability.route_id if capability else None,
        "changed": False,
    }

    if request.action == "travel":
        if not target.location_id:
            raise MapActionError("target_location_unavailable", status_code=409)
        previous_location_id = str(map_state.get("current_location_id") or "")
        map_state["current_location_id"] = target.location_id
        state["current_location_id"] = target.location_id
        state["current_location"] = target.label or target.location_id
        state["location"] = target.label or target.location_id
        player = raw_player if isinstance(raw_player := state.get("player"), dict) else {}
        player["location_id"] = target.location_id
        state["player"] = player
        _sync_world_graph_location(state, target.location_id)
        revision = increment_map_overlay_revision(state)
        result.update(
            {
                "changed": True,
                "previous_location_id": previous_location_id,
                "current_location_id": target.location_id,
                "overlay_revision": revision,
            }
        )
    elif request.action == "enter":
        if not target.child_map_id:
            raise MapActionError("child_map_unavailable", status_code=409)
        previous_map_id = str(map_state.get("current_map_id") or map_id)
        map_state, destination_location_id = switch_active_map(
            state,
            target.child_map_id,
            target.location_id,
            repository,
        )
        revision = increment_map_overlay_revision(state)
        state["current_location_id"] = destination_location_id
        state["current_location"] = target.label or destination_location_id
        state["location"] = target.label or destination_location_id
        player = raw_player if isinstance(raw_player := state.get("player"), dict) else {}
        player["location_id"] = destination_location_id
        state["player"] = player
        result_map_id = target.child_map_id
        result.update(
            {
                "changed": True,
                "previous_map_id": previous_map_id,
                "current_map_id": result_map_id,
                "current_location_id": destination_location_id,
                "overlay_revision": revision,
            }
        )
    elif request.action == "inspect":
        result.update(
            {
                "label": target.label,
                "description": target.description,
                "status": _object_status(map_state, target.id),
            }
        )
    else:
        result["requires_turn"] = True
        result["suggested_command"] = _suggested_command(request.action, target.label or target.id)

    _record_action(map_state, request, result)
    state["map_state"] = map_state
    updated["state"] = state
    projected = project_session_map_overlay(updated, result_map_id, repository)
    return {
        "ok": True,
        "idempotent": False,
        "map_id": result_map_id,
        "action_result": result,
        "session": updated,
        "overlay": projected,
    }


def map_action_error_payload(error: MapActionError, map_id: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error.code,
        "reason": error.reason,
        "map_id": map_id,
        "requires_turn": error.reason in {
            "route_requires_encounter_check",
            "target_requires_expansion",
            "route_unknown",
        },
    }


def _existing_action(session: Mapping[str, Any], client_action_id: str | None) -> Mapping[str, Any] | None:
    if not client_action_id:
        return None
    state = raw_state if isinstance(raw_state := session.get("state"), Mapping) else {}
    map_state = raw_map_state if isinstance(raw_map_state := state.get("map_state"), Mapping) else {}
    for item in reversed(_sequence(map_state.get("action_history"))):
        if isinstance(item, Mapping) and item.get("client_action_id") == client_action_id:
            result = item.get("result")
            return result if isinstance(result, Mapping) else item
    return None


def _authoritative_route_reason(session: Mapping[str, Any], route_id: str | None) -> str:
    if not route_id:
        return ""
    state = raw_state if isinstance(raw_state := session.get("state"), Mapping) else {}
    map_state = raw_map_state if isinstance(raw_map_state := state.get("map_state"), Mapping) else {}
    route_states = raw_route_states if isinstance(raw_route_states := map_state.get("route_states"), Mapping) else {}
    route_state = raw_route if isinstance(raw_route := route_states.get(route_id), Mapping) else {}
    return str(route_state.get("reason") or "").strip()


def _route_block_reason(route: object) -> str:
    if not bool(getattr(route, "known", False)):
        return "route_unknown"
    status = str(getattr(route, "status", "unknown"))
    if status != "open":
        return str(getattr(route, "reason", "") or f"route_{status}")
    if not bool(getattr(route, "safe", False)):
        return str(getattr(route, "reason", "") or "route_requires_encounter_check")
    return ""


def _sync_world_graph_location(state: dict[str, Any], location_id: str) -> None:
    graph = raw_world_graph if isinstance(raw_world_graph := state.get("world_graph"), dict) else None
    if graph is None:
        return
    known_ids = {
        str(item.get("id") or item.get("location_id") or "")
        for item in _sequence(graph.get("locations"))
        if isinstance(item, Mapping)
    }
    if isinstance(graph.get("locations"), Mapping):
        known_ids.update(str(key) for key in graph["locations"])
    if location_id not in known_ids:
        return
    graph["current_location_id"] = location_id
    discovered = [str(item) for item in _sequence(graph.get("discovered_location_ids"))]
    graph["discovered_location_ids"] = [*dict.fromkeys((*discovered, location_id))]
    state["world_graph"] = graph


def _record_action(map_state: dict[str, Any], request: MapActionRequest, result: Mapping[str, Any]) -> None:
    history = [item for item in _sequence(map_state.get("action_history")) if isinstance(item, Mapping)]
    history.append(
        {
            "client_action_id": request.client_action_id,
            "action": request.action,
            "target_object_id": request.target_object_id,
            "result": dict(result),
        }
    )
    map_state["action_history"] = history[-32:]


def _object_status(map_state: Mapping[str, Any], object_id: str) -> str:
    states = raw_object_states if isinstance(raw_object_states := map_state.get("object_states"), Mapping) else {}
    state = raw_state if isinstance(raw_state := states.get(object_id), Mapping) else {}
    return str(state.get("status") or "normal")


def _suggested_command(action: str, label: str) -> str:
    return {
        "talk": f"Talk to the people at {label}.",
        "trade": f"Trade at {label}.",
    }.get(action, f"Interact with {label}.")


def _sequence(value: object) -> tuple[object, ...]:
    return tuple(value) if isinstance(value, (list, tuple)) else ()
