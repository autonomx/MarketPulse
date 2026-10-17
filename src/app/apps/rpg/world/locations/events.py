from __future__ import annotations

from copy import deepcopy
from html import escape
from typing import Any, Dict, List

from app.apps.rpg.world.locations.graph import get_canonical_location
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

SOURCE = "deterministic_phase4_world_events"
REPORT_SOURCE = "deterministic_phase4_location_history_report"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def ensure_world_event_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    event_state = _safe_dict(state.get("world_event_state"))
    if not event_state:
        event_state = {}
        state["world_event_state"] = event_state
    event_state["events"] = list(_safe_list(event_state.get("events")))
    event_state["source"] = SOURCE
    return event_state


def record_world_event(
    simulation_state: Dict[str, Any],
    *,
    location_id: str,
    event_id: str,
    summary: str,
    kind: str = "local_event",
    turn_index: int = 0,
    source_detail: str = "manual_world_event",
) -> Dict[str, Any]:
    location_id = _safe_str(location_id)
    if not get_canonical_location(location_id):
        return {"ok": False, "reason": "unknown_location", "location_id": location_id, "source": SOURCE}
    event_state = ensure_world_event_state(simulation_state)
    entry = {
        "turn_index": int(turn_index or 0),
        "location_id": location_id,
        "event_id": _safe_str(event_id),
        "kind": _safe_str(kind) or "local_event",
        "summary": _safe_str(summary),
        "source_detail": _safe_str(source_detail),
        "source": SOURCE,
    }
    event_state["events"] = list(_safe_list(event_state.get("events"))) + [deepcopy(entry)]
    event_state["last_event"] = deepcopy(entry)
    return {"ok": True, "reason": "world_event_recorded", "event": entry, "source": SOURCE}


