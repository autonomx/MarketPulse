from __future__ import annotations

from copy import deepcopy
from html import escape
from typing import Any, Dict

from app.apps.rpg.world.locations.discovery import build_accessible_location_map_payload, ensure_discovery_state
from app.apps.rpg.world.locations.events import build_location_history_model
from app.apps.rpg.world.locations.graph import RUSTY_FLAGON, get_canonical_location
from app.apps.rpg.world.locations.time import ensure_time_state
from app.apps.rpg.world.locations.travel import ensure_travel_state
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_map_location_report"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _current_location_id(simulation_state: Dict[str, Any]) -> str:
    travel_state = ensure_travel_state(simulation_state)
    return _safe_str(travel_state.get("current_location_id")) or RUSTY_FLAGON


def _weather_state_from_time(time_state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "season": _safe_str(time_state.get("season")),
        "weather_id": _safe_str(time_state.get("weather_id")),
        "weather_label": _safe_str(time_state.get("weather_label")),
        "weather_visibility": _safe_str(time_state.get("weather_visibility")),
        "weather_travel_fatigue_delta": _safe_int(time_state.get("weather_travel_fatigue_delta"), 0),
        "weather_survival_thirst_delta": _safe_int(time_state.get("weather_survival_thirst_delta"), 0),
        "weather_source": _safe_str(time_state.get("weather_source")),
        "source": SOURCE,
    }


def build_map_location_panel_payload(
    simulation_state: Dict[str, Any],
    *,
    current_location_id: str | None = None,
) -> Dict[str, Any]:
    state = deepcopy(_safe_dict(simulation_state))
    current_id = _safe_str(current_location_id) or _current_location_id(state)
    location = get_canonical_location(current_id)
    accessible = build_accessible_location_map_payload(state, current_id)
    history = build_location_history_model(state)
    time_state = ensure_time_state(state)
    discovery_state = ensure_discovery_state(state)
    visible_exits = []
    for edge in _safe_list(accessible.get("visible_exits")):
        row = _safe_dict(edge)
        destination = get_canonical_location(_safe_str(row.get("destination_id"))) or {}
        visible_exits.append(
            {
                "edge_id": _safe_str(row.get("edge_id")),
                "name": _safe_str(row.get("name")),
                "destination_id": _safe_str(row.get("destination_id")),
                "destination_name": _safe_str(destination.get("name")),
                "discovered": row.get("discovered") is True,
                "blocked": row.get("blocked") is True,
                "block_reason": _safe_str(_safe_dict(row.get("block")).get("reason")),
                "source": SOURCE,
            }
        )
    return {
        "source": SOURCE,
        "current_location_id": current_id,
        "current_location": location,
        "visible_exits": visible_exits,
        "discovered_locations": list(_safe_list(accessible.get("discovered_locations"))),
        "discovered_routes": list(_safe_list(accessible.get("discovered_routes"))),
        "route_blocks": deepcopy(_safe_dict(accessible.get("route_blocks"))),
        "time_state": deepcopy(time_state),
        "weather_state": _weather_state_from_time(time_state),
        "location_history": history,
        "discovery_source": discovery_state.get("source"),
    }


def render_map_location_report_html(panel_payload: Dict[str, Any]) -> str:
    payload = _safe_dict(panel_payload)
    location = _safe_dict(payload.get("current_location"))
    rows = ["<section id=\"map-location-panel\"><h2>Map & Location</h2>"]
    rows.append(f"<p>Current: <strong>{escape(_safe_str(location.get('name') or payload.get('current_location_id')))}</strong></p>")
    if location.get("description"):
        rows.append(f"<p>{escape(_safe_str(location.get('description')))}</p>")
    time_state = _safe_dict(payload.get("time_state"))
    rows.append(
        "<p>Time: "
        f"Day {escape(str(_safe_int(time_state.get('day_count'), 1)))} "
        f"{escape(_safe_str(time_state.get('clock_time')))} "
        f"({escape(_safe_str(time_state.get('time_of_day_label')))})</p>"
    )
    weather_state = _safe_dict(payload.get("weather_state")) or _weather_state_from_time(time_state)
    rows.append(
        "<p>Season: "
        f"{escape(_safe_str(weather_state.get('season')).replace('_', ' '))} — "
        f"Weather: {escape(_safe_str(weather_state.get('weather_label')))}"
        "</p>"
    )
    if weather_state.get("weather_visibility"):
        rows.append(f"<p>Visibility: {escape(_safe_str(weather_state.get('weather_visibility')))}</p>")
    rows.append("<h3>Visible exits</h3><ul>")
    for edge in _safe_list(payload.get("visible_exits")):
        row = _safe_dict(edge)
        status = "blocked" if row.get("blocked") else ("available" if row.get("discovered") else "undiscovered")
        detail = f" — {row.get('block_reason')}" if row.get("blocked") and row.get("block_reason") else ""
        rows.append(
            "<li>"
            f"{escape(_safe_str(row.get('name')))} -> {escape(_safe_str(row.get('destination_name')))} "
            f"<em>{escape(status + detail)}</em>"
            "</li>"
        )
    rows.append("</ul>")
    history = _safe_dict(payload.get("location_history"))
    rows.append(f"<p>Location history events: <strong>{escape(str(_safe_int(history.get('event_count'), 0)))}</strong></p>")
    rows.append(f"<p class=\"source\">Source: {SOURCE}</p></section>")
    return "\n".join(rows)


