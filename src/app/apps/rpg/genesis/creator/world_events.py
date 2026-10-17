"""Phase 3B — Consequences + Event Generation.

Deterministic event/consequence generation from simulation state deltas.
No randomness. All events are derived from structured before/after state.
"""

from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _thread_event(thread_id: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    before_pressure = int(before.get("pressure", 0))
    after_pressure = int(after.get("pressure", 0))
    before_status = before.get("status")
    after_status = after.get("status")

    if after_pressure > before_pressure:
        return {
            "event_id": f"evt_thread_escalation_{thread_id}",
            "type": "thread_escalation",
            "entity_id": thread_id,
            "severity": after_status or "active",
            "summary": f"Thread {thread_id} escalated from {before_status or 'unknown'} to {after_status or 'unknown'}.",
            "details": {
                "before_pressure": before_pressure,
                "after_pressure": after_pressure,
                "before_status": before_status,
                "after_status": after_status,
            },
        }

    if after_pressure < before_pressure:
        return {
            "event_id": f"evt_thread_cooling_{thread_id}",
            "type": "thread_cooling",
            "entity_id": thread_id,
            "severity": after_status or "low",
            "summary": f"Thread {thread_id} cooled from {before_status or 'unknown'} to {after_status or 'unknown'}.",
            "details": {
                "before_pressure": before_pressure,
                "after_pressure": after_pressure,
                "before_status": before_status,
                "after_status": after_status,
            },
        }

    return None


def _faction_event(faction_id: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    before_status = before.get("status")
    after_status = after.get("status")
    if before_status == after_status:
        return None
    return {
        "event_id": f"evt_faction_shift_{faction_id}",
        "type": "faction_reaction",
        "entity_id": faction_id,
        "severity": after_status or "stable",
        "summary": f"Faction {faction_id} shifted from {before_status or 'unknown'} to {after_status or 'unknown'}.",
        "details": {
            "before": before,
            "after": after,
        },
    }


def _location_event(location_id: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    before_heat = int(before.get("heat", 0))
    after_heat = int(after.get("heat", 0))
    before_status = before.get("status")
    after_status = after.get("status")

    if after_status == "hot" and before_status != "hot":
        return {
            "event_id": f"evt_location_hot_{location_id}",
            "type": "location_hotspot",
            "entity_id": location_id,
            "severity": "hot",
            "summary": f"Location {location_id} became a hotspot.",
            "details": {
                "before_heat": before_heat,
                "after_heat": after_heat,
                "before_status": before_status,
                "after_status": after_status,
            },
        }

    if after_heat < before_heat:
        return {
            "event_id": f"evt_location_cooling_{location_id}",
            "type": "location_cooling",
            "entity_id": location_id,
            "severity": after_status or "quiet",
            "summary": f"Location {location_id} cooled from {before_status or 'unknown'} to {after_status or 'unknown'}.",
            "details": {
                "before_heat": before_heat,
                "after_heat": after_heat,
                "before_status": before_status,
                "after_status": after_status,
            },
        }

    return None


def generate_world_events(simulation_diff: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate deterministic events from simulation diffs."""
    events: list[dict[str, Any]] = []

    for change in _safe_list(simulation_diff.get("threads_changed")):
        thread_id = change.get("id")
        if not thread_id:
            continue
        evt = _thread_event(thread_id, _safe_dict(change.get("before")), _safe_dict(change.get("after")))
        if evt:
            events.append(evt)

    for change in _safe_list(simulation_diff.get("factions_changed")):
        faction_id = change.get("id")
        if not faction_id:
            continue
        evt = _faction_event(faction_id, _safe_dict(change.get("before")), _safe_dict(change.get("after")))
        if evt:
            events.append(evt)

    for change in _safe_list(simulation_diff.get("locations_changed")):
        location_id = change.get("id")
        if not location_id:
            continue
        evt = _location_event(location_id, _safe_dict(change.get("before")), _safe_dict(change.get("after")))
        if evt:
            events.append(evt)

    return events


def generate_consequences(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate structured consequences from events."""
    consequences: list[dict[str, Any]] = []

    for evt in _safe_list(events):
        evt_type = evt.get("type")
        entity_id = evt.get("entity_id")

        if evt_type == "thread_escalation":
            consequences.append({
                "consequence_id": f"cnsq_{entity_id}_pressure",
                "type": "pressure_increase",
                "entity_id": entity_id,
                "summary": f"Escalation in {entity_id} increases world pressure.",
                "source_event_id": evt.get("event_id"),
            })
        elif evt_type == "faction_reaction":
            consequences.append({
                "consequence_id": f"cnsq_{entity_id}_reaction",
                "type": "faction_response",
                "entity_id": entity_id,
                "summary": f"Faction {entity_id} is likely to respond to mounting strain.",
                "source_event_id": evt.get("event_id"),
            })
        elif evt_type == "location_hotspot":
            consequences.append({
                "consequence_id": f"cnsq_{entity_id}_hotspot",
                "type": "hotspot",
                "entity_id": entity_id,
                "summary": f"{entity_id} is now a hotspot and may produce further incidents.",
                "source_event_id": evt.get("event_id"),
            })

    return consequences