def derive_world_events_from_logs(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    state = _safe_dict(simulation_state)
    derived: List[Dict[str, Any]] = []
    for row in _safe_list(_safe_dict(state.get("travel_state")).get("travel_log")):
        entry = _safe_dict(row)
        location_id = _safe_str(entry.get("to"))
        if get_canonical_location(location_id):
            derived.append(
                {
                    "turn_index": _safe_int(entry.get("turn_index"), 0),
                    "location_id": location_id,
                    "event_id": f"event:travel:{_safe_str(entry.get('from'))}:{location_id}",
                    "kind": "travel_arrival",
                    "summary": f"Arrived at {location_id} after {_safe_int(entry.get('minutes'), 0)} minutes of travel.",
                    "source_detail": _safe_str(entry.get("source")) or "deterministic_phase4_travel_costs",
                    "source": SOURCE,
                }
            )
    for row in _safe_list(_safe_dict(state.get("encounter_state")).get("encounter_log")):
        entry = _safe_dict(row)
        encounter = _safe_dict(entry.get("encounter"))
        location_id = _safe_str(entry.get("location_id"))
        if not location_id and _safe_str(entry.get("edge_id")) == "route:old_road:old_mill":
            location_id = "location:old_mill"
        if get_canonical_location(location_id) and encounter:
            derived.append(
                {
                    "turn_index": _safe_int(entry.get("turn_index"), 0),
                    "location_id": location_id,
                    "event_id": _safe_str(encounter.get("encounter_id")),
                    "kind": f"encounter:{_safe_str(encounter.get('kind'))}",
                    "summary": _safe_str(encounter.get("summary")),
                    "source_detail": _safe_str(entry.get("source")) or "deterministic_phase4_seeded_encounters",
                    "source": SOURCE,
                }
            )
    for row in _safe_list(_safe_dict(state.get("discovery_state")).get("discovery_log")):
        entry = _safe_dict(row)
        location_id = _safe_str(entry.get("location_id"))
        if get_canonical_location(location_id):
            derived.append(
                {
                    "turn_index": _safe_int(entry.get("turn_index"), 0),
                    "location_id": location_id,
                    "event_id": f"event:discovery:{location_id}",
                    "kind": _safe_str(entry.get("kind")) or "location_discovered",
                    "summary": f"Discovered {location_id} via {_safe_str(entry.get('reason'))}.",
                    "source_detail": _safe_str(entry.get("source")) or "deterministic_phase4_discovery_route_blocking",
                    "source": SOURCE,
                }
            )
    return {"ok": True, "reason": "world_events_derived", "events": derived, "source": SOURCE}


def build_location_history_model(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    explicit_events = list(_safe_list(ensure_world_event_state(simulation_state).get("events")))
    derived_events = list(_safe_list(derive_world_events_from_logs(simulation_state).get("events")))
    all_events = explicit_events + derived_events
    all_events.sort(key=lambda row: (_safe_int(_safe_dict(row).get("turn_index"), 0), _safe_str(_safe_dict(row).get("event_id"))))
    locations: Dict[str, Dict[str, Any]] = {}
    for row in all_events:
        entry = _safe_dict(row)
        location_id = _safe_str(entry.get("location_id"))
        location = get_canonical_location(location_id)
        if not location:
            continue
        bucket = locations.setdefault(
            location_id,
            {"location_id": location_id, "name": location.get("name", location_id), "events": [], "source": REPORT_SOURCE},
        )
        bucket["events"].append(deepcopy(entry))
    return {
        "source": REPORT_SOURCE,
        "event_count": sum(len(row["events"]) for row in locations.values()),
        "locations": [locations[key] for key in sorted(locations)],
    }


def render_location_history_report_html(simulation_state: Dict[str, Any]) -> str:
    model = build_location_history_model(simulation_state)
    rows = ["<section id=\"location-history\"><h2>Location History</h2>"]
    rows.append(f"<p>Events: <strong>{escape(str(model['event_count']))}</strong></p>")
    for location in _safe_list(model.get("locations")):
        loc = _safe_dict(location)
        rows.append(f"<h3>{escape(_safe_str(loc.get('name')))}</h3><ul>")
        for event in _safe_list(loc.get("events")):
            row = _safe_dict(event)
            text = f"Turn {_safe_int(row.get('turn_index'), 0)} — {_safe_str(row.get('kind'))}: {_safe_str(row.get('summary'))}"
            rows.append(f"<li>{escape(text)} <em>{escape(_safe_str(row.get('source_detail')))}</em></li>")
        rows.append("</ul>")
    rows.append(f"<p class=\"source\">Source: {REPORT_SOURCE}</p></section>")
    return "\n".join(rows)


def build_world_event_narration_contract(event_result: Dict[str, Any]) -> Dict[str, Any]:
    result = _safe_dict(event_result)
    events = _safe_list(result.get("events")) or ([result.get("event")] if result.get("event") else [])
    allowed = []
    for event in events:
        row = _safe_dict(event)
        if row:
            allowed.append(f"Local world event: {row.get('location_id')} — {row.get('kind')} — {row.get('summary')}")
    return {
        "source": SOURCE,
        "allowed_world_event_claims": allowed,
        "forbidden_world_event_claims": [
            "Do not invent local events that are not in the deterministic world-event or derived history rows.",
            "Do not claim route access, combat results, rewards, inventory changes, XP, or quest progress changed from world events alone.",
            "Do not reveal undiscovered locations unless a deterministic discovery row supports it.",
        ],
    }


def assert_phase4_world_events_location_history_ready() -> Dict[str, Any]:
    from app.apps.rpg.world.locations.encounters import record_encounter, roll_seeded_encounter
    from app.apps.rpg.world.locations.travel import apply_travel

    state: Dict[str, Any] = {}
    apply_travel(state, start_location_id="location:rusty_flagon", end_location_id="location:old_road", turn_index=1)
    encounter = roll_seeded_encounter("phase4.5", 2, location_id="location:old_road")
    record_encounter(state, encounter, turn_index=2)
    manual = record_world_event(
        state,
        location_id="location:rusty_flagon",
        event_id="event:bran:warns_about_road",
        kind="npc_warning",
        summary="Bran warns travelers to watch the old road after dusk.",
        turn_index=0,
        source_detail="deterministic_phase4_world_events_fixture",
    )
    model = build_location_history_model(state)
    html = render_location_history_report_html(state)
    contract = build_world_event_narration_contract(derive_world_events_from_logs(state))
    blockers = []
    if manual.get("reason") != "world_event_recorded":
        blockers.append({"kind": "manual_event_not_recorded", "source": SOURCE})
    if model.get("event_count", 0) < 3:
        blockers.append({"kind": "missing_location_history_events", "source": SOURCE})
    if "Location History" not in html or "deterministic_phase4_location_history_report" not in html:
        blockers.append({"kind": "location_history_html_not_rendered", "source": SOURCE})
    if not contract.get("forbidden_world_event_claims"):
        blockers.append({"kind": "missing_world_event_guardrails", "source": SOURCE})
    return {
        "ok": not blockers,
        "reason": "phase4_world_events_location_history_ready" if not blockers else "phase4_world_events_location_history_not_ready",
        "model": model,
        "blockers": blockers,
        "source": SOURCE,
    }
