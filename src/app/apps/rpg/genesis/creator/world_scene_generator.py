"""Phase 4 — Scene / Encounter Generator

Transforms simulation incidents + threads + hotspots into playable
scenes, encounters, and narrative hooks that are player-facing.

Scene types supported:
    - conflict: Crisis escalation and high-tension confrontations
    - encounter: Flashpoint events at volatile locations
    - political: Faction instability and power shifts
    - investigation: Mystery threads requiring exploration
    - negotiation: Diplomatic challenges between factions
"""

from __future__ import annotations

import logging

from typing import Any

# Phase 8: player-facing state updates
from app.apps.rpg.rules.player import (
    set_current_scene,
    update_codex_from_state,
    update_journal_from_state,
)
from app.apps.rpg.foundation.safe_values import safe_list as _safe_list
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scene type constants
# ---------------------------------------------------------------------------

SCENE_TYPE_CONFLICT = "conflict"
SCENE_TYPE_ENCOUNTER = "encounter"
SCENE_TYPE_POLITICAL = "political"
SCENE_TYPE_INVESTIGATION = "investigation"
SCENE_TYPE_NEGOTIATION = "negotiation"

VALID_SCENE_TYPES = frozenset({
    SCENE_TYPE_CONFLICT,
    SCENE_TYPE_ENCOUNTER,
    SCENE_TYPE_POLITICAL,
    SCENE_TYPE_INVESTIGATION,
    SCENE_TYPE_NEGOTIATION,
})

# Maximum number of scenes returned to avoid overwhelming the UI
_MAX_SCENES = 20

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_str(v: Any, default: str = "") -> str:
    """Return *v* as a string, falling back to *default*."""
    if v is None:
        return default
    return str(v)


def _collect_scene_actors(source_id, simulation_state, max_actors=4):
    """Collect NPC actors relevant to a scene source from Phase 6 state."""
    simulation_state = simulation_state or {}
    npc_index = simulation_state.get("npc_index") or {}
    npc_minds = simulation_state.get("npc_minds") or {}

    actors = []
    for npc_id, npc in sorted(npc_index.items()):
        npc_location_id = _safe_str(npc.get("location_id"))
        npc_faction_id = _safe_str(npc.get("faction_id"))

        if source_id and (source_id == npc_location_id or source_id == npc_faction_id or source_id == npc_id):
            actor = {
                "id": npc_id,
                "name": _safe_str(npc.get("name")) or npc_id,
                "role": _safe_str(npc.get("role")),
                "faction_id": npc_faction_id,
                "location_id": npc_location_id,
            }
            mind = npc_minds.get(npc_id) or {}
            if isinstance(mind, dict):
                actor["mind_context"] = {
                    "last_decision": mind.get("last_decision") or {},
                }
            # Phase 6.5: Add faction stance to actors
            group_positions = (simulation_state.get("social_state") or {}).get("group_positions") or {}
            if npc_faction_id and npc_faction_id in group_positions:
                actor["faction_position"] = dict(group_positions[npc_faction_id])
            actors.append(actor)

    return actors[:max_actors]


def _build_scene_id(source_id: str, suffix: str) -> str:
    """Build a stable scene id from source and suffix.

    Parameters
    ----------
    source_id :
        The originating entity id (location, faction, thread, etc.)
    suffix :
        A short descriptor like ``"crisis"``, ``"flashpoint"``, etc.

    Returns
    -------
    str
        A scene id like ``"scene_thread_alpha_crisis"``.
    """
    return f"scene_{source_id}_{suffix}"


# ---------------------------------------------------------------------------
# Incident → Scene mappers
# ---------------------------------------------------------------------------


