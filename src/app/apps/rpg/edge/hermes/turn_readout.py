from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        candidate = _safe_dict(value)
        if candidate:
            return candidate
    return {}


def _entry_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


def _session_turns(session: dict[str, Any]) -> list[dict[str, Any]]:
    session_data = _safe_dict(session)
    state = _first_dict(session_data.get("state"), session_data.get("game"), session_data.get("simulation_state"))
    runtime_state = _safe_dict(session_data.get("runtime_state"))
    turns = (
        state.get("recent_turns")
        or state.get("turns")
        or runtime_state.get("recent_turns")
        or runtime_state.get("turns")
        or session_data.get("recent_turns")
        or session_data.get("turns")
        or []
    )
    return [_safe_dict(item) for item in _safe_list(turns) if _safe_dict(item)]


def _latest_turn_from_session(session: dict[str, Any]) -> dict[str, Any]:
    turns = _session_turns(session)
    return turns[-1] if turns else {}


def _category_for(command: str, turn: dict[str, Any]) -> str:
    explicit = _first_text(turn.get("category"), turn.get("action_category"), turn.get("intent"))
    if explicit:
        return explicit.lower()
    lowered = command.lower()
    if any(word in lowered for word in ("ask", "talk", "say", "tell")):
        return "dialogue"
    if any(word in lowered for word in ("buy", "sell", "rent", "shop")):
        return "service"
    if any(word in lowered for word in ("attack", "cast", "strike", "shoot")):
        return "combat"
    if any(word in lowered for word in ("travel", "go", "walk", "road")):
        return "travel"
    if any(word in lowered for word in ("inventory", "item", "equip")):
        return "inventory"
    if any(word in lowered for word in ("quest", "journal", "objective")):
        return "journal"
    return "general"


def _systems_for(category: str, turn: dict[str, Any], *, entry_count: int, has_grounding: bool) -> list[str]:
    systems = ["command_parser", "rpg_runtime"]
    if category == "dialogue":
        systems.append("npc_dialogue")
    elif category == "service":
        systems.append("economy_service")
    elif category == "combat":
        systems.append("combat_gate")
    elif category == "travel":
        systems.append("travel_gate")
    elif category == "inventory":
        systems.append("inventory_loadout")
    elif category == "journal":
        systems.append("journal_objectives")
    if entry_count:
        systems.append("runtime_effects")
    if has_grounding:
        systems.append("grounding_validator")
    if turn.get("narration") or turn.get("response") or turn.get("presentation"):
        systems.append("presentation")
    return systems


def hermes_rpg_turn_readout_payload(request: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(request)
    session_id = _safe_str(data.get("session_id")).strip()
    turn = _safe_dict(data.get("turn") or data.get("latest_turn"))
    if not turn and session_id:
        from app.apps.rpg.session.service import load_session

        session = load_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found", "session_id": session_id, "read_only": True, "source": "rpg_turn"}
        turn = _latest_turn_from_session(session)
    if not turn:
        return {"ok": False, "error": "missing_turn", "read_only": True, "source": "rpg_turn"}

    command = _first_text(turn.get("command"), turn.get("action"), turn.get("player_action"), turn.get("input"))
    category = _category_for(command, turn)
    effects = turn.get("effects") or turn.get("state_changes") or turn.get("delta") or turn.get("changes") or {}
    grounding = _safe_dict(turn.get("grounding") or turn.get("validation") or turn.get("grounding_validator"))
    count = _entry_count(effects)
    grounding_status = _first_text(grounding.get("status"), grounding.get("result"), grounding.get("category")) or "not_reported"

    return {
        "ok": True,
        "read_only": True,
        "source": "rpg_turn",
        "session_id": session_id or None,
        "turn": {
            "turn_id": turn.get("turn") or turn.get("turn_id") or turn.get("index") or None,
            "command": command,
            "category": category,
            "narration_present": bool(turn.get("narration") or turn.get("response") or turn.get("presentation")),
        },
        "systems": _systems_for(category, turn, entry_count=count, has_grounding=bool(grounding)),
        "effect_count": count,
        "grounding_status": grounding_status,
        "notes": ["Read-only Hermes RPG turn readout."],
    }
