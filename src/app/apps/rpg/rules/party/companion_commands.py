from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from app.apps.rpg.rules.party.companion_presence import (
    current_player_location_id,
    player_input_mentions_active_companion,
    project_active_companions_into_presence,
)
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list, safe_str as _safe_str

VALID_COMMANDS = {
    "follow",
    "stay",
    "wait",
    "guard",
    "scout",
    "support",
    "ready",
}

REJECTED_INTENT_MARKERS = {
    "teleport": "impossible_command",
    "instantly travel": "impossible_command",
    "assassinate": "unsafe_command",
    "murder": "unsafe_command",
    "kill everyone": "unsafe_command",
    "reveal secret": "unbacked_knowledge_command",
    "tell me the secret": "unbacked_knowledge_command",
    "read their mind": "impossible_command",
    "control them": "impossible_command",
}


def _normalize_text(value: Any) -> str:
    return _safe_str(value).strip().lower()


def _party_state(simulation_state: Dict[str, Any]) -> Dict[str, Any]:
    player_state = _safe_dict(simulation_state.get("player_state"))
    if not player_state:
        player_state = {}
        simulation_state["player_state"] = player_state

    party_state = _safe_dict(player_state.get("party_state"))
    if not party_state:
        party_state = {"companions": [], "max_size": 3}
        player_state["party_state"] = party_state

    if not isinstance(party_state.get("companions"), list):
        party_state["companions"] = []

    return party_state


def _command_from_text(player_input: str) -> Dict[str, Any]:
    text = _normalize_text(player_input)

    for marker, reason in REJECTED_INTENT_MARKERS.items():
        if marker in text:
            return {
                "recognized": True,
                "accepted": False,
                "command": "rejected",
                "rejection_reason": reason,
                "source": "deterministic_companion_command_runtime",
            }

    if any(token in text for token in ("follow me", "come with me", "stay with me", "with me")):
        return {
            "recognized": True,
            "accepted": True,
            "command": "follow",
            "source": "deterministic_companion_command_runtime",
        }

    if any(token in text for token in ("stay here", "wait here", "hold here", "remain here")):
        return {
            "recognized": True,
            "accepted": True,
            "command": "stay",
            "source": "deterministic_companion_command_runtime",
        }

    if any(token in text for token in ("guard", "watch the door", "keep watch", "stand guard")):
        return {
            "recognized": True,
            "accepted": True,
            "command": "guard",
            "source": "deterministic_companion_command_runtime",
        }

    if any(token in text for token in ("scout", "look ahead", "check ahead", "search ahead")):
        return {
            "recognized": True,
            "accepted": True,
            "command": "scout",
            "source": "deterministic_companion_command_runtime",
        }

    if any(token in text for token in ("help me fight", "support me", "back me up", "cover me")):
        return {
            "recognized": True,
            "accepted": True,
            "command": "support",
            "source": "deterministic_companion_command_runtime",
        }

    if "ready" in text:
        return {
            "recognized": True,
            "accepted": True,
            "command": "ready",
            "source": "deterministic_companion_command_runtime",
        }

    return {
        "recognized": False,
        "accepted": False,
        "command": "",
        "reason": "no_bounded_companion_command_detected",
        "source": "deterministic_companion_command_runtime",
    }


def _command_state_patch(command: str, location_id: str) -> Dict[str, Any]:
    if command == "follow":
        return {
            "follow_mode": "following_player",
            "companion_role_state": "following",
            "location_id": location_id,
        }

    if command in {"stay", "wait"}:
        return {
            "follow_mode": "waiting_here",
            "companion_role_state": "waiting",
            "location_id": location_id,
        }

    if command == "guard":
        return {
            "follow_mode": "waiting_here",
            "companion_role_state": "guarding",
            "location_id": location_id,
        }

    if command == "scout":
        return {
            "follow_mode": "scouting_ahead",
            "companion_role_state": "scouting",
            "location_id": location_id,
        }

    if command == "support":
        return {
            "follow_mode": "following_player",
            "companion_role_state": "supporting",
            "location_id": location_id,
        }

    if command == "ready":
        return {
            "companion_role_state": "ready",
            "location_id": location_id,
        }

    return {}