def _map_thread_crisis(inc: dict[str, Any]) -> dict[str, Any]:
    """Map a thread crisis incident to a playable conflict scene."""
    source = _safe_str(inc.get("source_id"), "unknown")
    summary = _safe_str(inc.get("summary"), "A crisis has escalated beyond containment.")
    pressure = inc.get("pressure", 0)

    stakes_parts = []
    if isinstance(pressure, (int, float)):
        if pressure >= 5:
            stakes_parts.append("Critical escalation imminent")
        elif pressure >= 3:
            stakes_parts.append("High tension conflict")
        else:
            stakes_parts.append("Moderate conflict")
    else:
        stakes_parts.append("High tension conflict")

    return {
        "scene_id": _build_scene_id(source, "crisis"),
        "type": SCENE_TYPE_CONFLICT,
        "title": f"Crisis Escalation: {source}",
        "summary": summary,
        "actors": [source],
        "stakes": ", ".join(stakes_parts),
        "severity": inc.get("severity", "neutral"),
        "source_incident_id": inc.get("incident_id") or inc.get("id"),
        "action_hooks": [
            {"type": "intervene_thread", "target_id": source},
            {"type": "escalate_conflict", "target_id": source},
        ],
    }


def _map_location_flashpoint(inc: dict[str, Any]) -> dict[str, Any]:
    """Map a location flashpoint incident to an encounter scene."""
    source = _safe_str(inc.get("source_id"), "unknown")
    summary = _safe_str(inc.get("summary"), "A volatile situation threatens this location.")
    heat = inc.get("heat", 0)

    stakes_parts = []
    if isinstance(heat, (int, float)):
        if heat >= 5:
            stakes_parts.append("Environment at boiling point")
        elif heat >= 3:
            stakes_parts.append("Volatile environment")
        else:
            stakes_parts.append("Rising tensions")
    else:
        stakes_parts.append("Volatile environment")

    return {
        "scene_id": _build_scene_id(source, "flashpoint"),
        "type": SCENE_TYPE_ENCOUNTER,
        "title": f"Flashpoint at {source}",
        "summary": summary,
        "actors": [source],
        "stakes": ", ".join(stakes_parts),
        "severity": inc.get("severity", "neutral"),
        "source_incident_id": inc.get("incident_id") or inc.get("id"),
        "action_hooks": [
            {"type": "intervene_thread", "target_id": source},
            {"type": "escalate_conflict", "target_id": source},
        ],
    }


def _map_faction_instability(inc: dict[str, Any]) -> dict[str, Any]:
    """Map a faction instability incident to a political scene."""
    source = _safe_str(inc.get("source_id"), "unknown")
    summary = _safe_str(inc.get("summary"), "Power dynamics within the faction are shifting.")
    pressure = inc.get("pressure", 0)

    stakes_parts = []
    if isinstance(pressure, (int, float)):
        if pressure >= 5:
            stakes_parts.append("Imminent power shift")
        elif pressure >= 3:
            stakes_parts.append("Power shift")
        else:
            stakes_parts.append("Factional tension")
    else:
        stakes_parts.append("Power shift")

    return {
        "scene_id": _build_scene_id(source, "instability"),
        "type": SCENE_TYPE_POLITICAL,
        "title": f"Faction Instability: {source}",
        "summary": summary,
        "actors": [source],
        "stakes": ", ".join(stakes_parts),
        "severity": inc.get("severity", "neutral"),
        "source_incident_id": inc.get("incident_id") or inc.get("id"),
        "action_hooks": [
            {"type": "intervene_thread", "target_id": source},
            {"type": "escalate_conflict", "target_id": source},
        ],
    }


def _map_thread_investigation(inc: dict[str, Any]) -> dict[str, Any]:
    """Map a mysterious thread to an investigation scene."""
    source = _safe_str(inc.get("source_id"), "unknown")
    summary = _safe_str(inc.get("summary"), "Unexplained events warrant investigation.")

    return {
        "scene_id": _build_scene_id(source, "investigation"),
        "type": SCENE_TYPE_INVESTIGATION,
        "title": f"Unexplained Events: {source}",
        "summary": summary,
        "actors": [source],
        "stakes": "Hidden truths await discovery",
        "severity": inc.get("severity", "neutral"),
        "source_incident_id": inc.get("incident_id") or inc.get("id"),
        "action_hooks": [
            {"type": "observe_situation", "target_id": source},
            {"type": "intervene_thread", "target_id": source},
        ],
    }


