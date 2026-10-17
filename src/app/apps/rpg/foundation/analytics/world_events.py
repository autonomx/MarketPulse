"""World Events view builder.

Constructs a structured view of local, global, and director-pressure events
for the frontend World Events panel.

Simulation truth stays in simulation_state.  This module produces
**presentation data only** — never written back as simulation authority.

Deterministic ordering everywhere: (scope_order, -priority, tick, event_id).
Hard-capped per section.
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

_MAX_LOCAL = 12
_MAX_GLOBAL = 12
_MAX_DIRECTOR = 12
_MAX_RECENT = 12
_MAX_PLAYER_WORLD_VIEW_ROWS = 8

_SCOPE_ORDER = {"local": 0, "global": 1, "director": 2}


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _normalize_world_consequence(record: Dict[str, Any]) -> Dict[str, Any]:
    record = _safe_dict(record)
    return {
        "consequence_id": _safe_str(record.get("consequence_id")),
        "kind": _safe_str(record.get("kind")),
        "scope": _safe_str(record.get("scope")) or "local",
        "location_id": _safe_str(record.get("location_id")),
        "summary": _safe_str(record.get("summary")),
        "source_actor_id": _safe_str(record.get("source_actor_id")),
        "source_activity_id": _safe_str(record.get("source_activity_id")),
        "tick": _safe_int(record.get("tick"), 0),
        "priority": max(1, _safe_int(record.get("priority"), 1)),
        "tags": [str(x).strip() for x in _safe_list(record.get("tags")) if str(x).strip()],
    }


# ── World View normalization helpers ─────────────────────────────────────────

def _player_bias(row: Dict[str, Any]) -> int:
    row = _safe_dict(row)
    tags = [str(x).strip() for x in _safe_list(row.get("tags")) if str(x).strip()]
    source = _safe_str(row.get("source"))
    kind = _safe_str(row.get("kind"))
    if (
        "player_action" in tags
        or "player_engaged" in tags
        or "crowd_attention" in tags
        or "rumor_seed" in tags
        or "scene_impact" in tags
        or source == "semantic_player_runtime"
        or source == "npc_reaction_layer"
        or kind in ("interaction_beat", "player_action_consequence", "world_pressure", "world_rumor", "npc_reaction_beat")
    ):
        return 1
    if "escalation" in tags:
        return 1
    return 0


def _row_sort_key(row: Dict[str, Any]) -> tuple:
    row = _safe_dict(row)
    scope = _safe_str(row.get("scope")).lower()
    source = _safe_str(row.get("source")).lower()
    tags = {str(x).strip().lower() for x in _safe_list(row.get("tags")) if str(x).strip()}

    interaction_priority = 1 if (
        source == "semantic_player_runtime"
        or row.get("kind") == "interaction_beat"
        or "player_engaged" in tags
        or "player_action" in tags
    ) else 0

    reaction_priority = 1 if (
        source == "npc_reaction_layer"
        or row.get("kind") == "npc_reaction_beat"
        or "npc_reaction" in tags
    ) else 0

    return (
        -interaction_priority,
        -reaction_priority,
        -(_player_bias(row) * 10),
        _SCOPE_ORDER.get(scope, 9),
        -float(row.get("priority", 0) or 0),
        -_safe_int(row.get("tick"), 0),
        _safe_str(row.get("event_id")),
    )


def _make_event_row(
    *,
    event_id: str,
    scope: str,
    kind: str,
    title: str,
    summary: str,
    tick: int = 0,
    actors: List[str] | None = None,
    actor_label: str = "",
    location_id: str = "",
    priority: float = 0.0,
    status: str = "active",
    source: str = "simulation",
    location_label: str = "",
    tags: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "scope": scope,
        "kind": kind,
        "title": title,
        "summary": summary,
        "tick": tick,
        "actors": actors or [],
        "actor_label": actor_label,
        "location_id": location_id,
        "priority": max(0.0, min(priority, 1.0)),
        "status": status,
        "source": source,
        "location_label": location_label,
        "tags": tags or [],
    }


# ── Local events ─────────────────────────────────────────────────────────

def _build_local_events(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build local event rows from ambient updates, incidents, and conversations."""
    rows: List[Dict[str, Any]] = []
    player_state = _safe_dict(simulation_state.get("player_state"))
    player_loc = _safe_str(player_state.get("location_id"))
    tick = _safe_int(simulation_state.get("tick"), 0)

    # From recent events at player location
    for event in _safe_list(simulation_state.get("events"))[-24:]:
        event = _safe_dict(event)
        loc = _safe_str(event.get("location_id"))
        if loc and loc != player_loc:
            continue
        eid = _safe_str(event.get("event_id"))
        rows.append(_make_event_row(
            event_id=eid or f"evt:{tick}:{len(rows)}",
            scope="local",
            kind=_safe_str(event.get("type") or "world_event"),
            title=_safe_str(event.get("type") or "Event"),
            summary=_safe_str(event.get("description") or event.get("summary") or "Something happens."),
            tick=_safe_int(event.get("tick"), tick),
            location_id=loc,
            priority=0.5,
            source="simulation",
        ))

    # From recent incidents at player location
    for inc in _safe_list(simulation_state.get("incidents"))[-8:]:
        inc = _safe_dict(inc)
        loc = _safe_str(inc.get("location_id"))
        if loc and loc != player_loc:
            continue
        iid = _safe_str(inc.get("incident_id"))
        rows.append(_make_event_row(
            event_id=iid or f"inc:{tick}:{len(rows)}",
            scope="local",
            kind="incident",
            title="Incident",
            summary=_safe_str(inc.get("description") or inc.get("summary") or "An incident unfolds."),
            tick=_safe_int(inc.get("tick"), tick),
            location_id=loc,
            priority=0.55,
            source="simulation",
        ))

    # From queued ambient updates (reaction beats)
    for update in _safe_list(runtime_state.get("ambient_queue"))[-12:]:
        update = _safe_dict(update)
        kind = _safe_str(update.get("kind"))
        if kind in ("follow_reaction", "caution_reaction", "assist_reaction", "warning", "npc_to_player"):
            rows.append(_make_event_row(
                event_id=_safe_str(update.get("ambient_id")) or f"amb:{tick}:{len(rows)}",
                scope="local",
                kind=kind,
                title=kind.replace("_", " ").title(),
                summary=_safe_str(update.get("text") or ""),
                tick=_safe_int(update.get("tick"), tick),
                actors=[_safe_str(update.get("speaker_id"))],
                location_id=_safe_str(update.get("location_id")),
                priority=float(update.get("priority", 0.4) or 0.4),
                source="ambient_runtime",
            ))

    rows.sort(key=_row_sort_key)
    return rows[:_MAX_LOCAL]