def _find_companion_index(companions: List[Any], npc_id: str) -> int:
    for index, companion in enumerate(companions):
        companion = _safe_dict(companion)
        if _safe_str(companion.get("npc_id")) == npc_id:
            return index
    return -1


def maybe_apply_companion_command(
    simulation_state: Dict[str, Any],
    *,
    player_input: str,
    tick: int = 0,
) -> Dict[str, Any]:
    """Parse and apply bounded player commands to an active companion.

    This is simulation-owned. It does not let the LLM decide whether the command
    is possible, accepted, or how party state mutates.
    """
    simulation_state = _safe_dict(simulation_state)
    mention = player_input_mentions_active_companion(simulation_state, player_input)

    if not mention.get("matched"):
        return {
            "recognized": False,
            "accepted": False,
            "reason": "no_active_companion_addressed",
            "addressed_companion": deepcopy(mention),
            "source": "deterministic_companion_command_runtime",
        }

    command_result = _command_from_text(player_input)
    npc_id = _safe_str(mention.get("npc_id"))
    name = _safe_str(mention.get("name") or npc_id.replace("npc:", ""))

    base = {
        "npc_id": npc_id,
        "name": name,
        "addressed_companion": deepcopy(mention),
        "source": "deterministic_companion_command_runtime",
    }

    if not command_result.get("recognized"):
        return {
            **base,
            "recognized": False,
            "accepted": False,
            "reason": _safe_str(command_result.get("reason")),
            "command": "",
        }

    if not command_result.get("accepted"):
        return {
            **base,
            "recognized": True,
            "accepted": False,
            "command": _safe_str(command_result.get("command") or "rejected"),
            "reason": _safe_str(command_result.get("rejection_reason") or "command_rejected"),
            "rejection_reason": _safe_str(command_result.get("rejection_reason") or "command_rejected"),
        }

    command = _safe_str(command_result.get("command"))
    if command not in VALID_COMMANDS:
        return {
            **base,
            "recognized": True,
            "accepted": False,
            "command": command,
            "reason": "unsupported_companion_command",
        }

    location_id = current_player_location_id(simulation_state)
    party_state = _party_state(simulation_state)
    companions = _safe_list(party_state.get("companions"))
    index = _find_companion_index(companions, npc_id)

    if index < 0:
        return {
            **base,
            "recognized": True,
            "accepted": False,
            "command": command,
            "reason": "companion_not_in_party",
        }

    before = deepcopy(_safe_dict(companions[index]))
    patch = _command_state_patch(command, location_id)
    updated = deepcopy(before)

    for key, value in patch.items():
        updated[key] = value

    updated["last_command_tick"] = int(tick or 0)
    updated["last_command"] = command
    updated["last_command_source"] = "player"

    companions[index] = updated
    party_state["companions"] = companions

    projection = project_active_companions_into_presence(
        simulation_state,
        location_id=current_player_location_id(simulation_state),
        tick=tick,
        reason=f"companion_command:{command}",
    )

    line = _line_for_command(name=name, command=command)

    return {
        **base,
        "recognized": True,
        "accepted": True,
        "command": command,
        "reason": "bounded_companion_command_applied",
        "before": before,
        "after": deepcopy(updated),
        "companion_presence_projection": deepcopy(projection),
        "npc_response_beat": {
            "kind": "companion_command_response",
            "speaker_id": npc_id,
            "speaker_name": name,
            "line": line,
            "command": command,
            "source": "deterministic_companion_command_runtime",
        },
    }


def _line_for_command(*, name: str, command: str) -> str:
    if command == "follow":
        return f'{name} nods. "I\'m with you."'
    if command == "stay":
        return f'{name} nods. "I\'ll hold here."'
    if command == "guard":
        return f'{name} squares his stance. "I\'ll keep watch."'
    if command == "scout":
        return f'{name} scans the way ahead. "I\'ll look, but I won\'t stray far."'
    if command == "support":
        return f'{name} grips his gear. "I\'ll back you up."'
    if command == "ready":
        return f'{name} nods. "Ready."'
    return f'{name} nods.'