def _map_thread_negotiation(inc: dict[str, Any]) -> dict[str, Any]:
    """Map a diplomatic thread to a negotiation scene."""
    source = _safe_str(inc.get("source_id"), "unknown")
    summary = _safe_str(inc.get("summary"), "Diplomatic skills are needed to resolve tensions.")

    return {
        "scene_id": _build_scene_id(source, "negotiation"),
        "type": SCENE_TYPE_NEGOTIATION,
        "title": f"Diplomatic Challenge: {source}",
        "summary": summary,
        "actors": [source],
        "stakes": "Peace hangs in the balance",
        "severity": inc.get("severity", "neutral"),
        "source_incident_id": inc.get("incident_id") or inc.get("id"),
        "action_hooks": [
            {"type": "intervene_thread", "target_id": source},
            {"type": "escalate_conflict", "target_id": source},
        ],
    }


# ---------------------------------------------------------------------------
# Incident type → mapper dispatch
# ---------------------------------------------------------------------------

_INCIDENT_MAPPERS: dict[str, Any] = {
    "thread_crisis": _map_thread_crisis,
    "location_flashpoint": _map_location_flashpoint,
    "faction_instability": _map_faction_instability,
    "thread_mystery": _map_thread_investigation,
    "thread_diplomatic": _map_thread_negotiation,
}

# Fallback mapping for partial matches
_INCIDENT_TYPE_KEYWORDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "crisis": _map_thread_crisis,
    "flashpoint": _map_location_flashpoint,
    "instability": _map_faction_instability,
    "mystery": _map_thread_investigation,
    "negotiation": _map_thread_negotiation,
    "diplomatic": _map_thread_negotiation,
    "investigation": _map_thread_investigation,
}


def _resolve_mapper(inc_type: str) -> Any:
    """Resolve the appropriate mapper for an incident type.

    Falls back to keyword matching if exact type not found.
    """
    if inc_type in _INCIDENT_MAPPERS:
        return _INCIDENT_MAPPERS[inc_type]

    lower = inc_type.lower()
    for keyword, mapper in _INCIDENT_TYPE_KEYWORDS.items():
        if keyword in lower:
            return mapper

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _thread_to_scene(thread: dict[str, Any]) -> dict[str, Any]:
    """Generate a scene from a high-pressure thread."""
    tid = _safe_str(thread.get("id"), "unknown_thread")
    pressure = thread.get("pressure", 0)
    return {
        "scene_id": _build_scene_id(tid, "pressure"),
        "type": SCENE_TYPE_CONFLICT,
        "title": f"Rising Tension: {tid}",
        "summary": f"Pressure at {pressure}. {thread.get('summary', '')}",
        "actors": [tid],
        "stakes": "Escalation likely",
        "severity": "moderate" if pressure >= 5 else "low",
        "source_incident_id": None,
        "action_hooks": [
            {"type": "intervene_thread", "target_id": tid},
            {"type": "escalate_conflict", "target_id": tid},
        ],
    }


def _location_to_scene(location: dict[str, Any]) -> dict[str, Any]:
    """Generate a scene from a hotspot location."""
    lid = _safe_str(location.get("location_id"), "unknown_loc")
    heat = location.get("heat", 0)
    return {
        "scene_id": _build_scene_id(lid, "hotspot"),
        "type": SCENE_TYPE_ENCOUNTER,
        "title": f"Hotspot: {location.get('name', lid)}",
        "summary": f"Heat level {heat}. {location.get('description', '')}",
        "actors": [lid],
        "stakes": "Volatile environment",
        "severity": "moderate" if heat >= 5 else "low",
        "source_incident_id": None,
        "action_hooks": [
            {"type": "intervene_thread", "target_id": lid},
            {"type": "escalate_conflict", "target_id": lid},
        ],
    }