# ── Global events ────────────────────────────────────────────────────────

def _build_global_events(
    simulation_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build global event rows from world consequences, faction shifts, threads."""
    rows: List[Dict[str, Any]] = []
    tick = _safe_int(simulation_state.get("tick"), 0)

    sandbox_state = _safe_dict(simulation_state.get("sandbox_state"))
    for wc in _safe_list(sandbox_state.get("world_consequences"))[-12:]:
        wc = _safe_dict(wc)
        rows.append(_make_event_row(
            event_id=_safe_str(wc.get("consequence_id")) or f"wc:{tick}:{len(rows)}",
            scope="global",
            kind="world_consequence",
            title=_safe_str(wc.get("type") or "World Change"),
            summary=_safe_str(wc.get("description") or wc.get("summary") or "The world shifts."),
            tick=_safe_int(wc.get("tick"), tick),
            priority=0.5,
            source="sandbox",
        ))

    # Faction pressure shifts
    faction_pressure = _safe_dict(simulation_state.get("faction_pressure"))
    for fid, fp in sorted(faction_pressure.items()):
        fp = _safe_dict(fp)
        level = float(fp.get("level", 0) or 0)
        if level > 0.4:
            fname = _safe_str(fp.get("name") or fid)
            rows.append(_make_event_row(
                event_id=f"fp:{fid}:{tick}",
                scope="global",
                kind="faction_pressure",
                title=f"{fname} Pressure",
                summary=f"Faction pressure at {level:.1f}",
                tick=tick,
                priority=min(level, 1.0),
                source="simulation",
            ))

    # Threads / hotspots
    for thread in _safe_list(simulation_state.get("threads"))[-6:]:
        thread = _safe_dict(thread)
        tid = _safe_str(thread.get("thread_id"))
        rows.append(_make_event_row(
            event_id=tid or f"thr:{tick}:{len(rows)}",
            scope="global",
            kind="thread",
            title=_safe_str(thread.get("name") or "Active Thread"),
            summary=_safe_str(thread.get("summary") or "An ongoing development."),
            tick=_safe_int(thread.get("tick"), tick),
            priority=0.45,
            source="simulation",
        ))

    rows.sort(key=_row_sort_key)
    return rows[:_MAX_GLOBAL]


# ── Director pressure ────────────────────────────────────────────────────

def _build_director_pressure(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build director pressure rows from narrative control structures.

    Always labeled scope="director" and source="director_bias".
    Never mixed into local/global.
    """
    rows: List[Dict[str, Any]] = []
    tick = _safe_int(simulation_state.get("tick"), 0)

    # Arc control structures
    arc_control = _safe_dict(simulation_state.get("arc_control"))
    if arc_control:
        current_arc = _safe_str(arc_control.get("current_arc"))
        if current_arc:
            rows.append(_make_event_row(
                event_id=f"arc:{current_arc}:{tick}",
                scope="director",
                kind="arc_control",
                title="Current Arc",
                summary=current_arc,
                tick=tick,
                priority=0.6,
                source="director_bias",
            ))

    # Narrative pressure
    narrative_pressure = _safe_dict(simulation_state.get("narrative_pressure"))
    if narrative_pressure:
        level = float(narrative_pressure.get("level", 0) or 0)
        if level > 0.2:
            rows.append(_make_event_row(
                event_id=f"np:{tick}",
                scope="director",
                kind="narrative_pressure",
                title="Narrative Pressure",
                summary=f"Pressure level: {level:.1f}",
                tick=tick,
                priority=min(level, 1.0),
                source="director_bias",
            ))

    # Director hints / biases from runtime
    opening_runtime = _safe_dict(runtime_state.get("opening_runtime"))
    if opening_runtime.get("active"):
        hook = _safe_str(opening_runtime.get("hook_id") or opening_runtime.get("starter_conflict"))
        if hook:
            rows.append(_make_event_row(
                event_id=f"opening:{hook}:{tick}",
                scope="director",
                kind="opening_hook",
                title="Opening Hook",
                summary=f"Active opening: {hook}",
                tick=tick,
                priority=0.55,
                source="director_bias",
            ))

    rows.sort(key=_row_sort_key)
    return rows[:_MAX_DIRECTOR]


# ── Public API ───────────────────────────────────────────────────────────

def build_world_events_view(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    *,
    limit: int = 48,
) -> Dict[str, Any]:
    """Build the full World Events view for the frontend panel.

    Returns presentation-only data with strict scope separation.
    Director rows are always labeled as bias/setup, never as world fact.
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)

    local_events = _build_local_events(simulation_state, runtime_state)
    global_events = _build_global_events(simulation_state)
    director_pressure = _build_director_pressure(simulation_state, runtime_state)

    # Recent changes: merge and sort all
    all_rows = local_events + global_events + director_pressure
    all_rows.sort(key=_row_sort_key)
    recent_changes = all_rows[:_MAX_RECENT]

    return {
        "current_tick": _safe_int(simulation_state.get("tick"), 0),
        "local_events": local_events,
        "global_events": global_events,
        "director_pressure": director_pressure,
        "recent_changes": recent_changes,
    }


def build_incremental_world_event_rows(
    simulation_state: Dict[str, Any],
    runtime_state: Dict[str, Any],
    debug_trace: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    """Build incremental event rows for appending to recent_world_event_rows.

    Returns a small bounded list of new rows from the current tick.
    """
    simulation_state = _safe_dict(simulation_state)
    runtime_state = _safe_dict(runtime_state)
    tick = _safe_int(simulation_state.get("tick"), 0)

    rows: List[Dict[str, Any]] = []
    seen_event_ids: set[str] = set()

    def _append_row(row: Dict[str, Any]) -> None:
        row = _safe_dict(row)
        event_id = _safe_str(row.get("event_id")).strip()
        if not event_id or event_id in seen_event_ids:
            return
        seen_event_ids.add(event_id)
        rows.append(row)

    # Pull from ambient queue — only recent items at current tick
    for update in _safe_list(runtime_state.get("ambient_queue"))[-8:]:
        update = _safe_dict(update)
        utick = _safe_int(update.get("tick"), 0)
        if utick < tick - 1:
            continue
        kind = _safe_str(update.get("kind"))
        if kind in ("system_summary",):
            continue
        scope = "local"
        if kind in ("world_event",) and not _safe_str(update.get("location_id")):
            scope = "global"
        _append_row(_make_event_row(
            event_id=_safe_str(update.get("ambient_id")) or f"inc_row:{tick}:{len(rows)}",
            scope=scope,
            kind=kind,
            title=kind.replace("_", " ").title(),
            summary=_safe_str(update.get("text") or "")[:200],
            tick=utick,
            actors=[_safe_str(update.get("speaker_id"))] if _safe_str(update.get("speaker_id")) else [],
            location_id=_safe_str(update.get("location_id")),
            priority=float(update.get("priority", 0.3) or 0.3),
            source="ambient_runtime",
        ))

    # Pull from accepted semantic state-change events — these are canonical
    # post-LLM events already accepted by the runtime and should surface in
    # the incremental world events feed.
    for event in _safe_list(runtime_state.get("accepted_state_change_events"))[-8:]:
        event = _safe_dict(event)
        etick = _safe_int(event.get("tick"), 0)

        if etick < tick - 1:
            continue
        beat = _safe_dict(event.get("beat"))
        summary = (
            _safe_str(event.get("summary")).strip()
            or _safe_str(beat.get("summary")).strip()
            or "A character changes behavior."
        )
        location_id = _safe_str(event.get("location_id"))

        _append_row(_make_event_row(
            event_id=_safe_str(event.get("event_id")) or f"state_change:{etick}:{len(rows)}",
            scope="local" if location_id else "global",
            kind="state_change",
            title="NPC Activity",
            summary=summary[:200],
            tick=etick,
            actors=[_safe_str(event.get("actor_id"))] if _safe_str(event.get("actor_id")) else [],
            location_id=location_id,
            priority=float(beat.get("priority", 0.65) or 0.65),
            status="active",
            source="semantic_runtime",
        ))

    # Pull from recent scene beats — especially state_change_beat entries
    # emitted during idle/world advancement. These are presentation-facing
    # beats and should appear in the recent feed.
    for beat in _safe_list(runtime_state.get("recent_scene_beats"))[-8:]:
        beat = _safe_dict(beat)
        btick = _safe_int(beat.get("tick"), 0)

        if btick < tick - 1:
            continue
        kind = _safe_str(beat.get("kind"))
        if not kind:
            continue
        if kind not in ("state_change_beat", "world_event", "director_pressure", "incident", "consequence", "interaction_beat", "activity_beat"):
            continue
        summary = _safe_str(beat.get("summary")).strip()
        if not summary:
            continue
        location_id = _safe_str(beat.get("location_id"))

        _append_row(_make_event_row(
            event_id=_safe_str(beat.get("beat_id")) or f"scene_beat:{btick}:{len(rows)}",
            scope="local" if location_id else "global",
            kind=kind,
            title="Scene Development",
            summary=summary[:200],
            tick=btick,
            actors=[_safe_str(beat.get("actor_id"))] if _safe_str(beat.get("actor_id")) else [],
            location_id=location_id,
            priority=float(beat.get("priority", 0.55) or 0.55),
            status="active",
            source="scene_beats",
        ))

    # ── Suppress generic NPC beats when player action is active ─────────
    # When the player is actively doing something notable, demote routine
    # NPC activity beats so player-action-related events surface first.
    last_player_action = _safe_dict(runtime_state.get("last_player_action"))
    player_action_active = bool(_safe_str(last_player_action.get("text")))
    if player_action_active:
        player_rows = []
        generic_rows = []
        for row in rows:
            row = _safe_dict(row)
            tags = [str(x).strip() for x in _safe_list(row.get("tags")) if str(x).strip()]
            source = _safe_str(row.get("source"))
            kind = _safe_str(row.get("kind"))
            is_player_related = (
                "player_action" in tags
                or "player_engaged" in tags
                or "crowd_attention" in tags
                or "rumor_seed" in tags
                or "scene_impact" in tags
                or source == "semantic_player_runtime"
                or kind in ("interaction_beat", "player_action_consequence", "world_pressure", "world_rumor")
            )
            if is_player_related:
                player_rows.append(row)
            else:
                generic_rows.append(row)
        # Keep all player-related rows, fill remaining slots with generic
        player_rows.sort(key=_row_sort_key)
        generic_rows.sort(key=_row_sort_key)
        rows = player_rows + generic_rows

    rows.sort(key=_row_sort_key)
    return rows[:8]
