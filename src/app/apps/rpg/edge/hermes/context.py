from __future__ import annotations

from typing import Any
from app.apps.rpg.foundation.safe_values import safe_dict as _safe_dict, safe_list as _safe_list


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        candidate = _safe_dict(value)
        if candidate:
            return candidate
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_str(value).strip()
        if text:
            return text
    return ""


def _name_from_item(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    data = _safe_dict(value)
    return _first_text(
        data.get("name"),
        data.get("label"),
        data.get("title"),
        data.get("id"),
    )


def _bounded_names(value: Any, *, limit: int = 8) -> list[str]:
    names: list[str] = []
    for item in _safe_list(value):
        name = _name_from_item(item)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _bounded_turn_summaries(value: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for item in _safe_list(value)[-limit:]:
        data = _safe_dict(item)
        if not data:
            continue
        turns.append(
            {
                "turn": data.get("turn") or data.get("turn_id") or data.get("index"),
                "action": _first_text(data.get("action"), data.get("player_action"), data.get("input"))[:160],
                "category": _first_text(data.get("category"), data.get("action_category"), data.get("intent"))[:80],
            }
        )
    return turns


def _active_objectives(state: dict[str, Any]) -> list[str]:
    quests = _safe_list(state.get("quests") or state.get("active_quests"))
    quest_objectives: list[str] = []
    for raw_quest in quests:
        quest = _safe_dict(raw_quest)
        if _safe_str(quest.get("status")).casefold() in {"complete", "completed", "failed"}:
            continue
        objectives = _safe_list(quest.get("objectives"))
        active = next(
            (
                _name_from_item(item)
                for item in objectives
                if _safe_str(_safe_dict(item).get("status")).casefold()
                not in {"complete", "completed", "failed"}
                and _name_from_item(item)
            ),
            "",
        )
        objective = active or _first_text(
            quest.get("next_step"),
            quest.get("objective"),
            quest.get("title"),
            quest.get("name"),
        )
        if objective and objective not in quest_objectives:
            quest_objectives.append(objective)
    if quest_objectives:
        return quest_objectives[:8]

    quest_state = _first_dict(state.get("quests"), state.get("quest_log"), state.get("journal"), state.get("objectives"))
    candidates = (
        quest_state.get("active")
        or quest_state.get("objectives")
        or quest_state.get("items")
        or state.get("active_objectives")
        or state.get("objectives")
    )
    return _bounded_names(candidates, limit=8)


def hermes_rpg_context_from_session(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded read-only context snapshot for Hermes.

    Hermes uses this as an observation surface only. The deterministic RPG
    session remains the source of truth and no session data is saved here.
    """

    session_data = _safe_dict(session)
    state = _first_dict(session_data.get("state"), session_data.get("game"), session_data.get("simulation_state"))
    runtime_state = _safe_dict(session_data.get("runtime_state"))
    manifest = _safe_dict(session_data.get("manifest"))
    player = _first_dict(state.get("player"), state.get("player_state"), session_data.get("player"))
    inventory = player.get("inventory") or state.get("inventory") or state.get("items")
    party = state.get("party") or player.get("party") or state.get("companions")
    recent_turns = (
        state.get("recent_turns")
        or state.get("turns")
        or runtime_state.get("recent_turns")
        or session_data.get("recent_turns")
    )
    combat = _first_dict(state.get("combat"), state.get("combat_state"), runtime_state.get("combat"))
    service = _first_dict(state.get("service"), state.get("service_state"), runtime_state.get("service"))
    travel = _first_dict(state.get("travel"), state.get("travel_state"), runtime_state.get("travel"))

    location = _first_text(
        state.get("current_location"),
        state.get("location"),
        state.get("place"),
        manifest.get("starting_location"),
    )
    active_npc = _first_text(
        state.get("active_npc"),
        state.get("current_npc"),
        runtime_state.get("active_npc"),
        service.get("npc"),
    )

    return {
        "ok": True,
        "read_only": True,
        "source": "rpg_session",
        "session_id": session_id,
        "context": {
            "session_id": session_id,
            "title": _first_text(session_data.get("name"), manifest.get("title"), manifest.get("name")),
            "location": location or "unknown",
            "active_npc": active_npc or None,
            "player": {
                "name": _first_text(player.get("name"), player.get("id")) or "player",
                "level": player.get("level"),
                "xp": player.get("xp") or player.get("experience"),
                "currency": _safe_dict(player.get("currency") or state.get("currency")),
            },
            "party": _bounded_names(party, limit=8),
            "inventory": _bounded_names(inventory, limit=12),
            "objectives": _active_objectives(state),
            "recent_turns": _bounded_turn_summaries(recent_turns),
            "state_flags": {
                "in_combat": bool(combat.get("active") or combat.get("in_combat")),
                "in_service": bool(service.get("active") or service.get("merchant") or service.get("innkeeper")),
                "can_travel": not bool(combat.get("active") or combat.get("in_combat")),
            },
        },
    }


def hermes_rpg_context_payload(request: dict[str, Any]) -> dict[str, Any]:
    data = _safe_dict(request)
    session_id = _safe_str(data.get("session_id")).strip()
    if not session_id:
        return {"ok": False, "error": "missing_session_id", "read_only": True, "source": "rpg_session"}

    from app.apps.rpg.session.service import load_session

    session = load_session(session_id)
    if not session:
        return {
            "ok": False,
            "error": "session_not_found",
            "session_id": session_id,
            "read_only": True,
            "source": "rpg_session",
        }
    return hermes_rpg_context_from_session(session_id, session)