def generate_scenes_from_incidents(
    incidents: list[dict[str, Any]],
    *,
    max_scenes: int = _MAX_SCENES,
) -> list[dict[str, Any]]:
    """Transform a list of simulation incidents into playable scenes.

    Parameters
    ----------
    incidents :
        List of incident dicts from the world simulation engine.
        Each incident should have at minimum a ``type`` and
        ``source_id`` key.
    max_scenes :
        Maximum number of scenes to return. Defaults to ``20``.

    Returns
    -------
    list[dict[str, Any]]
        A list of scene dicts ready for frontend display.  Each scene
        has keys: ``scene_id``, ``type``, ``title``, ``summary``,
        ``actors``, ``stakes``, ``severity``, and
        ``source_incident_id``.
    """
    scenes: list[dict[str, Any]] = []

    for inc in _safe_list(incidents):
        inc_type = _safe_str(inc.get("type"))
        mapper = _resolve_mapper(inc_type)

        if mapper is not None:
            try:
                scene = mapper(inc)
                scenes.append(scene)
            except Exception:
                # Skip incidents that fail to map — they will be logged
                # by the caller if needed.
                logger.debug("suppressed error in %s", "generate_scenes_from_incidents", exc_info=True)
                continue

    return scenes[:max_scenes]


def generate_extra_scenes(
    state: dict[str, Any],
    *,
    max_scenes: int = _MAX_SCENES,
    already: int = 0,
) -> list[dict[str, Any]]:
    """Generate scenes from non-incident sources (threads, hotspots, factions).

    Only kicks in when incident-based scenes are insufficient to fill
    the cap.

    Parameters
    ----------
    state :
        Simulation after-state dict (may contain ``threads``, ``hotspots``,
        ``factions``).
    max_scenes :
        Overall maximum scenes allowed.
    already :
        Number of scenes already generated from incidents.

    Returns
    -------
    list[dict[str, Any]]
        Additional scene dicts.
    """
    extra: list[dict[str, Any]] = []
    remaining = max_scenes - already
    if remaining <= 0:
        return extra

    # From threads with pressure >= 3
    threads = _safe_list(state.get("threads"))
    for t in threads:
        if len(extra) >= remaining:
            break
        if t.get("pressure", 0) >= 3:
            extra.append(_thread_to_scene(t))

    # From hotspots
    if len(extra) < remaining:
        hotspots = _safe_list(state.get("hotspots"))
        for h in hotspots:
            if len(extra) >= remaining:
                break
            extra.append(_location_to_scene(h))

    return extra


