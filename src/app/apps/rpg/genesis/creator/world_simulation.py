"""Phase 3A — World Simulation Engine Core.

Provides deterministic, stateful simulation state management for the
adventure builder.  All functions accept a raw ``setup_payload`` dict
(the same shape the creator routes already traffic) and return plain
dicts suitable for JSON serialization.

Simulation state lives in ``metadata.simulation_state`` so it stays
compatible with the existing creator flow.

Rules are intentionally simple and fully deterministic with **stateful
pressure evolution**: values step up or down from their previous state
rather than being recomputed from scratch each tick.

* **Threads** — base pressure steps up if connected to multiple factions
  or a hot location; steps down otherwise.  Capped at 0-5.
* **Factions** — pressure steps up if any connected threads are active;
  steps down otherwise.  Capped at 0-5.
* **Locations** — heat steps up if NPCs or threads present; steps down
  otherwise.  Faction pressure >= 3 also pushes heat up.  Capped at 0-5.
"""

from __future__ import annotations

import copy
from typing import Any, cast

from app.apps.rpg.narration.ai.llm_mind import NPCMind
from app.apps.rpg.foundation.persistence.save_schema import CURRENT_RPG_SCHEMA_VERSION, ENGINE_VERSION
from app.apps.rpg.foundation.sandbox import (
    build_world_consequences,
    project_outcomes_from_state,
    update_faction_trends,
    update_location_trends,
    update_rumor_feedback,
    update_thread_trends,
)
from app.apps.rpg.world.social import (
    AllianceSystem,
    BetrayalPropagation,
    GroupDecisionEngine,
    ReputationGraph,
    RumorSystem,
)
from app.apps.rpg.world.contracts import ConversationHooks
from app.apps.rpg.world.social.conversation_engine import (
    run_conversation_tick,
    try_start_party_reaction_conversation,
)
from app.apps.rpg.world.social.offscreen_conversations import run_offscreen_conversation_pass
from app.apps.rpg.world.social.rumor_from_conversations import (
    collect_rumors_from_recent_conversations,
)

