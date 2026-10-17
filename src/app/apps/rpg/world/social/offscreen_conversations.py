from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_str as _safe_str


def _npc_pairs_at_other_locations(simulation_state: Dict[str, Any], player_location: str) -> List[Dict[str, Any]]:
    """Find NPC pairs at locations the player is NOT in."""
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    by_loc: Dict[str, List[str]] = {}
    for npc_id, raw in sorted(npc_index.items()):
        row = _safe_dict(raw)
        loc = _safe_str(row.get("location_id"))
        if loc and loc != _safe_str(player_location):
            by_loc.setdefault(loc, []).append(_safe_str(npc_id))
    pairs: List[Dict[str, Any]] = []
    for loc, ids in sorted(by_loc.items()):
        for i in range(len(ids)):
            for j in range(i + 1, min(i + 3, len(ids))):
                pairs.append({"location_id": loc, "npc_a": ids[i], "npc_b": ids[j]})
    return pairs[:12]


def _summarize_offscreen_conversation(npc_a: str, npc_b: str, location_id: str, tick: int, simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a short offscreen conversation summary based on NPC roles."""
    npc_index = _safe_dict(_safe_dict(simulation_state).get("npc_index"))
    name_a = _safe_str(_safe_dict(npc_index.get(npc_a)).get("name")) or npc_a
    name_b = _safe_str(_safe_dict(npc_index.get(npc_b)).get("name")) or npc_b
    role_a = _safe_str(_safe_dict(npc_index.get(npc_a)).get("role")).lower()
    role_b = _safe_str(_safe_dict(npc_index.get(npc_b)).get("role")).lower()

    # Generate a summary based on role combinations
    if "guard" in (role_a, role_b) and "thief" in (role_a, role_b):
        summary = f"{name_a} and {name_b} exchange tense words about recent thefts."
    elif "merchant" in (role_a, role_b):
        summary = f"{name_a} and {name_b} discuss trade and local prices."
    elif "priest" in (role_a, role_b):
        summary = f"{name_a} and {name_b} reflect on recent events in the region."
    else:
        summary = f"{name_a} and {name_b} share observations about the area."

    return {
        "tick": int(tick or 0),
        "type": "offscreen_conversation",
        "location_id": _safe_str(location_id),
        "participants": [_safe_str(npc_a), _safe_str(npc_b)],
        "participant_names": [name_a, name_b],
        "summary": summary,
    }


def run_offscreen_conversation_pass(simulation_state: Dict[str, Any], runtime_state: Dict[str, Any], tick: int) -> Dict[str, Any]:
    """Generate offscreen conversation summaries for NPCs the player isn't near.

    These summaries appear in the runtime state and can feed the rumor system or
    ambient builder for "you overhear that…" style ambient lines later.
    """
    runtime_state = runtime_state if isinstance(runtime_state, dict) else {}
    simulation_state = _safe_dict(simulation_state)
    player_loc = _safe_str(
        _safe_dict(runtime_state).get("current_location_id")
        or _safe_dict(simulation_state.get("player_state")).get("location_id")
    )

    rows = runtime_state.setdefault("offscreen_conversation_summaries", [])
    if not isinstance(rows, list):
        rows = []
        runtime_state["offscreen_conversation_summaries"] = rows

    pairs = _npc_pairs_at_other_locations(simulation_state, player_loc)
    # Only generate one per tick to keep things manageable
    if pairs:
        pair = pairs[int(tick or 0) % len(pairs)]
        summary = _summarize_offscreen_conversation(
            pair["npc_a"], pair["npc_b"], pair["location_id"],
            tick, simulation_state,
        )
        rows.append(summary)

    runtime_state["offscreen_conversation_summaries"] = rows[-40:]
    return runtime_state