def generate_scenes_from_simulation(
    state: dict[str, Any],
    *,
    max_scenes: int = _MAX_SCENES,
) -> list[dict[str, Any]]:
    """High-level entry point: extract incidents from simulation state
    and generate playable scenes.

    Parameters
    ----------
    state :
        The simulation after-state dict returned by
        ``step_simulation_state()``.  Expected to contain the
        incidents list under keys like ``incidents``, or
        ``simulation_state.incidents``.  May also contain threads,
        hotspots, factions as secondary scene sources.
    max_scenes :
        Maximum number of scenes to return.

    Returns
    -------
    list[dict[str, Any]]
        Generated scenes.
    """
    # Try top-level incidents first
    incidents = _safe_list(state.get("incidents"))

    # Fall back to nested simulation_state
    if not incidents:
        sim_state = state.get("simulation_state")
        if isinstance(sim_state, dict):
            incidents = _safe_list(sim_state.get("incidents"))

    scenes = generate_scenes_from_incidents(incidents, max_scenes=max_scenes)

    # Also pull from non-incident sources (threads, hotspots)
    extra = generate_extra_scenes(state, max_scenes=max_scenes, already=len(scenes))
    scenes.extend(extra)

    # Phase 6: Enrich scenes with NPC actors from simulation state
    for scene in scenes:
        source_id = scene.get("source_incident_id") or ""
        # Also try to match by scene actors (which are often source_ids)
        if not source_id and scene.get("actors"):
            source_id = _safe_str(scene["actors"][0]) if scene["actors"] else ""

        # Phase 7: Add debug context to scenes
        source_id_for_debug = _safe_str(scene.get("source_incident_id") or "")
        if not source_id_for_debug:
            scene_actors = scene.get("actors") or []
            if isinstance(scene_actors, list) and scene_actors:
                source_id_for_debug = _safe_str(scene_actors[0])

        scene["debug_context"] = {
            "tick": int(state.get("tick", 0) or 0),
            "source_id": source_id_for_debug,
            "active_rumor_count": len(state.get("active_rumors") or []),
            "has_social_state": bool(state.get("social_state")),
        }
        scene["debug_context"]["top_pressure_sources"] = {
            "threads": sorted(
                [
                    {"id": k, "pressure": int(v.get("pressure", 0) or 0)}
                    for k, v in (state.get("threads") or {}).items()
                    if isinstance(v, dict)
                ],
                key=lambda x: (-x["pressure"], x["id"])
            )[:3],
            "factions": sorted(
                [
                    {"id": k, "pressure": int(v.get("pressure", 0) or 0)}
                    for k, v in (state.get("factions") or {}).items()
                    if isinstance(v, dict)
                ],
                key=lambda x: (-x["pressure"], x["id"])
            )[:3],
            "locations": sorted(
                [
                    {"id": k, "pressure": int(v.get("pressure", 0) or 0)}
                    for k, v in (state.get("locations") or {}).items()
                    if isinstance(v, dict)
                ],
                key=lambda x: (-x["pressure"], x["id"])
            )[:3],
        }

        # Phase 6.5: attach scene-level social context
        social_state = state.get("social_state") or {}
        scene["active_rumors"] = [
            dict(item)
            for item in (state.get("active_rumors") or [])[:3]
        ]
        scene["active_alliances"] = [
            dict(item)
            for item in (social_state.get("alliances") or [])
            if item.get("status") == "active"
        ][:3]
        scene["faction_positions"] = {
            key: dict(value)
            for key, value in sorted((social_state.get("group_positions") or {}).items())
        }

        enriched_actors = list(scene.get("actors") or [])
        enriched_actors.extend(_collect_scene_actors(
            source_id=source_id,
            simulation_state=state,
            max_actors=4,
        ))

        deduped = []
        seen_actor_ids = set()
        for actor in enriched_actors:
            if isinstance(actor, dict):
                actor_id = _safe_str(actor.get("id"))
            else:
                actor_id = _safe_str(actor)
                actor = {"id": actor_id, "name": actor_id}

            if not actor_id or actor_id in seen_actor_ids:
                continue
            seen_actor_ids.add(actor_id)
            deduped.append(actor)

        scene["actors"] = deduped[:6]
        scene["primary_npc_ids"] = [
            a["id"]
            for a in deduped
            if isinstance(a, dict) and _safe_str(a.get("id"))
            and (state.get("npc_index") or {}).get(_safe_str(a.get("id")))
        ][:4]

    # Phase 8: player-facing state update from primary scene
    # We need to mutate the original dict in-place for downstream consumers
    if scenes:
        primary_scene = scenes[0]
        active_npc_id = ""
        scene_actors = primary_scene.get("actors") or []
        for actor in scene_actors:
            if isinstance(actor, dict) and actor.get("id"):
                active_npc_id = str(actor.get("id"))
                break
        updated = set_current_scene(
            state,
            primary_scene,
            mode="scene",
            active_npc_id=active_npc_id,
        )
        updated = update_journal_from_state(updated, primary_scene)
        updated = update_codex_from_state(updated)
        # Copy player_state back to original state dict
        if "player_state" in updated:
            state["player_state"] = updated["player_state"]

    # Phase 8.3: Expose sandbox summary and world consequences into scenes
    for scene in scenes:
        scene["sandbox_summary"] = dict(state.get("sandbox_summary") or {})
        scene["world_consequences"] = list(
            ((state.get("sandbox_state") or {}).get("world_consequences") or [])[-3:]
        )

    return scenes[:max_scenes]


