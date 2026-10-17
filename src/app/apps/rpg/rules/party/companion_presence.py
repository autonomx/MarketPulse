from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

MAX_PROJECTED_COMPANIONS = 6


def _player_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    if not player_state:
        player_state = {}
        simulation_state["player_state"] = player_state
    return player_state


def _party_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _player_state(simulation_state)
    party_state = _safe_dict(player_state.get("party_state"))
    if not party_state:
        party_state = {"companions": [], "max_size": 3}
        player_state["party_state"] = party_state
    if not isinstance(party_state.get("companions"), list):
        party_state["companions"] = []
    party_state.setdefault("max_size", 3)
    return party_state


def current_player_location_id(simulation_state: Dict[str, Any]) -> str:
    player_state = _safe_dict(simulation_state.get("player_state"))
    return (
        _safe_str(player_state.get("location_id"))
        or _safe_str(simulation_state.get("location_id"))
        or _safe_str(simulation_state.get("current_location_id"))
        or "loc_tavern"
    )


def active_party_companions(simulation_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    party_state = _safe_dict(_safe_dict(simulation_state.get("player_state")).get("party_state"))
    companions = _safe_list(party_state.get("companions"))

    active: List[Dict[str, Any]] = []
    for companion in companions:
        companion = _safe_dict(companion)
        if not companion:
            continue
        if _safe_str(companion.get("status") or "active") != "active":
            continue
        if not _safe_str(companion.get("npc_id")):
            continue
        active.append(companion)

    return active[:MAX_PROJECTED_COMPANIONS]


def sync_active_companions_to_player_location(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    tick: int = 0,
    reason: str = "",
) -> Dict[str, Any]:
    """Keep active/following companions with the player.

    This is deterministic party-state projection only. It does not invent new
    companions and does not run LLM behavior.
    """
    simulation_state = _safe_dict(simulation_state)
    target_location = _safe_str(location_id) or current_player_location_id(simulation_state)

    party_state = _party_state(simulation_state)
    companions = _safe_list(party_state.get("companions"))

    changed: List[Dict[str, Any]] = []
    normalized: List[Dict[str, Any]] = []

    for companion in companions:
        companion = deepcopy(_safe_dict(companion))
        if not companion:
            continue

        npc_id = _safe_str(companion.get("npc_id"))
        status = _safe_str(companion.get("status") or "active")
        follow_mode = _safe_str(companion.get("follow_mode") or "")

        if status == "active" and not follow_mode:
            follow_mode = "following_player"
            companion["follow_mode"] = follow_mode

        if status == "active" and follow_mode == "following_player":
            before_location = _safe_str(companion.get("location_id"))
            if before_location != target_location:
                companion["location_id"] = target_location
                changed.append({
                    "npc_id": npc_id,
                    "from_location_id": before_location,
                    "to_location_id": target_location,
                    "reason": "following_player",
                })

        # Waiting/guarding companions remain where commanded.
        if status == "active" and follow_mode in {"waiting_here", "scouting_ahead"}:
            companion.setdefault("location_id", target_location)

        companion["last_party_sync_tick"] = int(tick or 0)
        normalized.append(companion)

    party_state["companions"] = normalized

    result = {
        "synced": True,
        "changed": changed,
        "location_id": target_location,
        "reason": _safe_str(reason),
        "active_companion_count": len(active_party_companions(simulation_state)),
        "source": "deterministic_companion_presence_runtime",
    }

    simulation_state["companion_presence_projection"] = result
    return result


def project_active_companions_into_presence(
    simulation_state: Dict[str, Any],
    *,
    location_id: str = "",
    tick: int = 0,
    reason: str = "",
) -> Dict[str, Any]:
    """Project active party companions into deterministic presence state.

    This makes joined companions visible to scene/conversation systems without
    requiring them to be normal static location NPCs.
    """
    simulation_state = _safe_dict(simulation_state)
    target_location = _safe_str(location_id) or current_player_location_id(simulation_state)

    sync_result = sync_active_companions_to_player_location(
        simulation_state,
        location_id=target_location,
        tick=tick,
        reason=reason or "project_active_companions_into_presence",
    )

    present_state = _safe_dict(simulation_state.get("present_npc_state"))
    if not present_state:
        present_state = {}
        simulation_state["present_npc_state"] = present_state

    by_location = _safe_dict(present_state.get("by_location"))
    present_state["by_location"] = by_location

    location_entry = _safe_dict(by_location.get(target_location))
    if not location_entry:
        location_entry = {
            "location_id": target_location,
            "present_npcs": [],
            "source": "deterministic_present_npc_state",
        }

    existing = []
    for item in _safe_list(location_entry.get("present_npcs")):
        item = _safe_dict(item)
        if item:
            existing.append(item)

    by_npc_id: Dict[str, Dict[str, Any]] = {
        _safe_str(item.get("npc_id")): item
        for item in existing
        if _safe_str(item.get("npc_id"))
    }

    projected: List[Dict[str, Any]] = []
    for companion in active_party_companions(simulation_state):
        companion = _safe_dict(companion)
        npc_id = _safe_str(companion.get("npc_id"))
        if not npc_id:
            continue

        item = {
            "npc_id": npc_id,
            "name": _safe_str(companion.get("name")) or npc_id.replace("npc:", ""),
            "location_id": target_location,
            "presence_kind": "party_companion",
            "role": _safe_str(companion.get("role") or "companion"),
            "status": _safe_str(companion.get("status") or "active"),
            "follow_mode": _safe_str(companion.get("follow_mode") or "following_player"),
            "identity_arc": _safe_str(companion.get("identity_arc")),
            "current_role": _safe_str(companion.get("current_role")),
            "source": "deterministic_companion_presence_runtime",
        }
        by_npc_id[npc_id] = item
        projected.append(item)

    location_entry["present_npcs"] = list(by_npc_id.values())[:24]
    location_entry["last_projected_tick"] = int(tick or 0)
    location_entry["source"] = "deterministic_present_npc_state"
    by_location[target_location] = location_entry

    result = {
        "projected": True,
        "location_id": target_location,
        "projected_companions": projected,
        "sync_result": deepcopy(sync_result),
        "source": "deterministic_companion_presence_runtime",
    }

    simulation_state["companion_presence_projection"] = result
    return result


def companion_presence_summary(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    location_id = current_player_location_id(simulation_state)
    companions = []
    for companion in active_party_companions(simulation_state):
        companions.append({
            "npc_id": _safe_str(companion.get("npc_id")),
            "name": _safe_str(companion.get("name")),
            "role": _safe_str(companion.get("role")),
            "status": _safe_str(companion.get("status") or "active"),
            "follow_mode": _safe_str(companion.get("follow_mode") or "following_player"),
            "location_id": _safe_str(companion.get("location_id")),
            "identity_arc": _safe_str(companion.get("identity_arc")),
            "current_role": _safe_str(companion.get("current_role")),
        })

    return {
        "location_id": location_id,
        "active_companions": companions,
        "count": len(companions),
        "source": "deterministic_companion_presence_runtime",
    }


def player_input_mentions_active_companion(
    simulation_state: Dict[str, Any],
    player_input: str,
) -> Dict[str, Any]:
    text = _safe_str(player_input).lower()
    if not text:
        return {
            "matched": False,
            "reason": "empty_player_input",
            "source": "deterministic_companion_presence_runtime",
        }

    for companion in active_party_companions(simulation_state):
        npc_id = _safe_str(companion.get("npc_id"))
        name = _safe_str(companion.get("name") or npc_id.replace("npc:", ""))
        tokens = {
            name.lower(),
            npc_id.lower(),
            npc_id.replace("npc:", "").lower(),
        }
        tokens = {token for token in tokens if token}
        if any(token in text for token in tokens):
            return {
                "matched": True,
                "npc_id": npc_id,
                "name": name,
                "companion": deepcopy(companion),
                "reason": "player_input_mentions_active_companion",
                "source": "deterministic_companion_presence_runtime",
            }

    return {
        "matched": False,
        "reason": "no_active_companion_mentioned",
        "source": "deterministic_companion_presence_runtime",
    }


def build_party_aware_turn_context(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
) -> Dict[str, Any]:
    location_id = current_player_location_id(simulation_state)
    projection = project_active_companions_into_presence(
        simulation_state,
        location_id=location_id,
        tick=tick,
        reason="party_aware_turn_context",
    )
    mention = player_input_mentions_active_companion(simulation_state, player_input)

    return {
        "party_aware": True,
        "location_id": location_id,
        "companion_presence_summary": companion_presence_summary(simulation_state),
        "companion_presence_projection": deepcopy(projection),
        "addressed_companion": deepcopy(mention),
        "source": "deterministic_companion_presence_runtime",
    }
