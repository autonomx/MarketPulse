"""Phase 3D — Incident Engine + Policy System.

Deterministic incident spawning and faction policy reactions derived from
simulation state transitions.

Rules
-----
* Incident spawning
    - Thread incidents: If a thread becomes critical → spawn thread_crisis
    - Location incidents: If a location becomes hot → spawn location_flashpoint
    - Faction incidents: If a faction becomes strained → spawn faction_instability

* Policy reactions
    - If faction is watchful → spawn faction_alert
    - If faction is strained → spawn faction_crackdown
    - If faction pressure drops to stable from higher → spawn faction_relief

All rules are fully deterministic and bounded.
"""

from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

MAX_INCIDENTS = 50
MAX_POLICY_REACTIONS = 50


def spawn_incidents_from_state_diff(
    simulation_diff: dict[str, Any],
) -> list[dict[str, Any]]:
    """Spawn incidents from a simulation diff.

    Parameters
    ----------
    simulation_diff :
        Structured diff with ``threads_changed``, ``locations_changed``,
        and ``factions_changed`` lists.

    Returns
    -------
    list[dict]
        Bounded list of structured incident dicts.
    """
    incidents: list[dict[str, Any]] = []

    # Thread incidents — critical threads spawn thread_crisis
    for change in _safe_list(simulation_diff.get("threads_changed")):
        thread_id = change.get("id")
        after = _safe_dict(change.get("after"))
        if thread_id and after.get("status") == "critical":
            incidents.append({
                "incident_id": f"inc_{thread_id}_critical",
                "type": "thread_crisis",
                "source_type": "thread",
                "source_id": thread_id,
                "severity": "critical",
                "summary": f"Thread {thread_id} has reached crisis level.",
                "duration": 2,
            })

    # Location incidents — hot locations spawn location_flashpoint
    for change in _safe_list(simulation_diff.get("locations_changed")):
        location_id = change.get("id")
        after = _safe_dict(change.get("after"))
        if location_id and after.get("status") == "hot":
            incidents.append({
                "incident_id": f"inc_{location_id}_hot",
                "type": "location_flashpoint",
                "source_type": "location",
                "source_id": location_id,
                "severity": "hot",
                "summary": f"Location {location_id} has become a flashpoint.",
                "duration": 2,
            })

    # Faction incidents — strained factions spawn faction_instability
    for change in _safe_list(simulation_diff.get("factions_changed")):
        faction_id = change.get("id")
        after = _safe_dict(change.get("after"))
        if faction_id and after.get("status") == "strained":
            incidents.append({
                "incident_id": f"inc_{faction_id}_strained",
                "type": "faction_instability",
                "source_type": "faction",
                "source_id": faction_id,
                "severity": "strained",
                "summary": f"Faction {faction_id} is showing signs of instability.",
                "duration": 2,
            })

    return incidents[:MAX_INCIDENTS]


def generate_policy_reactions(
    simulation_diff: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate faction policy reactions from simulation diff.

    Parameters
    ----------
    simulation_diff :
        Structured diff with ``factions_changed`` list containing
        ``before`` and ``after`` status info.

    Returns
    -------
    list[dict]
        Bounded list of structured policy reaction dicts.
    """
    reactions: list[dict[str, Any]] = []

    for change in _safe_list(simulation_diff.get("factions_changed")):
        faction_id = change.get("id")
        before = _safe_dict(change.get("before"))
        after = _safe_dict(change.get("after"))
        if not faction_id:
            continue

        before_status = before.get("status")
        after_status = after.get("status")

        if after_status == "watchful":
            reactions.append({
                "reaction_id": f"rxn_{faction_id}_alert",
                "type": "faction_alert",
                "faction_id": faction_id,
                "severity": "watchful",
                "summary": f"Faction {faction_id} is on alert.",
            })
        elif after_status == "strained":
            reactions.append({
                "reaction_id": f"rxn_{faction_id}_crackdown",
                "type": "faction_crackdown",
                "faction_id": faction_id,
                "severity": "strained",
                "summary": f"Faction {faction_id} is tightening control in response to strain.",
            })
        elif before_status in {"watchful", "strained"} and after_status == "stable":
            reactions.append({
                "reaction_id": f"rxn_{faction_id}_relief",
                "type": "faction_relief",
                "faction_id": faction_id,
                "severity": "stable",
                "summary": f"Faction {faction_id} has stabilized.",
            })

    return reactions[:MAX_POLICY_REACTIONS]


def merge_incidents(
    current_incidents: list[dict[str, Any]],
    new_incidents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge current and new incidents, deduplicating by incident_id.

    New incidents overwrite existing ones with the same id.
    """
    merged: dict[str, dict[str, Any]] = {}
    for inc in _safe_list(current_incidents):
        iid = inc.get("incident_id")
        if iid:
            merged[iid] = dict(inc)
    for inc in _safe_list(new_incidents):
        iid = inc.get("incident_id")
        if iid:
            merged[iid] = dict(inc)
    return sorted(merged.values(), key=lambda i: i.get("incident_id", ""))[:MAX_INCIDENTS]


def decay_incidents(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decay incidents by reducing duration and removing expired ones.

    Each incident has a ``duration`` field that decrements by 1 each tick.
    Incidents with duration <= 0 are removed.
    """
    remaining: list[dict[str, Any]] = []
    for inc in _safe_list(incidents):
        item = dict(inc)
        duration = int(item.get("duration", 0)) - 1
        if duration > 0:
            item["duration"] = duration
            remaining.append(item)
    return remaining[:MAX_INCIDENTS]


def compute_incident_diff(
    before_incidents: list[dict[str, Any]],
    after_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute a structured diff between before and after incident lists.

    Returns
    -------
    dict
        ``{"added": [...ids], "removed": [...ids], "changed": [...{id, fields}]}``
    """
    before_map = {
        x.get("incident_id"): x for x in _safe_list(before_incidents) if x.get("incident_id")
    }
    after_map = {
        x.get("incident_id"): x for x in _safe_list(after_incidents) if x.get("incident_id")
    }

    added = sorted([iid for iid in after_map if iid not in before_map])
    removed = sorted([iid for iid in before_map if iid not in after_map])
    changed: list[dict[str, Any]] = []

    for iid in sorted(set(before_map.keys()) & set(after_map.keys())):
        before = before_map[iid]
        after = after_map[iid]
        fields: list[str] = []
        for key in sorted(set(before.keys()) | set(after.keys())):
            if before.get(key) != after.get(key):
                fields.append(key)
        if fields:
            changed.append({
                "id": iid,
                "fields": fields,
            })

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def compute_policy_reaction_diff(
    before_reactions: list[dict[str, Any]],
    after_reactions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute a structured diff between before and after policy reactions.

    Returns
    -------
    dict
        ``{"added": [...ids], "removed": [...ids]}``
    """
    before_ids = sorted([x.get("reaction_id") for x in _safe_list(before_reactions) if x.get("reaction_id")])
    after_ids = sorted([x.get("reaction_id") for x in _safe_list(after_reactions) if x.get("reaction_id")])
    return {
        "added": sorted([rid for rid in after_ids if rid not in before_ids]),
        "removed": sorted([rid for rid in before_ids if rid not in after_ids]),
    }