def build_map_location_narration_contract(panel_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _safe_dict(panel_payload)
    location = _safe_dict(payload.get("current_location"))
    weather_state = _safe_dict(payload.get("weather_state"))
    allowed = [f"Current location: {payload.get('current_location_id')} — {location.get('name')}"]
    if weather_state:
        allowed.append(f"Season: {weather_state.get('season')}")
        allowed.append(f"Weather: {weather_state.get('weather_label')}")
    for edge in _safe_list(payload.get("visible_exits")):
        row = _safe_dict(edge)
        allowed.append(
            f"Visible exit: {row.get('edge_id')} -> {row.get('destination_id')} "
            f"discovered={row.get('discovered')} blocked={row.get('blocked')}"
        )
    return {
        "source": SOURCE,
        "allowed_map_location_claims": allowed,
        "forbidden_map_location_claims": [
            "Do not invent locations, exits, route blocks, services, NPCs, hazards, or map state.",
            "Do not invent weather or seasons beyond the deterministic panel payload.",
            "Do not reveal undiscovered destinations as known unless the panel payload marks them discovered.",
            "Do not claim blocked routes are passable unless the panel payload marks blocked=false.",
            "Only claim season/weather details present in the panel weather_state payload.",
            "Do not mutate travel, discovery, time, weather, inventory, combat, quest, or world-event state from map/report rendering.",
        ],
    }


def assert_phase4_map_location_report_ready() -> Dict[str, Any]:
    from app.apps.rpg.world.locations.discovery import discover_location, discover_route
    from app.apps.rpg.world.locations.travel import apply_travel

    state: Dict[str, Any] = {}
    initial = build_map_location_panel_payload(state)
    discover_location(state, location_id="location:old_mill", reason="scouted_old_road", turn_index=2)
    discover_route(state, edge_id="route:old_road:old_mill", reason="scouted_old_road", turn_index=2)
    apply_travel(state, start_location_id="location:rusty_flagon", end_location_id="location:old_road", turn_index=3)
    after_travel = build_map_location_panel_payload(state)
    html = render_map_location_report_html(after_travel)
    contract = build_map_location_narration_contract(after_travel)
    blockers = []
    if initial.get("current_location_id") != RUSTY_FLAGON:
        blockers.append({"kind": "unexpected_initial_location", "source": SOURCE})
    if not initial.get("visible_exits"):
        blockers.append({"kind": "missing_visible_exits", "source": SOURCE})
    if after_travel.get("current_location_id") != "location:old_road":
        blockers.append({"kind": "travel_location_not_reflected", "source": SOURCE})
    if not after_travel.get("weather_state", {}).get("weather_label"):
        blockers.append({"kind": "missing_panel_weather_state", "source": SOURCE})
    if "Map & Location" not in html or "Weather:" not in html or SOURCE not in html:
        blockers.append({"kind": "map_location_html_not_rendered", "source": SOURCE})
    if not contract.get("forbidden_map_location_claims"):
        blockers.append({"kind": "missing_map_location_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_map_location_report_ready" if not blockers else "phase4_map_location_report_not_ready",
        "initial": initial,
        "after_travel": after_travel,
        "blockers": blockers,
        "source": SOURCE,
    }