from .world_debug import summarize_tick_changes
from .world_effects import (
    apply_effects_to_simulation_state,
    build_effects_from_consequences,
    compute_effect_diff,
    decay_active_effects,
    merge_active_effects,
)
from .world_events import generate_consequences, generate_world_events
from .world_incidents import (
    compute_incident_diff,
    compute_policy_reaction_diff,
    decay_incidents,
    generate_policy_reactions,
    merge_incidents,
    spawn_incidents_from_state_diff,
)
from .world_simulation_reports import (
    _step_hash,
    compute_simulation_diff,
    evaluate_world_expansion,  # noqa: F401 - re-exported for the historical API.
    summarize_simulation_step,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_HISTORY = 20
PRESSURE_CAP = 5


# ---------------------------------------------------------------------------
# Phase 6 — NPC mind helpers
# ---------------------------------------------------------------------------


def _safe_str_p6(value):
    if value is None:
        return ""
    return str(value)


def _safe_dict_p6(value):
    if isinstance(value, dict):
        return value
    return {}


def _iter_npc_definitions(setup_payload):
    """Best-effort extractor for NPC definitions from creator setup."""
    setup_payload = setup_payload or {}

    direct = setup_payload.get("npcs")
    if isinstance(direct, list):
        for item in direct:
            if isinstance(item, dict):
                yield item

    # Also check npc_seeds (the standard key in this codebase)
    seeds = setup_payload.get("npc_seeds")
    if isinstance(seeds, list):
        for item in seeds:
            if isinstance(item, dict):
                yield item

    for section_key in ("world", "actors", "entities", "cast"):
        section = setup_payload.get(section_key)
        if isinstance(section, dict):
            npcs = section.get("npcs")
            if isinstance(npcs, list):
                for item in npcs:
                    if isinstance(item, dict):
                        yield item


def _build_npc_index(setup_payload):
    npc_index = {}
    for item in _iter_npc_definitions(setup_payload):
        npc_id = _safe_str_p6(item.get("id") or item.get("npc_id"))
        if not npc_id:
            continue
        npc_index[npc_id] = {
            "npc_id": npc_id,
            "name": _safe_str_p6(item.get("name")) or npc_id,
            "role": _safe_str_p6(item.get("role")),
            "faction_id": _safe_str_p6(item.get("faction_id")),
            "location_id": _safe_str_p6(item.get("location_id")),
        }
    return dict(sorted(npc_index.items()))


def _load_npc_minds(simulation_state, npc_index):
    simulation_state = simulation_state or {}
    raw = simulation_state.get("npc_minds") or {}
    minds = {}
    for npc_id, npc_ctx in sorted(npc_index.items()):
        if npc_id in raw and isinstance(raw[npc_id], dict):
            minds[npc_id] = NPCMind.from_dict(raw[npc_id])
        else:
            minds[npc_id] = NPCMind(npc_id=npc_id)
    return minds


def _decision_to_event(decision_dict, npc_context, tick):
    decision_dict = decision_dict or {}
    npc_context = npc_context or {}

    npc_id = _safe_str_p6(decision_dict.get("npc_id"))
    action_type = _safe_str_p6(decision_dict.get("action_type"))
    target_id = _safe_str_p6(decision_dict.get("target_id"))
    target_kind = _safe_str_p6(decision_dict.get("target_kind"))
    location_id = _safe_str_p6(decision_dict.get("location_id")) or _safe_str_p6(npc_context.get("location_id"))
    urgency = float(decision_dict.get("urgency", 0.0) or 0.0)

    if action_type in {"wait", ""}:
        return None

    npc_name = _safe_str_p6(npc_context.get("name")) or npc_id
    dialogue_hint = _safe_str_p6(
        decision_dict.get("dialogue")
        or decision_dict.get("text")
        or decision_dict.get("dialogue_hint")
    )
    npc_index = _safe_dict_p6(npc_context.get("npc_index"))
    target_name = ""
    if target_id:
        target_row = _safe_dict_p6(npc_index.get(target_id))
        target_name = _safe_str_p6(target_row.get("name")) or target_id

    # IMPORTANT:
    # `reason` is internal planner/debug metadata. Preserve it for inspector /
    # debugging, but never expose it directly as player-facing event text.
    public_summary = ""
    action_key = action_type.strip().lower()

    if action_key in {"move", "travel"}:
        destination = _safe_str_p6(
            decision_dict.get("target_location")
            or decision_dict.get("destination")
            or decision_dict.get("destination_id")
        )
        public_summary = (
            f"{npc_name} heads toward {destination}."
            if destination
            else f"{npc_name} moves to a new position."
        )
    elif action_key in {"depart", "leave"}:
        public_summary = f"{npc_name} leaves the area."
    elif action_key in {"arrive", "enter"}:
        public_summary = f"{npc_name} arrives."
    elif action_key in {"observe", "watch"}:
        public_summary = f"{npc_name} watches the situation carefully."
    elif action_key == "stabilize":
        public_summary = f"{npc_name} tries to restore order nearby."
    elif action_key == "retaliate":
        public_summary = f"{npc_name} prepares to strike back."
    elif action_key in {"avoid", "retreat", "withdraw"}:
        public_summary = f"{npc_name} keeps their distance."
    elif action_key in {"negotiate", "parley"}:
        public_summary = (
            f"{npc_name} speaks with {target_name}."
            if target_name else
            f"{npc_name} cautiously opens contact."
        )
    elif action_key == "investigate":
        public_summary = f"{npc_name} investigates suspicious developments."
    elif action_key == "support":
        public_summary = (
            f"{npc_name} checks in with {target_name}."
            if target_name else
            f"{npc_name} moves to support their allies."
        )
    elif action_key == "move":
        public_summary = f"{npc_name} heads elsewhere."
    elif action_key in {"speak", "talk", "address"}:
        public_summary = dialogue_hint or f"{npc_name} speaks."
    elif action_key in {"warn", "threaten"}:
        public_summary = dialogue_hint or f"{npc_name} issues a warning."
    elif action_key == "attack":
        public_summary = dialogue_hint or f"{npc_name} lashes out."
    else:
        public_summary = dialogue_hint or f"{npc_name} takes action."

    return {
        "event_id": f"npc_event:{tick}:{npc_id}:{action_type}:{target_id or 'none'}",
        "tick": int(tick),
        "type": action_type,
        "actor": npc_id,
        "target_id": target_id,
        "target_kind": target_kind,
        "location_id": location_id,
        "faction_id": _safe_str_p6(npc_context.get("faction_id")),
        "summary": public_summary,
        "internal_reason": _safe_str_p6(decision_dict.get("reason")),
        "salience": min(max(urgency, 0.2), 1.0),
        "source": "npc_mind",
    }


# ---------------------------------------------------------------------------
# Phase 6.5 — Social Simulation helpers
# ---------------------------------------------------------------------------


def _load_social_state(current):
    """Load social state from history state, initializing defaults."""
    current = current or {}
    social_state = current.get("social_state") or {}
    return {
        "reputation": ReputationGraph.from_dict(social_state.get("reputation")).to_dict(),
        "alliances": AllianceSystem.from_dict(social_state.get("alliances")).to_dict(),
        "rumors": RumorSystem.from_dict(social_state.get("rumors")).to_dict(),
        "group_positions": GroupDecisionEngine.from_dict(social_state.get("group_positions")).to_dict(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cap(value: int, lo: int = 0, hi: int = PRESSURE_CAP) -> int:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _step_up(v: int) -> int:
    """Increment pressure by one level, capped at 5."""
    return _cap(v + 1)


def _step_down(v: int) -> int:
    """Decrement pressure by one level, floored at 0."""
    return _cap(v - 1)


def _thread_status(pressure: int) -> str:
    if pressure <= 1:
        return "low"
    if pressure <= 3:
        return "active"
    return "critical"


def _faction_status(pressure: int) -> str:
    if pressure == 0:
        return "stable"
    if pressure <= 2:
        return "watchful"
    return "strained"


def _location_status(heat: int) -> str:
    if heat <= 1:
        return "quiet"
    if heat <= 3:
        return "active"
    return "hot"


# ---------------------------------------------------------------------------
# Payload introspection helpers
# ---------------------------------------------------------------------------


def _extract_threads(setup: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the regenerated threads from setup metadata."""
    meta = _safe_dict(setup.get("metadata"))
    return _safe_list(meta.get("regenerated_threads"))


def _extract_factions(setup: dict[str, Any]) -> list[dict[str, Any]]:
    return _safe_list(setup.get("factions"))


def _extract_locations(setup: dict[str, Any]) -> list[dict[str, Any]]:
    return _safe_list(setup.get("locations"))


def _extract_npcs(setup: dict[str, Any]) -> list[dict[str, Any]]:
    return _safe_list(setup.get("npc_seeds"))


# ---------------------------------------------------------------------------
# Simulation state builders
# ---------------------------------------------------------------------------


def build_initial_simulation_state(setup_payload: dict[str, Any]) -> dict[str, Any]:
    """Create a fresh tick-0 simulation state from the setup payload.

    Reads threads, factions, locations, and NPCs to compute initial
    pressures and heat values.
    """
    setup = _safe_dict(setup_payload)

    threads = _extract_threads(setup)
    factions = _extract_factions(setup)
    locations = _extract_locations(setup)
    npcs = _extract_npcs(setup)

    # Build lookup structures
    faction_ids = {f.get("faction_id") for f in factions if f.get("faction_id")}
    location_ids = {loc.get("location_id") for loc in locations if loc.get("location_id")}

    # NPC → location map
    npc_location_map: dict[str, list[str]] = {}
    for npc in npcs:
        loc_id = npc.get("location_id")
        if loc_id and loc_id in location_ids:
            npc_location_map.setdefault(loc_id, []).append(npc.get("npc_id", ""))

    # Thread → faction / location maps
    thread_faction_map: dict[str, list[str]] = {}  # thread_id → [faction_ids]
    thread_location_map: dict[str, list[str]] = {}  # thread_id → [location_ids]
    location_thread_count: dict[str, int] = {}  # location_id → thread count

    for thr in threads:
        tid = thr.get("thread_id", "")
        fac_ids = [
            fid for fid in _safe_list(thr.get("faction_ids"))
            if fid and fid in faction_ids
        ]
        loc_ids = [
            lid for lid in _safe_list(thr.get("location_ids"))
            if lid and lid in location_ids
        ]
        thread_faction_map[tid] = fac_ids
        thread_location_map[tid] = loc_ids
        for lid in loc_ids:
            location_thread_count[lid] = location_thread_count.get(lid, 0) + 1

    # --- Compute location heat first (needed by thread pressure) ---
    loc_states: dict[str, dict[str, Any]] = {}
    for loc in locations:
        lid = loc.get("location_id", "")
        if not lid:
            continue
        npc_count = len(npc_location_map.get(lid, []))
        thread_count = location_thread_count.get(lid, 0)
        heat = _cap(npc_count + thread_count)
        loc_states[lid] = {"heat": heat, "status": _location_status(heat)}

    # --- Compute thread pressure ---
    thr_states: dict[str, dict[str, Any]] = {}
    for thr in threads:
        tid = thr.get("thread_id", "")
        if not tid:
            continue
        fac_count = len(thread_faction_map.get(tid, []))
        pressure = 1  # base
        if fac_count > 1:
            pressure += fac_count - 1
        # +1 if connected to any hot location
        for lid in thread_location_map.get(tid, []):
            loc_st = loc_states.get(lid, {})
            if loc_st.get("status") == "hot":
                pressure += 1
                break
        pressure = _cap(pressure)
        thr_states[tid] = {"pressure": pressure, "status": _thread_status(pressure)}

    # --- Compute faction pressure ---
    # Build reverse map: faction_id → list of thread statuses
    faction_thread_statuses: dict[str, list[str]] = {}
    for tid, fac_ids in thread_faction_map.items():
        thr_st = thr_states.get(tid, {})
        for fid in fac_ids:
            faction_thread_statuses.setdefault(fid, []).append(
                thr_st.get("status", "low")
            )

    fac_states: dict[str, dict[str, Any]] = {}
    for fac in factions:
        fid = fac.get("faction_id", "")
        if not fid:
            continue
        active_critical = sum(
            1 for s in faction_thread_statuses.get(fid, [])
            if s in ("active", "critical")
        )
        pressure = _cap(active_critical)
        fac_states[fid] = {"pressure": pressure, "status": _faction_status(pressure)}

    after_state = {
        "tick": 0,
        "threads": thr_states,
        "factions": fac_states,
        "locations": loc_states,
        "history": [],
        "events": [],
        "consequences": [],
        "active_effects": [],
        "incidents": [],
        "policy_reactions": [],
    }
    after_state["step_hash"] = _step_hash({
        "tick": 0,
        "threads": thr_states,
        "factions": fac_states,
        "locations": loc_states,
        "active_effects": [],
        "incidents": [],
    })
    return after_state


def step_simulation_state(
    setup_payload: dict[str, Any],
    *,
    # Required: a caller without narration passes NO_CONVERSATION_HOOKS explicitly.
    conversation_hooks: ConversationHooks,
) -> dict[str, Any]:
    """Advance the simulation by one tick.

    Returns ``{"next_setup": ..., "before_state": ..., "after_state": ...}``.

    If ``metadata.simulation_state`` is missing the initial state is
    computed first.  The updated simulation state is written into a
    *copy* of the setup so the original is never mutated.

    Uses **stateful pressure evolution**: each entity's pressure/heat
    steps up or down from its previous value based on current conditions,
    rather than being recomputed from scratch.
    """
    setup = copy.deepcopy(_safe_dict(setup_payload))
    meta = setup.setdefault("metadata", {})

    # Ensure we have a current simulation state
    current = _safe_dict(meta.get("simulation_state"))
    if not current or "tick" not in current:
        current = build_initial_simulation_state(setup)

    before_state = copy.deepcopy(current)
    before_effects = _safe_list(current.get("active_effects"))
    before_incidents = _safe_list(current.get("incidents"))
    before_reactions = _safe_list(current.get("policy_reactions"))
    next_tick = current.get("tick", 0) + 1

    # Extract current setup data for condition evaluation
    threads = _extract_threads(setup)
    factions = _extract_factions(setup)
    locations = _extract_locations(setup)
    npcs = _extract_npcs(setup)

    # Build lookup structures
    faction_ids = {f.get("faction_id") for f in factions if f.get("faction_id")}
    location_ids = {loc.get("location_id") for loc in locations if loc.get("location_id")}

    # NPC → location map
    npc_location_map: dict[str, list[str]] = {}
    for npc in npcs:
        loc_id = npc.get("location_id")
        if loc_id and loc_id in location_ids:
            npc_location_map.setdefault(loc_id, []).append(npc.get("npc_id", ""))

    # Thread → faction / location maps
    thread_faction_map: dict[str, list[str]] = {}
    thread_location_map: dict[str, list[str]] = {}
    location_thread_count: dict[str, int] = {}

    for thr in threads:
        tid = thr.get("thread_id", "")
        fac_ids = [
            fid for fid in _safe_list(thr.get("faction_ids"))
            if fid and fid in faction_ids
        ]
        loc_ids = [
            lid for lid in _safe_list(thr.get("location_ids"))
            if lid and lid in location_ids
        ]
        thread_faction_map[tid] = fac_ids
        thread_location_map[tid] = loc_ids
        for lid in loc_ids:
            location_thread_count[lid] = location_thread_count.get(lid, 0) + 1

    # Build reverse map: faction_id → list of connected thread_ids
    faction_thread_map: dict[str, list[str]] = {}
    for tid, fac_ids in thread_faction_map.items():
        for fid in fac_ids:
            faction_thread_map.setdefault(fid, []).append(tid)

    # Get previous states for stateful evolution
    prev_threads = _safe_dict(current.get("threads"))
    prev_factions = _safe_dict(current.get("factions"))
    prev_locations = _safe_dict(current.get("locations"))

    # --- Step 1: Compute location heat with stateful evolution ---
    loc_states: dict[str, dict[str, Any]] = {}
    for loc in locations:
        lid = loc.get("location_id", "")
        if not lid:
            continue
        npc_count = len(npc_location_map.get(lid, []))
        thread_count = location_thread_count.get(lid, 0)
        prev_heat = prev_locations.get(lid, {}).get("heat", 0)

        if npc_count + thread_count > 0:
            heat = _step_up(prev_heat)
        else:
            heat = _step_down(prev_heat)

        loc_states[lid] = {"heat": heat, "status": _location_status(heat)}

    # --- Step 2: Compute thread pressure with stateful evolution ---
    thr_states: dict[str, dict[str, Any]] = {}
    for thr in threads:
        tid = thr.get("thread_id", "")
        if not tid:
            continue
        fac_count = len(thread_faction_map.get(tid, []))
        prev_pressure = prev_threads.get(tid, {}).get("pressure", 1)

        # Determine escalation conditions
        delta = 0
        if fac_count > 1:
            delta += 1
        # +1 if connected to any hot location
        for lid in thread_location_map.get(tid, []):
            loc_st = loc_states.get(lid, {})
            if loc_st.get("status") == "hot":
                delta += 1
                break

        if delta > 0:
            pressure = _step_up(prev_pressure)
        else:
            pressure = _step_down(prev_pressure)

        thr_states[tid] = {"pressure": pressure, "status": _thread_status(pressure)}

    # --- Step 3: Compute faction pressure with stateful evolution ---
    fac_states: dict[str, dict[str, Any]] = {}
    for fac in factions:
        fid = fac.get("faction_id", "")
        if not fid:
            continue
        active_thread_ids = faction_thread_map.get(fid, [])
        active_threads_count = len(active_thread_ids)
        prev_pressure = prev_factions.get(fid, {}).get("pressure", 0)

        if active_threads_count > 0:
            pressure = _step_up(prev_pressure)
        else:
            pressure = _step_down(prev_pressure)

        fac_states[fid] = {"pressure": pressure, "status": _faction_status(pressure)}

    # --- Step 4: Faction → location feedback loop ---
    # If faction_pressure >= 3, push heat up on connected locations
    for fid, fstate in fac_states.items():
        faction_pressure = fstate.get("pressure", 0)
        if faction_pressure >= 3:
            # Find locations connected to this faction's threads
            for tid in faction_thread_map.get(fid, []):
                for lid in thread_location_map.get(tid, []):
                    if lid in loc_states:
                        loc_states[lid]["heat"] = _step_up(loc_states[lid]["heat"])
                        loc_states[lid]["status"] = _location_status(loc_states[lid]["heat"])

    # Build after_state
    after_state: dict[str, Any] = {
        "tick": next_tick,
        "threads": thr_states,
        "factions": fac_states,
        "locations": loc_states,
        "history": list(current.get("history", [])),
    }

    # 1. Compute BASE diff (natural changes only, no effects)
    base_diff = compute_simulation_diff(before_state, after_state)
    events = generate_world_events(base_diff)
    consequences = generate_consequences(events)

    # 2. Build/merge/decay active effects.
    new_effects = build_effects_from_consequences(consequences)
    merged_effects = merge_active_effects(before_effects, new_effects)
    after_effects = decay_active_effects(merged_effects)
    after_state["active_effects"] = after_effects

    # 3. Apply effects separately (clean layering)
    after_state_with_effects = apply_effects_to_simulation_state(copy.deepcopy(after_state))

    effect_applied_diff = compute_simulation_diff(after_state, after_state_with_effects)
    final_diff = compute_simulation_diff(before_state, after_state_with_effects)
    effect_diff = compute_effect_diff(before_effects, after_effects)

    # 4. Spawn incidents and policy reactions from the updated state diff.
    new_incidents = spawn_incidents_from_state_diff(final_diff)
    merged_incidents = merge_incidents(before_incidents, new_incidents)
    after_incidents = decay_incidents(merged_incidents)
    after_state_with_effects["incidents"] = after_incidents

    after_reactions = generate_policy_reactions(final_diff)
    after_state_with_effects["policy_reactions"] = after_reactions

    incident_diff = compute_incident_diff(before_incidents, after_incidents)
    reaction_diff = compute_policy_reaction_diff(before_reactions, after_reactions)

    summary = summarize_simulation_step(
        final_diff,
        events=events,
        consequences=consequences,
        effect_diff=effect_diff,
        incident_diff=incident_diff,
        reaction_diff=reaction_diff,
    )

    # Use base_after state for history, but record final_diff for change counts
    history_state = after_state_with_effects
    history_state["history"] = list(current.get("history", []))
    history_state["history"].append({
        "tick": next_tick,
        "summary": summary,
        "changes": {
            "threads": len(final_diff.get("threads_changed", [])),
            "factions": len(final_diff.get("factions_changed", [])),
            "locations": len(final_diff.get("locations_changed", [])),
            "events": len(events),
            "consequences": len(consequences),
            "effects": len(_safe_list(effect_diff.get("added"))),
            "incidents": len(_safe_list(incident_diff.get("added"))),
            "reactions": len(_safe_list(reaction_diff.get("added"))),
        },
    })
    if len(history_state["history"]) > MAX_HISTORY:
        history_state["history"] = history_state["history"][-MAX_HISTORY:]

    # Add step hash for traceability (on final state with effects)
    history_state["step_hash"] = _step_hash({
        "tick": history_state["tick"],
        "threads": history_state["threads"],
        "factions": history_state["factions"],
        "locations": history_state["locations"],
        "active_effects": history_state.get("active_effects", []),
        "incidents": history_state.get("incidents", []),
    })
    history_state["events"] = events
    history_state["consequences"] = consequences

    # --- Phase 6: NPC Mind Integration ---
    npc_index = _build_npc_index(setup)
    npc_minds = _load_npc_minds(current, npc_index)

    observed_events = []
    for bucket_name in ("events", "consequences", "incidents"):
        bucket = history_state.get(bucket_name) or []
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, dict):
                    observed_events.append(item)

    new_npc_decisions = []
    new_npc_events = []

    for npc_id, mind in sorted(npc_minds.items()):
        npc_context = dict(npc_index.get(npc_id) or {"npc_id": npc_id})
        npc_context["npc_index"] = npc_index
        mind.observe_events(observed_events, tick=next_tick, npc_context=npc_context)
        mind.refresh_goals(simulation_state=history_state, npc_context=npc_context)
        decision = mind.decide(simulation_state=history_state, npc_context=npc_context, tick=next_tick)
        decision_dict = decision.to_dict()
        new_npc_decisions.append(decision_dict)

        npc_event = _decision_to_event(decision_dict, npc_context=npc_context, tick=next_tick)
        if npc_event is not None:
            new_npc_events.append(npc_event)

    new_npc_decisions = sorted(
        new_npc_decisions,
        key=lambda item: (
            str(item.get("npc_id") or ""),
            str(item.get("action_type") or ""),
            str(item.get("target_id") or ""),
        ),
    )[:12]

    # Canonical decision map for downstream systems.
    # IMPORTANT:
    # ambient_builder.py, ambient_dialogue.py, and world_event_director.py
    # all expect npc_decisions to be a dict keyed by npc_id.
    npc_decision_map = {
        str(item.get("npc_id") or ""): item
        for item in new_npc_decisions
        if str(item.get("npc_id") or "")
    }

    new_npc_events = sorted(
        new_npc_events,
        key=lambda item: (
            str(item.get("actor") or ""),
            str(item.get("type") or ""),
            str(item.get("target_id") or ""),
        ),
    )[:12]

    history_state["npc_index"] = npc_index
    history_state["npc_minds"] = {
        npc_id: mind.to_dict()
        for npc_id, mind in sorted(npc_minds.items())
    }
    history_state["npc_decisions"] = dict(sorted(npc_decision_map.items()))

    existing_events = history_state.get("events") or []
    history_state["events"] = list(existing_events) + list(new_npc_events)

    # --- Phase 6.5: Social Simulation ---
    social_state = _load_social_state(history_state)

    reputation = ReputationGraph.from_dict(social_state.get("reputation"))
    alliances = AllianceSystem.from_dict(social_state.get("alliances"))
    rumors = RumorSystem.from_dict(social_state.get("rumors"))
    group_engine = GroupDecisionEngine.from_dict(social_state.get("group_positions"))

    all_events = []
    for bucket_name in ("events", "consequences"):
        bucket = history_state.get(bucket_name) or []
        if isinstance(bucket, list):
            for item in bucket:
                if isinstance(item, dict):
                    all_events.append(item)

    # 6.5.1 reputation updates from player-facing and betrayal events
    for event in all_events:
        typ = str(event.get("type") or "")
        actor = str(event.get("actor") or "")
        target_id = str(event.get("target_id") or "")
        faction_id = str(event.get("faction_id") or "")

        if actor == "player" and faction_id:
            if typ == "player_support":
                reputation.update(faction_id, "player", "trust", 0.20)
                reputation.update(faction_id, "player", "respect", 0.10)
            elif typ == "player_escalation":
                reputation.update(faction_id, "player", "hostility", 0.20)
                reputation.update(faction_id, "player", "fear", 0.10)

        if typ == "betrayal":
            source_id = str(event.get("source_id") or "")
            if source_id and target_id:
                reputation.update(source_id, target_id, "hostility", 0.40)
                reputation.update(target_id, source_id, "trust", -0.40)
                reputation.update(target_id, source_id, "hostility", 0.20)
                reputation.update(target_id, source_id, "fear", 0.10)

    # 6.5.2 alliances
    sorted_faction_ids = sorted({
        str(npc.get("faction_id") or "")
        for npc in (history_state.get("npc_index") or {}).values()
        if str(npc.get("faction_id") or "")
    })
    for any in sorted_faction_ids:
        faction_view = reputation.get(any, "player")
        if faction_view.get("hostility", 0.0) >= 0.30:
            opposing = [
                other for other in sorted_faction_ids
                if other != any and reputation.get(other, "player").get("hostility", 0.0) >= 0.30
            ]
            if opposing:
                best = sorted(opposing, key=lambda x: (reputation.get(x, "player").get("hostility", 0), x))[0]
                alliances.propose_or_strengthen([any, best], "Shared hostility toward player", delta=0.10)

    # 6.5.3 betrayal propagation
    social_events = []
    for event in all_events:
        social_events.extend(BetrayalPropagation.apply(event, social_state))

    if social_events:
        history_state.setdefault("events", []).extend(social_events[:8])

    # 6.5.4 rumors
    rumors.spawn_from_events(all_events + social_events, tick=next_tick)
    rumors.advance()

    # 6.5.5 group positions from NPC minds
    npc_index = history_state.get("npc_index") or {}
    npc_minds = history_state.get("npc_minds") or {}
    members_by_faction: dict[Any, Any] = {}
    for npc_id, npc in sorted(npc_index.items()):
        faction_id = str(npc.get("faction_id") or "")
        if not faction_id:
            continue
        members_by_faction.setdefault(faction_id, {})
        if npc_id in npc_minds and isinstance(npc_minds[npc_id], dict):
            members_by_faction[faction_id][npc_id] = npc_minds[npc_id]

    for faction_id, member_minds in sorted(members_by_faction.items()):
        group_engine.evaluate_faction(faction_id, member_minds, tick=next_tick)

    history_state["social_state"] = {
        "reputation": reputation.to_dict(),
        "alliances": alliances.to_dict(),
        "rumors": rumors.to_dict(),
        "group_positions": group_engine.to_dict(),
    }
    history_state["active_rumors"] = rumors.active(limit=8)

    # --- Phase 8.3: sandbox/world simulation depth ---
    projected_outcomes = project_outcomes_from_state(history_state)
    history_state.setdefault("sandbox_state", {})
    history_state["sandbox_state"]["projected_outcomes"] = projected_outcomes[:24]

    history_state = update_location_trends(history_state, projected_outcomes)
    history_state = update_thread_trends(history_state, projected_outcomes)
    history_state = update_faction_trends(history_state, projected_outcomes)
    history_state = update_rumor_feedback(history_state, projected_outcomes)
    history_state = build_world_consequences(history_state, projected_outcomes)

    history_state["sandbox_summary"] = {
        "projected_outcome_count": len(projected_outcomes),
        "world_consequence_count": len((history_state.get("sandbox_state") or {}).get("world_consequences") or []),
    }

    history_state.setdefault("save_meta", {})
    history_state["save_meta"]["schema_version"] = CURRENT_RPG_SCHEMA_VERSION
    history_state["save_meta"]["engine_version"] = ENGINE_VERSION

    history_state.setdefault("timeline", {})
    timeline_events = list((history_state["timeline"].get("ticks") or []))
    timeline_events.append({
        "tick": int(history_state.get("tick", 0) or 0),
        "event_count": len(history_state.get("events") or []),
        "consequence_count": len(history_state.get("consequences") or []),
        "sandbox_summary": dict(history_state.get("sandbox_summary") or {}),
    })
    history_state["timeline"]["ticks"] = timeline_events[-200:]

    # --- Phase 7: GM overrides ---
    gm_overrides = history_state.get("gm_overrides") or {}

    forced_positions = gm_overrides.get("forced_faction_positions") or {}
    if forced_positions:
        history_state.setdefault("social_state", {})
        history_state["social_state"].setdefault("group_positions", {})
        for faction_id, position in sorted(forced_positions.items()):
            history_state["social_state"]["group_positions"][str(faction_id)] = dict(position or {})

    forced_npc_beliefs = gm_overrides.get("forced_npc_beliefs") or {}
    if forced_npc_beliefs:
        history_state.setdefault("npc_minds", {})
        for npc_id, belief_targets in sorted(forced_npc_beliefs.items()):
            if npc_id not in history_state["npc_minds"] or not isinstance(history_state["npc_minds"][npc_id], dict):
                continue
            mind = history_state["npc_minds"][npc_id]
            mind.setdefault("beliefs", {})
            for target_id, patch in sorted((belief_targets or {}).items()):
                mind["beliefs"][str(target_id)] = dict(patch or {})

    # --- Conversation system tick ---
    runtime_state = meta.setdefault("runtime_state", {})
    if not isinstance(runtime_state, dict):
        runtime_state = {}
        meta["runtime_state"] = runtime_state

    current_tick = int(history_state.get("tick", 0) or 0)

    # Start party-reaction conversations inside the authoritative simulation
    # step, not later in session/runtime.py.
    last_player_action = _safe_dict(runtime_state.get("last_player_action"))
    if last_player_action:
        try_start_party_reaction_conversation(
            history_state,
            runtime_state,
            last_player_action,
            current_tick,
        )

    run_conversation_tick(history_state, runtime_state, current_tick, conversation_hooks)

    if history_state.get("debug_meta") is None or not isinstance(history_state.get("debug_meta"), dict):
        history_state["debug_meta"] = {}
    history_state["debug_meta"]["conversation_debug"] = {
        "tick": current_tick,
        "active_conversation_count": len(
            _safe_list(_safe_dict(_safe_dict(history_state.get("social_state")).get("conversations")).get("active"))
        ),
        "recent_event_types": [
            _safe_str_p6(_safe_dict(e).get("type"))
            for e in _safe_list(history_state.get("events"))[-6:]
        ],
    }

    # Later expansion: offscreen conversation summaries also belong to the
    # world tick so they remain deterministic and bounded.
    run_offscreen_conversation_pass(history_state, runtime_state, current_tick)

    # Later expansion: closed conversations can feed rumor state.
    rumors = collect_rumors_from_recent_conversations(history_state)
    if rumors:
        social_state = history_state.setdefault("social_state", {})
        if not isinstance(social_state, dict):
            social_state = {}
            history_state["social_state"] = social_state
        rumor_rows = social_state.setdefault("rumors", [])
        if not isinstance(rumor_rows, list):
            rumor_rows = []
            social_state["rumors"] = rumor_rows
        emitted_sources_list = social_state.setdefault("emitted_rumor_sources", [])
        if not isinstance(emitted_sources_list, list):
            emitted_sources_list = []
            social_state["emitted_rumor_sources"] = emitted_sources_list
        emitted_sources = {str(x) for x in emitted_sources_list if str(x)}
        for rumor in rumors:
            source_id = cast(Any, rumor.get("source_conversation_id"))
            if source_id and source_id not in emitted_sources:
                rumor_rows.append(rumor)
                emitted_sources.add(source_id)
        social_state["rumors"] = rumor_rows[-80:]
        social_state["emitted_rumor_sources"] = sorted(emitted_sources)[-200:]

    # --- Phase 7: Debug Meta Tracking ---
    history_state.setdefault("debug_meta", {})
    history_state["debug_meta"]["last_step_reason"] = history_state["debug_meta"].get("last_step_reason") or "manual_step"
    history_state["debug_meta"]["last_step_tick"] = int(history_state.get("tick", 0) or 0)
    history_state["debug_meta"]["last_tick_changes"] = summarize_tick_changes(before_state, history_state)

    # Write back into setup copy (final state with effects applied)
    meta["simulation_state"] = history_state
    setup["metadata"] = meta

    return {
        "next_setup": setup,
        "before_state": before_state,
        "after_state": history_state,
        "after_state_base": after_state,
        "simulation_diff": final_diff,
        "base_diff": base_diff,
        "effect_applied_diff": effect_applied_diff,
        "summary": summary,
        "events": events,
        "consequences": consequences,
        "effect_diff": effect_diff,
        "incident_diff": incident_diff,
        "reaction_diff": reaction_diff,
    }
