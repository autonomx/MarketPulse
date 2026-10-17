"""World Event Director — autonomous world event surfacing.

Builds, filters, and converts world event candidates so the world produces
non-dialogue activity on its own.  Faction movements, rumor spread, resource
shortages, patrols, weather, and more are surfaced as player-visible events
when nearby, relevant, and sufficiently salient.

All logic is deterministic and bounded — no randomness.
"""
from __future__ import annotations

from app.runtime.clock import utc_now

from typing import Any, Dict, List, Set
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

# ── Hard caps ─────────────────────────────────────────────────────────────
MAX_WORLD_EVENTS_PER_TICK = 4
MAX_WORLD_EVENT_CANDIDATES = 16

# ── Event types ───────────────────────────────────────────────────────────

EVENT_TYPES = (
    "faction_movement",
    "rumor_spread",
    "resource_shortage",
    "public_disturbance",
    "weather_change",
    "patrol_arrival",
    "market_lockdown",
    "ritual_ceremony",
    "accident_explosion",
    "scouting_report",
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _utc_now_iso() -> str:
    return utc_now().isoformat()


def _is_internal_maintenance_event(raw: Dict[str, Any]) -> bool:
    """True if a raw event is simulation bookkeeping, not player-facing fiction."""
    raw = _safe_dict(raw)
    text = " ".join(
        [
            _safe_str(raw.get("type")),
            _safe_str(raw.get("event_type")),
            _safe_str(raw.get("description")),
            _safe_str(raw.get("summary")),
            _safe_str(raw.get("text")),
            _safe_str(raw.get("goal")),
            _safe_str(raw.get("label")),
        ]
    ).lower()

    blocked_phrases = (
        "maintain awareness",
        "awareness of player",
        "faction loyalty baseline",
        "loyalty baseline",
        "baseline",
        "goal maintenance",
        "state driver",
        "maintain awareness of player",
    )

    blocked_types = (
        "maintenance",
        "baseline",
        "goal_update",
        "awareness",
    )

    raw_type = _safe_str(raw.get("type") or raw.get("event_type")).lower()
    if raw_type in blocked_types:
        return True
    return any(p in text for p in blocked_phrases)


# ── Blank event template ─────────────────────────────────────────────────


def _blank_world_event() -> Dict[str, Any]:
    """Return an empty world event with all required fields."""
    return {
        "kind": "world_event",
        "event_type": "",
        "priority": 0.0,
        "text": "",
        "location_id": "",
        "faction_id": "",
        "visible_to_player": False,
        "interrupt": False,
        "source": "world_director",
        "tick": 0,
    }


def _make_world_event(**kwargs: Any) -> Dict[str, Any]:
    """Build a world event, filling missing fields with defaults."""
    event = _blank_world_event()
    for key, value in kwargs.items():
        if key in event:
            event[key] = value
    return event


# ── Player context helpers ────────────────────────────────────────────────


def _player_location(player_context: Dict[str, Any]) -> str:
    return _safe_str(player_context.get("player_location")).strip()


def _player_objective_location_ids(player_context: Dict[str, Any]) -> Set[str]:
    """Location IDs referenced by the player's active objectives."""
    ids: Set[str] = set()
    for obj in _safe_list(player_context.get("objectives")):
        obj = _safe_dict(obj)
        loc = _safe_str(obj.get("location_id"))
        if loc:
            ids.add(loc)
    return ids


def _player_opening_location_ids(player_context: Dict[str, Any]) -> Set[str]:
    """Location IDs tied to the opening state."""
    opening = _safe_dict(player_context.get("opening_runtime"))
    ids: Set[str] = set()
    for loc in _safe_list(opening.get("location_ids")):
        loc_str = _safe_str(loc)
        if loc_str:
            ids.add(loc_str)
    loc = _safe_str(opening.get("location_id"))
    if loc:
        ids.add(loc)
    return ids


def _recent_event_types(runtime_state: Dict[str, Any]) -> Set[str]:
    """Collect event_type values from recent world events in runtime state."""
    types: Set[str] = set()
    for evt in _safe_list(runtime_state.get("recent_world_events")):
        evt = _safe_dict(evt)
        t = _safe_str(evt.get("event_type"))
        if t:
            types.add(t)
    return types


def _is_relevant_location(
    loc: str,
    player_loc: str,
    objective_locs: Set[str],
    opening_locs: Set[str],
) -> bool:
    """True if *loc* is the player's location or tied to objective/opening."""
    if not loc:
        return True  # location-independent events are always relevant
    if loc == player_loc:
        return True
    if loc in objective_locs:
        return True
    if loc in opening_locs:
        return True
    return False


# ── Build candidates ──────────────────────────────────────────────────────


def build_world_event_candidates(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    player_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build a list of possible world event candidates from simulation state.

    Detection triggers:
    - Faction pressure changes
    - New incidents
    - Resource shortages
    - NPC movement patterns (arrival/departure)
    - Weather/environment changes
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    player_context = _safe_dict(player_context)

    candidates: List[Dict[str, Any]] = []
    tick = int(simulation_state.get("tick", 0) or 0)
    player_loc = _player_location(player_context)
    objective_locs = _player_objective_location_ids(player_context)
    opening_locs = _player_opening_location_ids(player_context)
    recent_types = _recent_event_types(runtime_state)

    # ── Faction pressure changes → faction_movement ──────────────────
    factions = _safe_dict(simulation_state.get("factions"))
    faction_pressure = _safe_dict(simulation_state.get("faction_pressure"))

    for fid in sorted(factions.keys()):
        faction = _safe_dict(factions.get(fid))
        fname = _safe_str(faction.get("name") or fid)
        loc = _safe_str(faction.get("location_id"))
        pressure = float(faction.get("pressure", 0) or 0)

        # Also check faction_pressure dict
        fp = _safe_dict(faction_pressure.get(fid))
        fp_level = float(fp.get("level", 0) or 0)
        fp_loc = _safe_str(fp.get("location_id"))
        effective_pressure = max(pressure, fp_level)
        effective_loc = loc or fp_loc

        if effective_pressure < 0.3:
            continue
        if not _is_relevant_location(effective_loc, player_loc, objective_locs, opening_locs):
            continue

        priority = 0.3 + min(effective_pressure, 1.0) * 0.3
        if effective_loc == player_loc:
            priority += 0.1

        candidates.append(_make_world_event(
            event_type="faction_movement",
            priority=min(priority, 1.0),
            text=f"The {fname} are on the move.",
            location_id=effective_loc,
            faction_id=fid,
            visible_to_player=True,
            interrupt=effective_pressure > 0.8,
            tick=tick,
        ))
        if len(candidates) >= MAX_WORLD_EVENT_CANDIDATES:
            return candidates[:MAX_WORLD_EVENT_CANDIDATES]

    # ── New incidents → public_disturbance / accident_explosion ───────
    incidents = _safe_list(simulation_state.get("incidents"))
    processed_incidents = set(_safe_list(runtime_state.get("processed_incident_ids")))

    for inc in incidents:
        inc = _safe_dict(inc)
        iid = _safe_str(inc.get("incident_id"))
        if not iid or iid in processed_incidents:
            continue

        loc = _safe_str(inc.get("location_id"))
        if not _is_relevant_location(loc, player_loc, objective_locs, opening_locs):
            continue

        severity = float(inc.get("severity", 0.3) or 0.3)
        inc_type = _safe_str(inc.get("type")).lower()

        if inc_type in ("explosion", "fire", "accident", "collapse"):
            event_type = "accident_explosion"
            text = _safe_str(inc.get("description") or f"An explosion rocks the area near {loc}.")
        else:
            event_type = "public_disturbance"
            text = _safe_str(inc.get("description") or "A disturbance erupts nearby.")

        priority = 0.35 + min(severity, 1.0) * 0.35
        if loc == player_loc:
            priority += 0.1

        candidates.append(_make_world_event(
            event_type=event_type,
            priority=min(priority, 1.0),
            text=text,
            location_id=loc,
            visible_to_player=True,
            interrupt=severity > 0.7,
            tick=tick,
        ))
        if len(candidates) >= MAX_WORLD_EVENT_CANDIDATES:
            return candidates[:MAX_WORLD_EVENT_CANDIDATES]

    # ── Resource shortages → resource_shortage ────────────────────────
    resources = _safe_dict(simulation_state.get("resources"))

    for rid in sorted(resources.keys()):
        res = _safe_dict(resources.get(rid))
        rname = _safe_str(res.get("name") or rid)
        loc = _safe_str(res.get("location_id"))
        supply = float(res.get("supply", 1.0) or 1.0)
        threshold = float(res.get("shortage_threshold", 0.3) or 0.3)

        if supply > threshold:
            continue
        if not _is_relevant_location(loc, player_loc, objective_locs, opening_locs):
            continue

        priority = 0.3 + (1.0 - min(supply / max(threshold, 0.01), 1.0)) * 0.3
        if loc == player_loc:
            priority += 0.1

        candidates.append(_make_world_event(
            event_type="resource_shortage",
            priority=min(priority, 1.0),
            text=f"Supplies of {rname} are running dangerously low.",
            location_id=loc,
            visible_to_player=True,
            interrupt=False,
            tick=tick,
        ))
        if len(candidates) >= MAX_WORLD_EVENT_CANDIDATES:
            return candidates[:MAX_WORLD_EVENT_CANDIDATES]

    # ── NPC movement patterns → patrol_arrival / scouting_report ─────
    npc_decisions = _safe_dict(simulation_state.get("npc_decisions"))
    npc_index = _safe_dict(simulation_state.get("npc_index"))

    for npc_id in sorted(npc_decisions.keys()):
        decision = _safe_dict(npc_decisions.get(npc_id))
        action = _safe_str(decision.get("action") or decision.get("action_type")).lower()

        if action not in ("move", "travel", "patrol", "scout", "arrive", "depart"):
            continue

        npc_info = _safe_dict(npc_index.get(npc_id))
        npc_name = _safe_str(npc_info.get("name") or npc_id)
        npc_role = _safe_str(npc_info.get("role")).lower()
        target_loc = _safe_str(decision.get("target_location") or decision.get("destination"))
        origin_loc = _safe_str(decision.get("location_id") or npc_info.get("location_id"))

        arriving_at_player = target_loc == player_loc
        departing_from_player = origin_loc == player_loc

        if not arriving_at_player and not departing_from_player:
            continue

        if npc_role in ("guard", "soldier", "patrol", "enforcer"):
            event_type = "patrol_arrival"
            text = f"A patrol led by {npc_name} {'arrives' if arriving_at_player else 'departs'}."
            priority = 0.45
        elif action in ("scout",):
            event_type = "scouting_report"
            text = f"{npc_name} returns with a scouting report."
            priority = 0.4
        else:
            continue

        candidates.append(_make_world_event(
            event_type=event_type,
            priority=min(priority, 1.0),
            text=text,
            location_id=player_loc,
            visible_to_player=True,
            interrupt=False,
            tick=tick,
        ))
        if len(candidates) >= MAX_WORLD_EVENT_CANDIDATES:
            return candidates[:MAX_WORLD_EVENT_CANDIDATES]

    # ── Weather / environment changes → weather_change ────────────────
    environment = _safe_dict(simulation_state.get("environment"))
    prev_weather = _safe_str(
        _safe_dict(runtime_state.get("previous_environment")).get("weather")
    )
    current_weather = _safe_str(environment.get("weather"))

    if current_weather and current_weather != prev_weather:
        candidates.append(_make_world_event(
            event_type="weather_change",
            priority=0.25,
            text=f"The weather shifts to {current_weather}.",
            location_id=player_loc,
            visible_to_player=True,
            interrupt=False,
            tick=tick,
        ))

    # ── World events from simulation_state["events"] ─────────────────
    # Catch-all: rumor_spread, market_lockdown, ritual_ceremony
    raw_events = _safe_list(simulation_state.get("events"))
    processed_event_ids = set(_safe_list(runtime_state.get("processed_event_ids")))

    _TYPE_MAP = {
        "rumor": "rumor_spread",
        "gossip": "rumor_spread",
        "market": "market_lockdown",
        "lockdown": "market_lockdown",
        "trade_halt": "market_lockdown",
        "ritual": "ritual_ceremony",
        "ceremony": "ritual_ceremony",
        "protest": "ritual_ceremony",
    }

    for raw in raw_events:
        raw = _safe_dict(raw)
        eid = _safe_str(raw.get("event_id"))
        if not eid or eid in processed_event_ids:
            continue

        # Never surface internal maintenance / bookkeeping to the player.
        if _is_internal_maintenance_event(raw):
            continue

        loc = _safe_str(raw.get("location_id"))
        if not _is_relevant_location(loc, player_loc, objective_locs, opening_locs):
            continue

        raw_type = _safe_str(raw.get("type") or raw.get("event_type")).lower()
        event_type = _TYPE_MAP.get(raw_type, "")

        # Try to infer from keywords in description
        if not event_type:
            desc_lower = _safe_str(raw.get("description") or raw.get("summary")).lower()
            for keyword, mapped in _TYPE_MAP.items():
                if keyword in desc_lower:
                    event_type = mapped
                    break

        if not event_type:
            continue

        priority = float(raw.get("priority", 0.3) or 0.3)
        if loc == player_loc:
            priority += 0.1

        candidates.append(_make_world_event(
            event_type=event_type,
            priority=min(priority, 1.0),
            text=_safe_str(raw.get("description") or raw.get("summary") or f"A {event_type.replace('_', ' ')} occurs."),
            location_id=loc,
            faction_id=_safe_str(raw.get("faction_id")),
            visible_to_player=True,
            interrupt=bool(raw.get("interrupt")),
            tick=tick,
        ))
        if len(candidates) >= MAX_WORLD_EVENT_CANDIDATES:
            break

    # Remove duplicates of recent event types (keep first occurrence)
    seen_types: Set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for c in candidates:
        etype = _safe_str(c.get("event_type"))
        if etype in recent_types:
            continue
        if etype in seen_types:
            continue
        seen_types.add(etype)
        deduped.append(c)

    return deduped[:MAX_WORLD_EVENT_CANDIDATES]


# ── Filter world events ──────────────────────────────────────────────────


def filter_world_events(
    events: List[Dict[str, Any]],
    session: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Filter world events for player visibility, relevance, salience, and duplication.

    Only emit if:
    - visible_to_player is True
    - nearby or relevant to objective/opening
    - sufficiently salient (priority >= 0.2)
    - not duplicate of recent event
    """
    events = _safe_list(events)
    session = _safe_dict(session)
    sim = _safe_dict(session.get("simulation_state"))
    runtime = _safe_dict(session.get("runtime_state"))

    player_state = _safe_dict(sim.get("player_state"))
    player_loc = _safe_str(player_state.get("location_id")).strip()

    objective_locs: Set[str] = set()
    for obj in _safe_list(sim.get("objectives")):
        obj = _safe_dict(obj)
        loc = _safe_str(obj.get("location_id"))
        if loc:
            objective_locs.add(loc)

    opening = _safe_dict(runtime.get("opening_runtime"))
    opening_locs: Set[str] = set()
    for loc in _safe_list(opening.get("location_ids")):
        loc_str = _safe_str(loc)
        if loc_str:
            opening_locs.add(loc_str)
    opening_loc = _safe_str(opening.get("location_id"))
    if opening_loc:
        opening_locs.add(opening_loc)

    recent_types = _recent_event_types(runtime)

    result: List[Dict[str, Any]] = []
    seen_types: Set[str] = set()

    for evt in events:
        evt = _safe_dict(evt)

        # Must be visible
        if not evt.get("visible_to_player"):
            continue

        # Salience threshold
        priority = float(evt.get("priority", 0) or 0)
        if priority < 0.2:
            continue

        # Location relevance
        loc = _safe_str(evt.get("location_id"))
        if loc and loc != player_loc and loc not in objective_locs and loc not in opening_locs:
            continue

        # Duplicate suppression
        etype = _safe_str(evt.get("event_type"))
        if etype in recent_types:
            continue
        if etype in seen_types:
            continue
        seen_types.add(etype)

        result.append(evt)

    # Sort by priority descending, then event_type for determinism
    result.sort(key=lambda e: (-float(e.get("priority", 0) or 0), _safe_str(e.get("event_type"))))

    return result[:MAX_WORLD_EVENTS_PER_TICK]


# ── Apply world behavior to events ───────────────────────────────────────

_AMBIENT_ACTIVITY_THRESHOLDS = {
    "low": 0.4,
    "medium": 0.2,
    "high": 0.1,
}

_WORLD_PRESSURE_PRIORITY_BONUS = {
    "gentle": -0.1,
    "standard": 0.0,
    "harsh": 0.1,
}


def apply_world_behavior_to_events(
    events: List[Dict[str, Any]],
    world_behavior: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply world_behavior config to world events.

    Effects (bias-only, never forcing impossible events):
    - ambient_activity adjusts the salience threshold
    - world_pressure adjusts priority bonuses and interrupt thresholds
    """
    events = _safe_list(events)
    world_behavior = _safe_dict(world_behavior)
    if not events:
        return []

    ambient_activity = _safe_str(world_behavior.get("ambient_activity")).lower()
    world_pressure = _safe_str(world_behavior.get("world_pressure")).lower()

    threshold = _AMBIENT_ACTIVITY_THRESHOLDS.get(ambient_activity, 0.2)
    pressure_bonus = _WORLD_PRESSURE_PRIORITY_BONUS.get(world_pressure, 0.0)

    result: List[Dict[str, Any]] = []
    for evt in events:
        evt = dict(_safe_dict(evt))
        priority = float(evt.get("priority", 0) or 0)

        # Apply world pressure bonus
        priority += pressure_bonus

        # Harsh pressure lowers interrupt threshold
        if world_pressure == "harsh" and priority >= 0.6:
            evt["interrupt"] = True
        # Gentle pressure raises interrupt threshold
        elif world_pressure == "gentle" and priority < 0.8:
            evt["interrupt"] = False

        priority = max(0.0, min(priority, 1.0))
        evt["priority"] = priority

        # Enforce ambient_activity threshold
        if priority < threshold:
            continue

        result.append(evt)

    # Sort by priority descending, then event_type for determinism
    result.sort(key=lambda e: (-float(e.get("priority", 0) or 0), _safe_str(e.get("event_type"))))

    return result[:MAX_WORLD_EVENTS_PER_TICK]


# ── Convert events to ambient updates ────────────────────────────────────


def _blank_ambient_update() -> Dict[str, Any]:
    """Return an empty ambient update matching ambient_builder format."""
    return {
        "ambient_id": "",
        "seq": 0,
        "tick": 0,
        "kind": "world_event",
        "priority": 0.0,
        "interrupt": False,
        "speaker_id": "",
        "speaker_name": "",
        "target_id": "",
        "target_name": "",
        "scene_id": "",
        "location_id": "",
        "text": "",
        "structured": {},
        "source_event_ids": [],
        "source": "simulation",
        "created_at": "",
    }


def convert_events_to_ambient_updates(
    events: List[Dict[str, Any]],
    runtime_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Convert world events into ambient_builder-compatible update dicts.

    Matches the structure of make_ambient_update in ambient_builder.py
    without importing from it.
    """
    events = _safe_list(events)
    runtime_state = _safe_dict(runtime_state)
    seq = int(runtime_state.get("ambient_seq", 0) or 0)

    updates: List[Dict[str, Any]] = []

    for evt in events:
        evt = _safe_dict(evt)
        update = _blank_ambient_update()

        update["tick"] = int(evt.get("tick", 0) or 0)
        update["kind"] = "world_event"
        update["priority"] = float(evt.get("priority", 0) or 0)
        update["interrupt"] = bool(evt.get("interrupt"))
        update["location_id"] = _safe_str(evt.get("location_id"))
        update["text"] = _safe_str(evt.get("text"))
        update["source"] = _safe_str(evt.get("source") or "world_director")
        update["created_at"] = _utc_now_iso()

        # Encode event_type and faction_id in structured metadata
        structured: Dict[str, Any] = {}
        event_type = _safe_str(evt.get("event_type"))
        if event_type:
            structured["event_type"] = event_type
        faction_id = _safe_str(evt.get("faction_id"))
        if faction_id:
            structured["faction_id"] = faction_id
        update["structured"] = structured

        updates.append(update)

        if len(updates) >= MAX_WORLD_EVENTS_PER_TICK:
            break

    return updates
