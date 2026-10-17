"""Phase 9.3 — Companion Narrative Integration.

Provides deterministic companion narrative hooks for scene interjections,
dialogue context, choice reactions, and bounded narrative history.

Key guarantees:
    - Companion interjections are deterministic (sorted by npc_id)
    - Downed/absent companions do not narrate
    - Loyalty/morale influence narrative stance
    - Narrative history is bounded (max 20 entries)
    - Builders compute, recorders mutate — never blur
    - Deduplication prevents same-event duplication
"""
from __future__ import annotations

from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import dict_copy as _safe_dict, list_copy as _safe_list, safe_str as _safe_str

_MAX_INTERJECTIONS = 3
_MAX_DIALOGUE_COMPANIONS = 3
_MAX_NARRATIVE_HISTORY = 20


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _normalize_narrative_state(party_state: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize narrative_state, ensuring correct types even if corrupted."""
    party_state = _safe_dict(party_state)
    ns = _safe_dict(party_state.get("narrative_state"))

    history = ns.get("history")
    last_interjection = ns.get("last_interjection")
    last_scene_reactions = ns.get("last_scene_reactions")

    return {
        "history": history if isinstance(history, list) else [],
        "last_interjection": last_interjection if isinstance(last_interjection, dict) else {},
        "last_scene_reactions": last_scene_reactions if isinstance(last_scene_reactions, list) else [],
    }


def _pick_tone(comp: Dict[str, Any]) -> str:
    """Pick a deterministic narrative tone based on companion state.

    Tone selection is derived from loyalty and morale values.
    """
    loyalty = _safe_float(comp.get("loyalty"), 0.0)
    morale = _safe_float(comp.get("morale"), 0.5)
    if loyalty < -0.3:
        return "resentful"
    if morale < 0.3:
        return "fearful"
    if loyalty > 0.6 and morale > 0.6:
        return "supportive"
    return "guarded"


def _sort_companions(companions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort companions by npc_id for deterministic output."""
    return sorted(companions, key=lambda c: str(c.get("npc_id", "")))


def _sort_interjections(interjections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort interjections by npc_id for deterministic output."""
    return sorted(interjections, key=lambda i: str(i.get("npc_id", "")))


def build_companion_presence_summary(player_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a summary of companions present for narrative context.

    Only includes companions with status='active' (not downed/absent).
    """
    from .party_state import (
        build_party_summary,
        ensure_party_state,
        get_active_companions,
    )

    player_state = ensure_party_state(player_state)
    # Only active companions are considered "present"
    companions = [
        c for c in get_active_companions(player_state)
        if _safe_str(c.get("status")) == "active"
    ]
    companions = _sort_companions(companions)[:_MAX_DIALOGUE_COMPANIONS]

    return {
        "party_summary": build_party_summary(player_state),
        "present_companions": [
            {
                "npc_id": _safe_str(comp.get("npc_id")),
                "name": _safe_str(comp.get("name")),
                "role": _safe_str(comp.get("role")),
                "tone": _pick_tone(comp),
                "loyalty": _safe_float(comp.get("loyalty"), 0.0),
                "morale": _safe_float(comp.get("morale"), 0.5),
            }
            for comp in companions
        ],
    }


def build_companion_scene_context(simulation_state: Dict[str, Any], scene_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build companion context for scene payloads.

    This is a pure builder — does not mutate state.
    """
    simulation_state = _safe_dict(simulation_state)
    player_state = _safe_dict(simulation_state.get("player_state"))
    scene_state = _safe_dict(scene_state)

    presence = build_companion_presence_summary(player_state)

    return {
        "scene_id": _safe_str(scene_state.get("scene_id")),
        "location_id": _safe_str(scene_state.get("location_id")),
        "present_companions": presence.get("present_companions", []),
        "party_summary": presence.get("party_summary", {}),
    }


def build_companion_dialogue_context(simulation_state: Dict[str, Any], dialogue_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build companion context for dialogue payloads.

    This is a pure builder — does not mutate state.
    """
    from .party_state import ensure_party_state, get_active_companions

    simulation_state = _safe_dict(simulation_state)
    player_state = ensure_party_state(_safe_dict(simulation_state.get("player_state")))
    dialogue_state = _safe_dict(dialogue_state)

    companions = [
        c for c in get_active_companions(player_state)
        if _safe_str(c.get("status")) == "active"
    ]
    companions = _sort_companions(companions)[:_MAX_DIALOGUE_COMPANIONS]

    return {
        "dialogue_active": bool(dialogue_state),
        "dialogue_target_id": _safe_str(dialogue_state.get("target_id")),
        "companions": [
            {
                "npc_id": _safe_str(comp.get("npc_id")),
                "name": _safe_str(comp.get("name")),
                "role": _safe_str(comp.get("role")),
                "tone": _pick_tone(comp),
            }
            for comp in companions
        ],
    }


def choose_scene_interjections(simulation_state: Dict[str, Any], scene_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Choose deterministic scene interjections for active companions.

    Only active (non-downed, non-absent) companions are considered.
    Selection is deterministic: sorted by npc_id.
    """
    from .party_state import ensure_party_state, get_active_companions

    simulation_state = _safe_dict(simulation_state)
    player_state = ensure_party_state(_safe_dict(simulation_state.get("player_state")))
    scene_state = _safe_dict(scene_state)

    all_companions = get_active_companions(player_state)
    # Filter absent companions — they should not interject
    companions = _sort_companions(
        [c for c in all_companions if _safe_str(c.get("status")) not in ("downed", "absent")]
    )[:_MAX_INTERJECTIONS]

    location_id = _safe_str(scene_state.get("location_id"))
    scene_tone = _safe_str(scene_state.get("tone"))
    interjections: List[Dict[str, Any]] = []

    for comp in companions:
        tone = _pick_tone(comp)
        name = _safe_str(comp.get("name") or comp.get("npc_id"))
        role = _safe_str(comp.get("role"))

        if tone == "supportive":
            summary = f"{name} encourages the player to press on."
        elif tone == "fearful":
            summary = f"{name} warns the party to stay cautious."
        elif tone == "resentful":
            summary = f"{name} keeps their distance and sounds unconvinced."
        elif role == "support":
            summary = f"{name} quietly watches the group's condition."
        else:
            summary = f"{name} studies the surroundings at {location_id or 'the scene'}."

        interjections.append({
            "type": "companion_interjection",
            "npc_id": _safe_str(comp.get("npc_id")),
            "name": name,
            "tone": tone,
            "summary": summary,
        })

    return _sort_interjections(interjections)


def build_companion_scene_reactions(player_state: Dict[str, Any], scene_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build reaction summaries for companions based on scene state.

    Only active companions react. Reaction stance is influenced by loyalty/morale.
    """
    from .party_state import ensure_party_state, get_active_companions

    player_state = ensure_party_state(player_state)
    scene_state = _safe_dict(scene_state)

    all_companions = get_active_companions(player_state)
    # Filter absent companions — they should not react
    companions = _sort_companions(
        [c for c in all_companions if _safe_str(c.get("status")) not in ("downed", "absent")]
    )

    reactions: List[Dict[str, Any]] = []

    for comp in companions:
        loyalty = _safe_float(comp.get("loyalty"), 0.0)
        morale = _safe_float(comp.get("morale"), 0.5)
        name = _safe_str(comp.get("name") or comp.get("npc_id"))
        npc_id = _safe_str(comp.get("npc_id"))

        # Determine stance based on loyalty/morale
        if loyalty < -0.3:
            stance = "negative"
        elif loyalty > 0.6 and morale > 0.6:
            stance = "positive"
        elif morale < 0.3:
            stance = "hesitant"
        else:
            stance = "neutral"

        reactions.append({
            "npc_id": npc_id,
            "name": name,
            "stance": stance,
            "tone": _pick_tone(comp),
            "summary": f"{name} reacts to the scene with a {stance} stance.",
        })

    return reactions


def apply_companion_choice_reactions(simulation_state: Dict[str, Any], choice_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply loyalty/morale deltas to companions based on choice tags.

    Mutates simulation_state by updating player_state party companions.
    Returns updated simulation_state with _companion_reaction_events.
    """
    from .party_state import (
        ensure_party_state,
        get_active_companions,
        update_companion_loyalty,
        update_companion_morale,
    )

    simulation_state = _safe_dict(simulation_state)
    player_state = ensure_party_state(_safe_dict(simulation_state.get("player_state")))
    choice_payload = _safe_dict(choice_payload)

    choice_tags = {_safe_str(tag) for tag in _safe_list(choice_payload.get("tags"))}
    reaction_events: List[Dict[str, Any]] = []

    companions = _sort_companions(get_active_companions(player_state))

    for comp in companions[:_MAX_DIALOGUE_COMPANIONS]:
        npc_id = _safe_str(comp.get("npc_id"))
        name = _safe_str(comp.get("name"))
        role = _safe_str(comp.get("role"))

        loyalty_delta = 0.0
        morale_delta = 0.0

        if "mercy" in choice_tags:
            loyalty_delta += 0.03 if role in {"support", "ally"} else 0.01
        if "cruelty" in choice_tags:
            loyalty_delta -= 0.04 if role in {"support", "ally"} else 0.02
        if "victory" in choice_tags:
            morale_delta += 0.03
        if "retreat" in choice_tags:
            morale_delta -= 0.03
        if "greed" in choice_tags:
            loyalty_delta -= 0.02
        if "generosity" in choice_tags:
            loyalty_delta += 0.02

        if loyalty_delta:
            player_state = update_companion_loyalty(player_state, npc_id, loyalty_delta)
        if morale_delta:
            player_state = update_companion_morale(player_state, npc_id, morale_delta)

        if loyalty_delta or morale_delta:
            reaction_events.append({
                "type": "companion_choice_reaction",
                "origin": "party",
                "npc_id": npc_id,
                "summary": f"{name} reacts to the player's choice.",
                "loyalty_delta": round(loyalty_delta, 3),
                "morale_delta": round(morale_delta, 3),
            })

    simulation_state["player_state"] = player_state
    simulation_state["_companion_reaction_events"] = reaction_events[:10]
    return simulation_state


def record_companion_narrative_event(player_state: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """Record a companion narrative event into bounded party history.

    Deduplication guards:
    - Same scene_id + tick + npc_id → skip (exact duplicate)
    - Same tick + npc_id + kind in recent history → skip (recent dupe)
    History is capped at _MAX_NARRATIVE_HISTORY entries (oldest dropped first).
    """
    from .party_state import ensure_party_state

    player_state = ensure_party_state(player_state)
    party_state = _safe_dict(player_state.get("party_state"))
    narrative_state = _normalize_narrative_state(party_state)

    history = list(narrative_state.get("history") or [])
    last = _safe_dict(narrative_state.get("last_interjection"))

    # Fix #1: Prevent duplicate interjections for same scene/tick/npc
    if (
        event.get("scene_id") == last.get("scene_id")
        and event.get("tick") == last.get("tick")
        and event.get("npc_id") == last.get("npc_id")
    ):
        return player_state

    # Fix #2: Deduplicate recent identical events
    for prev in history[-3:]:
        if (
            prev.get("tick") == event.get("tick")
            and prev.get("npc_id") == event.get("npc_id")
            and prev.get("kind") == event.get("kind")
        ):
            return player_state

    # Compact event for storage
    compact_event = {
        "tick": _safe_str(event.get("tick", "")),
        "scene_id": _safe_str(event.get("scene_id", "")),
        "npc_id": _safe_str(event.get("npc_id", "")),
        "kind": _safe_str(event.get("kind", "interjection")),
        "summary": _safe_str(event.get("summary", "")),
    }

    history.append(compact_event)

    # Trim to bound — oldest first
    if len(history) > _MAX_NARRATIVE_HISTORY:
        history = history[-_MAX_NARRATIVE_HISTORY:]

    narrative_state["history"] = history
    narrative_state["last_interjection"] = {
        "scene_id": event.get("scene_id"),
        "npc_id": event.get("npc_id"),
        "tick": event.get("tick"),
    }
    narrative_state.setdefault("last_scene_reactions", [])

    party_state["narrative_state"] = narrative_state
    player_state["party_state"] = party_state
    return player_state


def build_party_narrative_summary(party_state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a summary of party narrative state for timeline/inspector display."""
    party_state = _safe_dict(party_state)
    narrative_state = _normalize_narrative_state(party_state)

    history = narrative_state.get("history", [])
    last_interjection = narrative_state.get("last_interjection", {})

    return {
        "history_size": len(history),
        "last_interjection": last_interjection,
        "last_scene_reactions": _safe_list(narrative_state.get("last_scene_reactions"))[:5],
    }